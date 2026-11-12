import { useEffect, useRef, useState, type FormEvent } from "react";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";
import type { Connection } from "./stream";
import { dateTime } from "./format";

type Limit = { unit: string; value: string };
type Settings = {
  maxTrade: Limit;
  maxDailyLoss: Limit;
  stopLoss: Limit;
  profitTarget: Limit;
};
type SavedSettings = Settings & { updatedAt: string };
type TradingStatus = {
  execution: string;
  account: string;
  marketFeed: string;
  research: string;
  reason: string;
};
type AccountPanel = {
  endpoint: string;
  status: string;
  delivery?: "LIVE" | "CACHE_ONLY";
  retrieved_at?: string;
  last_http_status?: number;
  last_attempt_at?: string;
  last_validation_failure_at?: string;
  reason?: string;
  data?: {
    available_balance_inr?: string;
    count?: number;
    items?: Record<string, string | number>[];
  };
};
type AccountSnapshot = {
  status: string;
  panels: Record<string, AccountPanel>;
};
function accountFreshness(
  panel: AccountPanel | undefined,
  reauth: boolean,
  cacheOnly: boolean,
): string {
  if (panel?.reason === "CACHE_UNVERIFIABLE") return "UNVERIFIABLE";
  if (!panel?.retrieved_at) return "DATA UNAVAILABLE";
  const age = Date.now() - Date.parse(panel.retrieved_at);
  return reauth ||
    panel.status !== "AVAILABLE" ||
    !Number.isFinite(age) ||
    age < 0 ||
    age > 5 * 60_000 ||
    panel.last_http_status !== 200 ||
    panel.reason === "REQUEST_OR_STORAGE_FAILED" ||
    (panel.last_validation_failure_at !== undefined &&
      Date.parse(panel.last_validation_failure_at) >=
        Date.parse(panel.retrieved_at))
    ? "STALE"
    : cacheOnly || panel.delivery !== "LIVE"
      ? "CACHE_ONLY"
      : "LIVE";
}
export type HistoricalCache = {
  schema_version: "dhan-historical-cache-v1";
  status: "AVAILABLE" | "DATA_UNAVAILABLE";
  source: "DHAN_HISTORICAL" | null;
  instrument?: string;
  response_sha256?: string;
  retrieved_at?: string;
  coverage?: Record<string, number>;
  source_timestamp_internal_gaps?: Record<string, number>;
  unavailable_date?: string;
  bar_timestamp_semantics?: string;
  causal_eligibility: "BLOCKED";
  daily_status?: string;
  replay?: {
    mode: "HISTORICAL_REPLAY";
    status: "COMPLETE_NO_TRADE";
    source_sha256: string;
    processed_bars: number;
    total_bars: number;
    latest_session: string;
    latest_source_timestamp_ns: string;
    missing_source_minutes: number;
    retrospective_baseline_samples: number;
    latest_retrospective_baseline: {
      source_timestamp_ns: string;
      prior_only_5m_close_change_bps: string;
      volume_vs_prior_20m_median: string;
    } | null;
    baseline_eligibility: "DESCRIPTIVE_ONLY";
    outcomes: "WITHHELD_UNVERIFIED_COMPLETION";
    decision: "NO_TRADE";
    no_trade_evaluations: number;
    reasons: string[];
    paper_eligible: 0;
    paper_orders: 0;
    paper_fills: 0;
    qp01_records_written: 0;
    broker_orders: "DISABLED";
  };
  simulation?: import("./SimulationWorkspace").SimulationResult;
  bars: {
    timestamp_ns: string;
    open_nanos: string;
    high_nanos: string;
    low_nanos: string;
    close_nanos: string;
    volume: string;
  }[];
};
export type RestSnapshot = {source: "REST_SNAPSHOT"; received_at: string; sha256: string;
  snapshot_state?: "CURRENT_RECEIPT" | "STALE";
  refresh_error?: string | null;
  quotes: {symbol: string; security_id: string; last_price: number; volume?: number|null;
    net_change?: number; average_price?: number; last_quantity?: number;
    buy_quantity?: number; sell_quantity?: number;
    upper_circuit_limit?: number; lower_circuit_limit?: number;
    "52_week_high"?: number; "52_week_low"?: number;
    last_trade_time?: string|null; depth?: {buy?: {price: number; quantity: number; orders: number}[];
      sell?: {price: number; quantity: number; orders: number}[]}|null;
    ohlc?: {open: number; high: number; low: number; close: number}|null}[]};
const emptySettings: Settings = {
  maxTrade: { unit: "INR", value: "" },
  maxDailyLoss: { unit: "INR", value: "" },
  stopLoss: { unit: "PERCENT", value: "" },
  profitTarget: { unit: "PERCENT", value: "" },
};

