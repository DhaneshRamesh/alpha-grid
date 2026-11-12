import { useEffect, useMemo, useRef, useState } from "react";
import Chart from "./Chart";
import CommandCenter from "./CommandCenter";
import StorageSessionPanel from "./StorageSessionPanel";
import LiveMonitor from "./LiveMonitor";
import PaperWorkspace from "./PaperWorkspace";
import ResearchWorkspace from "./ResearchWorkspace";
import SimulationWorkspace from "./SimulationWorkspace";
import {
  AutomationSettings,
  TradeDesk,
  type HistoricalCache,
  type RestSnapshot,
} from "./TradingWorkspace";
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
import {
  forgetCockpitSession,
  useEngineStream,
  type Connection,
} from "./stream";

type View =
  | "Trade desk"
  | "Trading settings"
  | "Overview"
  | "Storage & Session"
  | "Live Monitor"
  | "Market intelligence"
  | "Candle workstation"
  | "Order book"
  | "Feature monitor"
  | "Reliability"
  | "Session audit"
  | "Paper research"
  | "Statistical research"
  | "Trading simulator";
const views: { name: View; icon: string; short: string }[] = [
  { name: "Trade desk", icon: "◎", short: "DESK" },
  { name: "Trading settings", icon: "⚙", short: "RULES" },
  { name: "Overview", icon: "◫", short: "01" },
  { name: "Storage & Session", icon: "⛁", short: "STOR" },
  { name: "Live Monitor", icon: "⌖", short: "LIVE" },
  { name: "Market intelligence", icon: "◈", short: "02" },
  { name: "Candle workstation", icon: "▥", short: "03" },
  { name: "Order book", icon: "≡", short: "04" },
  { name: "Feature monitor", icon: "⌁", short: "05" },
  { name: "Reliability", icon: "◷", short: "06" },
  { name: "Session audit", icon: "⋮", short: "07" },
  { name: "Paper research", icon: "◇", short: "08" },
  { name: "Statistical research", icon: "∿", short: "09" },
  { name: "Trading simulator", icon: "▤", short: "SIM" },
];
const timeframes = [
  ["1s", "1000000000"],
  ["5s", "5000000000"],
  ["15s", "15000000000"],
  ["1m", "60000000000"],
  ["5m", "300000000000"],
  ["15m", "900000000000"],
];
function recentRawBook(receivedNs: string | undefined, connected: boolean): boolean {
  if (!receivedNs || !connected) return false;
  try {
    const age = BigInt(Date.now()) * 1_000_000n - BigInt(receivedNs);
    return age >= 0n && age <= 5_000_000_000n;
  } catch {
    return false;
  }
}
type Capabilities = {
  local_service: string;
  gateway: string;
  telemetry_websocket: string;
  feed: string;
  feed_reason?: string | null;
  simulation: string;
  training: string;
  data_source: string;
  historical_data: string;
  all25_archive?: { state: string; instruments: number; bars: number };
  standard_socket_count: number;
  depth_socket_count: number;
  depth_frames_received?: number;
  depth_reason?: string | null;
  last_genuine_depth_frame_ns?: string | null;
  last_genuine_message_ns: string | null;
  last_raw_capture?: { feed_session_id: string; frames_received: number; last_frame_received_ns: string; state: string } | null;
  market_data_access?: { auto_connect: boolean; revoked_at: string | null; authorized_feeds: string[] } | null;
};
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
  tradingView,
  simulationView,
  restSnapshot,
  onForgetDevice,
}: {
  snapshot: WireEngineSnapshot | null;
  connection: Connection;
  tradingView: boolean;
  simulationView: boolean;
  restSnapshot: RestSnapshot | null;
  onForgetDevice: () => void;
}) {
  return (
    <header className="topbar">
      <div className="breadcrumbs">
        ALPHA GRID <span>/</span> {tradingView ? "TRADING" : "REPLAY LAB"}{" "}
        <span>/</span> <b>{tradingView ? "INTRADAY" : "COCKPIT"}</b>
      </div>
      <div className="top-actions">
        <button type="button" onClick={onForgetDevice}>
          Refresh local session
        </button>
        <span className="mode-pill">
          {simulationView
            ? "SIMULATION_ONLY · REFERENCE PRICES · NO FILLS"
            : snapshot?.mode === "OBSERVE_ONLY"
              ? snapshot.feed_connected
                ? restSnapshot ? "DHAN RAW CAPTURE LIVE · REST QUOTES · NO TRADE" : "DHAN RAW CAPTURE LIVE · NO TRADE"
                : "OBSERVATION ONLY · NO TRADE"
              : tradingView
                ? restSnapshot ? "DHAN REST SNAPSHOT · NO TRADE" : "DATA UNAVAILABLE · NO TRADE"
                : snapshot?.paper
                  ? "PAPER · SIMULATED FILLS"
                  : restSnapshot ? "DHAN REST SNAPSHOT · NO TRADE" : "DATA UNAVAILABLE · NO TRADE"}
        </span>
        {!tradingView && (
          <span
            className={`connection-pill ${connection === "CONNECTED" ? "online" : ""}`}
          >
            <i />
            {connection === "CONNECTED"
              ? "TELEMETRY ONLINE"
              : "UI DISCONNECTED"}
          </span>
        )}
        {!tradingView && (
          <span className="clock">
            {snapshot?.latest_data_timestamp_ns
              ? dateTime(snapshot.latest_data_timestamp_ns)
              : "NO DATA"}
          </span>
        )}
      </div>
    </header>
  );
}

function ReplayControls({ snapshot }: { snapshot: WireEngineSnapshot | null }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (snapshot?.mode !== "REPLAY") return null;
  async function control(action: string, speed?: number) {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/demo/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, speed }),
      });
      if (!response.ok) setError(`Replay control rejected (${response.status}).`);
    } catch {
      setError("Replay control unavailable.");
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
      {error && <span role="status">{error}</span>}
    </div>
  );
}

