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
}
