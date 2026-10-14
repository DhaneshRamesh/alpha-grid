//! Append-only framed journal. Only `sync` (or `EveryEvent`) acknowledges durability.
use quant_core::MarketEvent;
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::time::Instant;

pub mod segmented;

const MAGIC: [u8; 4] = *b"QJ01";
const HEADER: usize = 12;
const MAX_RECORD: usize = 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct JournalRecord {
    pub session_id: String,
    pub event: MarketEvent,
}

#[derive(Clone, Copy, Debug)]
pub enum DurabilityPolicy {
    EveryEvent,
    Group {
        max_events: usize,
        max_bytes: usize,
        max_delay_ms: u64,
    },
}

impl DurabilityPolicy {
    pub fn validate(self) -> io::Result<()> {
        if let Self::Group {
            max_events,
            max_bytes,
            max_delay_ms,
        } = self
            && (max_events == 0 || max_bytes == 0 || max_delay_ms == 0)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "zero group limit",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct Commit {
    pub events: usize,
    pub bytes: usize,
    pub write_ns: u64,
    pub fsync_ns: u64,
}

pub struct JournalWriter {
    file: File,
    policy: DurabilityPolicy,
    buffer: Vec<u8>,
    pending: usize,
    written_events: usize,
    poisoned: bool,
    recovered_tail_bytes: u64,
    #[cfg(test)]
    fail_next_write: bool,
    #[cfg(test)]
    fail_next_sync: bool,
}

impl JournalWriter {
    pub fn open(path: &Path, policy: DurabilityPolicy) -> io::Result<Self> {
        policy.validate()?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        let original_size = file.metadata()?.len();
        let recovered_tail_bytes = original_size - recover(&mut file)?;
        if recovered_tail_bytes > 0 {
            eprintln!("journal recovery truncated {recovered_tail_bytes} tail bytes");
        }
        file.seek(SeekFrom::End(0))?;
        Ok(Self {
            file,
            policy,
            buffer: Vec::new(),
            pending: 0,
            written_events: 0,
            poisoned: false,
            recovered_tail_bytes,
            #[cfg(test)]
            fail_next_write: false,
            #[cfg(test)]
            fail_next_sync: false,
        })
    }

    pub fn append(&mut self, record: &JournalRecord) -> io::Result<Option<Commit>> {
        if self.poisoned {
            return Err(io::Error::other("journal poisoned after storage error"));
        }
        let payload = postcard::to_stdvec(record).map_err(io::Error::other)?;
        if payload.len() > MAX_RECORD {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "record too large",
            ));
        }
        self.buffer.extend_from_slice(&MAGIC);
        self.buffer
            .extend_from_slice(&(payload.len() as u32).to_le_bytes());
        self.buffer
            .extend_from_slice(&crc32fast::hash(&payload).to_le_bytes());
        self.buffer.extend_from_slice(&payload);
        self.pending += 1;
        let commit = match self.policy {
            DurabilityPolicy::EveryEvent => true,
            DurabilityPolicy::Group {
                max_events,
                max_bytes,
                ..
            } => self.pending >= max_events || self.buffer.len() >= max_bytes,
        };
        if commit {
            self.sync().map(Some)
        } else {
            Ok(None)
        }
    }

    pub fn sync(&mut self) -> io::Result<Commit> {
        if self.poisoned {
            return Err(io::Error::other("journal poisoned after storage error"));
        }
        if self.pending == 0 {
            return Ok(Commit::default());
        }
        let started = Instant::now();
        #[cfg(test)]
        let write_result = if self.fail_next_write {
            self.fail_next_write = false;
            Err(io::Error::from_raw_os_error(28))
        } else {
            self.file.write_all(&self.buffer)
        };
        #[cfg(not(test))]
        let write_result = self.file.write_all(&self.buffer);
        if let Err(error) = write_result {
            self.poisoned = true;
            return Err(error);
        }
        // A complete write reached the kernel, but is not durable until sync_data.
        self.written_events += self.pending;
        let write_ns = started.elapsed().as_nanos().try_into().unwrap_or(u64::MAX);
        let sync_started = Instant::now();
        #[cfg(test)]
        let sync_result = if self.fail_next_sync {
            self.fail_next_sync = false;
            Err(io::Error::from_raw_os_error(5))
        } else {
            self.file.sync_data()
        };
        #[cfg(not(test))]
        let sync_result = self.file.sync_data();
        if let Err(error) = sync_result {
            self.poisoned = true;
            return Err(error);
        }
        let commit = Commit {
            events: self.pending,
            bytes: self.buffer.len(),
            write_ns,
            fsync_ns: sync_started
                .elapsed()
                .as_nanos()
                .try_into()
                .unwrap_or(u64::MAX),
        };
        self.buffer.clear();
        self.pending = 0;
        Ok(commit)
    }