function SelectedSourceEvidence({instrument, historical, restSnapshot}: {
  instrument: WireInstrumentSnapshot | null; historical: HistoricalCache | null; restSnapshot: RestSnapshot | null;
}) {
  const id = instrument?.instrument.security_id;
  const quote = restSnapshot?.quotes.find((row) => row.security_id === id);
  const archived = historical?.status === "AVAILABLE" && historical.instrument === `NSE_EQ:${id}:${instrument?.instrument.symbol}` ? historical.bars.at(-1) : null;
  const bid = quote?.depth?.buy?.find((level) => level.price > 0 && level.quantity > 0);
  const ask = quote?.depth?.sell?.find((level) => level.price > 0 && level.quantity > 0);
  const rupees = (value: number | null | undefined) => value == null ? "N/A" : `₹${value.toLocaleString("en-IN", {maximumFractionDigits: 2})}`;
  if (!quote && !archived) return <div className="source-note">No verified REST snapshot or historical archive for this selected instrument.</div>;
  return <section className="panel"><div className="panel-head"><div><div className="eyebrow">SEPARATE SOURCE EVIDENCE</div><h2>{instrument?.instrument.symbol} · {id}</h2></div></div>
    {quote && <p>REST_SNAPSHOT · last price ₹{quote.last_price} · Dhan last trade time {quote.last_trade_time ?? "unavailable"} · provider OHLC fields {quote.ohlc ? `${quote.ohlc.open} / ${quote.ohlc.high} / ${quote.ohlc.low} / ${quote.ohlc.close}` : "unavailable"} · volume {quote.volume ?? "unavailable"} · received {restSnapshot?.received_at} · original response SHA-256 {restSnapshot?.sha256}. This is a read-only snapshot, not a live WebSocket tick or fill.</p>}
    {quote && <div className="metric-grid three">
      <Metric label="DHAN REST LAST PRICE" value={rupees(quote.last_price)} note={`Last trade ${quote.last_trade_time ?? "unavailable"}`} />
      <Metric label="DHAN REST NET CHANGE" value={rupees(quote.net_change)} note="Provider net_change field" />
      <Metric label="DAY VOLUME" value={amount(quote.volume)} note="Provider cumulative volume" />
      <Metric label="DAY OPEN / HIGH" value={`${rupees(quote.ohlc?.open)} / ${rupees(quote.ohlc?.high)}`} />
      <Metric label="DAY LOW / DHAN CLOSE FIELD" value={`${rupees(quote.ohlc?.low)} / ${rupees(quote.ohlc?.close)}`} note="Close field is not assumed to be today's finalized close during the session" />
      <Metric label="AVERAGE PRICE" value={rupees(quote.average_price)} />
      <Metric label="LAST TRADE QUANTITY" value={amount(quote.last_quantity)} />
      <Metric label="DISPLAYED BUY / SELL QUANTITY" value={`${amount(quote.buy_quantity)} / ${amount(quote.sell_quantity)}`} />
      <Metric label="DAY CIRCUIT LIMITS" value={`${rupees(quote.lower_circuit_limit)} / ${rupees(quote.upper_circuit_limit)}`} />
      <Metric label="52-WEEK LOW / HIGH" value={`${rupees(quote["52_week_low"])} / ${rupees(quote["52_week_high"])}`} />
      <Metric label="VALID QUOTED SPREAD" value={bid && ask && bid.price < ask.price ? rupees(ask.price - bid.price) : "N/A"} note={bid && ask && bid.price < ask.price ? "5-level REST snapshot; not execution cost" : "No complete uncrossed bid/ask in this response"} />
      <Metric label="SNAPSHOT RECEIVED" value={restSnapshot?.received_at ?? "N/A"} />
    </div>}
    {quote && <p>REST_SNAPSHOT displayed top book: {bid && ask && bid.price < ask.price ? `bid ₹${bid.price} × ${bid.quantity}; ask ₹${ask.price} × ${ask.quantity}; quoted spread ₹${(ask.price - bid.price).toFixed(2)}` : "incomplete or crossed; no spread"}. Neither side proves a fill.</p>}
    {archived && <p>HISTORICAL_ARCHIVE · latest archived close ₹{(Number(archived.close_nanos) / 1e9).toFixed(2)} · source time {dateTime(archived.timestamp_ns)} · source SHA-256 {historical?.response_sha256}. Candle availability unverified.</p>}
  </section>;
}

