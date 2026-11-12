import { useEffect, useState } from "react";
import type { HistoricalCache } from "./TradingWorkspace";

type CurvePoint = {
  x: number;
  favorable?: number;
  adverse?: number;
  observed?: number;
  predicted?: number;
};
type ResearchStatus = {
  schema_version: string;
  status: string;
  source_kind: string | null;
  source?: string;
  feature_rows: number;
  label_rows: number;
  independent_sessions: number;
  label_fraction: number;
  quality_fraction: number;
  volume_complete_fraction?: number;
  horizon_coverage_fraction?: number;
  dataset_sha256: string | null;
  sufficiency_reasons: string[];
  model_version?: string;
  calibration?: CurvePoint[];
  cumulative_incidence?: CurvePoint[];
  net_return_vs_costs?: CurvePoint[];
  baseline_comparison?: string;
  no_trade_rate?: number;
  feature_sensitivity?: string;
};
type AcquisitionStatus = {
  schema_version: "acquisition-status-v1";
  status?: string;
  sources_configured: string[];
  access_status: string;
  source_files_available?: number;
  genuine_source_files_available?: number;
  source_verification_status?: string;
  calendar_completeness?: string;
  mapping_uncertainty?: string;
  first_date: string | null;
  last_date: string | null;
  instrument_count: number;
  independent_sessions: number;
  missing_bars: number | null;
  quality_warning_count: number | null;
  partition_count: number;
  partition_bytes: number;
  checksum_verified: boolean;
  collection_mode: string;
  research_eligibility: string;
  historical_dataset_eligibility?: string;
  training_blocked?: boolean;
  blocked_model_families: string[];
  block_reasons: string[];
};
type TrainingStatus = {
  id?: string;
  ticker?: string;
  state: string;
  reason?: string;
  sha256?: string;
  artifact?: string;
  research_eligibility?: string;
};
type ExploratoryArtifact = {
  source: "EXPLORATORY_ARCHIVE";
  sha256: string;
  source_bars: number;
  source_sessions: number;
  primary_60m: {
    evaluation_rows: number;
    evaluation_sessions: number;
    evaluation_mae_bps: number;
    simple_mean_mae_bps: number;
    positive_probability_calibration: { brier: number; unconditional_brier: number; bins: {count: number; observed_positive_fraction: number; predicted_positive_fraction: number}[] };
    primary_50bps: { selected_decisions: number; model_mean_session_reference_net_bps: number; no_trade_mean_session_reference_net_bps: number };
    sensitivity_25bps: { selected_decisions: number; model_mean_session_reference_net_bps: number };
    incremental_economic_value_demonstrated: boolean;
  };
};

