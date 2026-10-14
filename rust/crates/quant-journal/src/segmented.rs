//! QJ01 segments remain the source of truth. A manifest seals completed
//! segments; the final, unlisted segment is recovered and remains appendable.
use crate::{
    Commit, DurabilityPolicy, JournalReader, JournalRecord, JournalWriter, encoded_record_bytes,
};
use serde::{Deserialize, Serialize};
use std::ffi::CString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug)]
pub struct SegmentConfig {
    pub max_bytes: u64,
    pub max_age: Duration,
    pub max_disk_bytes: u64,
    pub min_free_bytes: u64,
    pub durability: DurabilityPolicy,
}

impl SegmentConfig {
    fn validate(self) -> io::Result<()> {
        self.durability.validate()?;
        if self.max_bytes == 0 || self.max_age.is_zero() || self.max_disk_bytes == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "zero segment limit",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SealedSegment {
    pub index: u64,
    pub bytes: u64,
    pub crc32: u32,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Manifest {
    pub version: u8,
    pub sealed: Vec<SealedSegment>,
}

fn segment_path(root: &Path, index: u64) -> PathBuf {
    root.join(format!("{index:016}.qj"))
}

fn reject_orphans(root: &Path, active_index: u64) -> io::Result<()> {
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if let Some(name) = entry.file_name().to_str()
            && let Some(stem) = name.strip_suffix(".qj")
            && let Ok(found) = stem.parse::<u64>()
            && found > active_index
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "orphaned journal segment",
            ));
        }
    }
    Ok(())
}

fn checksum(path: &Path) -> io::Result<(u64, u32)> {
    let mut file = File::open(path)?;
    let mut hash = crc32fast::Hasher::new();
    let mut bytes = 0;
    let mut block = [0_u8; 64 * 1024];
    loop {
        let n = file.read(&mut block)?;
        if n == 0 {
            break;
        }
        hash.update(&block[..n]);
        bytes += n as u64;
    }
    Ok((bytes, hash.finalize()))
}

fn save_manifest(root: &Path, manifest: &Manifest) -> io::Result<()> {
    let temp = root.join("manifest.json.tmp");
    let final_path = root.join("manifest.json");
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&temp)?;
    serde_json::to_writer(&mut file, manifest)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    fs::rename(temp, final_path)?;
    File::open(root)?.sync_all()?;
    Ok(())
}