    pub fn pending(&self) -> usize {
        self.pending
    }

    pub fn estimated_bytes(&self) -> io::Result<u64> {
        Ok(self.file.metadata()?.len() + self.buffer.len() as u64)
    }

    pub fn written_events(&self) -> usize {
        self.written_events
    }

    pub fn recovered_tail_bytes(&self) -> u64 {
        self.recovered_tail_bytes
    }

    pub fn close(mut self) -> io::Result<Commit> {
        self.sync()
    }
    pub fn max_delay_ms(&self) -> Option<u64> {
        match self.policy {
            DurabilityPolicy::EveryEvent => None,
            DurabilityPolicy::Group { max_delay_ms, .. } => Some(max_delay_ms),
        }
    }
}

pub fn encoded_record_bytes(record: &JournalRecord) -> io::Result<u64> {
    let len = postcard::to_stdvec(record).map_err(io::Error::other)?.len();
    if len > MAX_RECORD {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "record too large",
        ));
    }
    Ok((HEADER + len) as u64)
}

impl Drop for JournalWriter {
    fn drop(&mut self) {
        // Do not silently claim unsynced bytes as durable. Callers must sync explicitly.
        if self.pending > 0 && !std::thread::panicking() {
            eprintln!("journal closed with {} uncommitted events", self.pending);
        }
    }
}

pub struct JournalReader {
    file: File,
    recovered_tail_bytes: u64,
}

impl JournalReader {
    pub fn open(path: &Path) -> io::Result<Self> {
        let mut file = OpenOptions::new().read(true).write(true).open(path)?;
        let original_size = file.metadata()?.len();
        let recovered_tail_bytes = original_size - recover(&mut file)?;
        if recovered_tail_bytes > 0 {
            eprintln!("journal recovery truncated {recovered_tail_bytes} tail bytes");
        }
        file.seek(SeekFrom::Start(0))?;
        Ok(Self {
            file,
            recovered_tail_bytes,
        })
    }

    pub fn recovered_tail_bytes(&self) -> u64 {
        self.recovered_tail_bytes
    }

    pub fn next_record(&mut self) -> io::Result<Option<JournalRecord>> {
        let Some(payload) = read_frame(&mut self.file)? else {
            return Ok(None);
        };
        postcard::from_bytes(&payload)
            .map(Some)
            .map_err(io::Error::other)
    }
}

fn read_frame(file: &mut File) -> io::Result<Option<Vec<u8>>> {
    let mut header = [0_u8; HEADER];
    let n = file.read(&mut header)?;
    if n == 0 {
        return Ok(None);
    }
    if n < HEADER {
        file.read_exact(&mut header[n..])?;
    }
    if header[..4] != MAGIC {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid journal magic",
        ));
    }
    let len = u32::from_le_bytes(header[4..8].try_into().unwrap()) as usize;
    if len > MAX_RECORD {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "oversized journal frame",
        ));
    }
    let expected = u32::from_le_bytes(header[8..12].try_into().unwrap());
    let mut payload = vec![0_u8; len];
    file.read_exact(&mut payload)?;
    if crc32fast::hash(&payload) != expected {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "journal checksum mismatch",
        ));
    }
    Ok(Some(payload))
}

