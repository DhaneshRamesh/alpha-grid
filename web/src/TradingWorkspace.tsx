import { useEffect, useState, type FormEvent } from "react";
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
  response_sha256?: string;
  retrieved_at?: string;
  coverage?: Record<string, number>;
  source_timestamp_internal_gaps?: Record<string, number>;
  unavailable_date?: string;
  bar_timestamp_semantics?: string;
  causal_eligibility: "BLOCKED";
  daily_status?: string;
  bars: {
    timestamp_ns: string;
    open_nanos: string;
    high_nanos: string;
    low_nanos: string;
    close_nanos: string;
    volume: string;
  }[];
};
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
}: {
  onSettings: () => void;
  engineSnapshot: WireEngineSnapshot | null;
  engineConnection: Connection;
  historical: HistoricalCache | null;
}) {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const [settings, setSettings] = useState<SavedSettings | null>(null);
  const [account, setAccount] = useState<AccountSnapshot | null>(null);
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
        if (!response.ok) throw new Error("Account unavailable");
        setAccount((await response.json()) as AccountSnapshot);
      } catch {
        if (!controller.signal.aborted) setAccount(null);
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
            Verified Dhan account data appears below. Market candidates and
            execution remain unavailable until their separate gates pass.
          </p>
        </div>
        <div className="session-card">
          <span>EXECUTION</span>
          <strong>{status?.execution ?? "UNAVAILABLE"}</strong>
          <small>Automatic orders are inactive</small>
        </div>
      </div>
      <div className="trade-notice" role="status">
        <strong>DATA UNAVAILABLE · NO TRADE</strong>
        <p>
          {status?.reason ?? "Trading service is not connected."} Account panels
          below are read-only and do not enable stock picks or orders.
        </p>
      </div>
      <div className="trade-notice secondary" role="status">
        <strong>MARKET DATA · {onlineState}</strong>
        <p>
          RELIANCE Quote-17 observation only ·{" "}
          {feedStage ?? "NO FEED ATTEMPT RECORDED"} · NO TRADE
        </p>
      </div>
      <div className="trade-notice secondary" role="status">
        <strong>
          {accountStale
            ? "DHAN ACCOUNT: REAUTHENTICATION REQUIRED"
            : account?.status === "CACHE_ONLY"
              ? "DHAN ACCOUNT: VERIFIED LOCAL CACHE"
              : account?.status === "READ_ONLY"
                ? "DHAN ACCOUNT: READ-ONLY SYNC"
                : "DHAN ACCOUNT: DATA UNAVAILABLE"}
        </strong>
        <p>
          {accountStale
            ? "REAUTHENTICATION REQUIRED. The saved Dhan token is expired or was rejected. Previously verified account data is stale; renew manually and restart the cockpit."
            : account?.status === "CACHE_ONLY"
              ? "Only previously verified local account responses are shown. No Dhan refresh is running."
              : account?.status === "READ_ONLY"
                ? `Funds ${funds?.status ?? "unavailable"}; holdings ${holdings?.status ?? "unavailable"}; positions ${positions?.status ?? "unavailable"}; orders ${orders?.status ?? "unavailable"}; trades ${trades?.status ?? "unavailable"}. Each source refreshes independently.`
                : "No verified current account response is available. Previously archived Dhan candles remain separate from account sync."}
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
              ? `Authentic archived 1-minute rows for ${Object.keys(historical.coverage ?? {}).join(", ")}; retrieved ${historical.retrieved_at}. Source timestamp gaps: ${Object.values(historical.source_timestamp_internal_gaps ?? {}).reduce((a, b) => a + b, 0)}. October 27 unavailable. Historical only; not a current quote.`
              : "No verified local candle cache loaded. October 27 retrieval failed authentication."}
          </p>
          <span className="trade-card-state">
            DAILY UNAVAILABLE · CAUSAL RESEARCH BLOCKED
          </span>
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
          <div className="trade-card-value">DATA UNAVAILABLE · NO TRADE</div>
          <p>
            Candidate ranking needs current NSE prices, a defined stock universe
            and a checked research process.
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
      <div className="trade-notice secondary">
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
      </div>
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
    </div>
  );
}
