//! Append-only framed journal. Only `sync` (or `EveryEvent`) acknowledges durability.
use quant_core::{MarketEvent, RawStandardFrame};
use ring::digest::{SHA256, digest};
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

pub fn raw_standard_record(
    session_id: &str,
    frame_id: u64,
    receive_timestamp_ns: i64,
    bytes: &[u8],
) -> io::Result<JournalRecord> {
    if session_id.is_empty() || frame_id == 0 || receive_timestamp_ns <= 0 || bytes.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid raw frame metadata",
        ));
    }
    let sha256: [u8; 32] = digest(&SHA256, bytes)
        .as_ref()
        .try_into()
        .expect("SHA-256 length");
    let record = JournalRecord {
        session_id: session_id.into(),
        event: MarketEvent::RawStandardFrame(Box::new(RawStandardFrame {
            feed_session_id: session_id.into(),
            frame_id,
            receive_timestamp_ns,
            sha256,
            bytes: bytes.to_vec(),
        })),
    };
    encoded_record_bytes(&record)?;
    Ok(record)
}

pub fn raw_depth_record(
    session_id: &str,
    frame_id: u64,
    receive_timestamp_ns: i64,
    bytes: &[u8],
) -> io::Result<JournalRecord> {
    let mut record = raw_standard_record(session_id, frame_id, receive_timestamp_ns, bytes)?;
    if let MarketEvent::RawStandardFrame(raw) = record.event {
        record.event = MarketEvent::RawDepthFrame(raw);
    }
    Ok(record)
}

fn verify_raw_frame(record: &JournalRecord) -> io::Result<()> {
    if let MarketEvent::RawStandardFrame(raw) | MarketEvent::RawDepthFrame(raw) = &record.event {
        if raw.feed_session_id != record.session_id
            || raw.feed_session_id.is_empty()
            || raw.frame_id == 0
            || raw.receive_timestamp_ns <= 0
            || raw.bytes.is_empty()
            || digest(&SHA256, &raw.bytes).as_ref() != raw.sha256
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "raw frame provenance mismatch",
            ));
        }
    }
    Ok(())
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
    file: Option<File>,
    policy: DurabilityPolicy,
    buffer: Vec<u8>,
    pending: usize,
    written_events: usize,
    poisoned: bool,
    recovered_tail_bytes: u64,
    segmented: Option<Box<segmented::SegmentedJournal>>,
    max_allowed_bytes: u64,
    #[cfg(test)]
    pub fail_next_write: bool,
    #[cfg(test)]
    pub fail_next_sync: bool,
}

