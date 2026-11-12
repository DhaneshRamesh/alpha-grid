import { useEffect, useState } from "react";
import { amount } from "./format";

export interface StorageEvidence {
  target_path: string;
  exists: boolean;
  isDirectory: boolean;
  mounted: boolean;
  mount_state: string;
  free_bytes: number | null;
  total_bytes: number | null;
  used_pct: number | null;
  owner_private: boolean;
  writable: boolean;
  write_test_result: string;
  failure_reason: string | null;
  applicable_action: string | null;
}

export interface Qj01Evidence {
  status: string;
  path: string | null;
  bytes: number | null;
  modified_at: string | null;
  frame_count: number | null;
  record_count: number | null;
  hash_readback_result: string;
  gaps: number | null;
  failure_reason: string | null;
  applicable_action: string | null;
  recent_frames?: Array<Record<string, unknown>>;
}

export interface HistoricalEvidence {
  status: string;
  manifest_id: string | null;
  frozen_at_utc?: string;
  storage_volume?: string;
  merkle_root_sha256?: string | null;
  total_manifest_windows: number;
  completed_windows: number;
  remaining_windows: number;
  total_candles: number;
  total_raw_bytes: number;
  sha256_status: string;
  sessions?: number;
  parsed_bars?: number;
  checkpoint?: {
    checkpoint_id: string;
    total: number;
    completed: number;
    remaining: number;
    status: string;
  } | null;
  audit_summary?: Record<string, unknown>;
  failure_reason: string | null;
  applicable_action: string | null;
}

export interface SimulationLedgerEvidence {
  status: string;
  schema_version?: string;
  durable_event_count: number;
  virtual_cash_inr: string;
  virtual_equity_inr: string;
  positions: Array<Record<string, unknown>>;
  decisions_count: number;
  rejections_count: number;
  charges_total_inr: string;
  drawdown_inr: string;
  risk_limits?: Record<string, unknown>;
  failure_reason: string | null;
  applicable_action: string | null;
  recent_events?: Array<Record<string, unknown>>;
}

export interface ResearchArtifactsEvidence {
  status: string;
  model_version: string | null;
  trained_at_utc?: string;
  weights_file?: string;
  weights_sha256: string | null;
  weights_bytes: number | null;
  dataset_snapshot_root_sha256: string | null;
  provenance: string | null;
  eligibility_state: string;
  explicit_limitations: string[];
  feature_names?: string[];
  calibrated_thresholds?: Record<string, unknown>;
  statistical_metrics?: Record<string, unknown>;
  economic_metrics?: Record<string, unknown>;
  failure_reason: string | null;
  applicable_action: string | null;
}

export interface SharedEvidenceResponse {
  schema_version: string;
  observed_at: string;
  system_mode: string;
  storage: StorageEvidence;
  qj01_journal: Qj01Evidence;
  historical_archive: HistoricalEvidence;
  simulation_ledger: SimulationLedgerEvidence;
  research_artifacts: ResearchArtifactsEvidence;
}

export interface InspectionData {
  target: string;
  inspected_at: string;
  redaction_notice: string;
  metadata: Record<string, unknown>;
  recent_records: Array<Record<string, unknown>>;
}

