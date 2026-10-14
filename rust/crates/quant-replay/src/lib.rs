//! Replay preserves journal ordering and, optionally, original receive intervals.
use quant_journal::{JournalReader, JournalRecord};
use std::io;
use std::path::Path;
use std::time::Duration;
use tokio::sync::mpsc;

pub fn replay_now(path: &Path) -> io::Result<Vec<JournalRecord>> {
    let mut reader = JournalReader::open(path)?;
    let mut events = Vec::new();
    while let Some(record) = reader.next_record()? {
        events.push(record);
    }
    Ok(events)
}

/// Replay sealed and active QJ01 segments in manifest order, including side
/// updates and derived books. Original single-file journals use `replay_now`.
pub fn replay_segmented_now(root: &Path) -> io::Result<Vec<JournalRecord>> {
    quant_journal::segmented::replay_segments(root)
}

/// Stream large journals one record at a time without retaining them all.
pub fn replay_segmented_each(
    root: &Path,
    mut emit: impl FnMut(JournalRecord) -> io::Result<()>,
) -> io::Result<u64> {
    let mut reader = quant_journal::segmented::SegmentedReader::open(root)?;
    let mut count = 0;
    while let Some(record) = reader.next_record()? {
        emit(record)?;
        count += 1;
    }
    Ok(count)
}

pub async fn replay_timed(path: &Path, sender: mpsc::Sender<JournalRecord>) -> io::Result<usize> {
    let mut reader = JournalReader::open(path)?;
    let mut previous_ns = None;
    let mut count = 0;
    while let Some(record) = reader.next_record()? {
        let current_ns = record.event.receive_timestamp_ns();
        if let Some(previous) = previous_ns {
            if current_ns > previous {
                tokio::time::sleep(Duration::from_nanos((current_ns - previous) as u64)).await;
            }
        }
        previous_ns = Some(current_ns);
        sender
            .send(record)
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::BrokenPipe, "replay receiver closed"))?;
        count += 1;
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::MarketEvent;
    use quant_journal::{DurabilityPolicy, JournalWriter};
    use quant_market_data::{normalize, synthetic_packet};

    #[tokio::test]
    async fn replay_preserves_order() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.qj");
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        for index in 0..3 {
            let mut event = normalize(synthetic_packet(index, 2)).unwrap();
            event.receive_timestamp_ns = 1_000 + index as i64;
            writer
                .append(&JournalRecord {
                    session_id: "s".into(),
                    event: MarketEvent::Depth(Box::new(event)),
                })
                .unwrap();
        }
        drop(writer);
        let events = replay_now(&path).unwrap();
        assert_eq!(events.len(), 3);
        let (tx, mut rx) = mpsc::channel(3);
        assert_eq!(replay_timed(&path, tx).await.unwrap(), 3);
        for event in events {
            assert_eq!(rx.recv().await, Some(event));
        }
    }

    #[test]
    fn segmented_replay_preserves_order() {
        use quant_journal::segmented::{SegmentConfig, SegmentedJournal};
        let dir = tempfile::tempdir().unwrap();
        let mut writer = SegmentedJournal::open(
            dir.path(),
            SegmentConfig {
                max_bytes: 600,
                max_age: Duration::from_secs(3600),
                max_disk_bytes: 1_000_000,
                min_free_bytes: 0,
                durability: DurabilityPolicy::EveryEvent,
            },
        )
        .unwrap();
        let expected: Vec<_> = (0..4)
            .map(|index| JournalRecord {
                session_id: "s".into(),
                event: MarketEvent::Depth(Box::new(normalize(synthetic_packet(index, 2)).unwrap())),
            })
            .collect();
        for item in &expected {
            writer.append(item).unwrap();
        }
        writer.close().unwrap();
        assert_eq!(replay_segmented_now(dir.path()).unwrap(), expected);
        let mut streamed = Vec::new();
        assert_eq!(
            replay_segmented_each(dir.path(), |row| {
                streamed.push(row);
                Ok(())
            })
            .unwrap(),
            4
        );
        assert_eq!(streamed, expected);
    }
}