impl JournalWriter {
    pub fn open(path: &Path, policy: DurabilityPolicy) -> io::Result<Self> {
        policy.validate()?;
        if path.is_dir()
            || path.extension().is_none()
            || path.extension().map_or(false, |ext| ext != "qj")
        {
            let config = segmented::SegmentConfig::default_for_policy(policy);
            return Self::open_segmented(path, config);
        }
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
            file: Some(file),
            policy,
            buffer: Vec::new(),
            pending: 0,
            written_events: 0,
            poisoned: false,
            recovered_tail_bytes,
            segmented: None,
            max_allowed_bytes: 512 * 1024 * 1024,
            #[cfg(test)]
            fail_next_write: false,
            #[cfg(test)]
            fail_next_sync: false,
        })
    }

    pub fn open_segmented(root: &Path, config: segmented::SegmentConfig) -> io::Result<Self> {
        config.validate()?;
        let seg = segmented::SegmentedJournal::open(root, config)?;
        let recovered_tail_bytes = seg.recovered_tail_bytes();
        let max_disk_bytes = seg.config().max_disk_bytes;
        let policy = config.durability;
        Ok(Self {
            file: None,
            policy,
            buffer: Vec::new(),
            pending: 0,
            written_events: 0,
            poisoned: false,
            recovered_tail_bytes,
            segmented: Some(Box::new(seg)),
            max_allowed_bytes: max_disk_bytes,
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
        if let Some(segmented) = &mut self.segmented {
            return segmented.append(record);
        }
        verify_raw_frame(record)?;
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
        if let Some(segmented) = &mut self.segmented {
            return segmented.sync();
        }
        if self.pending == 0 {
            return Ok(Commit::default());
        }
        let file = self.file.as_mut().expect("file writer initialized");
        let started = Instant::now();
        #[cfg(test)]
        let write_result = if self.fail_next_write {
            self.fail_next_write = false;
            Err(io::Error::from_raw_os_error(28))
        } else {
            file.write_all(&self.buffer)
        };
        #[cfg(not(test))]
        let write_result = file.write_all(&self.buffer);
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
            file.sync_data()
        };
        #[cfg(not(test))]
        let sync_result = file.sync_data();
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
        if let Some(segmented) = &self.segmented {
            segmented.pending()
        } else {
            self.pending
        }
    }

    pub fn estimated_bytes(&self) -> io::Result<u64> {
        if let Some(segmented) = &self.segmented {
            segmented.total_bytes()
        } else {
            let file = self.file.as_ref().expect("file writer initialized");
            Ok(file.metadata()?.len() + self.buffer.len() as u64)
        }
    }

    pub fn written_events(&self) -> usize {
        if let Some(segmented) = &self.segmented {
            segmented.written_events()
        } else {
            self.written_events
        }
    }

    pub fn recovered_tail_bytes(&self) -> u64 {
        if let Some(segmented) = &self.segmented {
            segmented.recovered_tail_bytes()
        } else {
            self.recovered_tail_bytes
        }
    }

    pub fn close(mut self) -> io::Result<Commit> {
        if let Some(segmented) = self.segmented.take() {
            segmented.close()
        } else {
            self.sync()
        }
    }

    pub fn max_delay_ms(&self) -> Option<u64> {
        match self.policy {
            DurabilityPolicy::EveryEvent => None,
            DurabilityPolicy::Group { max_delay_ms, .. } => Some(max_delay_ms),
        }
    }

    pub fn max_allowed_bytes(&self) -> u64 {
        self.max_allowed_bytes
    }

    pub fn is_segmented(&self) -> bool {
        self.segmented.is_some()
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

enum JournalReaderInner {
    Flat {
        file: File,
        recovered_tail_bytes: u64,
    },
    Segmented(Box<segmented::SegmentedReader>),
}

pub struct JournalReader {
    inner: JournalReaderInner,
}

impl JournalReader {
    pub fn open(path: &Path) -> io::Result<Self> {
        if path.is_dir() {
            let seg = segmented::SegmentedReader::open(path)?;
            return Ok(Self {
                inner: JournalReaderInner::Segmented(Box::new(seg)),
            });
        }
        let mut file = OpenOptions::new().read(true).write(true).open(path)?;
        let original_size = file.metadata()?.len();
        let recovered_tail_bytes = original_size - recover(&mut file)?;
        if recovered_tail_bytes > 0 {
            eprintln!("journal recovery truncated {recovered_tail_bytes} tail bytes");
        }
        file.seek(SeekFrom::Start(0))?;
        Ok(Self {
            inner: JournalReaderInner::Flat {
                file,
                recovered_tail_bytes,
            },
        })
    }

    pub fn recovered_tail_bytes(&self) -> u64 {
        match &self.inner {
            JournalReaderInner::Flat {
                recovered_tail_bytes,
                ..
            } => *recovered_tail_bytes,
            JournalReaderInner::Segmented(seg) => seg.recovered_tail_bytes(),
        }
    }

    pub fn next_record(&mut self) -> io::Result<Option<JournalRecord>> {
        match &mut self.inner {
            JournalReaderInner::Flat { file, .. } => {
                let Some(payload) = read_frame(file)? else {
                    return Ok(None);
                };
                let record: JournalRecord = postcard::from_bytes(&payload).map_err(io::Error::other)?;
                verify_raw_frame(&record)?;
                Ok(Some(record))
            }
            JournalReaderInner::Segmented(seg) => seg.next_record(),
        }
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
        let parsed = postcard::from_bytes::<JournalRecord>(&payload);
        if parsed.is_err() {
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
        verify_raw_frame(&parsed.expect("checked above"))?;
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
    fn raw_frame_roundtrip_and_sha256_rejects_crc_valid_tampering() {
        let dir = tempfile::tempdir().unwrap();
        let path = std::env::var_os("ALPHA_QJ01_PRIVATE_TEST_DIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| dir.path().into())
            .join("raw.qj");
        let bytes = [4_u8, 50, 0, 1, 0, 0, 0, 0];
        let record = raw_standard_record("feed-one", 1, 123, &bytes).unwrap();
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        assert_eq!(writer.append(&record).unwrap().unwrap().events, 1);
        writer.close().unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        assert_eq!(reader.next_record().unwrap(), Some(record.clone()));
        assert!(reader.next_record().unwrap().is_none());

        let mut altered = record;
        let MarketEvent::RawStandardFrame(raw) = &mut altered.event else {
            unreachable!()
        };
        raw.bytes[0] ^= 1;
        let payload = postcard::to_stdvec(&altered).unwrap();
        let mut file = File::create(&path).unwrap();
        file.write_all(&MAGIC).unwrap();
        file.write_all(&(payload.len() as u32).to_le_bytes())
            .unwrap();
        file.write_all(&crc32fast::hash(&payload).to_le_bytes())
            .unwrap();
        file.write_all(&payload).unwrap();
        file.sync_all().unwrap();
        assert_eq!(
            JournalReader::open(&path).err().unwrap().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn raw_depth_frame_sha256_rejects_crc_valid_tampering() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("depth.qj");
        let bytes = [1_u8, 2, 3, 4];
        let record = raw_depth_record("depth-one", 1, 123, &bytes).unwrap();
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        writer.append(&record).unwrap();
        writer.close().unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        assert_eq!(reader.next_record().unwrap(), Some(record.clone()));

        let mut altered = record;
        let MarketEvent::RawDepthFrame(raw) = &mut altered.event else {
            unreachable!()
        };
        raw.bytes[0] ^= 1;
        let payload = postcard::to_stdvec(&altered).unwrap();
        let mut file = File::create(&path).unwrap();
        file.write_all(&MAGIC).unwrap();
        file.write_all(&(payload.len() as u32).to_le_bytes())
            .unwrap();
        file.write_all(&crc32fast::hash(&payload).to_le_bytes())
            .unwrap();
        file.write_all(&payload).unwrap();
        file.sync_all().unwrap();
        assert_eq!(
            JournalReader::open(&path).err().unwrap().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn qj01_legacy_depth_and_trade_variants_remain_readable() {
        #[derive(Serialize)]
        enum LegacyEvent {
            Depth(Box<quant_core::DepthEvent>),
            Trade(quant_core::TradeEvent),
            DepthSide(Box<quant_core::DepthSideUpdate>),
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
        let side = quant_core::DepthSideUpdate {
            instrument: book.instrument.clone(),
            side: quant_core::DepthSide::Bid,
            levels: book.bids,
            receive_timestamp_ns: 2,
            feed_session_id: "old-depth".into(),
            frame_id: 7,
            packet_offset: 0,
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
            LegacyRecord {
                session_id: "old".into(),
                event: LegacyEvent::DepthSide(Box::new(side.clone())),
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
        assert_eq!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::DepthSide(Box::new(side))
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

    #[test]
    fn journal_writer_segmented_rotation_and_recovery() {
        use crate::segmented::{SegmentConfig, replay_segments};
        use std::time::Duration;

        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("segmented_journal");
        let config = SegmentConfig {
            max_bytes: 400, // Small segment limit to force rapid rotation
            max_age: Duration::from_secs(3600),
            max_disk_bytes: 10 * 1024 * 1024,
            min_free_bytes: 1024,
            durability: DurabilityPolicy::EveryEvent,
        };

        let mut writer = JournalWriter::open_segmented(&root, config).unwrap();
        assert!(writer.is_segmented());
        assert_eq!(writer.max_allowed_bytes(), 10 * 1024 * 1024);

        // Append 6 records (each record is ~100+ bytes postcard encoded)
        let mut expected = Vec::new();
        for i in 0..6 {
            let r = record(i);
            expected.push(r.clone());
            writer.append(&r).unwrap();
        }
        writer.close().unwrap();

        // Verify rotation produced multiple segments in manifest
        let manifest_path = root.join("manifest.json");
        assert!(manifest_path.exists());
        let manifest: crate::segmented::Manifest =
            serde_json::from_reader(std::fs::File::open(&manifest_path).unwrap()).unwrap();
        assert!(manifest.sealed.len() >= 1, "Expected at least 1 sealed rotated segment");

        // Verify restart recovery: reopening detects tail without errors
        let writer2 = JournalWriter::open_segmented(&root, config).unwrap();
        assert_eq!(writer2.recovered_tail_bytes(), 0);
        writer2.close().unwrap();

        // Verify replay preserves exact record contents and ordering
        let replayed = replay_segments(&root).unwrap();
        assert_eq!(replayed, expected);
    }

    #[test]
    fn journal_writer_directory_auto_detection() {
        let dir = tempfile::tempdir().unwrap();
        let journal_dir = dir.path().join("auto_detected_dir");
        std::fs::create_dir_all(&journal_dir).unwrap();

        let writer = JournalWriter::open(&journal_dir, DurabilityPolicy::EveryEvent).unwrap();
        assert!(writer.is_segmented());
        assert_eq!(writer.max_allowed_bytes(), 4 * 1024 * 1024 * 1024);
    }

    #[test]
    fn journal_writer_nonexistent_dir_auto_segmented() {
        let dir = tempfile::tempdir().unwrap();
        // Path without .qj extension that does not exist yet
        let segmented_path = dir.path().join("raw-standard-2026-11-05");

        let writer = JournalWriter::open(&segmented_path, DurabilityPolicy::EveryEvent).unwrap();
        assert!(writer.is_segmented());
        assert_eq!(writer.max_allowed_bytes(), 4 * 1024 * 1024 * 1024);
    }

    #[test]
    fn journal_reader_transparent_segmented_and_flat() {
        let dir = tempfile::tempdir().unwrap();

        // 1. Flat journal file
        let flat_path = dir.path().join("flat.qj");
        let mut flat_writer = JournalWriter::open(&flat_path, DurabilityPolicy::EveryEvent).unwrap();
        assert!(!flat_writer.is_segmented());
        let r0 = record(10);
        let r1 = record(11);
        flat_writer.append(&r0).unwrap();
        flat_writer.append(&r1).unwrap();
        flat_writer.close().unwrap();

        let mut flat_reader = JournalReader::open(&flat_path).unwrap();
        assert_eq!(flat_reader.next_record().unwrap(), Some(r0.clone()));
        assert_eq!(flat_reader.next_record().unwrap(), Some(r1.clone()));
        assert_eq!(flat_reader.next_record().unwrap(), None);

        // 2. Segmented journal directory
        let seg_dir = dir.path().join("segmented_read_test");
        let mut seg_writer = JournalWriter::open(&seg_dir, DurabilityPolicy::EveryEvent).unwrap();
        assert!(seg_writer.is_segmented());
        seg_writer.append(&r0).unwrap();
        seg_writer.append(&r1).unwrap();
        seg_writer.close().unwrap();

        let mut seg_reader = JournalReader::open(&seg_dir).unwrap();
        assert_eq!(seg_reader.next_record().unwrap(), Some(r0));
        assert_eq!(seg_reader.next_record().unwrap(), Some(r1));
        assert_eq!(seg_reader.next_record().unwrap(), None);
    }

    #[test]
    fn journal_writer_rotation_and_fd_hygiene() {
        use crate::segmented::SegmentConfig;
        use std::time::Duration;

        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("fd_hygiene_test");
        let config = SegmentConfig {
            max_bytes: 350, // Rapid rotation
            max_age: Duration::from_secs(3600),
            max_disk_bytes: 10 * 1024 * 1024,
            min_free_bytes: 1024,
            durability: DurabilityPolicy::EveryEvent,
        };

        let mut writer = JournalWriter::open_segmented(&root, config).unwrap();
        for i in 0..10 {
            writer.append(&record(i)).unwrap();
        }
        writer.close().unwrap();

        // Check manifest has multiple rotated sealed segments
        let manifest_path = root.join("manifest.json");
        let manifest: crate::segmented::Manifest =
            serde_json::from_reader(std::fs::File::open(&manifest_path).unwrap()).unwrap();
        assert!(manifest.sealed.len() >= 2, "Expected multiple sealed segments");

        // Verify all segments can be sequentially read and closed cleanly
        let mut reader = JournalReader::open(&root).unwrap();
        let mut count = 0;
        while let Some(r) = reader.next_record().unwrap() {
            assert_eq!(r, record(count));
            count += 1;
        }
        assert_eq!(count, 10);
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
