import { useMemo, useState } from "react";
import Chart from "./Chart";
import PaperWorkspace from "./PaperWorkspace";
import {
  amount,
  dateTime,
  delta,
  latency,
  percent,
  price,
  ratio,
  time,
} from "./format";
import type {
  WireCandle,
  WireDepth,
  WireEngineSnapshot,
  WireInstrumentSnapshot,
  WireQuality,
} from "./generated/engine-snapshot";
import { useEngineStream, type Connection } from "./stream";

type View =
  | "Overview"
  | "Market intelligence"
  | "Candle workstation"
  | "Order book"
  | "Feature monitor"
  | "Reliability"
  | "Session audit"
  | "Paper research";
const views: { name: View; icon: string; short: string }[] = [
  { name: "Overview", icon: "◫", short: "01" },
  { name: "Market intelligence", icon: "◈", short: "02" },
  { name: "Candle workstation", icon: "▥", short: "03" },
  { name: "Order book", icon: "≡", short: "04" },
  { name: "Feature monitor", icon: "⌁", short: "05" },
  { name: "Reliability", icon: "◷", short: "06" },
  { name: "Session audit", icon: "⋮", short: "07" },
  { name: "Paper research", icon: "◇", short: "08" },
];
const timeframes = [
  ["1s", "1000000000"],
  ["5s", "5000000000"],
  ["15s", "15000000000"],
  ["1m", "60000000000"],
  ["5m", "300000000000"],
  ["15m", "900000000000"],
];
function statusClass(value: string): string {
  return value === "CONNECTED" || value === "RUNNING" || value === "CLEAR"
    ? "good"
    : value === "PAUSED" || value === "REPLAY" || value === "COMPLETE"
      ? "warn"
      : "bad";
}
function Status({ label, value }: { label: string; value: string }) {
  return (
    <div className="status-item">
      <span className="status-label">{label}</span>
      <span className={`status-value ${statusClass(value)}`}>
        <i />
        {value}
      </span>
    </div>
  );
}
function Metric({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${tone ?? ""}`}>{value}</div>
      {note && <div className="metric-note">{note}</div>}
    </div>
  );
}
function Panel({
  eyebrow,
  title,
  action,
  children,
  className = "",
}: {
  eyebrow: string;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-head">
        <div>
          <div className="eyebrow">{eyebrow}</div>
          <h2>{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
function Empty({ message }: { message: string }) {
  return <div className="empty">{message}</div>;
}
function qualityFlags(quality?: WireQuality | null): string[] {
  if (!quality) return [];
  return Object.entries(quality)
    .filter(([, flagged]) => flagged === true)
    .map(([name]) => name.replaceAll("_", " "));
}
function Quality({ quality }: { quality?: WireQuality | null }) {
  const flags = qualityFlags(quality);
  return (
    <div className="quality-list">
      {flags.length ? (
        flags.map((flag) => (
          <span className="quality-tag" key={flag}>
            {flag}
          </span>
        ))
      ) : (
        <span className="muted">No active quality flags</span>
      )}
    </div>
  );
}
function Field({
  name,
  value,
  hint,
}: {
  name: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="field">
      <div>
        <div className="field-name">{name}</div>
        {hint && <div className="field-hint">{hint}</div>}
      </div>
      <strong>{value}</strong>
    </div>
  );
}
function Header({
  snapshot,
  connection,
}: {
  snapshot: WireEngineSnapshot | null;
  connection: Connection;
}) {
  return (
    <header className="topbar">
      <div className="breadcrumbs">
        ALPHA GRID <span>/</span> OBSERVABILITY <span>/</span> <b>COCKPIT</b>
      </div>
      <div className="top-actions">
        <span className="mode-pill">
          {snapshot?.paper
            ? "PAPER · SYNTHETIC · REPLAY"
            : `${snapshot?.mode ?? "DATA_ONLY"} · SIMULATED`}
        </span>
        <span
          className={`connection-pill ${connection === "CONNECTED" ? "online" : ""}`}
        >
          <i />
          {connection === "CONNECTED" ? "TELEMETRY ONLINE" : "UI DISCONNECTED"}
        </span>
        <span className="clock">
          {snapshot?.latest_data_timestamp_ns
            ? dateTime(snapshot.latest_data_timestamp_ns)
            : "NO DATA"}
        </span>
      </div>
    </header>
  );
}

function ReplayControls({ snapshot }: { snapshot: WireEngineSnapshot | null }) {
  const [busy, setBusy] = useState(false);
  if (snapshot?.mode !== "REPLAY") return null;
  async function control(action: string, speed?: number) {
    setBusy(true);
    try {
      await fetch("/api/demo/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, speed }),
      });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="replay-controls">
      <span className="section-kicker">LOCAL REPLAY</span>
      <button
        disabled={busy || !snapshot.engine_running}
        onClick={() =>
          void control(snapshot.replay_paused ? "resume" : "pause")
        }
      >
        {snapshot.replay_paused ? "▶ Resume" : "Ⅱ Pause"}
      </button>
      <button disabled={busy} onClick={() => void control("restart")}>
        ↺ Restart
      </button>
      <div className="segmented" aria-label="Replay speed">
        {[1, 2, 5].map((speed) => (
          <button
            key={speed}
            className={snapshot.replay_speed === speed ? "active" : ""}
            disabled={busy}
            onClick={() => void control("speed", speed)}
          >
            {speed}×
          </button>
        ))}
      </div>
    </div>
  );
}

function Overview({
  snapshot,
  instrument,
  connection,
}: {
  snapshot: WireEngineSnapshot | null;
  instrument: WireInstrumentSnapshot | null;
  connection: Connection;
}) {
  const c = snapshot?.counters;
  const display =
    instrument?.instrument.symbol ?? instrument?.instrument.security_id ?? "—";
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">SYSTEM OVERVIEW / 01</div>
          <h1>
            Command center<span className="heading-dot">.</span>
          </h1>
          <p>
            Live view of a local, durable replay. Every number below comes from
            the Rust engine or journal.
          </p>
        </div>
        <div className="session-card">
          <span>ACTIVE SESSION</span>
          <strong>{snapshot?.session_id ?? "Awaiting engine"}</strong>
          <small>Capture mode: {snapshot?.mode ?? "unknown"}</small>
        </div>
      </div>
      <div className="status-strip">
        <Status
          label="ENGINE"
          value={snapshot?.engine_running ? "RUNNING" : "STOPPED"}
        />
        <Status
          label="FEED"
          value={snapshot?.feed_connected ? "CONNECTED" : "DISCONNECTED"}
        />
        <Status
          label="JOURNAL"
          value={
            snapshot?.journal_blocked
              ? "BLOCKED"
              : snapshot
                ? "DURABLE"
                : "UNKNOWN"
          }
        />
        <Status label="TELEMETRY" value={connection} />
        <Status
          label="REPLAY"
          value={
            snapshot?.replay_paused
              ? "PAUSED"
              : snapshot?.mode === "REPLAY"
                ? snapshot.engine_running
                  ? "RUNNING"
                  : "COMPLETE"
                : "N/A"
          }
        />
      </div>
      <div className="metric-grid">
        <Metric
          label="REPLAYED RECORDS"
          value={amount(c?.replayed_records)}
          note="Read from checksummed QJ01 journal"
        />
        <Metric
          label="DURABLE RECORDS"
          value={amount(c?.durably_committed_records)}
          note="This replay creates no new writes"
        />
        <Metric
          label="MONITORED INSTRUMENTS"
          value={amount(snapshot?.instruments.length)}
          note="Configured NSE securities"
        />
        <Metric
          label="CURRENT QUEUE"
          value={amount(c?.queue_current)}
          note={`High water ${amount(c?.queue_high_water)}`}
        />
      </div>
      <div className="overview-grid">
        <Panel
          eyebrow="01 / MARKET CONTEXT"
          title={display}
          action={
            <span className="chip">
              {instrument?.instrument.exchange_segment ?? "NSE_EQ"}
            </span>
          }
        >
          <div className="hero-price">
            {price(instrument?.market?.price_nanos)}
          </div>
          <div className="hero-meta">
            <span>
              PREVIOUS CLOSE <b>{price(instrument?.previous_close_nanos)}</b>
            </span>
            <span>
              CHANGE{" "}
              <b>
                {delta(
                  instrument?.market?.price_nanos,
                  instrument?.previous_close_nanos,
                )}
              </b>
            </span>
          </div>
          <div className="thin-rule" />
          <div className="overview-market">
            <Field
              name="Best bid"
              value={price(instrument?.market?.best_bid_price_nanos)}
            />
            <Field
              name="Best ask"
              value={price(instrument?.market?.best_ask_price_nanos)}
            />
            <Field
              name="Midprice"
              value={price(instrument?.features?.midprice_nanos)}
            />
            <Field
              name="Book imbalance"
              value={ratio(instrument?.features?.bid_ask_imbalance)}
            />
          </div>
          <div className="source-note">
            Quote-sampled LTP · paired depth sides are not atomic exchange
            snapshots.
          </div>
        </Panel>
        <Panel
          eyebrow="02 / DATA INTEGRITY"
          title="Capture & persistence"
          action={
            <span
              className={`chip ${snapshot?.journal_blocked ? "alert" : ""}`}
            >
              {snapshot?.storage_status ?? "UNKNOWN"}
            </span>
          }
        >
          <div className="integrity-list">
            <Field
              name="Input frames / sec"
              value={
                snapshot?.counters.offered_frames_per_second?.toFixed(1) ??
                "N/A"
              }
            />
            <Field
              name="Processed events / sec"
              value={
                snapshot?.counters.processed_events_per_second?.toFixed(1) ??
                "N/A"
              }
            />
            <Field
              name="Durable events / sec"
              value={
                snapshot?.counters.durable_events_per_second?.toFixed(1) ??
                "N/A"
              }
            />
            <Field
              name="Queue rejections"
              value={amount(c?.queue_rejections)}
            />
            <Field
              name="Telemetry coalesced"
              value={amount(c?.telemetry_coalesced_updates)}
            />
            <Field name="Fsync p99" value={latency(c?.fsync_latency.p99_ns)} />
          </div>
          <div className="source-note">
            Rates and latency remain N/A until measured by a capture runtime.
          </div>
        </Panel>
      </div>
      <div className="overview-footer">
        <span>
          <i className="tiny-dot" /> DATA_ONLY SYSTEM · NO BROKER ORDERS
        </span>
        <span>
          Latest source receive: {dateTime(snapshot?.latest_data_timestamp_ns)}
        </span>
      </div>
    </>
  );
}

function MarketIntelligence({
  instrument,
}: {
  instrument: WireInstrumentSnapshot | null;
}) {
  const m = instrument?.market,
    f = instrument?.features;
  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">MARKET STATE / 02</div>
          <h1>
            Market intelligence<span className="heading-dot">.</span>
          </h1>
          <p>
            Observed prices and derived top-of-book features, with explicit
            unknowns.
          </p>
        </div>
      </div>
      <div className="market-grid">
        <Panel eyebrow="OBSERVED / STANDARD FEED" title="Price & volume">
          <Field name="Last sampled price" value={price(m?.price_nanos)} />
          <Field
            name="Previous close"
            value={price(instrument?.previous_close_nanos)}
            hint="Only after a previous-close packet"
          />
          <Field
            name="Price change"
            value={delta(m?.price_nanos, instrument?.previous_close_nanos)}
          />
          <Field
            name="Incremental volume"
            value={amount(m?.incremental_volume)}
          />
          <Field
            name="Last trade source time"
            value={time(m?.source_last_trade_timestamp_ns)}
          />
          <Field
            name="Local receive time"
            value={time(m?.source_receive_timestamp_ns)}
          />
        </Panel>
        <Panel eyebrow="DERIVED / PHASE 2B" title="Book & microstructure">
          <Field name="Best bid" value={price(m?.best_bid_price_nanos)} />
          <Field name="Best ask" value={price(m?.best_ask_price_nanos)} />
          <Field name="Spread" value={price(f?.absolute_spread_nanos)} />
          <Field name="Midprice" value={price(f?.midprice_nanos)} />
          <Field name="Microprice" value={price(f?.microprice_nanos)} />
          <Field
            name="Bid / ask imbalance"
            value={ratio(f?.bid_ask_imbalance)}
          />
          <Field
            name="Snapshot OFI"
            value={amount(f?.snapshot_ofi_quantity)}
            hint="Best-level snapshot change; not individual orders"
          />
        </Panel>
        <Panel eyebrow="ROLLING / PHASE 2B" title="Statistical context">
          <Field
            name="Rolling volatility"
            value={ratio(f?.rolling_return_volatility)}
          />
          <Field
            name="Volume activity"
            value={ratio(f?.rolling_volume_activity)}
          />
          <Field
            name="Price / volume correlation"
            value={ratio(f?.price_volume_correlation)}
          />
          <Field name="Latest return" value={percent(f?.latest_price_return)} />
          <Field name="Sample count" value={amount(f?.rolling_sample_count)} />
          <Field name="Feature freshness" value={f?.freshness ?? "UNKNOWN"} />
          <Field
            name="Feature updated"
            value={time(f?.processing_timestamp_ns)}
          />
        </Panel>
      </div>
      <Panel eyebrow="DATA QUALITY" title="Known limitations">
        <Quality quality={m?.quality} />
        <p className="provenance">
          Source: {m?.provenance.source_protocol ?? "N/A"} · Feed session{" "}
          {m?.provenance.feed_session_id ?? "N/A"} · Exchange clock and
          paired-book atomicity unverified.
        </p>
      </Panel>
    </>
  );
}

function CandleWorkstation({
  instrument,
  timeframe,
  setTimeframe,
}: {
  instrument: WireInstrumentSnapshot | null;
  timeframe: string;
  setTimeframe: (v: string) => void;
}) {
  const selected =
    instrument?.candles.filter((c) => c.timeframe_ns === timeframe) ?? [];
  const latest: WireCandle | undefined = selected[selected.length - 1];
  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">PRICE STRUCTURE / 03</div>
          <h1>
            Candle workstation<span className="heading-dot">.</span>
          </h1>
          <p>
            Six quote-sampled intervals from Phase 2B. Quiet periods remain
            empty.
          </p>
        </div>
      </div>
      <Panel
        eyebrow="OHLC / HISTORICAL REPLAY"
        title={instrument?.instrument.symbol ?? "Select an instrument"}
        action={
          <div className="segmented">
            {timeframes.map(([name, ns]) => (
              <button
                key={ns}
                className={timeframe === ns ? "active" : ""}
                onClick={() => setTimeframe(ns)}
              >
                {name}
              </button>
            ))}
          </div>
        }
      >
        <div className="chart-info">
          <span>
            OPEN <b>{price(latest?.open_nanos)}</b>
          </span>
          <span>
            HIGH <b>{price(latest?.high_nanos)}</b>
          </span>
          <span>
            LOW <b>{price(latest?.low_nanos)}</b>
          </span>
          <span>
            CLOSE <b>{price(latest?.close_nanos)}</b>
          </span>
          <span className="candle-state">
            {latest
              ? latest.finalized
                ? "FINALIZED"
                : "PROVISIONAL"
              : "NO BAR"}
          </span>
        </div>
        <Chart
          candles={instrument?.candles ?? []}
          timeframe={timeframe}
          instrumentKey={instrument?.instrument.key ?? ""}
        />
        <div className="chart-caption">
          <span>Quote-sampled LTP · not exchange trade candles</span>
          <span>Replay cursor {time(latest?.processing_timestamp_ns)}</span>
        </div>
      </Panel>
      <div className="metric-grid three">
        <Metric
          label="BAR VOLUME"
          value={amount(latest?.volume)}
          note={
            latest?.volume == null
              ? "Incomplete or unavailable cumulative interval"
              : "Observed complete interval"
          }
        />
        <Metric
          label="OBSERVED DELTA"
          value={amount(latest?.observed_volume_delta)}
          note="Defensible in-bar cumulative change"
        />
        <Metric
          label="BAR SAMPLES"
          value={amount(latest?.observation_count)}
          note={latest?.timestamp_policy ?? "Timestamp policy unknown"}
        />
      </div>
      <Quality quality={latest?.quality} />
    </>
  );
}

function BookSide({
  side,
  depth,
  maxQty,
}: {
  side: "BID" | "ASK";
  depth: WireDepth;
  maxQty: bigint;
}) {
  const levels = side === "BID" ? depth.bids : depth.asks;
  return (
    <div className={`book-side ${side.toLowerCase()}`}>
      <div className="book-header">
        <span>LEVEL</span>
        <span>PRICE</span>
        <span>DISPLAYED</span>
        <span>ORDERS</span>
        <span>CUM. QTY</span>
      </div>
      {levels.map((level, index) => (
        <div className="book-row" key={index}>
          <div
            className="depth-bar"
            style={{
              width: `${maxQty ? Number((BigInt(level.displayed_quantity) * 100n) / maxQty) : 0}%`,
            }}
          />
          <span>{String(index + 1).padStart(2, "0")}</span>
          <strong>{price(level.price_nanos)}</strong>
          <span>{amount(level.displayed_quantity)}</span>
          <span>{amount(level.order_count)}</span>
          <span>{amount(level.cumulative_visible_quantity)}</span>
        </div>
      ))}
    </div>
  );
}
function OrderBook({
  instrument,
}: {
  instrument: WireInstrumentSnapshot | null;
}) {
  const depth = instrument?.depth;
  const maxQty = depth
    ? [...depth.bids, ...depth.asks].reduce(
        (m, v) =>
          BigInt(v.displayed_quantity) > m ? BigInt(v.displayed_quantity) : m,
        0n,
      )
    : 0n;
  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">DEPTH / 04</div>
          <h1>
            20-level order book<span className="heading-dot">.</span>
          </h1>
          <p>
            Displayed quantity and order counts from decoded depth sides. No
            individual order reconstruction.
          </p>
        </div>
      </div>
      <div className="book-summary">
        <Metric label="BEST BID" value={price(depth?.bids[0]?.price_nanos)} />
        <Metric label="BEST ASK" value={price(depth?.asks[0]?.price_nanos)} />
        <Metric
          label="SPREAD"
          value={price(instrument?.features?.absolute_spread_nanos)}
        />
        <Metric
          label="SNAPSHOT AGE"
          value={depth ? time(depth.receive_timestamp_ns) : "N/A"}
          note="Local receive timestamp"
        />
      </div>
      <Panel
        eyebrow="BOOK SNAPSHOT / DERIVED PAIR"
        title={instrument?.instrument.symbol ?? "Select an instrument"}
        action={<span className="chip alert">ATOMICITY UNVERIFIED</span>}
      >
        {depth ? (
          <>
            <div className="book-grid">
              <BookSide side="BID" depth={depth} maxQty={maxQty} />
              <BookSide side="ASK" depth={depth} maxQty={maxQty} />
            </div>
            <div className="source-note">
              Bid and ask sides are paired conservatively. Exchange timestamp:{" "}
              {time(depth.exchange_timestamp_ns)}. Individual additions,
              cancellations and trades are not observable.
            </div>
          </>
        ) : (
          <Empty message="Awaiting a paired 20-level depth snapshot" />
        )}
      </Panel>
    </>
  );
}

const featureDescriptions = [
  [
    "Microprice",
    "Quantity-weighted top-of-book price.",
    (i: WireInstrumentSnapshot) => price(i.features?.microprice_nanos),
  ],
  [
    "Snapshot OFI",
    "Change in best-level supply and demand between snapshots; not individual order flow.",
    (i: WireInstrumentSnapshot) => amount(i.features?.snapshot_ofi_quantity),
  ],
  [
    "Relative spread",
    "Absolute bid–ask spread divided by midprice.",
    (i: WireInstrumentSnapshot) => percent(i.features?.relative_spread),
  ],
  [
    "Bid / ask imbalance",
    "Visible bid share minus ask share, normalized by total visible depth.",
    (i: WireInstrumentSnapshot) => ratio(i.features?.bid_ask_imbalance),
  ],
  [
    "Rolling volatility",
    "Sample variability of observed returns in the configured window.",
    (i: WireInstrumentSnapshot) => ratio(i.features?.rolling_return_volatility),
  ],
  [
    "Volume activity",
    "Rolling observed incremental volume activity.",
    (i: WireInstrumentSnapshot) => ratio(i.features?.rolling_volume_activity),
  ],
  [
    "Price / volume correlation",
    "Correlation of observed return and volume samples.",
    (i: WireInstrumentSnapshot) => ratio(i.features?.price_volume_correlation),
  ],
] as const;
function FeatureMonitor({
  instrument,
}: {
  instrument: WireInstrumentSnapshot | null;
}) {
  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">SIGNAL INPUTS / 05</div>
          <h1>
            Feature monitor<span className="heading-dot">.</span>
          </h1>
          <p>Actual Phase 2B features. Predictive models are not configured.</p>
        </div>
      </div>
      <div className="feature-grid">
        {featureDescriptions.map(([name, description, render]) => (
          <div className="feature-card" key={name}>
            <div className="eyebrow">ENGINE DERIVED</div>
            <h2>{name}</h2>
            <div className="feature-value">
              {instrument ? render(instrument) : "N/A"}
            </div>
            <p>{description}</p>
          </div>
        ))}
      </div>
      <Panel eyebrow="MODEL STATE" title="No predictive model configured">
        <div className="model-empty">
          <span>—</span>
          <div>
            Kalman, OU, Hawkes and policy models are future work. No alpha or
            confidence score is available.
          </div>
        </div>
      </Panel>
      <div className="page-footnote">
        Last feature update:{" "}
        {time(instrument?.features?.processing_timestamp_ns)} · Freshness:{" "}
        {instrument?.features?.freshness ?? "UNKNOWN"} · Samples:{" "}
        {amount(instrument?.features?.rolling_sample_count)}
      </div>
    </>
  );
}

function Reliability({
  snapshot,
  lag,
  reconnects,
}: {
  snapshot: WireEngineSnapshot | null;
  lag: number | null;
  reconnects: number;
}) {
  const c = snapshot?.counters;
  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">SYSTEM INTEGRITY / 06</div>
          <h1>
            Reliability & latency<span className="heading-dot">.</span>
          </h1>
          <p>
            Presentation latency and storage durability are separate
            measurements.
          </p>
        </div>
      </div>
      <div className="metric-grid">
        <Metric
          label="FRAMES RECEIVED"
          value={amount(c?.frames_received)}
          note="Current replay does not capture live frames"
        />
        <Metric
          label="REPLAYED RECORDS"
          value={amount(c?.replayed_records)}
          note="Read from source journal"
        />
        <Metric
          label="QUEUE REJECTIONS"
          value={amount(c?.queue_rejections)}
          note="Critical capture failure if nonzero"
          tone={c?.queue_rejections ? "negative" : ""}
        />
        <Metric
          label="UI MESSAGE LAG"
          value={lag == null ? "N/A" : `${lag} ms`}
          note="Local wall-clock estimate"
        />
      </div>
      <div className="market-grid">
        <Panel eyebrow="AUTHORITATIVE" title="Journal & queue">
          <Field
            name="Journal records accepted"
            value={amount(c?.journal_records_accepted)}
          />
          <Field
            name="Durably committed"
            value={amount(c?.durably_committed_records)}
          />
          <Field
            name="Recovered records"
            value={amount(c?.recovered_records)}
          />
          <Field
            name="Queue current / high water"
            value={`${amount(c?.queue_current)} / ${amount(c?.queue_high_water)}`}
          />
          <Field
            name="Backpressure"
            value={snapshot?.backpressure_state ?? "UNKNOWN"}
          />
          <Field name="Storage" value={snapshot?.storage_status ?? "UNKNOWN"} />
        </Panel>
        <Panel
          eyebrow="TIMING / SAMPLE DISTRIBUTION"
          title="Processing & fsync"
        >
          <Field
            name="Processing p50"
            value={latency(c?.processing_latency.p50_ns)}
          />
          <Field
            name="Processing p95"
            value={latency(c?.processing_latency.p95_ns)}
          />
          <Field
            name="Processing p99"
            value={latency(c?.processing_latency.p99_ns)}
          />
          <Field name="Fsync p50" value={latency(c?.fsync_latency.p50_ns)} />
          <Field name="Fsync p95" value={latency(c?.fsync_latency.p95_ns)} />
          <Field name="Fsync p99" value={latency(c?.fsync_latency.p99_ns)} />
          <Field
            name="Fsync samples"
            value={amount(c?.fsync_latency.sample_count)}
          />
        </Panel>
        <Panel eyebrow="PRESENTATION / LOSSY" title="Telemetry health">
          <Field
            name="Connected browser clients"
            value={amount(c?.connected_clients)}
          />
          <Field
            name="Coalesced updates"
            value={amount(c?.telemetry_coalesced_updates)}
          />
          <Field
            name="Skipped publications"
            value={amount(c?.telemetry_skipped_updates)}
          />
          <Field
            name="Publication p95"
            value={latency(c?.telemetry_publication_latency?.p95_ns)}
          />
          <Field
            name="Publication p99"
            value={latency(c?.telemetry_publication_latency?.p99_ns)}
          />
          <Field name="Browser reconnects" value={amount(reconnects)} />
          <Field name="Malformed frames" value={amount(c?.malformed_frames)} />
          <Field name="Feed reconnects" value={amount(c?.reconnects)} />
          <Field
            name="Disk capacity"
            value="N/A"
            hint="Not monitored by this local replay"
          />
        </Panel>
      </div>
      <div className="source-note wide">
        Unknown timing and capacity metrics remain N/A. A local replay must not
        be interpreted as a live feed performance test.
      </div>
    </>
  );
}

function Audit({ snapshot }: { snapshot: WireEngineSnapshot | null }) {
  const [filter, setFilter] = useState("");
  const events = useMemo(
    () =>
      (snapshot?.audit ?? [])
        .filter((event) =>
          `${event.kind} ${event.detail} ${event.instrument_key ?? ""}`
            .toLowerCase()
            .includes(filter.toLowerCase()),
        )
        .slice()
        .reverse(),
    [snapshot?.audit, filter],
  );
  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">TRACEABILITY / 07</div>
          <h1>
            Session audit<span className="heading-dot">.</span>
          </h1>
          <p>
            Read-only replay, integrity and quality transitions. No trading
            blotter.
          </p>
        </div>
      </div>
      <Panel
        eyebrow="ACTIVE SESSION"
        title={snapshot?.session_id ?? "No session"}
        action={
          <span className="chip">EPOCH {snapshot?.replay_epoch ?? "—"}</span>
        }
      >
        <div className="audit-filters">
          <label htmlFor="audit-filter">FILTER EVENTS</label>
          <input
            id="audit-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Kind, instrument, detail…"
          />
        </div>
        <div className="audit-list">
          {events.length ? (
            events.map((event, index) => (
              <div className="audit-row" key={`${event.timestamp_ns}-${index}`}>
                <span>{time(event.timestamp_ns)}</span>
                <strong>{event.kind}</strong>
                <span>{event.instrument_key ?? "SESSION"}</span>
                <p>{event.detail}</p>
              </div>
            ))
          ) : (
            <Empty message="No matching audit events" />
          )}
        </div>
      </Panel>
      <div className="source-note wide">
        Audit is bounded to the latest 200 presentation events. The append-only
        journal remains the source of truth.
      </div>
    </>
  );
}

export default function App() {
  const { snapshot, connection, messageLagMs, reconnects } = useEngineStream();
  const [view, setView] = useState<View>("Overview");
  const [instrumentKey, setInstrumentKey] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState("5000000000");
  const instrument =
    snapshot?.instruments.find(
      (item) => item.instrument.key === instrumentKey,
    ) ??
    snapshot?.instruments[0] ??
    null;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <span>α</span>
          </div>
          <div>
            <strong>ALPHA GRID</strong>
            <small>RESEARCH SYSTEMS</small>
          </div>
        </div>
        <div className="sidebar-group">
          <div className="nav-caption">WORKSPACE / 01</div>
          <nav aria-label="Cockpit views">
            {views.map((item) => (
              <button
                key={item.name}
                className={`nav-item ${view === item.name ? "selected" : ""}`}
                onClick={() => setView(item.name)}
              >
                <span className="nav-icon">{item.icon}</span>
                <span>{item.name}</span>
                <small>{item.short}</small>
              </button>
            ))}
          </nav>
        </div>
        <div className="sidebar-bottom">
          <div className="sidebar-system">
            <span className="small-indicator" />
            <div>
              <strong>
                {snapshot?.paper ? "PAPER SIMULATION" : "DATA_ONLY MODE"}
              </strong>
              <small>Real execution disabled</small>
            </div>
          </div>
          <div className="sidebar-version">
            ALPHA GRID <span>·</span> PHASE 3A
          </div>
        </div>
      </aside>
      <div className="workspace">
        <Header snapshot={snapshot} connection={connection} />
        <main>
          <div className="toolbar">
            <div className="instrument-select">
              <label htmlFor="instrument">INSTRUMENT</label>
              <select
                id="instrument"
                value={instrument?.instrument.key ?? ""}
                onChange={(event) => setInstrumentKey(event.target.value)}
              >
                {snapshot?.instruments.map((item) => (
                  <option key={item.instrument.key} value={item.instrument.key}>
                    {item.instrument.symbol ?? item.instrument.security_id} ·{" "}
                    {item.instrument.security_id}
                  </option>
                )) ?? <option value="">Awaiting engine</option>}
              </select>
            </div>
            <ReplayControls snapshot={snapshot} />
          </div>
          {view === "Overview" && (
            <Overview
              snapshot={snapshot}
              instrument={instrument}
              connection={connection}
            />
          )}
          {view === "Market intelligence" && (
            <MarketIntelligence instrument={instrument} />
          )}
          {view === "Candle workstation" && (
            <CandleWorkstation
              instrument={instrument}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
            />
          )}
          {view === "Order book" && <OrderBook instrument={instrument} />}
          {view === "Feature monitor" && (
            <FeatureMonitor instrument={instrument} />
          )}
          {view === "Reliability" && (
            <Reliability
              snapshot={snapshot}
              lag={messageLagMs}
              reconnects={reconnects}
            />
          )}
          {view === "Session audit" && <Audit snapshot={snapshot} />}
          {view === "Paper research" && (
            <PaperWorkspace
              snapshot={snapshot}
              connection={connection}
              instrumentKey={instrument?.instrument.key ?? null}
            />
          )}
        </main>
      </div>
    </div>
  );
}