function ArchiveResearchPanels({ artifact }: { artifact: ExploratoryArtifact }) {
  const result = artifact.primary_60m;
  return <>
    <section className="panel acquisition-panel"><div className="panel-head"><div><div className="eyebrow">EXPLORATORY_ARCHIVE</div><h2>Verified local research archive</h2></div></div>
      <p>1 instrument · {artifact.source_bars.toLocaleString()} original Dhan one-minute bars · {artifact.source_sessions} sessions · artifact SHA-256 {artifact.sha256}. Historical candle availability remains unverified.</p></section>
    <div className="metric-grid research-metrics">
      <div className="metric"><div className="metric-label">DATASET</div><div className="metric-value">RELIANCE · 2885</div><div className="metric-note">EXPLORATORY_ARCHIVE · {artifact.source_bars.toLocaleString()} bars</div></div>
      <div className="metric"><div className="metric-label">EVALUATION SESSIONS</div><div className="metric-value">{result.evaluation_sessions}</div><div className="metric-note">{result.evaluation_rows.toLocaleString()} 60-minute observations</div></div>
      <div className="metric"><div className="metric-label">MODEL STATUS</div><div className="metric-value">EXPLORATORY ONLY</div><div className="metric-note">No promotion or order connection</div></div>
    </div>
    <div className="paper-grid"><section className="panel"><div className="panel-head"><div><div className="eyebrow">MEASURED / 60M</div><h2>Probability calibration</h2></div></div>
      <p>Brier {result.positive_probability_calibration.brier.toFixed(4)} · unconditional baseline {result.positive_probability_calibration.unconditional_brier.toFixed(4)}.</p>
      <table><thead><tr><th>Bin</th><th>Rows</th><th>Predicted positive</th><th>Observed positive</th></tr></thead><tbody>{result.positive_probability_calibration.bins.map((bin, i) => <tr key={i}><td>{i + 1}</td><td>{bin.count}</td><td>{(100 * bin.predicted_positive_fraction).toFixed(1)}%</td><td>{(100 * bin.observed_positive_fraction).toFixed(1)}%</td></tr>)}</tbody></table>
    </section><section className="panel"><div className="panel-head"><div><div className="eyebrow">MEASUREMENT LIMIT</div><h2>Cumulative incidence</h2></div></div><p>NOT MEASURED in this saved 60-minute artifact. It reports return sign calibration, not first-hit favorable/adverse incidence.</p></section></div>
    <div className="paper-grid"><section className="panel"><div className="panel-head"><div><div className="eyebrow">REFERENCE OUTCOMES</div><h2>Net return versus costs</h2></div></div>
      <p>50 bps assumed round trip: {result.primary_50bps.selected_decisions} selected; mean per evaluation session {result.primary_50bps.model_mean_session_reference_net_bps.toFixed(4)} bps.</p>
      <p>25 bps assumed round trip: {result.sensitivity_25bps.selected_decisions} selected; mean per evaluation session {result.sensitivity_25bps.model_mean_session_reference_net_bps.toFixed(4)} bps. Historical close references are not fills.</p>
    </section><section className="panel"><div className="panel-head"><div><div className="eyebrow">PAIRED BASELINES</div><h2>Baseline comparison</h2></div></div>
      <p>Model MAE {result.evaluation_mae_bps.toFixed(3)} bps · simple mean MAE {result.simple_mean_mae_bps.toFixed(3)} bps · NO TRADE reference net {result.primary_50bps.no_trade_mean_session_reference_net_bps.toFixed(3)} bps.</p>
      <p>Incremental economic value demonstrated: {result.incremental_economic_value_demonstrated ? "YES" : "NO"}.</p>
    </section></div>
  </>;
}

function Curve({
  points,
  title,
  keys,
}: {
  points?: CurvePoint[];
  title: string;
  keys: [keyof CurvePoint, keyof CurvePoint];
}) {
  if (!points?.length)
    return (
      <div className="empty">N/A · independent validation data unavailable</div>
    );
  const line = (key: keyof CurvePoint) =>
    points
      .filter((point) => typeof point[key] === "number")
      .map((point) => `${point.x * 100},${100 - Number(point[key]) * 100}`)
      .join(" ");
  return (
    <svg
      className="research-curve"
      viewBox="0 0 100 100"
      role="img"
      aria-label={title}
    >
      <polyline
        points={line(keys[0])}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2"
      />
      <polyline
        points={line(keys[1])}
        fill="none"
        stroke="var(--negative)"
        strokeWidth="2"
      />
    </svg>
  );
}

