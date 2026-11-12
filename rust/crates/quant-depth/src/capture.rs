use crate::health::CaptureMetrics;
use quant_core::{DepthEvent, DepthSide, DepthSideUpdate, MarketEvent};
use quant_journal::segmented::{SegmentConfig, SegmentedJournal, count_recovered_records};
use quant_journal::{Commit, JournalRecord, raw_depth_record};
use quant_market_data::dhan20::{DecodeError, DecodedPacket, decode_frame};
use std::collections::{BTreeMap, BTreeSet};
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
    poisoned: bool,
    allowed_ids: Option<BTreeSet<String>>,
    latest_sides: BTreeMap<String, (Option<DepthSideUpdate>, Option<DepthSideUpdate>)>,
    latest_books: BTreeMap<String, DepthEvent>,
    last_receive_ns: Option<i64>,
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
            poisoned: false,
            allowed_ids: None,
            latest_sides: BTreeMap::new(),
            latest_books: BTreeMap::new(),
            last_receive_ns: None,
        })
    }

    /// Bind live capture to its preflighted NSE cash subscriptions.
    pub fn allow_security_ids(&mut self, ids: BTreeSet<String>) -> io::Result<()> {
        if ids.is_empty() || self.frame_id != 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid depth scope",
            ));
        }
        self.allowed_ids = Some(ids);
        Ok(())
    }

    pub fn new_feed_session(&mut self, session_id: String) {
        self.session_id = session_id;
        self.latest_sides.clear();
        self.latest_books.clear();
        self.last_receive_ns = None;
    }

    pub fn all_latest_books(&self) -> &BTreeMap<String, DepthEvent> {
        &self.latest_books
    }

    fn pair_side(&mut self, side: &DepthSideUpdate) -> Option<DepthEvent> {
        let entry = self
            .latest_sides
            .entry(side.instrument.security_id.clone())
            .or_default();
        match side.side {
            DepthSide::Bid => entry.0 = Some(side.clone()),
            DepthSide::Ask => entry.1 = Some(side.clone()),
        }
        let (Some(bid), Some(ask)) = (&entry.0, &entry.1) else {
            self.metrics.unpaired_sides += 1;
            return None;
        };
        if bid.feed_session_id != ask.feed_session_id
            || bid.receive_timestamp_ns.abs_diff(ask.receive_timestamp_ns) > 1_000_000_000
        {
            self.metrics.stale_pairs += 1;
            self.metrics.unpaired_sides += 1;
            return None;
        }
        if bid
            .levels
            .iter()
            .chain(ask.levels.iter())
            .any(|level| level.price_nanos <= 0 || level.quantity == 0)
        {
            self.metrics.incomplete_books += 1;
            return None;
        }
        if bid.levels[0].price_nanos >= ask.levels[0].price_nanos {
            self.metrics.crossed_books += 1;
            return None;
        }
        let event = DepthEvent {
            instrument: side.instrument.clone(),
            exchange_timestamp_ns: None,
            receive_timestamp_ns: bid.receive_timestamp_ns.max(ask.receive_timestamp_ns),
            sequence: None,
            bids: bid.levels,
            asks: ask.levels,
        };
        self.latest_books
            .insert(side.instrument.security_id.clone(), event.clone());
        Some(event)
    }

    async fn commit(&mut self, records: Vec<JournalRecord>, started: Instant) -> io::Result<()> {
        let count = records.len() as u64;
        let (reply, recv) = oneshot::channel();
        let queued_at = Instant::now();
        if self.sender.try_send(FrameBatch { records, reply }).is_err() {
            self.metrics.queue_rejections += 1;
            self.metrics.last_quality_error = Some(started);
            self.poisoned = true;
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
                Ok(())
            }
            Err(error) => {
                self.metrics.fsync_errors += 1;
                self.metrics.last_quality_error = Some(started);
                self.poisoned = true;
                Err(error)
            }
        }
    }

    pub async fn ingest_frame(
        &mut self,
        bytes: &[u8],
        receive_timestamp_ns: i64,
    ) -> io::Result<bool> {
        if self.poisoned {
            return Err(io::Error::other("depth capture poisoned"));
        }
        let started = Instant::now();
        self.metrics.websocket_frames += 1;
        self.metrics.last_frame = Some(started);
        self.frame_id = self
            .frame_id
            .checked_add(1)
            .ok_or_else(|| io::Error::other("depth frame order overflow"))?;
        let raw = raw_depth_record(&self.session_id, self.frame_id, receive_timestamp_ns, bytes)?;
        self.commit(vec![raw], started).await?;
        if self
            .last_receive_ns
            .is_some_and(|last| receive_timestamp_ns < last)
        {
            self.poisoned = true;
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "depth receipt clock moved backward",
            ));
        }
        self.last_receive_ns = Some(receive_timestamp_ns);
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
                    self.poisoned = true;
                    return Err(io::Error::new(io::ErrorKind::InvalidData, error));
                }
            };
        self.metrics.decode_time_ns += started.elapsed().as_nanos() as u64;
        self.metrics.parsed_packets += packets.len() as u64;
        let mut records = Vec::new();
        let mut books = Vec::new();
        let mut disconnected = false;
        for packet in &packets {
            match packet {
                DecodedPacket::Side(side) => {
                    self.metrics.packet_received += 1;
                    if side.instrument.exchange_segment != "NSE_EQ"
                        || self
                            .allowed_ids
                            .as_ref()
                            .is_some_and(|ids| !ids.contains(&side.instrument.security_id))
                    {
                        self.metrics.identity_rejections += 1;
                        self.poisoned = true;
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "unexpected depth instrument",
                        ));
                    }
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
                    if let Some(book) = self.pair_side(side) {
                        books.push(book);
                    }
                }
                DecodedPacket::Unsupported { .. } => {
                    self.metrics.unsupported_packets += 1;
                    self.poisoned = true;
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "unsupported depth packet",
                    ));
                }
                DecodedPacket::Disconnect { .. } => disconnected = true,
            }
        }
        self.metrics.complete_books += books.len() as u64;
        for book in books {
            records.push(JournalRecord {
                session_id: self.session_id.clone(),
                event: MarketEvent::Depth(Box::new(book)),
            });
        }
        if records.is_empty() {
            return Ok(disconnected);
        }
        self.commit(records, started).await?;
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
            p.extend_from_slice(
                &(if code == 51 {
                    200.0 + i as f64
                } else {
                    100.0 + i as f64
                })
                .to_le_bytes(),
            );
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
            (2, 4, 4)
        );
        let rows = replay_segments(dir.path()).unwrap();
        assert_eq!(rows.len(), 4);
        assert!(matches!(rows[0].event, MarketEvent::RawDepthFrame(_)));
        assert!(matches!(rows[1].event, MarketEvent::DepthSide(_)));
        assert!(matches!(rows[2].event, MarketEvent::DepthSide(_)));
        assert!(matches!(rows[3].event, MarketEvent::Depth(_)));
    }
    #[tokio::test]
    async fn malformed_frame_is_retained_before_decode_and_stops_capture() {
        let dir = tempfile::tempdir().unwrap();
        let mut capture = Capture::open(dir.path(), config(), 1, "mock-b".into()).unwrap();
        assert!(capture.ingest_frame(&[1, 2], 123).await.is_err());
        assert_eq!(capture.metrics.decode_errors, 1);
        assert!(capture.ingest_frame(&side(41), 124).await.is_err());
        capture.close().await.unwrap();
        let rows = replay_segments(dir.path()).unwrap();
        assert_eq!(rows.len(), 1);
        let MarketEvent::RawDepthFrame(raw) = &rows[0].event else {
            panic!("raw depth required")
        };
        assert_eq!(raw.bytes, [1, 2]);
    }

    #[tokio::test]
    async fn separate_sides_pair_only_when_fresh_and_uncrossed() {
        let dir = tempfile::tempdir().unwrap();
        let mut capture = Capture::open(dir.path(), config(), 2, "separate".into()).unwrap();
        capture
            .ingest_frame(&side(41), 1_000_000_000)
            .await
            .unwrap();
        capture
            .ingest_frame(&side(51), 1_500_000_000)
            .await
            .unwrap();
        assert_eq!(capture.metrics.complete_books, 1);
        capture
            .ingest_frame(&side(41), 3_000_000_000)
            .await
            .unwrap();
        assert_eq!(capture.metrics.stale_pairs, 1);
        let mut crossed_ask = side(51);
        crossed_ask[12..20].copy_from_slice(&99.0_f64.to_le_bytes());
        capture
            .ingest_frame(&crossed_ask, 3_500_000_000)
            .await
            .unwrap();
        assert_eq!(capture.metrics.crossed_books, 1);
        let metrics = capture.close().await.unwrap();
        assert_eq!(metrics.complete_books, 1);
        let rows = replay_segments(dir.path()).unwrap();
        assert_eq!(
            rows.iter()
                .filter(|r| matches!(r.event, MarketEvent::Depth(_)))
                .count(),
            1
        );
        assert_eq!(
            rows.iter()
                .filter(|r| matches!(r.event, MarketEvent::RawDepthFrame(_)))
                .count(),
            4
        );
    }

    #[tokio::test]
    async fn wrong_instrument_is_retained_raw_then_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let mut capture = Capture::open(dir.path(), config(), 2, "scope".into()).unwrap();
        capture
            .allow_security_ids(["1333".to_string()].into())
            .unwrap();
        let mut wrong = side(41);
        wrong[4..8].copy_from_slice(&2885_i32.to_le_bytes());
        assert!(capture.ingest_frame(&wrong, 123).await.is_err());
        capture.close().await.unwrap();
        let rows = replay_segments(dir.path()).unwrap();
        assert_eq!(rows.len(), 1);
        assert!(matches!(rows[0].event, MarketEvent::RawDepthFrame(_)));
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
