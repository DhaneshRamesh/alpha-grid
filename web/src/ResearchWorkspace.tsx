import { useEffect, useState } from "react";

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
  sources_configured: string[];
  access_status: string;
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
  blocked_model_families: string[];
  block_reasons: string[];
};

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

export default function ResearchWorkspace() {
  const [status, setStatus] = useState<ResearchStatus | null>(null);
  const [acquisition, setAcquisition] = useState<AcquisitionStatus | null>(
    null,
  );
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/research", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Research status ${response.status}`);
        return response.json() as Promise<ResearchStatus>;
      })
      .then(setStatus)
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
        if (value.schema_version === "acquisition-status-v1")
          setAcquisition(value);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  const fixture = status?.source_kind === "SYNTHETIC";
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
        {fixture ? "TEST FIXTURE / UNVALIDATED" : "NO VALIDATED MARKET ALPHA"} ·
        NO LIVE ORDERS · PAPER POLICY UNCHANGED
      </div>
      <section className="panel acquisition-panel">
        <div className="panel-head">
          <div>
            <div className="eyebrow">MARKET DATA / 00</div>
            <h2>Acquisition and quality</h2>
          </div>
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
          Eligibility {acquisition?.research_eligibility ?? "NOT_TRAINED"} ·
          Blocked{" "}
          {acquisition?.blocked_model_families?.join(", ") ??
            "DEPTH, ORDER_FLOW, HAWKES, EXECUTION"}
          .{" "}
          {acquisition?.block_reasons?.join(" · ") ??
            "No verified real market dataset"}
        </div>
      </section>
      {error && (
        <div className="panel">
          <div className="empty">
            Research status unavailable. The market and paper engines are
            unaffected.
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
    </>
  );
}
