//! Independent QP01 checksummed, fsync-per-transition paper ledger.
//! QJ01 market-data bytes and historical tags remain untouched.
use crate::contracts::{
    DecisionReason, DecisionRecord, OutcomeAttribution, PAPER_SCHEMA_VERSION, PaperFill,
    PaperOrderEvent,
};
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;

const MAGIC: [u8; 4] = *b"QP01";
const HEADER: usize = 12;
const MAX_RECORD: usize = 2 * 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum LedgerEvent {
    Decision(Box<DecisionRecord>),
    Order(PaperOrderEvent),
    Fill(PaperFill),
    Outcome {
        decision_id: String,
        attribution: OutcomeAttribution,
        timestamp_ns: i64,
    },
    Halt {
        reason: DecisionReason,
        timestamp_ns: i64,
    },
    Resume {
        timestamp_ns: i64,
    },
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LedgerRecord {
    pub schema_version: u16,
    pub source_record_index: u64,
    pub event: LedgerEvent,
}

impl LedgerRecord {
    pub fn new(source_record_index: u64, event: LedgerEvent) -> Self {
        Self {
            schema_version: PAPER_SCHEMA_VERSION,
            source_record_index,
            event,
        }
    }
}

fn invalid(detail: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, detail)
}

pub struct PaperLedgerReader {
    file: File,
}
impl PaperLedgerReader {
    pub fn open(path: &Path) -> io::Result<Self> {
        Ok(Self {
            file: File::open(path)?,
        })
    }
    pub fn next_record(&mut self) -> io::Result<Option<LedgerRecord>> {
        let mut header = [0_u8; HEADER];
        let mut n = 0;
        while n < HEADER {
            let read = self.file.read(&mut header[n..])?;
            if read == 0 {
                return if n == 0 {
                    Ok(None)
                } else {
                    Err(invalid("truncated paper header"))
                };
            }
            n += read;
        }
        if header[..4] != MAGIC {
            return Err(invalid("paper magic mismatch"));
        }
        let len = u32::from_le_bytes(header[4..8].try_into().expect("four bytes")) as usize;
        if len == 0 || len > MAX_RECORD {
            return Err(invalid("paper record length invalid"));
        }
        let crc = u32::from_le_bytes(header[8..12].try_into().expect("four bytes"));
        let mut payload = vec![0_u8; len];
        self.file
            .read_exact(&mut payload)
            .map_err(|_| invalid("truncated paper payload"))?;
        if crc32fast::hash(&payload) != crc {
            return Err(invalid("paper checksum mismatch"));
        }
        let record: LedgerRecord = serde_json::from_slice(&payload).map_err(io::Error::other)?;
        if record.schema_version != PAPER_SCHEMA_VERSION {
            return Err(invalid("paper schema mismatch"));
        }
        Ok(Some(record))
    }
}

pub struct PaperLedgerWriter {
    file: File,
    poisoned: bool,
    recovered_tail_bytes: u64,
    #[cfg(test)]
    fail_next_sync: bool,
}
impl PaperLedgerWriter {
    pub fn open(path: &Path) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        let size = file.metadata()?.len();
        let end = recover(&mut file)?;
        file.seek(SeekFrom::Start(end))?;
        Ok(Self {
            file,
            poisoned: false,
            recovered_tail_bytes: size - end,
            #[cfg(test)]
            fail_next_sync: false,
        })
    }
    pub fn recovered_tail_bytes(&self) -> u64 {
        self.recovered_tail_bytes
    }
    pub fn append(&mut self, record: &LedgerRecord) -> io::Result<()> {
        if self.poisoned {
            return Err(io::Error::other("paper ledger poisoned"));
        }
        if record.schema_version != PAPER_SCHEMA_VERSION {
            return Err(invalid("paper schema mismatch"));
        }
        let payload = serde_json::to_vec(record).map_err(io::Error::other)?;
        if payload.is_empty() || payload.len() > MAX_RECORD {
            return Err(invalid("paper record too large"));
        }
        let mut frame = Vec::with_capacity(HEADER + payload.len());
        frame.extend_from_slice(&MAGIC);
        frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        frame.extend_from_slice(&crc32fast::hash(&payload).to_le_bytes());
        frame.extend_from_slice(&payload);
        if let Err(error) = self.file.write_all(&frame) {
            self.poisoned = true;
            return Err(error);
        }
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
        Ok(())
    }
    pub fn close(self) -> io::Result<()> {
        self.file.sync_all()
    }
}

fn recover(file: &mut File) -> io::Result<u64> {
    let size = file.metadata()?.len();
    let mut position = 0_u64;
    while position < size {
        file.seek(SeekFrom::Start(position))?;
        let mut header = [0_u8; HEADER];
        if size - position < HEADER as u64 {
            break;
        }
        file.read_exact(&mut header)?;
        if header[..4] != MAGIC {
            if position > 0 && size - position <= HEADER as u64 {
                break;
            }
            return Err(invalid("paper interior magic mismatch"));
        }
        let len = u32::from_le_bytes(header[4..8].try_into().expect("four bytes")) as usize;
        if len == 0 || len > MAX_RECORD {
            return Err(invalid("paper interior length invalid"));
        }
        let next = position + HEADER as u64 + len as u64;
        if next > size {
            break;
        }
        let mut payload = vec![0_u8; len];
        file.read_exact(&mut payload)?;
        let crc = u32::from_le_bytes(header[8..12].try_into().expect("four bytes"));
        let valid = crc32fast::hash(&payload) == crc
            && serde_json::from_slice::<LedgerRecord>(&payload)
                .is_ok_and(|r| r.schema_version == PAPER_SCHEMA_VERSION);
        if !valid {
            if next == size {
                break;
            }
            return Err(invalid("paper interior checksum or payload invalid"));
        }
        position = next;
    }
    if position < size {
        file.set_len(position)?;
        file.sync_all()?;
    }
    Ok(position)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tail_recovery_and_interior_corruption_are_distinct() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("paper.qp");
        let mut writer = PaperLedgerWriter::open(&path).unwrap();
        for index in 1..=2 {
            writer
                .append(&LedgerRecord::new(
                    index,
                    LedgerEvent::Resume {
                        timestamp_ns: index as i64,
                    },
                ))
                .unwrap();
        }
        writer.close().unwrap();
        let original = std::fs::read(&path).unwrap();
        std::fs::write(&path, &original[..original.len() - 4]).unwrap();
        let writer = PaperLedgerWriter::open(&path).unwrap();
        assert!(writer.recovered_tail_bytes() > 0);
        writer.close().unwrap();
        let mut reader = PaperLedgerReader::open(&path).unwrap();
        assert_eq!(
            reader.next_record().unwrap().unwrap().source_record_index,
            1
        );
        assert!(reader.next_record().unwrap().is_none());
        std::fs::write(&path, &original).unwrap();
        let mut corrupt = original;
        corrupt[20] ^= 1;
        std::fs::write(&path, corrupt).unwrap();
        assert!(PaperLedgerWriter::open(&path).is_err());
    }

    #[test]
    fn failed_sync_never_acknowledges_and_poison_prevents_followup() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("paper.qp");
        let mut writer = PaperLedgerWriter::open(&path).unwrap();
        writer.fail_next_sync = true;
        let record = LedgerRecord::new(1, LedgerEvent::Resume { timestamp_ns: 1 });
        assert!(writer.append(&record).is_err());
        assert!(writer.append(&record).is_err());
    }
}