fn disk_free(root: &Path) -> io::Result<u64> {
    let path = CString::new(root.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let stats = unsafe { stats.assume_init() };
    let bytes = u128::from(stats.f_bavail).saturating_mul(u128::from(stats.f_frsize));
    Ok(u64::try_from(bytes).unwrap_or(u64::MAX))
}

pub struct SegmentedJournal {
    root: PathBuf,
    config: SegmentConfig,
    manifest: Manifest,
    active: JournalWriter,
    opened_at: Instant,
    sealed_bytes: u64,
    recovered_tail_bytes: u64,
    poisoned: bool,
}

impl SegmentedJournal {
    pub fn open(root: &Path, config: SegmentConfig) -> io::Result<Self> {
        config.validate()?;
        let root_existed = root.exists();
        fs::create_dir_all(root)?;
        if !root_existed {
            if let Some(parent) = root.parent().filter(|part| !part.as_os_str().is_empty()) {
                File::open(parent)?.sync_all()?;
            }
        }
        let path = root.join("manifest.json");
        let manifest: Manifest = if path.exists() {
            serde_json::from_reader(File::open(path)?)?
        } else {
            Manifest {
                version: 1,
                sealed: vec![],
            }
        };
        if manifest.version != 1 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "unknown manifest version",
            ));
        }
        let mut sealed_bytes = 0;
        for (expected, segment) in manifest.sealed.iter().enumerate() {
            if segment.index != expected as u64
                || checksum(&segment_path(root, segment.index))? != (segment.bytes, segment.crc32)
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "sealed segment checksum/index mismatch",
                ));
            }
            sealed_bytes += segment.bytes;
        }
        let index = manifest.sealed.len() as u64;
        reject_orphans(root, index)?;
        let active = JournalWriter::open(&segment_path(root, index), config.durability)?;
        File::open(root)?.sync_all()?;
        let recovered_tail_bytes = active.recovered_tail_bytes();
        Ok(Self {
            root: root.into(),
            config,
            manifest,
            active,
            opened_at: Instant::now(),
            sealed_bytes,
            recovered_tail_bytes,
            poisoned: false,
        })
    }

    pub fn recovered_tail_bytes(&self) -> u64 {
        self.recovered_tail_bytes
    }
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }
    pub fn disk_remaining_bytes(&self) -> io::Result<u64> {
        disk_free(&self.root)
    }
    pub fn pending(&self) -> usize {
        self.active.pending()
    }

    fn check_budget(&self, extra: u64, available: u64) -> io::Result<()> {
        let projected = self
            .sealed_bytes
            .saturating_add(self.active.estimated_bytes()?)
            .saturating_add(extra);
        if projected > self.config.max_disk_bytes
            || available < self.config.min_free_bytes.saturating_add(extra)
        {
            return Err(io::Error::other(
                "journal disk budget/free-space threshold reached",
            ));
        }
        Ok(())
    }

    pub fn append(&mut self, record: &JournalRecord) -> io::Result<Option<Commit>> {
        if self.poisoned {
            return Err(io::Error::other("segmented journal poisoned"));
        }
        let extra = encoded_record_bytes(record)?;
        // Guard before rotation as well: no event enters memory without capacity.
        self.check_budget(extra, disk_free(&self.root)?)?;
        if self.active.estimated_bytes()? > 0
            && (self.active.estimated_bytes()?.saturating_add(extra) > self.config.max_bytes
                || self.opened_at.elapsed() >= self.config.max_age)
        {
            self.rotate()?;
        }
        self.active.append(record)
    }

    pub fn sync(&mut self) -> io::Result<Commit> {
        if self.poisoned {
            return Err(io::Error::other("segmented journal poisoned"));
        }
        self.active.sync()
    }

    pub fn rotate(&mut self) -> io::Result<()> {
        if self.poisoned {
            return Err(io::Error::other("segmented journal poisoned"));
        }
        // Failure after this point must never permit another append in-process.
        self.poisoned = true;
        self.active.sync()?;
        let index = self.manifest.sealed.len() as u64;
        let path = segment_path(&self.root, index);
        let (bytes, crc32) = checksum(&path)?;
        if bytes == 0 {
            self.poisoned = false;
            return Ok(());
        }
        let mut next = self.manifest.clone();
        next.sealed.push(SealedSegment {
            index,
            bytes,
            crc32,
        });
        save_manifest(&self.root, &next)?;
        self.manifest = next;
        self.sealed_bytes += bytes;
        self.active =
            JournalWriter::open(&segment_path(&self.root, index + 1), self.config.durability)?;
        File::open(&self.root)?.sync_all()?;
        self.opened_at = Instant::now();
        self.poisoned = false;
        Ok(())
    }

    pub fn close(mut self) -> io::Result<Commit> {
        self.sync()
    }
}

pub struct SegmentedReader {
    root: PathBuf,
    sealed_len: u64,
    next_index: u64,
    current: Option<JournalReader>,
}

impl SegmentedReader {
    pub fn open(root: &Path) -> io::Result<Self> {
        let manifest: Manifest = if root.join("manifest.json").exists() {
            serde_json::from_reader(File::open(root.join("manifest.json"))?)?
        } else {
            Manifest {
                version: 1,
                sealed: vec![],
            }
        };
        if manifest.version != 1 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "unknown manifest version",
            ));
        }
        let sealed_len = manifest.sealed.len() as u64;
        reject_orphans(root, sealed_len)?;
        for (index, segment) in manifest.sealed.iter().enumerate() {
            if segment.index != index as u64
                || checksum(&segment_path(root, segment.index))? != (segment.bytes, segment.crc32)
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "sealed segment checksum/index mismatch",
                ));
            }
        }
        Ok(Self {
            root: root.into(),
            sealed_len,
            next_index: 0,
            current: None,
        })
    }

    pub fn next_record(&mut self) -> io::Result<Option<JournalRecord>> {
        loop {
            if let Some(reader) = &mut self.current {
                if let Some(record) = reader.next_record()? {
                    return Ok(Some(record));
                }
                self.current = None;
            }
            if self.next_index > self.sealed_len {
                return Ok(None);
            }
            let path = segment_path(&self.root, self.next_index);
            let sealed = self.next_index < self.sealed_len;
            self.next_index += 1;
            if !path.exists() {
                if sealed {
                    return Err(io::Error::new(
                        io::ErrorKind::NotFound,
                        "sealed segment missing",
                    ));
                }
                return Ok(None);
            }
            self.current = Some(JournalReader::open(&path)?);
        }
    }
}