/// Truncate only an incomplete/corrupt *final* frame; fail on interior corruption.
pub fn recover(file: &mut File) -> io::Result<u64> {
    let size = file.metadata()?.len();
    file.seek(SeekFrom::Start(0))?;
    let mut good = 0;
    while good < size {
        file.seek(SeekFrom::Start(good))?;
        let mut header = [0_u8; HEADER];
        if size - good < HEADER as u64 {
            file.set_len(good)?;
            file.sync_data()?;
            break;
        }
        file.read_exact(&mut header)?;
        if header[..4] != MAGIC {
            return bad_tail_or_interior(file, good, size, "invalid journal magic");
        }
        let len = u32::from_le_bytes(header[4..8].try_into().unwrap()) as u64;
        if len > MAX_RECORD as u64 {
            return bad_tail_or_interior(file, good, size, "oversized journal frame");
        }
        let next = good + HEADER as u64 + len;
        if next > size {
            file.set_len(good)?;
            file.sync_data()?;
            break;
        }
        let mut payload = vec![0_u8; len as usize];
        file.read_exact(&mut payload)?;
        let expected = u32::from_le_bytes(header[8..12].try_into().unwrap());
        if crc32fast::hash(&payload) != expected {
            if next != size {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "interior checksum mismatch",
                ));
            }
            file.set_len(good)?;
            file.sync_data()?;
            break;
        }
        if postcard::from_bytes::<JournalRecord>(&payload).is_err() {
            if next != size {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "interior invalid record",
                ));
            }
            file.set_len(good)?;
            file.sync_data()?;
            break;
        }
        good = next;
    }
    Ok(good)
}

