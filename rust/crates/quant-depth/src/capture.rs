use crate::health::CaptureMetrics;
use quant_core::{DepthSide, MarketEvent};
use quant_journal::segmented::{SegmentConfig, SegmentedJournal, count_recovered_records};
use quant_journal::{Commit, JournalRecord};
use quant_market_data::dhan20::{DecodeError, DecodedPacket, assemble_frame, decode_frame};
use std::io;
use std::path::{Path, PathBuf};
use std::time::Instant;
use tokio::sync::{mpsc, oneshot};

struct FrameBatch {
    records: Vec<JournalRecord>,
    reply: oneshot::Sender<io::Result<WriteAck>>,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct WriteAck {
    pub kernel_written: u64,
    pub fsync_confirmed: u64,
    pub journal_time_ns: u64,
    pub disk_remaining_bytes: u64,
}

fn write_batch(journal: &mut SegmentedJournal, records: &[JournalRecord]) -> io::Result<WriteAck> {
    let started = Instant::now();
    let mut committed = 0;
    for record in records {
        if let Some(Commit { events, .. }) = journal.append(record)? {
            committed += events as u64;
        }
    }
    let Commit { events, .. } = journal.sync()?;
    committed += events as u64;
    if committed != records.len() as u64 {
        return Err(io::Error::other("journal commit count mismatch"));
    }
    Ok(WriteAck {
        kernel_written: records.len() as u64,
        fsync_confirmed: committed,
        journal_time_ns: started.elapsed().as_nanos().try_into().unwrap_or(u64::MAX),
        disk_remaining_bytes: journal.disk_remaining_bytes()?,
    })
}

pub struct Capture {
    sender: mpsc::Sender<FrameBatch>,
    writer: tokio::task::JoinHandle<io::Result<()>>,
    pub metrics: CaptureMetrics,
    pub session_id: String,
    frame_id: u64,
    root: PathBuf,
}

impl Capture {
    pub fn open(
        root: &Path,
        config: SegmentConfig,
        queue_frames: usize,
        session_id: String,
    ) -> io::Result<Self> {
        if queue_frames == 0 || session_id.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "empty queue or session",
            ));
        }
        let mut journal = SegmentedJournal::open(root, config)?;
        let recovered_tail_bytes = journal.recovered_tail_bytes();
        let (sender, mut receiver) = mpsc::channel::<FrameBatch>(queue_frames);
        let writer = tokio::spawn(async move {
            while let Some(batch) = receiver.recv().await {
                let result = write_batch(&mut journal, &batch.records);
                let failed = result.is_err();
                let _ = batch.reply.send(result);
                if failed {
                    return Err(io::Error::other("journal writer failed; capture stopped"));
                }
            }
            journal.close()?;
            Ok(())
        });
        let mut metrics = CaptureMetrics::default();
        if recovered_tail_bytes > 0 {
            metrics.replay_errors += 1;
        }
        Ok(Self {
            sender,
            writer,
            metrics,
            session_id,
            frame_id: 0,
            root: root.into(),
        })
    }

    pub async fn ingest_frame(
        &mut self,
        bytes: &[u8],
        receive_timestamp_ns: i64,
    ) -> io::Result<bool> {
        let started = Instant::now();
        self.metrics.websocket_frames += 1;
        self.metrics.last_frame = Some(started);
        self.frame_id += 1;
        let packets =
            match decode_frame(bytes, receive_timestamp_ns, &self.session_id, self.frame_id) {
                Ok(packets) => packets,
                Err(error) => {
                    self.metrics.malformed_frames += 1;
                    self.metrics.decode_errors += 1;
                    self.metrics.last_quality_error = Some(started);
                    if matches!(
                        error,
                        DecodeError::InvalidLength { .. }
                            | DecodeError::UnexpectedDepthLength { .. }
                            | DecodeError::TruncatedPacket { .. }
                    ) {
                        self.metrics.unexpected_lengths += 1;
                    }
                    return Err(io::Error::new(io::ErrorKind::InvalidData, error));
                }
            };
        self.metrics.decode_time_ns += started.elapsed().as_nanos() as u64;
        self.metrics.parsed_packets += packets.len() as u64;
        let mut records = Vec::new();
        let mut side_count = 0_u64;
        let mut disconnected = false;
        for packet in &packets {
            match packet {
                DecodedPacket::Side(side) => {
                    self.metrics.packet_received += 1;
                    side_count += 1;
                    match side.side {
                        DepthSide::Bid => self.metrics.bid_packets += 1,
                        DepthSide::Ask => self.metrics.ask_packets += 1,
                    }
                    self.metrics.observe_instrument(
                        &format!(
                            "{}:{}",
                            side.instrument.exchange_segment, side.instrument.security_id
                        ),
                        side.side,
                        receive_timestamp_ns,
                        started,
                    );
                    records.push(JournalRecord {
                        session_id: self.session_id.clone(),
                        event: MarketEvent::DepthSide(side.clone()),
                    });
                }
                DecodedPacket::Unsupported { .. } => self.metrics.unsupported_packets += 1,
                DecodedPacket::Disconnect { .. } => disconnected = true,
            }
        }
        let books = assemble_frame(&packets);
        self.metrics.complete_books += books.len() as u64;
        self.metrics.unpaired_sides += side_count - 2 * books.len() as u64;
        for book in books {
            records.push(JournalRecord {
                session_id: self.session_id.clone(),
                event: MarketEvent::Depth(Box::new(book)),
            });
        }
        if records.is_empty() {
            return Ok(disconnected);
        }
        let count = records.len() as u64;
        let (reply, recv) = oneshot::channel();
        let queued_at = Instant::now();
        if self.sender.try_send(FrameBatch { records, reply }).is_err() {
            self.metrics.queue_rejections += 1;
            self.metrics.last_quality_error = Some(started);
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "bounded journal queue full or writer stopped",
            ));
        }
        self.metrics.queue_accepted += count;
        let result = recv
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::BrokenPipe, "journal writer stopped"))?;
        self.metrics.queue_wait_time_ns += queued_at.elapsed().as_nanos() as u64;
        match result {
            Ok(ack) => {
                self.metrics.kernel_written += ack.kernel_written;
                self.metrics.fsync_confirmed += ack.fsync_confirmed;
                self.metrics.journal_time_ns += ack.journal_time_ns;
                self.metrics.disk_remaining_bytes = Some(ack.disk_remaining_bytes);
            }
            Err(error) => {
                self.metrics.fsync_errors += 1;
                self.metrics.last_quality_error = Some(started);
                return Err(error);
            }
        }
        Ok(disconnected)
    }

    pub async fn finish(mut self) -> (CaptureMetrics, io::Result<()>) {
        drop(self.sender);
        let writer_result = self
            .writer
            .await
            .map_err(io::Error::other)
            .and_then(|result| result);
        let recovery_result = match count_recovered_records(&self.root) {
            Ok(count) => {
                self.metrics.recovered = count;
                Ok(())
            }
            Err(error) => {
                self.metrics.replay_errors += 1;
                Err(error)
            }
        };
        (self.metrics, writer_result.and(recovery_result))
    }

    pub async fn close(self) -> io::Result<CaptureMetrics> {
        let (metrics, result) = self.finish().await;
        result?;
        Ok(metrics)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_journal::{DurabilityPolicy, segmented::replay_segments};
    use std::time::Duration;
    fn config() -> SegmentConfig {
        SegmentConfig {
            max_bytes: 1_000,
            max_age: Duration::from_secs(3600),
            max_disk_bytes: 1_000_000,
            min_free_bytes: 0,
            durability: DurabilityPolicy::EveryEvent,
        }
    }
    fn side(code: u8) -> Vec<u8> {
        let mut p = Vec::new();
        p.extend_from_slice(&332_i16.to_le_bytes());
        p.extend([code, 1]);
        p.extend_from_slice(&1333_i32.to_le_bytes());
        p.extend_from_slice(&0_u32.to_le_bytes());
        for i in 0..20 {
            p.extend_from_slice(&(100.0 + i as f64).to_le_bytes());
            p.extend_from_slice(&10_u32.to_le_bytes());
            p.extend_from_slice(&1_u32.to_le_bytes());
        }
        p
    }
    #[tokio::test]
    async fn side_and_derived_book_are_durable_and_replayable() {
        let dir = tempfile::tempdir().unwrap();
        let mut capture = Capture::open(dir.path(), config(), 2, "mock-a".into()).unwrap();
        let mut frame = side(41);
        frame.extend(side(51));
        assert!(!capture.ingest_frame(&frame, 123).await.unwrap());
        let metrics = capture.close().await.unwrap();
        assert_eq!(
            (
                metrics.packet_received,
                metrics.queue_accepted,
                metrics.fsync_confirmed
            ),
            (2, 3, 3)
        );
        let rows = replay_segments(dir.path()).unwrap();
        assert_eq!(rows.len(), 3);
        assert!(matches!(rows[0].event, MarketEvent::DepthSide(_)));
        assert!(matches!(rows[1].event, MarketEvent::DepthSide(_)));
        assert!(matches!(rows[2].event, MarketEvent::Depth(_)));
    }
    #[tokio::test]
    async fn malformed_frame_is_rejected_without_journal_rows() {
        let dir = tempfile::tempdir().unwrap();
        let mut capture = Capture::open(dir.path(), config(), 1, "mock-b".into()).unwrap();
        assert!(capture.ingest_frame(&[1, 2], 123).await.is_err());
        assert_eq!(capture.metrics.decode_errors, 1);
        let mut valid = side(41);
        valid.extend(side(51));
        capture.ingest_frame(&valid, 124).await.unwrap();
        capture.close().await.unwrap();
        assert_eq!(replay_segments(dir.path()).unwrap().len(), 3);
    }

    #[tokio::test]
    async fn full_queue_rejects_frame_visibly() {
        let dir = tempfile::tempdir().unwrap();
        let mut capture = Capture::open(dir.path(), config(), 1, "mock-c".into()).unwrap();
        let (reply, _received) = oneshot::channel();
        assert!(
            capture
                .sender
                .try_send(FrameBatch {
                    records: vec![],
                    reply
                })
                .is_ok()
        );
        let error = capture.ingest_frame(&side(41), 123).await.unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
        assert_eq!(capture.metrics.queue_rejections, 1);
        capture.close().await.unwrap();
        assert!(replay_segments(dir.path()).unwrap().is_empty());
    }

    #[tokio::test]
    async fn disk_guard_failure_stops_capture_without_ack() {
        let dir = tempfile::tempdir().unwrap();
        let mut guarded = config();
        guarded.min_free_bytes = u64::MAX;
        let mut capture = Capture::open(dir.path(), guarded, 1, "mock-d".into()).unwrap();
        assert!(capture.ingest_frame(&side(41), 123).await.is_err());
        assert_eq!(capture.metrics.queue_accepted, 1);
        assert_eq!(capture.metrics.fsync_confirmed, 0);
        assert_eq!(capture.metrics.fsync_errors, 1);
        assert!(capture.ingest_frame(&side(51), 124).await.is_err());
        let (metrics, shutdown) = capture.finish().await;
        assert!(shutdown.is_err());
        assert_eq!(metrics.recovered, 0);
        assert!(replay_segments(dir.path()).unwrap().is_empty());
    }
}