function Overview({
  snapshot,
  instrument,
  connection,
  historical,
  restSnapshot,
}: {
  snapshot: WireEngineSnapshot | null;
  instrument: WireInstrumentSnapshot | null;
  connection: Connection;
  historical: HistoricalCache | null;
  restSnapshot: RestSnapshot | null;
}) {
  const c = snapshot?.counters;
  const display =
    instrument?.instrument.symbol ?? instrument?.instrument.security_id ?? "—";
  const restQuote = restSnapshot?.quotes.find((row) => row.security_id === instrument?.instrument.security_id);
  const restBid = restQuote?.depth?.buy?.find((level) => level.price > 0 && level.quantity > 0);
  const restAsk = restQuote?.depth?.sell?.find((level) => level.price > 0 && level.quantity > 0);
  const restBookValid = Boolean(restBid && restAsk && restBid.price < restAsk.price);
  const restRupees = (value: number | null | undefined) =>
    value == null ? "N/A" : `₹${value.toLocaleString("en-IN", {maximumFractionDigits: 2})}`;
  const focusedIds = ["2885", "1333", "4963", "1594", "11536", "10604", "3045", "5900", "11483", "5097"];
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">SYSTEM OVERVIEW / 01</div>
          <h1>
            Command center<span className="heading-dot">.</span>
          </h1>
          <p>
            Observed state from the Rust engine and journal. Source quality and
            NO TRADE reasons remain visible when the feed is stale.
          </p>
          {snapshot?.mode === "OBSERVE_ONLY" && (
            <p>
              Provisional Dhan quotes only. Session-specific CAS classification
              UNVERIFIED; research and trading blocked.
            </p>
          )}
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
        <Status label="JOURNAL" value={snapshot?.storage_status ?? "UNKNOWN"} />
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
          label={snapshot?.mode === "OBSERVE_ONLY" ? "RAW FRAMES RECEIVED" : "FILE-SYNCED RECORDS"}
          value={amount(snapshot?.mode === "OBSERVE_ONLY" ? c?.frames_received : c?.durably_committed_records)}
          note={
            snapshot?.mode === "OBSERVE_ONLY"
              ? "Raw QJ01 capture; validation pending"
              : snapshot?.storage_status === "FILE_SYNC_ONLY"
              ? "Windows directory crash durability is unverified"
              : "Per-record file sync acknowledgements"
          }
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
      <Panel eyebrow="FOCUSED WATCH / OBSERVATIONS" title="Ten-stock liquidity radar">
        <p>Fixed display subset of the authorized 25-stock universe. Depth pressure is descriptive; a blank book means no valid five-level Full packet is available.</p>
        <div className="paper-table-wrap"><table className="paper-table"><thead><tr>
          <th>Stock</th><th>Source / freshness</th><th>Bid</th><th>Ask</th><th>Spread</th><th>Five-level bid / ask</th><th>Imbalance</th><th>Depth change / OFI proxy</th>
        </tr></thead><tbody>{focusedIds.map((id) => {
          const row = snapshot?.instruments.find((item) => item.instrument.security_id === id);
          const depth = (row?.depth?.source?.startsWith("STANDARD_FULL") || row?.depth?.source === "DEPTH_20") ? row.depth : null;
          const metrics = depth?.book_metrics;
          const raw = depth?.source === "STANDARD_FULL_RAW";
          const rawRecent = raw && recentRawBook(depth?.receive_timestamp_ns, snapshot?.feed_connected ?? false);
          const live = !raw && row?.market?.freshness === "FRESH" && (depth?.source === "DEPTH_20" || depth?.receive_timestamp_ns === row.market.source_receive_timestamp_ns);
          const saved = restSnapshot?.quotes.find((item) => item.security_id === id);
          return <tr key={id}><td>{row?.instrument.symbol ?? saved?.symbol ?? id} · {id}</td>
            <td>{depth ? `${raw ? `RAW OBSERVATION · VALIDATION PENDING · ${rawRecent ? "RECENT" : "STALE"}` : live ? `LIVE ${depth.source}` : `STALE ${depth.source}`} · ${time(depth.receive_timestamp_ns)}` : saved ? `REST_SNAPSHOT · ${restSnapshot?.received_at}` : "BOOK UNAVAILABLE"}</td>
            <td>{depth ? price(depth.bids[0]?.price_nanos) : "N/A"}</td>
            <td>{depth ? price(depth.asks[0]?.price_nanos) : "N/A"}</td>
            <td>{metrics ? `${metrics.quoted_spread_bps.toFixed(1)} bps` : "N/A"}</td>
            <td>{metrics ? `${amount(metrics.bid_quantity)} / ${amount(metrics.ask_quantity)}` : "N/A"}</td>
            <td>{metrics ? ratio(metrics.five_level_imbalance) : "N/A"}</td>
            <td>{metrics?.bid_quantity_change != null ? `${metrics.bid_quantity_change} bid · ${metrics.ask_quantity_change} ask · OFI ${metrics.ofi_proxy_quantity ?? "N/A"}` : "N/A"}</td>
          </tr>;
        })}</tbody></table></div>
      </Panel>
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
            {instrument?.market?.price_nanos ? price(instrument.market.price_nanos) : restQuote ? `₹${restQuote.last_price.toLocaleString("en-IN", {maximumFractionDigits: 2})}` : "N/A"}
          </div>
          <div className="hero-meta">
            <span>
              {restQuote && !instrument?.previous_close_nanos ? "DHAN CLOSE FIELD" : "PREVIOUS CLOSE"} <b>{instrument?.previous_close_nanos ? price(instrument.previous_close_nanos) : restRupees(restQuote?.ohlc?.close)}</b>
            </span>
            <span>
              CHANGE{" "}
              <b>
                {instrument?.market?.price_nanos
                  ? delta(instrument.market.price_nanos, instrument.previous_close_nanos)
                  : restRupees(restQuote?.net_change)}
              </b>
            </span>
          </div>
          <div className="thin-rule" />
          <div className="overview-market">
            {(() => {
              const liveBid = instrument?.market?.best_bid_price_nanos ?? (instrument?.depth?.source?.startsWith("STANDARD_FULL") ? instrument.depth.bids[0]?.price_nanos : undefined);
              const liveAsk = instrument?.market?.best_ask_price_nanos ?? (instrument?.depth?.source?.startsWith("STANDARD_FULL") ? instrument.depth.asks[0]?.price_nanos : undefined);
              const liveMid = (liveBid && liveAsk) ? ((BigInt(liveBid) + BigInt(liveAsk)) / 2n).toString() : instrument?.features?.midprice_nanos;
              return (
                <>
                  <Field
                    name="Best bid"
                    value={liveBid ? price(liveBid) : restBookValid ? restRupees(restBid?.price) : "N/A"}
                  />
                  <Field
                    name="Best ask"
                    value={liveAsk ? price(liveAsk) : restBookValid ? restRupees(restAsk?.price) : "N/A"}
                  />
                  <Field
                    name="Midprice"
                    value={liveMid ? price(liveMid) : restBookValid ? restRupees((restBid!.price + restAsk!.price) / 2) : "N/A"}
                  />
                </>
              );
            })()}
            <Field
              name="Book imbalance"
              value={ratio(instrument?.depth?.book_metrics?.five_level_imbalance ?? instrument?.features?.bid_ask_imbalance)}
            />
          </div>
          <div className="source-note">
            {instrument?.market?.price_nanos ? instrument.market.packet_kind?.startsWith("RAW_") ? `RAW_CAPTURE_ONLY · genuine received Dhan LTP · frame ${instrument.market.provenance.raw_frame_sha256 ?? "unavailable"} · validation pending; no candle or fill inference.` : instrument.depth?.source === "STANDARD_FULL" ? "STANDARD_FULL · five displayed levels from the same saved packet; no executable fill claim." : "STANDARD_QUOTE · no same-packet depth; separately paired book only when labeled." : restQuote ? `REST_SNAPSHOT · received ${restSnapshot?.received_at} · source last trade ${restQuote.last_trade_time ?? "unavailable"}; not a live WebSocket tick.` : "No observed current price."}
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
          <i className="tiny-dot" /> {snapshot?.mode ?? "DATA_ONLY"} · NO BROKER
          ORDERS
        </span>
        <span>
          Latest source receive: {dateTime(snapshot?.latest_data_timestamp_ns)}
        </span>
      </div>
      <SelectedSourceEvidence instrument={instrument} historical={historical} restSnapshot={restSnapshot} />
    </>
  );
}