export function TradeDesk({
  onSettings,
  engineSnapshot,
  engineConnection,
  historical,
  restSnapshot = null,
  restError = "",
  onRefreshRest = () => {},
}: {
  onSettings: () => void;
  engineSnapshot: WireEngineSnapshot | null;
  engineConnection: Connection;
  historical: HistoricalCache | null;
  restSnapshot?: RestSnapshot | null;
  restError?: string;
  onRefreshRest?: () => void;
}) {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const [settings, setSettings] = useState<SavedSettings | null>(null);
  const [account, setAccount] = useState<AccountSnapshot | null>(null);
  const [accountError, setAccountError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/trade/status", { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error("Trading service unavailable");
        return r.json() as Promise<TradingStatus>;
      })
      .then(setStatus)
      .catch(() => setStatus(null));
    void fetch("/trade/settings", { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error("Settings unavailable");
        return r.json() as Promise<{ settings: SavedSettings | null }>;
      })
      .then((value) => setSettings(value.settings))
      .catch(() => setSettings(null));
    async function readAccount() {
      try {
        const response = await fetch("/trade/account", {
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) {
          setAccountError(`LOCAL_ACCOUNT_HTTP_${response.status}`);
          throw new Error("Account unavailable");
        }
        setAccount((await response.json()) as AccountSnapshot);
        setAccountError(null);
      } catch {
        if (!controller.signal.aborted) {
          setAccount(null);
          setAccountError((previous) => previous ?? "LOCAL_ACCOUNT_REQUEST_FAILED");
        }
      }
    }
    void readAccount();
    const accountTimer = setInterval(() => {
      void readAccount();
    }, 60_000);
    return () => {
      controller.abort();
      clearInterval(accountTimer);
    };
  }, []);
  const funds = account?.panels.fundlimit;
  const holdings = account?.panels.holdings;
  const positions = account?.panels.positions;
  const orders = account?.panels.orders;
  const trades = account?.panels.trades;
  const accountStale = account?.status === "REAUTHENTICATION_REQUIRED";
  const accountCacheOnly = account?.status === "CACHE_ONLY";
  const rawCapture = engineSnapshot?.feed_connected &&
    engineSnapshot.no_trade_reasons.includes("RAW_CAPTURE_VALIDATION_PENDING");
  const feedStage = engineSnapshot?.audit
    .filter((event) => event.kind === "FEED_LIFECYCLE")
    .at(-1)?.detail;
  const onlineState =
    feedStage === "STOPPED:DHAN_DISCONNECT_807"
      ? "AUTH_EXPIRED"
      : status?.marketFeed !== "ONLINE_REQUESTED" &&
          engineSnapshot?.mode !== "OBSERVE_ONLY"
        ? "CACHE_ONLY"
        : accountStale ||
            !engineSnapshot ||
            engineSnapshot.journal_blocked ||
            engineSnapshot.mode === "DATA_ONLY" ||
            (feedStage?.startsWith("STOPPED:") &&
              !/(?:DNS_FAILED|TCP_FAILED|CONNECT_TIMED_OUT|TRANSPORT_EOF|SOCKET_FAILED|IDLE_TIMED_OUT|CAPTURE_WINDOW_ENDED|CANCELLED)/.test(
                feedStage,
              ))
          ? "BLOCKED"
          : engineSnapshot?.feed_connected &&
              engineSnapshot.instruments.some(
                (item) => item.market && !item.market.quality.stale,
              )
            ? "ONLINE"
            : "DISCONNECTED";
  return (
    <div className="trade-workspace">
      <div className="page-heading">
        <div>
          <div className="eyebrow">INDIAN EQUITIES / INTRADAY</div>
          <h1>
            Trade desk<span className="heading-dot">.</span>
          </h1>
          <p>
            Read-only account status appears below. Genuine market observations
            and archived research remain available independently of trade candidates.
          </p>
        </div>
        <div className="session-card">
          <span>EXECUTION</span>
          <strong>{status?.execution === "UNAVAILABLE" ? "DISABLED · NO TRADE" : status?.execution ?? "LOCAL SERVICE UNAVAILABLE"}</strong>
          <small>Automatic orders are inactive</small>
        </div>
      </div>
      <div className="trade-notice" role="status">
        <strong>{rawCapture ? "RAW MARKET DATA AVAILABLE · SIMULATION PAUSED" : "NO VALIDATED TRADE CANDIDATES · NO REAL ORDERS"}</strong>
        <p>
          {rawCapture ? "The standard feed is saving genuine frames; dated validation is pending, so those frames cannot drive virtual decisions." : status?.reason ?? "Trading service is not connected."} Account panels
          below are read-only and do not enable broker orders.
        </p>
      </div>
      <div className="trade-notice secondary" role="status">
        <strong>MARKET DATA · {rawCapture ? "RAW_CAPTURE + REST_SNAPSHOT" : engineSnapshot?.feed_connected ? "LIVE_WS" : restSnapshot ? "REST_SNAPSHOT" : onlineState}</strong>
        <p>
          {engineSnapshot?.feed_connected ? `${feedStage ?? "WebSocket connected"} · ` : ""}
          {restSnapshot
            ? `${restSnapshot.quotes.length} genuine Dhan REST quotes · received ${restSnapshot.received_at} · SHA-256 ${restSnapshot.sha256}`
            : restError || "No REST snapshot received"} · NO TRADE
        </p>
        {!engineSnapshot?.feed_connected && <button type="button" onClick={onRefreshRest}>Refresh read-only Dhan snapshot</button>}
        {restSnapshot && <p>{restSnapshot.quotes.map((item) => `${item.symbol} · ${item.security_id}: ₹${item.last_price}`).join(" · ")}</p>}
        {engineSnapshot?.instruments && engineSnapshot.instruments.some((item) => item.market?.price_nanos || item.depth?.bids[0]) && (
          <p>
            LIVE WEBSOCKET: {engineSnapshot.instruments.filter((item) => item.market?.price_nanos || item.depth?.bids[0]).map((item) => {
              const sym = item.instrument.symbol ?? item.instrument.security_id;
              const p = item.market?.price_nanos ? `₹${(Number(item.market.price_nanos) / 1e9).toFixed(2)}` : "—";
              const b = item.depth?.bids[0] ? `bid ₹${(Number(item.depth.bids[0].price_nanos) / 1e9).toFixed(2)}` : "";
              const a = item.depth?.asks[0] ? `ask ₹${(Number(item.depth.asks[0].price_nanos) / 1e9).toFixed(2)}` : "";
              const bookInfo = (b && a) ? ` (${b} / ${a})` : "";
              return `${sym}: ${p}${bookInfo}`;
            }).join(" · ")}
          </p>
        )}
      </div>
      <div className="trade-notice secondary" role="status">
        <strong>
          {accountStale
            ? "DHAN ACCOUNT: REAUTHENTICATION REQUIRED"
            : account?.status === "CACHE_ONLY"
              ? "DHAN ACCOUNT: VERIFIED LOCAL CACHE"
              : account?.status === "READ_ONLY"
                ? "DHAN ACCOUNT: READ-ONLY SYNC"
                : `DHAN ACCOUNT: ${accountError ?? "NO VERIFIED RESPONSE"}`}
        </strong>
        <p>
          {accountStale
            ? "REAUTHENTICATION REQUIRED. The saved Dhan token is expired or was rejected. Previously verified account data is stale; renew manually and restart the cockpit."
            : account?.status === "CACHE_ONLY"
              ? "Only previously verified local account responses are shown. No Dhan refresh is running."
              : account?.status === "READ_ONLY"
                ? `Funds ${funds?.status ?? "unavailable"}; holdings ${holdings?.status ?? "unavailable"}; positions ${positions?.status ?? "unavailable"}. Each read-only source refreshes independently.`
                : `No verified current account response is available${accountError ? ` (${accountError})` : ""}. Previously archived Dhan candles remain separate from account sync.`}
        </p>
      </div>
      <div className="trade-notice secondary" role="status">
        <strong>RUST ENGINE · {engineConnection}</strong>
        <p>
          Mode {engineSnapshot?.mode ?? "UNAVAILABLE"} · Feed{" "}
          {engineSnapshot?.feed_connected ? "CONNECTED" : "UNAVAILABLE"} · Last
          source update {dateTime(engineSnapshot?.latest_data_timestamp_ns)} ·
          Reasons{" "}
          {engineSnapshot?.no_trade_reasons.join(", ") ?? "ENGINE DISCONNECTED"}{" "}
          · Paper positions{" "}
          {engineSnapshot?.paper?.portfolio.positions.length ?? "UNAVAILABLE"} ·
          Live orders DISABLED
        </p>
        {engineSnapshot?.mode === "OBSERVE_ONLY" && (
          <p>Feed stage: {feedStage ?? "NO ATTEMPT RECORDED"}</p>
        )}
      </div>
      <div className="trade-card-grid">
        <section className="trade-card">
          <span className="eyebrow">ARCHIVED DHAN DATA</span>
          <h2>RELIANCE minute candles</h2>
          <div className="trade-card-value">
            {historical?.status === "AVAILABLE"
              ? historical.bars.length
              : "Unavailable"}
          </div>
          <p>
            {historical?.status === "AVAILABLE"
              ? `Authentic archived 1-minute rows for ${Object.keys(historical.coverage ?? {}).join(", ")}; retrieved ${historical.retrieved_at}. Source timestamp gaps: ${Object.values(historical.source_timestamp_internal_gaps ?? {}).reduce((a, b) => a + b, 0)}. ${historical.unavailable_date ? `${historical.unavailable_date} unavailable. ` : ""}Historical only; not a current quote.`
              : "No verified local candle cache loaded. Check the local archive and verifier output; the cockpit does not retry historical requests."}
          </p>
          <span className="trade-card-state">
            HISTORICAL_ARCHIVE · EXPLORATORY_ONLY
          </span>
          {historical?.status === "AVAILABLE" && historical.replay && (
            <div>
              <p>
                HISTORICAL REPLAY · {historical.replay.processed_bars} /{" "}
                {historical.replay.total_bars} source-integrity-verified archive
                rows processed. Latest archived session{" "}
                {historical.replay.latest_session}; last source timestamp{" "}
                {dateTime(historical.replay.latest_source_timestamp_ns)}.
              </p>
              <p>
                {historical.replay.no_trade_evaluations} NO TRADE evaluations ·{" "}
                paper orders {historical.replay.paper_orders} · fills{" "}
                {historical.replay.paper_fills}. Reasons:{" "}
                {historical.replay.reasons.join(", ")}.
              </p>
              <p>
                Retrospective price/volume baseline:{" "}
                {historical.replay.retrospective_baseline_samples} rows.
                Outcomes withheld; net edge and executable costs unavailable.{" "}
                Historical candles are not live quotes or attainable fills.
              </p>
            </div>
          )}
        </section>
        <section className="trade-card">
          <span className="eyebrow">DHAN ACCOUNT</span>
          <h2>Funds and positions</h2>
          <div className="trade-card-value">
            {funds?.status === "AVAILABLE"
              ? `₹${funds.data?.available_balance_inr}`
              : "Unavailable"}
          </div>
          <p>
            Available funds · Dhan retrieved{" "}
            {funds?.retrieved_at ?? "unavailable"} ·{" "}
            {accountStale
              ? "STALE / REAUTHENTICATION REQUIRED"
              : (funds?.reason ?? funds?.status ?? "unavailable")}
          </p>
          <p>
            Holdings:{" "}
            {holdings?.status === "AVAILABLE"
              ? holdings.data?.count
              : "unavailable"}{" "}
            · Positions:{" "}
            {positions?.status === "AVAILABLE"
              ? positions.data?.count
              : "unavailable"}
          </p>
          <p>
            Freshness: funds{" "}
            {accountFreshness(funds, accountStale, accountCacheOnly)}; holdings{" "}
            {accountFreshness(holdings, accountStale, accountCacheOnly)}
            {holdings?.last_http_status
              ? ` (last HTTP ${holdings.last_http_status})`
              : ""}
            ; positions{" "}
            {accountFreshness(positions, accountStale, accountCacheOnly)}.
          </p>
          {holdings?.data?.items?.map((item, index) => (
            <p key={`holding-${index}`}>
              {item.tradingSymbol} · held {item.totalQty ?? "unavailable"} ·
              available {item.availableQty ?? "unavailable"}
            </p>
          ))}
          {positions?.data?.items?.map((item, index) => (
            <p key={`position-${index}`}>
              {item.tradingSymbol} · {item.productType} · net{" "}
              {item.netQty ?? "unavailable"}
            </p>
          ))}
          <span className="trade-card-state">
            {status?.account ?? "NOT CONNECTED"}
          </span>
        </section>
        <section className="trade-card">
          <span className="eyebrow">CURRENT MARKET</span>
          <h2>Trade candidates</h2>
          <div className="trade-card-value">NO VALIDATED CANDIDATES · NO TRADE</div>
          <p>
            Genuine market observations are available separately. No eligible
            point-in-time candidate has been produced for this desk.
          </p>
          <span className="trade-card-state">
            {status?.marketFeed ?? "NOT CONNECTED"}
          </span>
        </section>
        <section className="trade-card">
          <span className="eyebrow">AUTOMATION</span>
          <h2>Intraday rules</h2>
          <div className="trade-card-value">
            {settings ? "Limits saved" : "Limits needed"}
          </div>
          <p>
            {settings
              ? "Your limits are saved locally. They do not activate live orders."
              : "Set maximum trade size, daily loss, stop-loss and profit target."}
          </p>
          <button className="trade-action" onClick={onSettings}>
            Configure limits
          </button>
        </section>
      </div>
      {(orders || trades) && <div className="trade-notice secondary">
        <strong>
          Dhan orders and executed trades · account ledger unavailable
        </strong>
        <p>
          Today’s orders:{" "}
          {orders?.status === "AVAILABLE" ? orders.data?.count : "unavailable"}{" "}
          (retrieved {orders?.retrieved_at ?? "unavailable"}). Today’s executed
          trades:{" "}
          {trades?.status === "AVAILABLE" ? trades.data?.count : "unavailable"}{" "}
          (retrieved {trades?.retrieved_at ?? "unavailable"}).{" "}
          {accountStale
            ? "Both panels are stale pending reauthentication."
            : "Each panel refreshes independently at most once per five minutes."}{" "}
          Historical trades and ledger are not authorized.
        </p>
        <p>
          Freshness: orders{" "}
          {accountFreshness(orders, accountStale, accountCacheOnly)}; executed
          trades {accountFreshness(trades, accountStale, accountCacheOnly)}.
        </p>
        {orders?.status === "AVAILABLE" &&
          orders.data?.items?.map((item, index) => (
            <p key={`order-${index}`}>
              {item.tradingSymbol} · {item.transactionType} · {item.orderStatus}{" "}
              · {item.quantity} · source{" "}
              {item.updateTime ?? item.createTime ?? "unavailable"}
            </p>
          ))}
        {trades?.status === "AVAILABLE" &&
          trades.data?.items?.map((item, index) => (
            <p key={`trade-${index}`}>
              {item.tradingSymbol} · {item.transactionType} ·{" "}
              {item.tradedQuantity} @ {item.tradedPrice} · source{" "}
              {item.exchangeTime ?? item.updateTime ?? "unavailable"}
            </p>
          ))}
      </div>}
      <div className="trade-notice secondary">
        <strong>Replay lab</strong>
        <p>
          Market research and paper decisions require verified Dhan
          observations. No verified market feed or executable trade suggestion
          is available.
        </p>
      </div>
    </div>
  );
}