pub fn replay_segments(root: &Path) -> io::Result<Vec<JournalRecord>> {
    let mut reader = SegmentedReader::open(root)?;
    let mut output = Vec::new();
    while let Some(record) = reader.next_record()? {
        output.push(record);
    }
    Ok(output)
}

/// Count replayable records without retaining market data in memory.
pub fn count_recovered_records(root: &Path) -> io::Result<u64> {
    let mut reader = SegmentedReader::open(root)?;
    let mut count = 0;
    while reader.next_record()?.is_some() {
        count += 1;
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::MarketEvent;
    use quant_market_data::{normalize, synthetic_packet};

    fn config() -> SegmentConfig {
        SegmentConfig {
            max_bytes: 600,
            max_age: Duration::from_secs(3600),
            max_disk_bytes: 100_000,
            min_free_bytes: 0,
            durability: DurabilityPolicy::EveryEvent,
        }
    }
    fn record(i: u64) -> JournalRecord {
        JournalRecord {
            session_id: "s".into(),
            event: MarketEvent::Depth(Box::new(normalize(synthetic_packet(i, 2)).unwrap())),
        }
    }
    #[test]
    fn rotation_restart_and_order() {
        let root = tempfile::tempdir().unwrap();
        let input: Vec<_> = (0..5).map(record).collect();
        let mut writer = SegmentedJournal::open(root.path(), config()).unwrap();
        for row in &input {
            writer.append(row).unwrap();
        }
        writer.close().unwrap();
        let writer = SegmentedJournal::open(root.path(), config()).unwrap();
        assert!(!writer.manifest().sealed.is_empty());
        assert_eq!(replay_segments(root.path()).unwrap(), input);
    }
    #[test]
    fn budget_and_free_space_fail_before_acceptance() {
        let root = tempfile::tempdir().unwrap();
        let mut writer = SegmentedJournal::open(root.path(), config()).unwrap();
        assert!(writer.check_budget(1, 0).is_err());
        let mut tight = config();
        tight.min_free_bytes = u64::MAX;
        let mut rejected = SegmentedJournal::open(root.path(), tight).unwrap();
        assert!(rejected.append(&record(1)).is_err());
        assert_eq!(replay_segments(root.path()).unwrap().len(), 0);
        writer.append(&record(1)).unwrap();
        let mut tiny = config();
        tiny.max_disk_bytes = 1;
        assert!(
            SegmentedJournal::open(root.path(), tiny)
                .unwrap()
                .append(&record(2))
                .is_err()
        );
    }
    #[test]
    fn sealed_checksum_detects_tampering() {
        let root = tempfile::tempdir().unwrap();
        let mut writer = SegmentedJournal::open(root.path(), config()).unwrap();
        writer.append(&record(1)).unwrap();
        writer.rotate().unwrap();
        std::fs::OpenOptions::new()
            .append(true)
            .open(segment_path(root.path(), 0))
            .unwrap()
            .write_all(b"x")
            .unwrap();
        assert!(SegmentedJournal::open(root.path(), config()).is_err());
        assert!(replay_segments(root.path()).is_err());
    }

    #[test]
    fn active_segment_recovers_interrupted_final_record() {
        let root = tempfile::tempdir().unwrap();
        let mut writer = SegmentedJournal::open(root.path(), config()).unwrap();
        let first = record(1);
        writer.append(&first).unwrap();
        writer.close().unwrap();
        OpenOptions::new()
            .append(true)
            .open(segment_path(root.path(), 0))
            .unwrap()
            .write_all(b"QJ01\x10")
            .unwrap();
        let writer = SegmentedJournal::open(root.path(), config()).unwrap();
        assert_eq!(writer.recovered_tail_bytes(), 5);
        assert_eq!(replay_segments(root.path()).unwrap(), vec![first]);
    }
}