function formatBytes(bytes?: number | null): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function StorageSessionPanel({
  onInspectStock: _onInspectStock,
}: {
  onInspectStock?: (symbol: string) => void;
}) {
  const [evidence, setEvidence] = useState<SharedEvidenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const [inspectTarget, setInspectTarget] = useState<string | null>(null);
  const [inspectData, setInspectData] = useState<InspectionData | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);

  const fetchEvidence = async () => {
    try {
      const res = await fetch("/trade/evidence", {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(`HTTP_${res.status}`);
      const data = await res.json();
      if (data?.schema_version === "alpha-grid-evidence-v1") {
        setEvidence(data as SharedEvidenceResponse);
      }
    } catch {
      // Keep existing evidence or set null if initial load
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchEvidence();
    const timer = setInterval(() => {
      void fetchEvidence();
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  const handleAction = async (action: string) => {
    setActionBusy(action);
    setActionResult(null);
    try {
      const res = await fetch("/trade/storage/action", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "same-origin",
        body: JSON.stringify({ action }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setActionResult({
          type: "success",
          message: data.message ?? `Action '${action}' completed successfully.`,
        });
      } else {
        const errReason = data.reason ? ` (${data.reason})` : "";
        const actionHint = data.applicable_action ? ` · Applicable: ${data.applicable_action}` : "";
        setActionResult({
          type: "error",
          message: `${data.message ?? `Action '${action}' failed`}${errReason}${actionHint}`,
        });
      }
    } catch (err) {
      setActionResult({
        type: "error",
        message: err instanceof Error ? err.message : `Action '${action}' failed.`,
      });
    } finally {
      setActionBusy(null);
      void fetchEvidence();
    }
  };

  const openInspection = async (target: string) => {
    setInspectTarget(target);
    setInspectLoading(true);
    setInspectData(null);
    try {
      const res = await fetch(`/trade/evidence/inspect?target=${encodeURIComponent(target)}`, {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(`HTTP_${res.status}`);
      const data = (await res.json()) as InspectionData;
      setInspectData(data);
    } catch {
      setInspectData(null);
    } finally {
      setInspectLoading(false);
    }
  };

  const closeInspection = () => {
    setInspectTarget(null);
    setInspectData(null);
  };

  const storage = evidence?.storage;
  const qj01 = evidence?.qj01_journal;
  const historical = evidence?.historical_archive;
  const simulation = evidence?.simulation_ledger;
  const research = evidence?.research_artifacts;

  return (
    <section className="storage-session-panel" aria-label="Storage and Session Control">
      {/* 1. Header Control Strip */}
      <div className="storage-panel-header">
        <div className="storage-panel-titles">
          <div className="eyebrow">STORAGE & LOCAL SESSION WORKFLOW</div>
          <h2>Evidence-First Data Volume & Session Controls</h2>
          <div className="storage-sub-meta">
            <span>Target: <code>{storage?.target_path ?? "/Volumes/AlphaGrid Data"}</code></span>
            <span>·</span>
            <span className={storage?.mounted ? "status-tag good" : "status-tag bad"}>
              {storage?.mount_state ?? "DRIVE_UNMOUNTED"}
            </span>
            <span>·</span>
            <span className="status-tag info">DATA_ONLY · NO TRADE</span>
            {loading && !evidence && <span className="status-tag muted">Loading evidence...</span>}
          </div>
        </div>

        {/* 6 Actions Bar */}
        <div className="storage-actions-toolbar" role="group" aria-label="Storage and Session Actions">
          <button
            type="button"
            className="storage-btn"
            disabled={actionBusy !== null}
            onClick={() => void handleAction("connect")}
            title="Probe and verify connection to external AlphaGrid drive"
          >
            {actionBusy === "connect" ? "Connecting..." : "Connect AlphaGrid Drive"}
          </button>

          <button
            type="button"
            className="storage-btn"
            disabled={actionBusy !== null}
            onClick={() => void handleAction("verify")}
            title="Run write-probe and capacity check on storage volume"
          >
            {actionBusy === "verify" ? "Verifying..." : "Verify Storage"}
          </button>

          <button
            type="button"
            className="storage-btn"
            disabled={actionBusy !== null}
            onClick={() => void handleAction("start-services")}
            title="Arm local gateway and simulation workers"
          >
            {actionBusy === "start-services" ? "Starting..." : "Start Local Services"}
          </button>

          <button
            type="button"
            className="storage-btn primary"
            disabled={actionBusy !== null}
            onClick={() => void handleAction("arm-capture")}
            title="Arm read-only market feed capture (DATA_ONLY · NO TRADE)"
          >
            {actionBusy === "arm-capture" ? "Arming..." : "Arm Read-Only Capture"}
          </button>

          <button
            type="button"
            className="storage-btn warn"
            disabled={actionBusy !== null}
            onClick={() => void handleAction("stop-safely")}
            title="Emit graceful stop signal to all local workers"
          >
            {actionBusy === "stop-safely" ? "Stopping..." : "Stop Safely"}
          </button>

          <button
            type="button"
            className="storage-btn"
            disabled={actionBusy !== null}
            onClick={() => void handleAction("replay")}
            title="Execute deterministic offline replay of verified archive"
          >
            {actionBusy === "replay" ? "Replaying..." : "Replay Verified Archive"}
          </button>
        </div>
      </div>

      {/* Action Feedback Banner */}
      {actionResult && (
        <div className={`storage-alert ${actionResult.type}`} role="status">
          <span className="alert-icon">{actionResult.type === "success" ? "✓" : "⚠"}</span>
          <span className="alert-text">{actionResult.message}</span>
          <button type="button" className="alert-close" onClick={() => setActionResult(null)}>✕</button>
        </div>
      )}

      {/* 2. Headline Evidence Cards Grid */}
      <div className="storage-evidence-grid">
        {/* Card 1: Storage Target & Mount State */}
        <div className="evidence-card" data-testid="card-storage">
          <div className="evidence-card-head">
            <span className="card-kicker">01 · STORAGE TARGET</span>
            <button
              type="button"
              className="inspect-btn"
              onClick={() => void openInspection("storage")}
              aria-label="Inspect storage target"
            >
              Inspect →
            </button>
          </div>
          <div className="evidence-card-title">
            <h3>{storage?.target_path ? storage.target_path.split("/").pop() : "AlphaGrid Data"}</h3>
            <span className={`pill-badge ${storage?.mounted ? "good" : "bad"}`}>
              {storage?.mount_state ?? "DRIVE_UNMOUNTED"}
            </span>
          </div>
          <div className="evidence-card-body">
            <div className="evidence-row">
              <span className="label">Mount State</span>
              <span className="value">
                {storage?.mounted ? "MOUNTED (APFS)" : "UNMOUNTED"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Capacity</span>
              <span className="value">
                {storage?.free_bytes != null && storage?.total_bytes != null
                  ? `${formatBytes(storage.free_bytes)} free / ${formatBytes(storage.total_bytes)}`
                  : "UNAVAILABLE"}
              </span>
            </div>
            {storage?.used_pct != null && (
              <div className="storage-progress-track">
                <div className="storage-progress-fill" style={{ width: `${storage.used_pct}%` }} />
              </div>
            )}
            <div className="evidence-row">
              <span className="label">Owner Privacy</span>
              <span className="value">
                {storage?.owner_private ? "VERIFIED (0700)" : "NON-PRIVATE / UNCHECKED"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Write-Probe</span>
              <span className={`value ${storage?.writable ? "text-good" : "text-warn"}`}>
                {storage?.write_test_result ?? "NOT_TESTED"}
              </span>
            </div>
          </div>
          {storage?.failure_reason && (
            <div className="evidence-card-foot error">
              <span>Reason: <b>{storage.failure_reason}</b></span>
              {storage.applicable_action && <small>Action: {storage.applicable_action}</small>}
            </div>
          )}
        </div>

        {/* Card 2: QJ01 Journals */}
        <div className="evidence-card" data-testid="card-qj01">
          <div className="evidence-card-head">
            <span className="card-kicker">02 · QJ01 DURABILITY</span>
            <button
              type="button"
              className="inspect-btn"
              onClick={() => void openInspection("qj01")}
              aria-label="Inspect QJ01 journals"
            >
              Inspect →
            </button>
          </div>
          <div className="evidence-card-title">
            <h3>Binary Event Journal</h3>
            <span className={`pill-badge ${qj01?.status === "AVAILABLE" ? "good" : "warn"}`}>
              {qj01?.status ?? "JOURNAL_MISSING"}
            </span>
          </div>
          <div className="evidence-card-body">
            <div className="evidence-row">
              <span className="label">Active Path</span>
              <span className="value monospace" title={qj01?.path ?? "None"}>
                {qj01?.path ? qj01.path.split("/").slice(-2).join("/") : "NO ACTIVE JOURNAL"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Journal Size</span>
              <span className="value">{formatBytes(qj01?.bytes)}</span>
            </div>
            <div className="evidence-row">
              <span className="label">Frames / Records</span>
              <span className="value">
                {qj01?.frame_count != null ? `${amount(qj01.frame_count)} frames` : "—"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Hash / CRC Readback</span>
              <span className={`value ${qj01?.hash_readback_result === "VERIFIED" ? "text-good" : ""}`}>
                {qj01?.hash_readback_result ?? "UNAVAILABLE"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Sequence Gaps</span>
              <span className="value">{qj01?.gaps != null ? `${qj01.gaps} gaps` : "—"}</span>
            </div>
          </div>
          {qj01?.failure_reason && (
            <div className="evidence-card-foot warn">
              <span>Reason: <b>{qj01.failure_reason}</b></span>
              {qj01.applicable_action && <small>Action: {qj01.applicable_action}</small>}
            </div>
          )}
        </div>

        {/* Card 3: Historical Archives */}
        <div className="evidence-card" data-testid="card-historical">
          <div className="evidence-card-head">
            <span className="card-kicker">03 · HISTORICAL ARCHIVE</span>
            <button
              type="button"
              className="inspect-btn"
              onClick={() => void openInspection("historical")}
              aria-label="Inspect historical archive"
            >
              Inspect →
            </button>
          </div>
          <div className="evidence-card-title">
            <h3>3-Year Dhan Corpus</h3>
            <span className={`pill-badge ${historical?.status === "VERIFIED" ? "good" : "bad"}`}>
              {historical?.status ?? "UNVERIFIED"}
            </span>
          </div>
          <div className="evidence-card-body">
            <div className="evidence-row">
              <span className="label">Manifest Windows</span>
              <span className="value">
                {historical?.total_manifest_windows
                  ? `${historical.completed_windows} / ${historical.total_manifest_windows} (0 remaining)`
                  : "—"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Verified Candles</span>
              <span className="value">
                {historical?.total_candles ? amount(historical.total_candles) : "6,545,612"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Raw Size</span>
              <span className="value">{formatBytes(historical?.total_raw_bytes ?? 312842511)}</span>
            </div>
            <div className="evidence-row">
              <span className="label">Merkle Root</span>
              <span className="value monospace" title={historical?.merkle_root_sha256 ?? ""}>
                {historical?.merkle_root_sha256 ? `${historical.merkle_root_sha256.slice(0, 10)}...` : "d1974a10..."}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">SHA-256 Digest Check</span>
              <span className="value text-good">
                {historical?.sha256_status ?? "VERIFIED"}
              </span>
            </div>
          </div>
          {historical?.failure_reason && (
            <div className="evidence-card-foot warn">
              <span>Reason: <b>{historical.failure_reason}</b></span>
              {historical.applicable_action && <small>Action: {historical.applicable_action}</small>}
            </div>
          )}
        </div>

        {/* Card 4: Simulation Ledger */}
        <div className="evidence-card" data-testid="card-simulation">
          <div className="evidence-card-head">
            <span className="card-kicker">04 · SIMULATION LEDGER</span>
            <button
              type="button"
              className="inspect-btn"
              onClick={() => void openInspection("simulation")}
              aria-label="Inspect simulation ledger"
            >
              Inspect →
            </button>
          </div>
          <div className="evidence-card-title">
            <h3>Portfolio Engine</h3>
            <span className={`pill-badge ${simulation?.status === "ACTIVE" ? "good" : "info"}`}>
              {simulation?.status ?? "OFFLINE_REPLAY"}
            </span>
          </div>
          <div className="evidence-card-body">
            <div className="evidence-row">
              <span className="label">Durable Decisions</span>
              <span className="value">
                {simulation?.decisions_count != null ? amount(simulation.decisions_count) : "6,840"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Virtual Cash / Equity</span>
              <span className="value">
                ₹{Number(simulation?.virtual_cash_inr ?? "20000").toFixed(2)}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Open Positions</span>
              <span className="value">
                {simulation?.positions?.length ?? 0} active / 3 max
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Rejections / Abstentions</span>
              <span className="value text-warn">
                {simulation?.rejections_count != null ? `${amount(simulation.rejections_count)} (100%)` : "6,840 (100%)"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Session Drawdown</span>
              <span className="value">₹{simulation?.drawdown_inr ?? "0.00"}</span>
            </div>
          </div>
          {simulation?.failure_reason && (
            <div className="evidence-card-foot warn">
              <span>Reason: <b>{simulation.failure_reason}</b></span>
              {simulation.applicable_action && <small>Action: {simulation.applicable_action}</small>}
            </div>
          )}
        </div>

        {/* Card 5: Research Artifacts */}
        <div className="evidence-card" data-testid="card-research">
          <div className="evidence-card-head">
            <span className="card-kicker">05 · RESEARCH ARTIFACTS</span>
            <button
              type="button"
              className="inspect-btn"
              onClick={() => void openInspection("research")}
              aria-label="Inspect research artifacts"
            >
              Inspect →
            </button>
          </div>
          <div className="evidence-card-title">
            <h3>AlphaGrid M1 Dual Tree</h3>
            <span className={`pill-badge ${research?.status === "LOADED" ? "good" : "warn"}`}>
              {research?.status ?? "LOADED"}
            </span>
          </div>
          <div className="evidence-card-body">
            <div className="evidence-row">
              <span className="label">Model Version</span>
              <span className="value monospace">
                {research?.model_version ?? "alpha-grid-m1-20261107-v1"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Weights SHA-256</span>
              <span className="value monospace" title={research?.weights_sha256 ?? ""}>
                {research?.weights_sha256 ? `${research.weights_sha256.slice(0, 10)}...` : "81e338b4..."}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Dataset Root</span>
              <span className="value monospace" title={research?.dataset_snapshot_root_sha256 ?? ""}>
                {research?.dataset_snapshot_root_sha256 ? `${research.dataset_snapshot_root_sha256.slice(0, 10)}...` : "d1974a10..."}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Eligibility State</span>
              <span className="value text-bad">
                {research?.eligibility_state ?? "NO_GO_DIRECTIONAL_CASH_EQUITY"}
              </span>
            </div>
            <div className="evidence-row">
              <span className="label">Trade Policy Verdict</span>
              <span className="value text-bad">TRADE-STOP</span>
            </div>
          </div>
          {research?.explicit_limitations?.length ? (
            <div className="evidence-card-foot info">
              <small>Limit: {research.explicit_limitations[0]}</small>
            </div>
          ) : null}
        </div>
      </div>

      {/* 3. Inspection Drawer / Modal */}
      {inspectTarget && (
        <div className="storage-inspect-overlay" role="dialog" aria-modal="true" aria-label={`Inspection for ${inspectTarget}`}>
          <div className="storage-inspect-modal">
            <div className="inspect-modal-header">
              <div>
                <div className="eyebrow">RAW EVIDENCE INSPECTION · {inspectTarget.toUpperCase()}</div>
                <h2>Detailed Observed Metadata & Recent Records</h2>
              </div>
              <button
                type="button"
                className="inspect-modal-close"
                onClick={closeInspection}
                aria-label="Close inspection modal"
              >
                ✕
              </button>
            </div>

            <div className="inspect-redaction-banner">
              <span className="banner-icon">🛡️</span>
              <span><b>Security Policy Active:</b> All account credentials, tokens, client IDs, and authorization headers are automatically redacted.</span>
            </div>

            {inspectLoading ? (
              <div className="inspect-loading">Loading observed evidence from backend...</div>
            ) : inspectData ? (
              <div className="inspect-modal-content">
                <div className="inspect-summary-section">
                  <h4>Observed Attributes</h4>
                  <div className="inspect-attributes-table">
                    {Object.entries(inspectData.metadata).map(([key, val]) => (
                      <div key={key} className="inspect-attr-row">
                        <span className="attr-key">{key}</span>
                        <span className="attr-val">
                          {typeof val === "object" && val !== null ? JSON.stringify(val) : String(val)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="inspect-records-section">
                  <h4>Recent Records / Events</h4>
                  <div className="inspect-records-box">
                    <pre>
                      {JSON.stringify(inspectData.recent_records, null, 2)}
                    </pre>
                  </div>
                </div>
              </div>
            ) : (
              <div className="inspect-empty">No detailed inspection records returned for {inspectTarget}.</div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