const fields: {
  key: keyof Settings;
  title: string;
  hint: string;
  units: { value: string; label: string }[];
}[] = [
  {
    key: "maxTrade",
    title: "Maximum per trade",
    hint: "₹ total or % of available Dhan funds",
    units: [
      { value: "INR", label: "₹ total" },
      { value: "PERCENT", label: "% available funds" },
    ],
  },
  {
    key: "maxDailyLoss",
    title: "Maximum daily loss",
    hint: "₹ total or % of opening Dhan funds",
    units: [
      { value: "INR", label: "₹ total" },
      { value: "PERCENT", label: "% opening funds" },
    ],
  },
  {
    key: "stopLoss",
    title: "Stop-loss",
    hint: "₹ per share or % below the entry price",
    units: [
      { value: "PERCENT", label: "% below entry" },
      { value: "INR_PER_SHARE", label: "₹ per share" },
    ],
  },
  {
    key: "profitTarget",
    title: "Profit target",
    hint: "₹ per share or % above the entry price",
    units: [
      { value: "PERCENT", label: "% above entry" },
      { value: "INR_PER_SHARE", label: "₹ per share" },
    ],
  },
];

export function AutomationSettings() {
  const [form, setForm] = useState<Settings>(emptySettings);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/trade/settings", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Settings service unavailable");
        return response.json() as Promise<{ settings: SavedSettings | null }>;
      })
      .then(({ settings }) => {
        if (settings) setForm(settings);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMessage(error.message);
      });
    return () => controller.abort();
  }, []);

  function update(key: keyof Settings, part: Partial<Limit>) {
    setForm((previous) => ({
      ...previous,
      [key]: { ...previous[key], ...part },
    }));
    setMessage("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/trade/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const result = (await response.json()) as {
        settings?: SavedSettings;
        error?: string;
      };
      if (!response.ok)
        throw new Error(result.error ?? "Could not save settings");
      setMessage(
        "Limits saved. Automatic trading is still unavailable until live services are connected.",
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Could not save settings",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="trade-workspace">
      <div className="page-heading">
        <div>
          <div className="eyebrow">AUTOMATION / INTRADAY ONLY</div>
          <h1>
            Trading settings<span className="heading-dot">.</span>
          </h1>
          <p>
            Choose the limits that will govern automatic intraday trades. You
            can change them before activation.
          </p>
        </div>
      </div>
      <form className="trade-settings" onSubmit={(event) => void save(event)}>
        {fields.map(({ key, title, hint, units }) => (
          <label className="trade-setting" key={key} htmlFor={`limit-${key}`}>
            <span>
              <strong>{title}</strong>
              <small>{hint}</small>
            </span>
            <div>
              <input
                id={`limit-${key}`}
                type="text"
                inputMode="decimal"
                required
                value={form[key].value}
                onChange={(event) => update(key, { value: event.target.value })}
                placeholder="Enter amount"
              />
              <select
                aria-label={`${title} unit`}
                value={form[key].unit}
                onChange={(event) => update(key, { unit: event.target.value })}
              >
                {units.map(({ value, label }) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </label>
        ))}
        <div className="trade-settings-footer">
          <p>
            These settings are saved by the local trading service. Saving does
            not send an order or start automation.
          </p>
          <button className="trade-action" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save limits"}
          </button>
        </div>
        {message && (
          <p className="trade-form-message" role="status">
            {message}
          </p>
        )}
      </form>
      <MarketDataConnection />
    </div>
  );
}

type MarketAccess = {
  authorized_security_ids: string[];
  authorized_feeds: string[];
  duration: "SESSION_ONLY" | "PERSISTENT";
  expires_at: string | null;
  auto_connect: boolean;
  revoked_at: string | null;
};

interface TokenDetailsResponse {
  status?: "VALID" | "EXPIRING" | "EXPIRED" | "AUTH REQUIRED";
  expires_at_ist?: string | null;
  token_type?: string | null;
  is_renewable?: boolean;
  client_id_masked?: string;
}

function MarketDataConnection() {
  const [access, setAccess] = useState<MarketAccess | null>(null);
  const [universe, setUniverse] = useState<{ symbol: string; security_id: string }[]>([]);
  const [depthAvailable, setDepthAvailable] = useState(false);
  const [depthRequested, setDepthRequested] = useState(false);
  const depthTouched = useRef(false);
  const [standardMode, setStandardMode] = useState<"QUOTE" | "FULL">("QUOTE");
  const modeTouched = useRef(false);
  const [duration, setDuration] = useState<"SESSION_ONLY" | "PERSISTENT">("PERSISTENT");
  const [expiry, setExpiry] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Loading market-data permission…");
  const [feed, setFeed] = useState("STARTING");
  const [reason, setReason] = useState<string | null>(null);
  const [profileStatus, setProfileStatus] = useState("");
  const [tokenStatus, setTokenStatus] = useState<"VALID" | "EXPIRING" | "EXPIRED" | "AUTH REQUIRED">("AUTH REQUIRED");
  const [tokenDetails, setTokenDetails] = useState<TokenDetailsResponse | null>(null);
  const [clientIdInput, setClientIdInput] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [tokenTypeInput, setTokenTypeInput] = useState<"API" | "WEB">("API");
  const [expiryInput, setExpiryInput] = useState("");
  const [tokenActionMsg, setTokenActionMsg] = useState("");
  const [renewing, setRenewing] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const [permission, capability, tokenRes] = await Promise.all([
          fetch("/trade/market-data-access", { cache: "no-store", signal: controller.signal }),
          fetch("/trade/capabilities", { cache: "no-store", signal: controller.signal }),
          fetch("/trade/dhan/token-status", { cache: "no-store", signal: controller.signal }).catch(() => null),
        ]);
        if (!permission.ok || !capability.ok) throw new Error("Market data controls unavailable");
        const saved = await permission.json() as { access: MarketAccess | null; universe: { symbol: string; security_id: string }[]; depth_available: boolean };
        const health = await capability.json() as { feed: string; feed_reason: string | null };
        setAccess(saved.access ?? null);
        if (!modeTouched.current)
          setStandardMode(saved.access?.authorized_feeds[0] === "STANDARD_FULL" ? "FULL" : "QUOTE");
        if (!depthTouched.current)
          setDepthRequested(saved.access?.authorized_feeds.includes("DEPTH_20") ?? false);
        setUniverse(Array.isArray(saved.universe) ? saved.universe : []);
        setDepthAvailable(saved.depth_available === true);
        setFeed(health.feed);
        setReason(health.feed_reason);
        if (tokenRes && tokenRes.ok) {
          const tData = await tokenRes.json() as TokenDetailsResponse;
          if (tData.status) setTokenStatus(tData.status);
          setTokenDetails(tData);
        }
        setMessage("");
      } catch (error) {
        if (!controller.signal.aborted)
          setMessage(error instanceof Error ? error.message : "Market data controls unavailable");
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);
  async function submitToken(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setTokenActionMsg("");
    try {
      const res = await fetch("/trade/dhan/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clientId: clientIdInput.trim() || undefined,
          accessToken: tokenInput.trim(),
          tokenType: tokenTypeInput,
          expiryTime: expiryInput || undefined,
        }),
      });
      const data = await res.json() as { error?: string; verified_locally?: boolean };
      if (!res.ok) throw new Error(data.error ?? "Token installation failed");
      if (!data.verified_locally) throw new Error("Credential could not be verified by shared loader");
      setTokenActionMsg("DhanHQ token securely saved in private storage and verified.");
      setClientIdInput("");
      setTokenInput("");
      setExpiryInput("");
      const tRes = await fetch("/trade/dhan/token-status");
      if (tRes.ok) {
        const tData = await tRes.json() as TokenDetailsResponse;
        if (tData.status) setTokenStatus(tData.status);
        setTokenDetails(tData);
      }
    } catch (err) {
      setTokenActionMsg(err instanceof Error ? err.message : "Token installation failed");
    } finally {
      setBusy(false);
    }
  }
  async function renewToken() {
    setRenewing(true);
    setTokenActionMsg("");
    try {
      const res = await fetch("/trade/dhan/renew-token", { method: "POST" });
      const data = await res.json() as { error?: string };
      if (!res.ok) throw new Error(data.error ?? "Renewal failed");
      setTokenActionMsg("Dhan token successfully renewed via RenewToken API.");
      const tRes = await fetch("/trade/dhan/token-status");
      if (tRes.ok) {
        const tData = await tRes.json() as TokenDetailsResponse;
        if (tData.status) setTokenStatus(tData.status);
        setTokenDetails(tData);
      }
    } catch (err) {
      setTokenActionMsg(err instanceof Error ? err.message : "Renewal failed");
    } finally {
      setRenewing(false);
    }
  }
  async function act(action: "approve" | "connect" | "disconnect" | "revoke") {
    setBusy(true);
    setMessage("");
    try {
      const body = action === "approve" ? {
        action, confirmed, universe: "FROZEN_25_NSE_EQ", standard_feed: true,
        standard_mode: standardMode,
        depth_feed: depthRequested,
        duration: active ? access.duration : duration,
        expires_at: active ? access.expires_at : expiry ? new Date(expiry).toISOString() : null,
        auto_connect: true,
      } : { action };
      const response = await fetch("/trade/market-data-access", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await response.json() as { access?: MarketAccess; error?: string };
      if (!response.ok || !result.access) throw new Error(result.error ?? "Permission update failed");
      setAccess(result.access);
      modeTouched.current = false;
      setStandardMode(result.access.authorized_feeds[0] === "STANDARD_FULL" ? "FULL" : "QUOTE");
      setConfirmed(false);
      setMessage(action === "approve" ? "Read-only scope saved. Connection awaits an eligible session and preflight." :
        action === "connect" ? "Auto-connect enabled; connection awaits all gates." :
          action === "disconnect" ? "Auto-connect disabled; active feed will disconnect." :
            "Market-data access revoked.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Permission update failed");
    } finally { setBusy(false); }
  }
  const active = access && !access.revoked_at;
  return <section className="trade-settings" aria-label="Market Data Connection">
    <h2>Market Data Connection</h2>
    <p>Read-only Dhan data for the frozen 25 NSE cash equities. Virtual simulation only; broker orders remain disabled.</p>

    <div className="token-status-hud" style={{ border: "1px solid #315652", borderRadius: 4, padding: "8px 12px", margin: "8px 0", background: "rgba(0,0,0,0.15)" }}>
      <p style={{ margin: "4px 0" }}>
        <strong>Dhan Access Token:</strong>{" "}
        <span className={`status-badge status-${tokenStatus.toLowerCase().replace(/\s+/g, "-")}`}
              style={{ fontWeight: "bold", padding: "2px 6px", borderRadius: 3,
                       background: tokenStatus === "VALID" ? "#1e4620" : tokenStatus === "EXPIRING" ? "#614000" : "#5c1d1d" }}>
          {tokenStatus}
        </span>
        {tokenDetails?.client_id_masked && <span> · Client {tokenDetails.client_id_masked}</span>}
        {tokenDetails?.token_type && <span> · Type {tokenDetails.token_type}</span>}
        {tokenDetails?.expires_at_ist && <span> · Expires {tokenDetails.expires_at_ist}</span>}
      </p>
      <p style={{ margin: "4px 0", fontSize: "0.85em", color: "#888" }}>
        Automatic renewal: <em>Disabled</em> (Requires explicit operator approval per session rules).
      </p>
      {tokenDetails?.is_renewable && tokenStatus !== "EXPIRED" && tokenStatus !== "AUTH REQUIRED" && (
        <button type="button" disabled={busy || renewing} onClick={() => void renewToken()} style={{ marginTop: 4 }}>
          {renewing ? "Renewing…" : "Renew Active Token Now (via RenewToken API)"}
        </button>
      )}
    </div>

    <details open={tokenStatus === "AUTH REQUIRED" || tokenStatus === "EXPIRED"}>
      <summary>Supply DhanHQ Access Token (No Browser Login Required)</summary>
      <form onSubmit={(e) => void submitToken(e)} style={{ display: "flex", flexDirection: "column", gap: 8, margin: "8px 0" }}>
        <p style={{ margin: "2px 0", fontSize: "0.9em" }}>
          Paste your DhanHQ Access Token below. Tokens are written atomically to owner-private storage and never exposed to browser storage, logs, or telemetry.
        </p>
        <label>
          Dhan Client ID:{" "}
          <input
            type="text"
            pattern="[0-9]*"
            value={clientIdInput}
            onChange={(e) => setClientIdInput(e.target.value)}
            placeholder={tokenDetails?.client_id_masked ? `Using stored ${tokenDetails.client_id_masked} (or enter new)` : "e.g. 1000000001"}
          />
        </label>
        <label>
          Access Token:{" "}
          <input type="password" autoComplete="off" required value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} placeholder="Paste token here" />
        </label>
        <label>
          Token Type:{" "}
          <select value={tokenTypeInput} onChange={(e) => setTokenTypeInput(e.target.value as "API" | "WEB")}>
            <option value="API">DhanHQ API Access Token (Standard)</option>
            <option value="WEB">Dhan Web Access Token (Renewable via RenewToken)</option>
          </select>
        </label>
        <label>
          Optional Expiry IST:{" "}
          <input type="datetime-local" value={expiryInput} onChange={(e) => setExpiryInput(e.target.value)} />
        </label>
        <button
          type="submit"
          disabled={busy || !tokenInput.trim() || (!clientIdInput.trim() && !tokenDetails?.client_id_masked)}
          style={{ alignSelf: "flex-start" }}
        >
          Save Token Locally
        </button>
        {tokenActionMsg && <p role="status" style={{ margin: "4px 0" }}>{tokenActionMsg}</p>}
      </form>
    </details>

    <details><summary>Install a fresh Dhan Client ID and Access Token via Terminal</summary>
      <p>Run this in Terminal. The prompts hide both values and store them in owner-private backend files:</p>
      <pre>{'cd ~/alpha-grid && PYTHONPATH=src .venv/bin/python -m quant.apps.live_feed_credentials --root . --private-dir "/Volumes/AlphaGrid Data/Live/offline-dev" --install-token'}</pre>
      <p>Enter the expiry shown by Dhan. A new valid local token creates the standard-feed 25-stock scope unless you previously revoked it.</p>
    </details>
    <button type="button" disabled={busy} onClick={() => { setProfileStatus("CHECKING"); void fetch("/trade/dhan/profile", { method: "POST" }).then(async (response) => {
      const value = await response.json() as { status?: string }; setProfileStatus(value.status ?? `HTTP_${response.status}`);
    }).catch(() => setProfileStatus("PROFILE_CHECK_UNAVAILABLE")); }}>Test Dhan token and Data API now</button>
    {profileStatus && <p role="status">Dhan profile: {profileStatus}</p>}
    <details><summary>Approved universe: {universe.length} instruments</summary>
      <p>{universe.map((item) => `${item.symbol} ${item.security_id}`).join(" · ")}</p>
    </details>
    <p role="status">Feed {feed}{reason ? ` · ${reason}` : ""} · Auto-connect {active && access.auto_connect ? "ON" : "OFF"}</p>
    {active ? <>
      <p>Approved: {access.authorized_security_ids.length} instruments · {access.authorized_feeds.join(", ")} · {access.duration === "PERSISTENT" ? "until revoked" : "this session only"}{access.expires_at ? ` · expires ${access.expires_at}` : ""}</p>
      <p>Changing from Quote to Full expands the approved standard-feed data. Full uses the same shared socket and includes five displayed bid/ask levels.</p>
      <label>Standard feed mode <select value={standardMode} onChange={(event) => { modeTouched.current = true; setStandardMode(event.target.value as "QUOTE" | "FULL"); setConfirmed(false); }}>
        <option value="QUOTE">Quote · last price and volume</option><option value="FULL">Full · five-level order book</option>
      </select></label>
      <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> I approve this exact read-only {standardMode === "FULL" ? "Full" : "Quote"} feed for the frozen 25 IDs.</label>
      <div className="trade-settings-footer">
        <button type="button" disabled={busy || !confirmed || access.authorized_feeds[0] === `STANDARD_${standardMode}`} onClick={() => void act("approve")}>Save selected feed mode</button>
        <button type="button" className="trade-action" disabled={busy || access.auto_connect} onClick={() => void act("connect")}>Connect / Auto-connect ON</button>
        <button type="button" disabled={busy || !access.auto_connect} onClick={() => void act("disconnect")}>Disconnect / Auto-connect OFF</button>
        <button type="button" disabled={busy} onClick={() => void act("revoke")}>Revoke Access</button>
      </div>
    </> : <>
      <label>Authorization duration <select value={duration} onChange={(event) => setDuration(event.target.value as "SESSION_ONLY" | "PERSISTENT")}>
        <option value="SESSION_ONLY">This session only</option><option value="PERSISTENT">Persistent until revoked</option>
      </select></label>
      <label>Optional expiry <input type="datetime-local" value={expiry} onChange={(event) => setExpiry(event.target.value)} /></label>
      <p>Standard market feed: ON (one shared socket).</p>
      <label>Standard feed mode <select value={standardMode} onChange={(event) => { modeTouched.current = true; setStandardMode(event.target.value as "QUOTE" | "FULL"); setConfirmed(false); }}>
        <option value="QUOTE">Quote · last price and volume</option><option value="FULL">Full · five-level order book</option>
      </select></label>
      <label><input type="checkbox" disabled={!depthAvailable} checked={depthRequested} onChange={(event) => { depthTouched.current = true; setDepthRequested(event.target.checked); setConfirmed(false); }} /> 20-level depth feed (one separate read-only socket){!depthAvailable ? " — collector unavailable" : ""}</label>
      <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> I approve this exact read-only 25-stock {standardMode === "FULL" ? "Full" : "Quote"} scope{depthRequested ? " with 20-level depth" : ""} and confirm the required Dhan data entitlement is active.</label>
      <button type="button" className="trade-action" disabled={busy || !confirmed} onClick={() => void act("approve")}>Approve and enable auto-connect</button>
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