fn bad_tail_or_interior(file: &mut File, good: u64, size: u64, message: &str) -> io::Result<u64> {
    file.seek(SeekFrom::Start(good + 1))?;
    let mut suffix = Vec::new();
    file.read_to_end(&mut suffix)?;
    if suffix.windows(MAGIC.len()).any(|window| window == MAGIC) {
        return Err(io::Error::new(io::ErrorKind::InvalidData, message));
    }
    file.set_len(good)?;
    file.sync_data()?;
    debug_assert!(good <= size);
    Ok(good)
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::MarketEvent;
    use quant_market_data::{normalize, synthetic_packet};
    use std::process::{Command, exit};

    #[test]
    fn qj01_legacy_depth_and_trade_variants_remain_readable() {
        #[derive(Serialize)]
        enum LegacyEvent {
            Depth(Box<quant_core::DepthEvent>),
            Trade(quant_core::TradeEvent),
        }
        #[derive(Serialize)]
        struct LegacyRecord {
            session_id: String,
            event: LegacyEvent,
        }
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("legacy.qj");
        let depth = record(1);
        let MarketEvent::Depth(book) = depth.event.clone() else {
            unreachable!()
        };
        let trade = quant_core::TradeEvent {
            instrument: book.instrument.clone(),
            exchange_timestamp_ns: None,
            receive_timestamp_ns: 1,
            sequence: None,
            price_nanos: 1_000_000_000,
            quantity: 1,
        };
        let old = [
            LegacyRecord {
                session_id: "old".into(),
                event: LegacyEvent::Depth(book.clone()),
            },
            LegacyRecord {
                session_id: "old".into(),
                event: LegacyEvent::Trade(trade.clone()),
            },
        ];
        let mut file = File::create(&path).unwrap();
        for item in old {
            let payload = postcard::to_stdvec(&item).unwrap();
            file.write_all(&MAGIC).unwrap();
            file.write_all(&(payload.len() as u32).to_le_bytes())
                .unwrap();
            file.write_all(&crc32fast::hash(&payload).to_le_bytes())
                .unwrap();
            file.write_all(&payload).unwrap();
        }
        file.sync_all().unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        assert_eq!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::Depth(book)
        );
        assert_eq!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::Trade(trade)
        );
        assert!(reader.next_record().unwrap().is_none());
    }

    fn record(index: u64) -> JournalRecord {
        let mut event = normalize(synthetic_packet(index, 20)).unwrap();
        event.receive_timestamp_ns = 1_000 + index as i64;
        JournalRecord {
            session_id: "2026-10-12-am".into(),
            event: MarketEvent::Depth(Box::new(event)),
        }
    }

    #[test]
    fn restart_roundtrip_and_truncated_final() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.qj");
        let records: Vec<_> = (0..4).map(record).collect();
        {
            let mut journal = JournalWriter::open(
                &path,
                DurabilityPolicy::Group {
                    max_events: 10,
                    max_bytes: 100_000,
                    max_delay_ms: 50,
                },
            )
            .unwrap();
            for item in records.iter().take(3) {
                assert!(journal.append(item).unwrap().is_none());
            }
            assert_eq!(journal.sync().unwrap().events, 3);
        }
        let valid_size = path.metadata().unwrap().len();
        std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(b"QJ01\x20")
            .unwrap();
        let mut journal = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        assert_eq!(path.metadata().unwrap().len(), valid_size);
        assert!(journal.recovered_tail_bytes() > 0);
        assert_eq!(journal.append(&records[3]).unwrap().unwrap().events, 1);
        drop(journal);
        let mut reader = JournalReader::open(&path).unwrap();
        for item in records {
            assert_eq!(reader.next_record().unwrap(), Some(item));
        }
        assert!(reader.next_record().unwrap().is_none());
    }

    #[test]
    fn corrupt_final_is_removed_but_interior_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.qj");
        let mut journal = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let first = record(0);
        journal.append(&first).unwrap();
        let first_end = path.metadata().unwrap().len();
        journal.append(&record(1)).unwrap();
        drop(journal);
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .unwrap();
        file.seek(SeekFrom::Start(first_end + HEADER as u64))
            .unwrap();
        file.write_all(b"X").unwrap();
        drop(file);
        let mut reader = JournalReader::open(&path).unwrap();
        assert_eq!(reader.next_record().unwrap(), Some(first));
        assert!(reader.next_record().unwrap().is_none());
        let mut journal = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        journal.append(&record(2)).unwrap();
        drop(journal);
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .unwrap();
        file.seek(SeekFrom::Start(HEADER as u64)).unwrap();
        file.write_all(b"X").unwrap();
        drop(file);
        assert!(JournalReader::open(&path).is_err());
    }

    #[test]
    fn graceful_close_commits_buffered_records() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.qj");
        let mut journal = JournalWriter::open(
            &path,
            DurabilityPolicy::Group {
                max_events: 10,
                max_bytes: 100_000,
                max_delay_ms: 50,
            },
        )
        .unwrap();
        assert!(journal.append(&record(0)).unwrap().is_none());
        assert_eq!(journal.written_events(), 0);
        assert_eq!(journal.close().unwrap().events, 1);
        let mut reader = JournalReader::open(&path).unwrap();
        assert_eq!(reader.next_record().unwrap(), Some(record(0)));
    }

    #[test]
    fn write_failure_poisoned_without_acknowledgement() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.qj");
        let mut journal = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        journal.fail_next_write = true;
        assert!(journal.append(&record(0)).is_err());
        assert_eq!(journal.written_events(), 0);
        assert!(journal.append(&record(1)).is_err());
        assert_eq!(path.metadata().unwrap().len(), 0);
    }

    #[test]
    fn sync_failure_poisoned_without_durable_acknowledgement() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.qj");
        let mut journal = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        journal.fail_next_sync = true;
        assert!(journal.append(&record(0)).is_err());
        assert_eq!(journal.written_events(), 1);
        assert!(journal.append(&record(1)).is_err());
        // The written frame may be recoverable, but no fsync acknowledged it.
        assert!(path.metadata().unwrap().len() > 0);
    }

    #[test]
    fn abrupt_exit_loses_only_uncommitted_memory_buffer() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.qj");
        let child = Command::new(std::env::current_exe().unwrap())
            .arg("--ignored")
            .arg("--exact")
            .arg("tests::crash_child")
            .env("QUANT_CRASH_TEST_PATH", &path)
            .output()
            .unwrap();
        assert_eq!(child.status.code(), Some(37));
        let mut reader = JournalReader::open(&path).unwrap();
        assert_eq!(reader.next_record().unwrap(), Some(record(0)));
        assert_eq!(reader.next_record().unwrap(), Some(record(1)));
        assert_eq!(reader.next_record().unwrap(), None);
    }

    #[test]
    #[ignore = "run only by abrupt_exit_loses_only_uncommitted_memory_buffer"]
    fn crash_child() {
        let path = std::env::var_os("QUANT_CRASH_TEST_PATH").unwrap();
        let mut journal =
            JournalWriter::open(Path::new(&path), DurabilityPolicy::EveryEvent).unwrap();
        journal.append(&record(0)).unwrap();
        journal.append(&record(1)).unwrap();
        drop(journal);
        let mut journal = JournalWriter::open(
            Path::new(&path),
            DurabilityPolicy::Group {
                max_events: 10,
                max_bytes: 100_000,
                max_delay_ms: 50,
            },
        )
        .unwrap();
        journal.append(&record(2)).unwrap();
        exit(37);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn disk_full_is_reported() {
        let mut journal =
            JournalWriter::open(Path::new("/dev/full"), DurabilityPolicy::EveryEvent).unwrap();
        assert!(journal.append(&record(0)).is_err());
        assert!(journal.append(&record(1)).is_err());
    }
}