export default function ResearchWorkspace({ historical = null }: { historical?: HistoricalCache | null }) {
  const [status, setStatus] = useState<ResearchStatus | null>(null);
  const [acquisition, setAcquisition] = useState<AcquisitionStatus | null>(
    null,
  );
  const [error, setError] = useState(false);
  const [ticker, setTicker] = useState("");
  const [training, setTraining] = useState<TrainingStatus | null>(null);
  const [trainingError, setTrainingError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [exploratory, setExploratory] = useState<ExploratoryArtifact | null>(null);
  useEffect(() => { void fetch("/trade/exploratory-artifact", { cache: "no-store" })
    .then((response) => response.ok ? response.json() : null)
    .then((value) => { if (value?.source === "EXPLORATORY_ARCHIVE") setExploratory(value); })
    .catch(() => undefined); }, []);
  useEffect(() => {
    let mounted = true;
    async function refresh() {
      try {
        const response = await fetch("/trade/training", { cache: "no-store" });
        if (!response.ok) throw new Error(`Training worker HTTP ${response.status}`);
        const value = (await response.json()) as TrainingStatus;
        if (typeof value.state !== "string") throw new Error("Training worker response invalid");
        if (mounted) { setTraining(value); setTrainingError(""); }
      } catch (reason) {
        if (mounted) setTrainingError(String(reason));
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);
  async function queueTraining(selectedTicker: string) {
    setSubmitting(true);
    setTrainingError("");
    try {
      const response = await fetch("/trade/training", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: selectedTicker }),
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error ?? `Training HTTP ${response.status}`);
      setTraining(value as TrainingStatus);
    } catch (reason) { setTrainingError(String(reason)); }
    finally { setSubmitting(false); }
  }
  function addAndTrain(event: React.FormEvent) {
    event.preventDefault();
    void queueTraining(ticker.trim().toUpperCase());
  }
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/research", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Research status ${response.status}`);
        return response.json() as Promise<ResearchStatus>;
      })
      .then((value) => {
        if (
          value.source_kind === "REAL_VERIFIED" &&
          value.source === "DHAN_HISTORICAL"
        ) {
          setStatus(value);
        } else {
          setError(true);
        }
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError"))
          setError(true);
      });
    fetch("/api/acquisition", { signal: controller.signal })
      .then((response) => {
        if (!response.ok)
          throw new Error(`Acquisition status ${response.status}`);
        return response.json() as Promise<AcquisitionStatus>;
      })
      .then((value) => {
        if (
          value.schema_version === "acquisition-status-v1" &&
          value.sources_configured.every((source) => source.startsWith("DHAN_"))
        )
          setAcquisition(value);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  const validated =
    status?.source_kind === "REAL_VERIFIED" &&
    (status.status === "VALIDATED_RESEARCH" ||
      status.status === "SHADOW_ELIGIBLE");
  const reasons = status?.sufficiency_reasons ?? ["No research dataset loaded"];
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">OFFLINE RESEARCH / 09</div>
          <h1>
            Statistical research<span className="heading-dot">.</span>
          </h1>
          <p>
            Evidence and model readiness from a separate offline dataset. No
            model is active in paper decisions.
          </p>
        </div>
      </div>
      <div className="paper-warning">
        DATA UNAVAILABLE / NO TRADE · NO VALIDATED MARKET ALPHA · NO LIVE ORDERS
        · PAPER POLICY UNCHANGED
      </div>
      <section className="panel">
        <div className="panel-head"><div><div className="eyebrow">VERIFIED LOCAL SOURCE</div><h2>Historical archive</h2></div></div>
        <p>{historical?.status === "AVAILABLE" && historical.source === "DHAN_HISTORICAL"
          ? `${historical.bars.length} genuine RELIANCE one-minute bars · SHA-256 ${historical.response_sha256} · exploratory only; candle availability and causal eligibility remain unverified.`
          : "No verified local historical archive is currently loaded."}</p>
      </section>
      <section className="panel">
        <div className="panel-head"><div><div className="eyebrow">LOCAL EXPLORATORY JOB</div><h2>Add &amp; Train</h2></div></div>
        <form onSubmit={addAndTrain}>
          <label htmlFor="training-ticker">Ticker in frozen 25-stock universe</label>{" "}
          <input id="training-ticker" value={ticker} onChange={(event) => setTicker(event.target.value)} placeholder="RELIANCE" required />{" "}
          <button type="submit" disabled={submitting || training?.state === "RUNNING" || training?.state === "QUEUED"}>Add &amp; Train</button>
        </form>
        <button type="button" disabled={submitting || training?.state === "RUNNING" || training?.state === "QUEUED"} onClick={() => void queueTraining("ALL_25")}>Fetch 25-stock October archive and replay frozen rule</button>
        <p role="status">{trainingError || (training ? `${training.state}${training.ticker ? ` · ${training.ticker}` : ""}${training.reason ? ` · ${training.reason}` : ""}` : "Training worker status unavailable")}</p>
        {training?.state === "RUNNING" && <p>Acquiring or verifying genuine Dhan responses and running the existing exploratory job. No automatic retries. Completion time is not yet known.</p>}
        {training?.sha256 && <p>Immutable artifact SHA-256: <code>{training.sha256}</code> · {training.research_eligibility}; no promotion.</p>}
        <p className="source-note">For other tickers, the backend requests only 2026-10-26 through exclusive 2026-10-31 one-minute intraday bars, retains raw responses and replays the frozen simulator rule. The RELIANCE source is reused. A new fitted cross-stock model is not claimed.</p>
      </section>
      {exploratory && historical?.status === "AVAILABLE" ? <ArchiveResearchPanels artifact={exploratory} /> : <>
      <section className="panel acquisition-panel">
        <div className="panel-head">
          <div>
            <div className="eyebrow">MARKET DATA / 00</div>
            <h2>Gateway research registration</h2>
          </div>
        </div>
        <div className="paper-foot">
          {acquisition?.status ?? "WAITING_FOR_REAL_SOURCE"} · Original files{" "}
          {acquisition?.source_files_available ?? 0} · Confirmed genuine{" "}
          {acquisition?.genuine_source_files_available ?? 0} · Source{" "}
          {acquisition?.source_verification_status ?? "UNVERIFIED"}
        </div>
        <div className="metric-grid research-metrics">
          <div className="metric">
            <div className="metric-label">SOURCE / ACCESS</div>
            <div className="metric-value">
              {acquisition?.access_status ?? "UNVERIFIED"}
            </div>
            <div className="metric-note">
              {acquisition?.sources_configured?.join(" · ") ??
                "No source status loaded"}
            </div>
          </div>
          <div className="metric">
            <div className="metric-label">HISTORICAL COVERAGE</div>
            <div className="metric-value">
              {acquisition?.instrument_count ?? 0} instruments
            </div>
            <div className="metric-note">
              {acquisition?.first_date ?? "N/A"} →{" "}
              {acquisition?.last_date ?? "N/A"} ·{" "}
              {acquisition?.independent_sessions ?? 0} sessions
            </div>
          </div>
          <div className="metric">
            <div className="metric-label">QUALITY</div>
            <div className="metric-value">
              {acquisition?.missing_bars ?? "UNKNOWN"} missing bars
            </div>
            <div className="metric-note">
              {acquisition?.quality_warning_count ?? "UNKNOWN"} warnings ·
              checksum{" "}
              {acquisition?.checksum_verified ? "verified" : "unverified"}
            </div>
          </div>
          <div className="metric">
            <div className="metric-label">DATA PARTITIONS</div>
            <div className="metric-value">
              {acquisition?.partition_count ?? 0}
            </div>
            <div className="metric-note">
              {acquisition
                ? (acquisition.partition_bytes / 1048576).toFixed(1)
                : "0.0"}{" "}
              MiB · {acquisition?.collection_mode ?? "DATA_ONLY_OFFLINE"}
            </div>
          </div>
        </div>
        <div className="paper-foot">
          Calendar {acquisition?.calendar_completeness ?? "UNVERIFIED"} ·
          Mapping {acquisition?.mapping_uncertainty ?? "UNVERIFIED"} ·
          Historical eligibility{" "}
          {acquisition?.historical_dataset_eligibility ?? "BLOCKED"} · Training{" "}
          {acquisition?.training_blocked === false ? "permitted" : "blocked"} ·
          Eligibility {acquisition?.research_eligibility ?? "NOT_TRAINED"} ·
          Blocked{" "}
          {acquisition?.blocked_model_families?.join(", ") ??
            "DEPTH, ORDER_FLOW, HAWKES, EXECUTION"}
          .{" "}
          {acquisition?.block_reasons?.join(" · ") ??
            "No dataset registered in this gateway research view"}
        </div>
      </section>
      {error && (
        <div className="panel">
          <div className="empty">
            No causally eligible Dhan research dataset is registered with the
            gateway. The verified local archive above remains exploratory.
          </div>
        </div>
      )}
      <div className="metric-grid research-metrics">
        <div className="metric">
          <div className="metric-label">DATASET</div>
          <div className="metric-value">
            {status ? (status.source_kind ?? "NONE") : "N/A"}
          </div>
          <div className="metric-note">
            {status?.feature_rows ?? 0} as-of rows · {status?.label_rows ?? 0}{" "}
            horizon labels
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">INDEPENDENT SESSIONS</div>
          <div className="metric-value">
            {status?.independent_sessions ?? "N/A"}
          </div>
          <div className="metric-note">Repeated replay is one session</div>
        </div>
        <div className="metric">
          <div className="metric-label">FORWARD COVERAGE</div>
          <div className="metric-value">
            {status ? `${(status.label_fraction * 100).toFixed(1)}%` : "N/A"}
          </div>
          <div className="metric-note">
            Observed first hit or complete horizon
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">MODEL STATUS</div>
          <div className="metric-value">{status?.status ?? "NOT_TRAINED"}</div>
          <div className="metric-note">
            {status?.model_version ?? "No candidate artifact"}
          </div>
        </div>
      </div>
      <div className="paper-grid">
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">SUFFICIENCY / 01</div>
              <h2>Why training is blocked</h2>
            </div>
          </div>
          <div className="paper-reliability">
            {reasons.length ? (
              reasons.map((reason) => (
                <span key={reason}>{reason.replaceAll("_", " ")}</span>
              ))
            ) : (
              <span>
                Eligibility gate passed; independent validation still required
              </span>
            )}
          </div>
          <div className="paper-foot">
            Quality pass fraction{" "}
            {status ? `${(status.quality_fraction * 100).toFixed(1)}%` : "N/A"}.
            Volume complete{" "}
            {status?.volume_complete_fraction != null
              ? `${(status.volume_complete_fraction * 100).toFixed(1)}%`
              : "N/A"}
            . Full horizon coverage{" "}
            {status?.horizon_coverage_fraction != null
              ? `${(status.horizon_coverage_fraction * 100).toFixed(1)}%`
              : "N/A"}
            . Dataset SHA-256 {status?.dataset_sha256?.slice(0, 16) ?? "N/A"}.
          </div>
        </section>
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">CANDIDATES / 02</div>
              <h2>Reference families</h2>
            </div>
          </div>
          <div className="paper-reliability">
            <span>Empirical conditional</span>
            <span>K nearest neighbour</span>
            <span>Kernel conditional</span>
            <span>Regularized regression</span>
            <span>Competing risks</span>
            <span>State space / OU</span>
          </div>
          <div className="paper-foot">
            Methods are implemented as research references. No market-trained or
            calibrated model artifact is available.
          </div>
        </section>
      </div>
      <div className="paper-grid">
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">VALIDATION / 03</div>
              <h2>Probability calibration</h2>
            </div>
          </div>
          <Curve
            points={validated ? status?.calibration : undefined}
            title="Calibration curve"
            keys={["observed", "predicted"]}
          />
        </section>
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">COMPETING RISKS / 04</div>
              <h2>Cumulative incidence</h2>
            </div>
          </div>
          <Curve
            points={validated ? status?.cumulative_incidence : undefined}
            title="Favorable and adverse cumulative incidence"
            keys={["favorable", "adverse"]}
          />
        </section>
      </div>
      <div className="paper-grid">
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">COST SCIENCE / 05</div>
              <h2>Net return versus costs</h2>
            </div>
          </div>
          <Curve
            points={validated ? status?.net_return_vs_costs : undefined}
            title="Net return under costs"
            keys={["observed", "predicted"]}
          />
          <div className="paper-foot">
            Provisional fees, spread, slippage, impact and fill uncertainty must
            all be represented before a decision.
          </div>
        </section>
        <section className="panel">
          <div className="panel-head">
            <div>
              <div className="eyebrow">EVIDENCE / 06</div>
              <h2>Baseline comparison</h2>
            </div>
          </div>
          <div className="paper-reliability">
            <span>
              Baseline:{" "}
              {validated ? (status?.baseline_comparison ?? "N/A") : "N/A"}
            </span>
            <span>
              NO TRADE rate:{" "}
              {validated && status?.no_trade_rate != null
                ? `${(status.no_trade_rate * 100).toFixed(1)}%`
                : "N/A"}
            </span>
            <span>
              Feature sensitivity:{" "}
              {validated ? (status?.feature_sensitivity ?? "N/A") : "N/A"}
            </span>
          </div>
          <div className="paper-foot">
            Charts appear only when independently validated offline artifacts
            supply real measurements.
          </div>
        </section>
      </div>
      </>}
    </>
  );
}