function MarketIntelligence({
  instrument,
  observationOnly,
  historical,
  restSnapshot,
}: {
  instrument: WireInstrumentSnapshot | null;
  observationOnly: boolean;
  historical: HistoricalCache | null;
  restSnapshot: RestSnapshot | null;
}) {
  const m = instrument?.market,
    f = instrument?.features;
  const fullBook = (instrument?.depth?.source?.startsWith("STANDARD_FULL") || instrument?.depth?.source === "DEPTH_20") ? instrument.depth : null;
  const fullBid = fullBook?.bids[0]?.price_nanos;
  const fullAsk = fullBook?.asks[0]?.price_nanos;
  const restQuote = restSnapshot?.quotes.find((row) => row.security_id === instrument?.instrument.security_id);
  const restBid = restQuote?.depth?.buy?.find((level) => level.price > 0 && level.quantity > 0);
  const restAsk = restQuote?.depth?.sell?.find((level) => level.price > 0 && level.quantity > 0);
  const restBookValid = Boolean(restBid && restAsk && restBid.price < restAsk.price);
  const restRupees = (value: number | null | undefined) =>
    value == null ? "N/A" : `₹${value.toLocaleString("en-IN", {maximumFractionDigits: 2})}`;
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
          {observationOnly && (
            <p>OBSERVATION ONLY · CAS classification UNVERIFIED · NO TRADE</p>
          )}
        </div>
      </div>
      <SelectedSourceEvidence instrument={instrument} historical={historical} restSnapshot={restSnapshot} />
      <div className="market-grid">
        <Panel eyebrow={m?.price_nanos ? "OBSERVED / STANDARD FEED" : restQuote ? "OBSERVED / REST_SNAPSHOT" : "OBSERVED / UNAVAILABLE"} title="Price & volume">
          <Field name="Last sampled price" value={m?.price_nanos ? price(m.price_nanos) : restRupees(restQuote?.last_price)} />
          <Field
            name={instrument?.previous_close_nanos ? "Previous close" : "Dhan close field"}
            value={instrument?.previous_close_nanos ? price(instrument.previous_close_nanos) : restRupees(restQuote?.ohlc?.close)}
            hint={instrument?.previous_close_nanos ? "Previous-close packet" : "REST OHLC field; today's final close not assumed"}
          />
          <Field
            name="Price change"
            value={m?.price_nanos ? delta(m.price_nanos, instrument?.previous_close_nanos) : restRupees(restQuote?.net_change)}
          />
          <Field
            name="Cumulative traded volume"
            value={amount(m?.cumulative_traded_volume ?? restQuote?.volume)}
          />
          <Field
            name="Incremental volume"
            value={observationOnly ? "N/A" : amount(m?.incremental_volume)}
          />
          <Field name="Quote freshness" value={m?.freshness ?? restSnapshot?.snapshot_state ?? "UNAVAILABLE"} />
          <Field
            name="Last trade source time"
            value={m?.source_last_trade_timestamp_ns ? time(m.source_last_trade_timestamp_ns) : restQuote?.last_trade_time ?? "N/A"}
          />
          <Field
            name="Local receive time"
            value={m?.source_receive_timestamp_ns ? time(m.source_receive_timestamp_ns) : restSnapshot?.received_at ?? "N/A"}
          />
        </Panel>
        <Panel eyebrow={fullBook ? fullBook.source === "STANDARD_FULL_RAW" ? "RAW OBSERVATION / VALIDATION PENDING" : "OBSERVED / STANDARD_FULL" : m?.best_bid_price_nanos ? "DERIVED / PHASE 2B" : restBookValid ? "DISPLAYED / REST_SNAPSHOT" : "BOOK / UNAVAILABLE"} title="Book & microstructure">
          <Field name="Best bid" value={fullBid ? price(fullBid) : m?.best_bid_price_nanos ? price(m.best_bid_price_nanos) : restBookValid ? restRupees(restBid?.price) : "N/A"} />
          <Field name="Best ask" value={fullAsk ? price(fullAsk) : m?.best_ask_price_nanos ? price(m.best_ask_price_nanos) : restBookValid ? restRupees(restAsk?.price) : "N/A"} />
          <Field name="Spread" value={fullBook?.book_metrics ? `${fullBook.book_metrics.quoted_spread_bps.toFixed(1)} bps` : f?.absolute_spread_nanos ? price(f.absolute_spread_nanos) : restBookValid ? restRupees(restAsk!.price - restBid!.price) : "N/A"} />
          <Field name="Midprice" value={fullBid && fullAsk ? price(((BigInt(fullBid) + BigInt(fullAsk)) / 2n).toString()) : f?.midprice_nanos ? price(f.midprice_nanos) : restBookValid ? restRupees((restBid!.price + restAsk!.price) / 2) : "N/A"} />
          <Field name="Microprice" value={price(f?.microprice_nanos)} />
          <Field
            name="Bid / ask imbalance"
            value={ratio(fullBook?.book_metrics?.five_level_imbalance ?? f?.bid_ask_imbalance)}
          />
          <Field
            name="Snapshot OFI"
            value={amount(fullBook?.book_metrics?.ofi_proxy_quantity ?? f?.snapshot_ofi_quantity)}
            hint="Receipt-ordered best-level snapshot change; not individual orders or trades"
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
  historical,
  feedConnected,
}: {
  instrument: WireInstrumentSnapshot | null;
  timeframe: string;
  setTimeframe: (v: string) => void;
  historical: HistoricalCache | null;
  feedConnected: boolean;
}) {
  const [historicalDay, setHistoricalDay] = useState("");
  const [displayMode, setDisplayMode] = useState<"LIVE" | "ARCHIVE">("LIVE");
  const archiveMatches = historical?.status === "AVAILABLE" && historical.instrument === `NSE_EQ:${instrument?.instrument.security_id}:${instrument?.instrument.symbol}`;
  const selectedDay = archiveMatches && historical?.coverage?.[historicalDay]
    ? historicalDay
    : Object.keys(archiveMatches ? historical?.coverage ?? {} : {})[0] ?? "";
  const archived =
    archiveMatches
      ? historical.bars.filter(
          (bar) =>
            new Date(
              Number(BigInt(bar.timestamp_ns) / 1_000_000n),
            ).toLocaleDateString("sv-SE", { timeZone: "Asia/Kolkata" }) ===
            selectedDay,
        )
      : [];
  const selected =
    instrument?.candles.filter((c) => c.timeframe_ns === timeframe) ?? [];
  const latest: WireCandle | undefined = selected[selected.length - 1];
  const liveMarket = feedConnected && instrument?.market?.freshness === "FRESH" &&
    instrument.market.price_nanos ? instrument.market : null;
  const showLive = displayMode === "LIVE" && liveMarket !== null;
  const archiveReplay = !showLive && !latest && archived.length > 0;
  const archivedLatest = archiveReplay ? archived[archived.length - 1] : null;
  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">PRICE STRUCTURE / 03</div>
          <h1>
            Candle workstation<span className="heading-dot">.</span>
          </h1>
          <p>Genuine received Dhan LTP observations are shown separately from historical OHLCV. A live price line is not a Dhan minute candle or a fill.</p>
        </div>
      </div>
      <Panel
        eyebrow={showLive ? "LIVE PRICE OBSERVATIONS / NO TRADE" : "OHLC / HISTORICAL REPLAY"}
        title={instrument?.instrument.symbol ?? "Select an instrument"}
        action={<div className="segmented">
            <button className={showLive ? "active" : ""} disabled={!liveMarket} onClick={() => setDisplayMode("LIVE")}>Live observations</button>
            <button className={!showLive ? "active" : ""} onClick={() => setDisplayMode("ARCHIVE")}>Historical replay</button>
            {!showLive && !archiveReplay && timeframes.map(([name, ns]) => (
              <button key={ns} className={timeframe === ns ? "active" : ""} onClick={() => setTimeframe(ns)}>{name}</button>
            ))}
          </div>}
      >
        {showLive ? (() => {
          const liveBid = liveMarket.best_bid_price_nanos ?? (instrument?.depth?.source?.startsWith("STANDARD_FULL") ? instrument.depth.bids[0]?.price_nanos : undefined);
          const liveAsk = liveMarket.best_ask_price_nanos ?? (instrument?.depth?.source?.startsWith("STANDARD_FULL") ? instrument.depth.asks[0]?.price_nanos : undefined);
          const liveSpread = (liveBid && liveAsk && Number(liveBid) > 0)
            ? (((Number(liveAsk) - Number(liveBid)) / ((Number(liveAsk) + Number(liveBid)) / 2)) * 10000).toFixed(1)
            : instrument?.depth?.book_metrics?.quoted_spread_bps?.toFixed(1);
          return (
            <div className="chart-info">
              <span>LAST OBSERVED LTP <b>{price(liveMarket.price_nanos)}</b></span>
              {liveBid && <span>BID <b>{price(liveBid)}</b></span>}
              {liveAsk && <span>ASK <b>{price(liveAsk)}</b></span>}
              {liveSpread && <span>SPREAD <b>{liveSpread} bps</b></span>}
              <span>RECEIVED <b>{time(liveMarket.source_receive_timestamp_ns)}</b></span>
              <span className="candle-state">{liveMarket.packet_kind?.startsWith("RAW_") ? "RAW_CAPTURE_ONLY · NO LIVE BAR" : "OBSERVED STANDARD FEED · NO LIVE BAR"}</span>
            </div>
          );
        })() : <div className="chart-info">
          <span>
            OPEN <b>{price(latest?.open_nanos ?? archivedLatest?.open_nanos)}</b>
          </span>
          <span>
            HIGH <b>{price(latest?.high_nanos ?? archivedLatest?.high_nanos)}</b>
          </span>
          <span>
            LOW <b>{price(latest?.low_nanos ?? archivedLatest?.low_nanos)}</b>
          </span>
          <span>
            CLOSE <b>{price(latest?.close_nanos ?? archivedLatest?.close_nanos)}</b>
          </span>
          <span className="candle-state">
            {latest
              ? latest.finalized
                ? "FINALIZED"
                : "PROVISIONAL"
              : archiveReplay ? "HISTORICAL_ARCHIVE · NO LIVE BAR" : "NO BAR"}
          </span>
        </div>}
        <Chart
          candles={showLive ? [] : instrument?.candles ?? []}
          timeframe={archiveReplay ? "60000000000" : timeframe}
          instrumentKey={`${instrument?.instrument.key ?? ""}:${showLive ? "observed-ltp" : archiveReplay ? selectedDay : "replay"}`}
          historicalBars={archiveReplay ? archived : undefined}
          liveQuote={showLive ? { price_nanos: liveMarket.price_nanos!, receive_timestamp_ns: liveMarket.source_receive_timestamp_ns } : undefined}
        />
        <div className="chart-caption">
          <span>{showLive ? `Observed Dhan standard LTP · ${liveMarket.packet_kind} · original frame ${liveMarket.provenance.raw_frame_sha256 ?? "unavailable"} · no candle or fill inference` : archiveReplay ? "Dhan historical OHLCV · source timestamp meaning unverified · not a fill" : "Quote-sampled LTP · not exchange trade candles"}</span>
          <span>{showLive ? `Receive time ${time(liveMarket.source_receive_timestamp_ns)} · axis IST` : archiveReplay ? `Latest source label ${time(archivedLatest?.timestamp_ns)} · axis IST` : `Replay cursor ${time(latest?.processing_timestamp_ns)} · axis IST`}</span>
        </div>
      </Panel>
      {showLive ? <div className="metric-grid three">
        <Metric label="CUMULATIVE VOLUME" value={amount(liveMarket.cumulative_traded_volume)} note="Observed provider field; no interval-volume inference" />
        <Metric label="RAW FRAME" value={liveMarket.provenance.source_frame_id ?? "N/A"} note="Original frame synced before display" />
        <Metric label="SOURCE LAST TRADE" value={time(liveMarket.source_last_trade_timestamp_ns)} note="Provider timestamp, meaning unverified" />
      </div> : <div className="metric-grid three">
        <Metric
          label="BAR VOLUME"
          value={amount(latest?.volume ?? archivedLatest?.volume)}
          note={
            archiveReplay ? "Original Dhan historical bar volume" : latest?.volume == null
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
      </div>}
      <Quality quality={showLive ? liveMarket.quality : latest?.quality} />
      <Panel
        eyebrow="AUTHENTIC ARCHIVE / EXPLORATORY ONLY"
        title={`${instrument?.instrument.symbol ?? "Selected instrument"} 1-minute Dhan candles`}
      >
        {archiveMatches ? (
          <>
            <p>
              Source Dhan historical · Retrieved {historical.retrieved_at} ·
              SHA-256 {historical.response_sha256} · Source timestamp meaning
              unverified · NO TRADE
            </p>
            <label htmlFor="historical-day">Session </label>
            <select
              id="historical-day"
              value={selectedDay}
              onChange={(event) => setHistoricalDay(event.target.value)}
            >
              {Object.entries(historical.coverage ?? {}).map(([day, count]) => (
                <option key={day} value={day}>
                  {day} ({count} rows)
                </option>
              ))}
            </select>
            <div style={{ maxHeight: 420, overflowY: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Source time IST</th>
                    <th>Open</th>
                    <th>High</th>
                    <th>Low</th>
                    <th>Close</th>
                    <th>Volume</th>
                  </tr>
                </thead>
                <tbody>
                  {archived.map((bar) => (
                    <tr key={bar.timestamp_ns}>
                      <td>{dateTime(bar.timestamp_ns)}</td>
                      <td>{price(bar.open_nanos)}</td>
                      <td>{price(bar.high_nanos)}</td>
                      <td>{price(bar.low_nanos)}</td>
                      <td>{price(bar.close_nanos)}</td>
                      <td>{amount(bar.volume)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              Daily Dhan candles unavailable. Historical identity and candle
              completion timing remain unverified; causal research blocked.
            </p>
          </>
        ) : (
          <p>
            DATA UNAVAILABLE · No source-integrity-verified local candle cache.
          </p>
        )}
      </Panel>
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
  restSnapshot,
  feedConnected,
}: {
  instrument: WireInstrumentSnapshot | null;
  restSnapshot: RestSnapshot | null;
  feedConnected: boolean;
}) {
  const depth = instrument?.depth;
  const full = depth?.source?.startsWith("STANDARD_FULL") ?? false;
  const raw = depth?.source === "STANDARD_FULL_RAW";
  const rawRecent = raw && recentRawBook(depth?.receive_timestamp_ns, feedConnected);
  const depthFresh = Boolean(
    feedConnected &&
    depth &&
    (depth.source === "DEPTH_20"
      ? recentRawBook(depth.receive_timestamp_ns, feedConnected)
      : (instrument?.market?.freshness === "FRESH" &&
         depth.receive_timestamp_ns === instrument.market.source_receive_timestamp_ns))
  );
  const restBook = restSnapshot?.quotes.find((row) => row.security_id === instrument?.instrument.security_id);
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
            Order book<span className="heading-dot">.</span>
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
          value={depth?.bids[0] && depth?.asks[0] ?
            price((BigInt(depth.asks[0].price_nanos) - BigInt(depth.bids[0].price_nanos)).toString()) :
            price(instrument?.features?.absolute_spread_nanos)}
        />
        <Metric
          label="SNAPSHOT AGE"
          value={depth ? time(depth.receive_timestamp_ns) : "N/A"}
          note="Local receive timestamp"
        />
      </div>
      <Panel
        eyebrow={raw ? "STANDARD_FULL_RAW / VALIDATION PENDING" : full ? "STANDARD_FULL / SAME PACKET / FIVE LEVELS" : "DEPTH_20 / INDEPENDENT ASYNC FEED / 20 LEVELS"}
        title={instrument?.instrument.symbol ?? "Select an instrument"}
        action={<span className={`chip ${depthFresh ? "" : "alert"}`}>{raw ? `RAW OBSERVATION · ${rawRecent ? "RECENT" : "STALE"} · NO SIMULATION` : depthFresh ? "FRESH RECEIPT" : "STALE / UNVERIFIED"}</span>}
      >
        {depth ? (
          <>
            <div className="book-grid">
              <BookSide side="BID" depth={depth} maxQty={maxQty} />
              <BookSide side="ASK" depth={depth} maxQty={maxQty} />
            </div>
            <div className="source-note">
              {full ? `Five levels came from one Full packet${raw ? " preserved before research validation" : ""}. Book publication time and executable liquidity are unverified.` :
                `DEPTH_20: Bid and ask sides are derived from separate async depth socket (protocol: ${depth.provenance?.source_protocol ?? "DhanDepth20"}). Touch prices never overwrite market quotes. Exchange timestamp: ${time(depth.exchange_timestamp_ns)}.`}
              {" "}Displayed changes do not distinguish additions, cancellations or executions.
              {depth.provenance?.raw_frame_sha256 && <span> Raw frame SHA-256: <code>{depth.provenance.raw_frame_sha256}</code>; durable at {time(depth.provenance.raw_durable_at_ns)}.</span>}
            </div>
            {depth.book_metrics && <p>Quoted spread {depth.book_metrics.quoted_spread_bps.toFixed(1)} bps · bid/ask displayed {amount(depth.book_metrics.bid_quantity)} / {amount(depth.book_metrics.ask_quantity)} · orders {amount(depth.book_metrics.bid_orders)} / {amount(depth.book_metrics.ask_orders)} · five-level imbalance {ratio(depth.book_metrics.five_level_imbalance)} · bid/ask change {depth.book_metrics.bid_quantity_change ?? "N/A"} / {depth.book_metrics.ask_quantity_change ?? "N/A"} · depletion {depth.book_metrics.bid_depletion ?? "N/A"} / {depth.book_metrics.ask_depletion ?? "N/A"} · replenishment {depth.book_metrics.bid_replenishment ?? "N/A"} / {depth.book_metrics.ask_replenishment ?? "N/A"} · arrival-order OFI proxy {depth.book_metrics.ofi_proxy_quantity ?? "N/A"}. Observations only.</p>}
          </>
        ) : (
          <Empty message="Awaiting a valid five-level Full or paired 20-level depth snapshot" />
        )}
      </Panel>
      {!depth && restBook?.depth && <Panel eyebrow="REST_SNAPSHOT / FIVE DISPLAYED LEVELS" title={`${restBook.symbol} · ${restBook.security_id}`}>
        <p>Dhan REST quote received {restSnapshot?.received_at}; source last trade time {restBook.last_trade_time ?? "unavailable"}. This is separate from the 20-level WebSocket book and may be stale outside the session.</p>
        <div className="book-grid">{(["buy", "sell"] as const).map((side) => <div key={side}><h3>{side === "buy" ? "Bids" : "Asks"}</h3>
          {(restBook.depth?.[side] ?? []).map((level, index) => <p key={index}>{index + 1} · ₹{level.price} · displayed {level.quantity} · orders {level.orders}</p>)}</div>)}</div>
      </Panel>}
    </>
  );
}

const featureDescriptions = [
  [
    "Microprice",
    "Quantity-weighted top-of-book price.",
    (i: WireInstrumentSnapshot) => {
      if (i.features?.microprice_nanos) return price(i.features.microprice_nanos);
      const bid = i.depth?.bids[0];
      const ask = i.depth?.asks[0];
      if (bid && ask) {
        const bp = Number(bid.price_nanos);
        const ap = Number(ask.price_nanos);
        const bq = Number(bid.displayed_quantity);
        const aq = Number(ask.displayed_quantity);
        if (bq + aq > 0) {
          return price(Math.round((bp * aq + ap * bq) / (bq + aq)).toString());
        }
      }
      return "N/A";
    },
  ],
  [
    "Snapshot OFI",
    "Change in best-level supply and demand between snapshots; not individual order flow.",
    (i: WireInstrumentSnapshot) =>
      amount(
        i.depth?.book_metrics?.ofi_proxy_quantity ??
          i.features?.snapshot_ofi_quantity,
      ),
  ],
  [
    "Relative spread",
    "Absolute bid–ask spread divided by midprice.",
    (i: WireInstrumentSnapshot) =>
      i.depth?.book_metrics
        ? `${(i.depth.book_metrics.quoted_spread_bps / 100).toFixed(2)}%`
        : percent(i.features?.relative_spread),
  ],
  [
    "Bid / ask imbalance",
    "Visible bid share minus ask share, normalized by total visible depth.",
    (i: WireInstrumentSnapshot) =>
      ratio(
        i.depth?.book_metrics?.five_level_imbalance ??
          i.features?.bid_ask_imbalance,
      ),
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
  historical,
  restSnapshot,
}: {
  instrument: WireInstrumentSnapshot | null;
  historical: HistoricalCache | null;
  restSnapshot: RestSnapshot | null;
}) {
  const fullBook = (instrument?.depth?.source?.startsWith("STANDARD_FULL") || instrument?.depth?.source === "DEPTH_20") ? instrument.depth : null;
  const bookMetrics = fullBook?.book_metrics;
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
      <SelectedSourceEvidence instrument={instrument} historical={historical} restSnapshot={restSnapshot} />
      <Panel eyebrow={fullBook?.source === "STANDARD_FULL_RAW" ? "RAW OBSERVATION / VALIDATION PENDING" : "OBSERVED / STANDARD_FULL"} title="Five-level displayed book">
        <p>{fullBook ? `Received ${time(fullBook.receive_timestamp_ns)} · ${fullBook.source}. Descriptive only; no fill, signal or training eligibility implied.` : "No genuine standard-feed Full book has been observed for this instrument."}</p>
        {bookMetrics && <p>Spread {bookMetrics.quoted_spread_bps.toFixed(1)} bps · displayed bid/ask {amount(bookMetrics.bid_quantity)} / {amount(bookMetrics.ask_quantity)} · orders {amount(bookMetrics.bid_orders)} / {amount(bookMetrics.ask_orders)} · imbalance {ratio(bookMetrics.five_level_imbalance)} · bid/ask change {bookMetrics.bid_quantity_change ?? "N/A"} / {bookMetrics.ask_quantity_change ?? "N/A"} · depletion {bookMetrics.bid_depletion ?? "N/A"} / {bookMetrics.ask_depletion ?? "N/A"} · replenishment {bookMetrics.bid_replenishment ?? "N/A"} / {bookMetrics.ask_replenishment ?? "N/A"} · OFI proxy {bookMetrics.ofi_proxy_quantity ?? "N/A"}.</p>}
      </Panel>
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
  const [view, setView] = useState<View>("Overview");
  const tradingView = view === "Trade desk" || view === "Trading settings";
  const { snapshot, connection, messageLagMs, reconnects } = useEngineStream();
  const [historical, setHistorical] = useState<HistoricalCache | null>(null);
  const [restSnapshot, setRestSnapshot] = useState<RestSnapshot | null>(null);
  const [restError, setRestError] = useState("");
  const attemptedRest = useRef(false);
  async function refreshRestSnapshot() {
    try {
      const response = await fetch("/trade/market-snapshot", {method: "POST"});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error ?? `HTTP_${response.status}`);
      if (value.source !== "REST_SNAPSHOT") throw new Error("REST_SNAPSHOT_INVALID");
      setRestSnapshot(value as RestSnapshot); setRestError("");
    } catch (error) { setRestError(error instanceof Error ? error.message : "REST_SNAPSHOT_UNAVAILABLE"); }
  }
  useEffect(() => { if (connection === "CONNECTED" && !snapshot?.feed_connected && !attemptedRest.current) {
    attemptedRest.current = true; void refreshRestSnapshot();
  } }, [connection, snapshot?.feed_connected]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  useEffect(() => {
    let mounted = true;
    async function refresh() {
      try {
        const response = await fetch("/trade/capabilities", { cache: "no-store" });
        if (!response.ok) throw new Error("capabilities unavailable");
        const value = (await response.json()) as Capabilities;
        if (mounted) setCapabilities(value);
      } catch { if (mounted) setCapabilities(null); }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (connection !== "CONNECTED") return;
    let mounted = true;
    const read = () => { void fetch("/trade/market-snapshot", {cache: "no-store"})
      .then((response) => response.ok ? response.json() : null)
      .then((value) => { if (mounted && value?.source === "REST_SNAPSHOT") setRestSnapshot(value); })
      .catch(() => undefined); };
    read();
    const timer = window.setInterval(read, 15_000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [connection]);
  useEffect(() => {
    if (connection !== "CONNECTED") return;
    const controller = new AbortController();
    void fetch("/api/historical/reliance", {
      signal: controller.signal,
      cache: "no-store",
    })
      .then((response) => {
        if (!response.ok) throw new Error("historical cache unavailable");
        return response.json() as Promise<HistoricalCache>;
      })
      .then((value) => {
        if (
          !controller.signal.aborted &&
          value.schema_version === "dhan-historical-cache-v1" &&
          value.causal_eligibility === "BLOCKED" &&
          (value.status !== "AVAILABLE" ||
            (value.source === "DHAN_HISTORICAL" &&
              ((value.response_sha256 ===
                "bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861" &&
                value.bars.length === 720 &&
                value.coverage?.["2026-10-15"] === 360 &&
                value.coverage?.["2026-10-16"] === 360) ||
                (value.response_sha256 ===
                  "a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653" &&
                  value.bars.length === 1800 &&
                  [26, 27, 28, 29, 30].every(
                    (day) => value.coverage?.[`2026-10-${day}`] === 360,
                  )))))
        ) {
          setHistorical(value);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setHistorical(null);
      });
    return () => controller.abort();
  }, [connection]);
  const forgetDevice = async () => {
    if (await forgetCockpitSession()) window.location.reload();
  };
  const [instrumentKey, setInstrumentKey] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState("5000000000");
  const instrument =
    snapshot?.instruments.find(
      (item) => item.instrument.key === instrumentKey,
    ) ??
    snapshot?.instruments[0] ??
    null;
  const [selectedHistorical, setSelectedHistorical] = useState<HistoricalCache | null>(null);
  const selectedSymbol = instrument?.instrument.symbol;
  useEffect(() => {
    if (!selectedSymbol || selectedSymbol === "RELIANCE") return;
    const controller = new AbortController();
    setSelectedHistorical(null);
    void fetch(`/trade/historical/${encodeURIComponent(selectedSymbol)}`, { signal: controller.signal, cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((value) => { if (!controller.signal.aborted && value?.source === "DHAN_HISTORICAL" && value?.status === "AVAILABLE") setSelectedHistorical(value); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [selectedSymbol]);
  const visibleHistorical = selectedSymbol === "RELIANCE" ? historical : selectedHistorical;
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
          <div className="nav-caption">TRADING / REPLAY LAB</div>
          <nav aria-label="Cockpit views">
            {views.map((item) => (
              <button
                key={item.name}
                className={`nav-item ${view === item.name ? "selected" : ""}`}
                onClick={() => setView(item.name)}
              >
                <span className="nav-icon">{item.icon}</span>
                <span>{item.name === "Overview" ? "Command Center" : item.name}</span>
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
                {tradingView
                  ? restSnapshot ? "REST SNAPSHOT" : "DATA UNAVAILABLE"
                  : snapshot?.mode === "OBSERVE_ONLY"
                    ? snapshot.feed_connected ? "DHAN RAW CAPTURE LIVE" : "OBSERVATION ONLY"
                    : snapshot?.paper
                      ? "PAPER SIMULATION"
                      : restSnapshot ? "REST SNAPSHOT" : "DATA UNAVAILABLE"}
              </strong>
              <small>
                {tradingView
                  ? "NO TRADE · Dhan market data unverified"
                  : "NO TRADE · real execution disabled"}
              </small>
            </div>
          </div>
          <div className="sidebar-version">
            ALPHA GRID <span>·</span> PHASE 3B
          </div>
        </div>
      </aside>
      <div className="workspace">
        <Header
          snapshot={snapshot}
          connection={connection}
          tradingView={tradingView}
          simulationView={view === "Trading simulator"}
          restSnapshot={restSnapshot}
          onForgetDevice={() => void forgetDevice()}
        />
        <main>
          <div className="paper-foot" role="status">
            Local service {capabilities?.local_service ?? "STARTING"} · gateway {capabilities?.gateway ?? "STARTING"} · telemetry {connection === "CONNECTED" ? "READY" : "UNAVAILABLE"} · Dhan feed {capabilities?.feed ?? "STARTING"}{capabilities?.feed_reason ? ` (${capabilities.feed_reason})` : ""} · sockets {capabilities?.standard_socket_count ?? 0} standard / {capabilities?.depth_socket_count ?? 0} depth{capabilities?.depth_reason ? ` (${capabilities.depth_reason})` : ""} · depth frames {capabilities?.depth_frames_received ?? 0} · RELIANCE archive {capabilities?.historical_data ?? "STARTING"} · ALL_25 archive {capabilities?.all25_archive ? `${capabilities.all25_archive.state} ${capabilities.all25_archive.instruments} stocks / ${capabilities.all25_archive.bars.toLocaleString("en-IN")} bars` : "STARTING"} · simulation {capabilities?.simulation ?? "STARTING"} · training {capabilities?.training ?? "STARTING"} · last genuine feed quote {capabilities?.last_genuine_message_ns ? dateTime(capabilities.last_genuine_message_ns) : "none"} · Dhan REST snapshot {restSnapshot ? `${restSnapshot.snapshot_state ?? "CURRENT_RECEIPT"} ${restSnapshot.quotes.length} instruments at ${restSnapshot.received_at}${restSnapshot.refresh_error ? ` (${restSnapshot.refresh_error})` : ""}` : restError || "none"} · last genuine depth {capabilities?.last_genuine_depth_frame_ns ? dateTime(capabilities.last_genuine_depth_frame_ns) : "none"}
          </div>
          {capabilities?.last_raw_capture && <div className="source-note" role="status">
            {capabilities.last_raw_capture.state} · {capabilities.last_raw_capture.frames_received.toLocaleString("en-IN")} original Dhan frames · last receipt {dateTime(capabilities.last_raw_capture.last_frame_received_ns)} · {capabilities.last_raw_capture.feed_session_id}
          </div>}
          {!tradingView && (
            <div className="toolbar">
              <div className="instrument-select">
                <label htmlFor="instrument">INSTRUMENT</label>
                <select
                  id="instrument"
                  value={instrument?.instrument.key ?? ""}
                  onChange={(event) => setInstrumentKey(event.target.value)}
                >
                  {snapshot?.instruments.map((item) => (
                    <option
                      key={item.instrument.key ?? item.instrument.security_id}
                      value={item.instrument.key ?? item.instrument.security_id}
                    >
                      {item.instrument.symbol ?? item.instrument.security_id} ·{" "}
                      {item.instrument.security_id}
                    </option>
                  )) ?? <option value="">Awaiting engine</option>}
                </select>
              </div>
              <ReplayControls snapshot={snapshot} />
            </div>
          )}
          {view === "Trade desk" && (
            <TradeDesk
              onSettings={() => setView("Trading settings")}
              engineSnapshot={snapshot}
              engineConnection={connection}
              historical={historical}
              restSnapshot={restSnapshot}
              restError={restError}
              onRefreshRest={() => void refreshRestSnapshot()}
            />
          )}
          {view === "Trading settings" && <AutomationSettings />}
          {view === "Overview" && (
            <>
              <CommandCenter snapshot={snapshot} restSnapshot={restSnapshot}
                feedState={capabilities?.feed} standardSockets={capabilities?.standard_socket_count ?? 0}
                depthSockets={capabilities?.depth_socket_count ?? 0}
                onInspect={(key) => { setInstrumentKey(key); setView("Candle workstation"); }}
                onSimulator={() => setView("Trading simulator")} />
              <details className="command-legacy"><summary>Detailed gateway overview</summary>
                <Overview snapshot={snapshot} instrument={instrument} connection={connection}
                  historical={visibleHistorical} restSnapshot={restSnapshot} />
              </details>
            </>
          )}
          {view === "Storage & Session" && (
            <div className="storage-view-wrapper">
              <StorageSessionPanel
                onInspectStock={(key) => {
                  setInstrumentKey(key);
                  setView("Candle workstation");
                }}
              />
            </div>
          )}
          {view === "Live Monitor" && (
            <LiveMonitor
              snapshot={snapshot}
              connection={connection}
              historical={visibleHistorical}
              restSnapshot={restSnapshot}
              selectedInstrumentKey={instrumentKey}
              onSelectInstrument={(key) => setInstrumentKey(key)}
              feedState={capabilities?.feed}
            />
          )}
          {view === "Market intelligence" && (
            <MarketIntelligence
              instrument={instrument}
              observationOnly={snapshot?.mode === "OBSERVE_ONLY"}
              historical={visibleHistorical}
              restSnapshot={restSnapshot}
            />
          )}
          {view === "Candle workstation" && (
            <CandleWorkstation
              instrument={instrument}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
              historical={visibleHistorical}
              feedConnected={snapshot?.feed_connected ?? false}
            />
          )}
          {view === "Order book" && <OrderBook instrument={instrument} restSnapshot={restSnapshot} feedConnected={snapshot?.feed_connected ?? false} />}
          {view === "Feature monitor" && (
            <FeatureMonitor instrument={instrument} historical={visibleHistorical} restSnapshot={restSnapshot} />
          )}
          {view === "Reliability" && (
            <Reliability
              snapshot={snapshot}
              lag={messageLagMs}
              reconnects={reconnects}
            />
          )}
          {view === "Session audit" && <Audit snapshot={snapshot} />}
          {view === "Statistical research" && <ResearchWorkspace historical={historical} />}
          {view === "Trading simulator" && (
            <SimulationWorkspace historical={historical} />
          )}
          {view === "Paper research" && (
            <PaperWorkspace
              snapshot={snapshot}
              connection={connection}
              instrumentKey={instrument?.instrument.key ?? null}
              allowPaperMutations={false}
              historical={visibleHistorical}
            />
          )}
        </main>
      </div>
    </div>
  );
}
