import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import StorageSessionPanel, { type SharedEvidenceResponse, type InspectionData } from "./StorageSessionPanel";

describe("StorageSessionPanel", () => {
  const mockEvidence: SharedEvidenceResponse = {
    schema_version: "alpha-grid-evidence-v1",
    observed_at: "2026-11-09T01:50:00Z",
    system_mode: "DATA_ONLY / SIMULATION_ONLY / NO TRADE",
    storage: {
      target_path: "/Volumes/AlphaGrid Data",
      exists: true,
      isDirectory: true,
      mounted: true,
      mount_state: "MOUNTED",
      free_bytes: 20_000_000_000,
      total_bytes: 250_000_000_000,
      used_pct: 92.0,
      owner_private: true,
      writable: true,
      write_test_result: "PASSED",
      failure_reason: null,
      applicable_action: "Verify Storage",
    },
    qj01_journal: {
      status: "AVAILABLE",
      path: "/Volumes/AlphaGrid Data/Live/offline-dev/qj01/raw.qj",
      bytes: 1048576,
      modified_at: "2026-11-09T01:45:00Z",
      frame_count: 8192,
      record_count: 8192,
      hash_readback_result: "VERIFIED",
      gaps: 0,
      failure_reason: null,
      applicable_action: "Arm Read-Only Capture",
      recent_frames: [{ index: 1, magic: "QJ01", size_bytes: 1048576 }],
    },
    historical_archive: {
      status: "VERIFIED",
      manifest_id: "alpha-grid-snapshot-2026-11-07-v1",
      total_manifest_windows: 325,
      completed_windows: 325,
      remaining_windows: 0,
      total_candles: 6545612,
      total_raw_bytes: 312842511,
      merkle_root_sha256: "d1974a10450b85555fc83ce33d8b5191eddfd011a77ffbff78d82a3d7e7badec",
      sha256_status: "VERIFIED",
      checkpoint: {
        checkpoint_id: "3yr-full-acquisition",
        total: 325,
        completed: 325,
        remaining: 0,
        status: "COMPLETE",
      },
      failure_reason: null,
      applicable_action: "Replay Verified Archive",
    },
    simulation_ledger: {
      status: "OFFLINE_REPLAY",
      durable_event_count: 6840,
      virtual_cash_inr: "20000.00",
      virtual_equity_inr: "20000.00",
      positions: [],
      decisions_count: 6840,
      rejections_count: 6840,
      charges_total_inr: "0.00",
      drawdown_inr: "0.00",
      failure_reason: null,
      applicable_action: "Replay Verified Archive",
      recent_events: [{ symbol: "RELIANCE", action: "NO_TRADE", reason: "MODEL_STAGE3_REJECTED" }],
    },
    research_artifacts: {
      status: "LOADED",
      model_version: "alpha-grid-m1-20261107-v1",
      weights_sha256: "81e338b41b674c185b4a9e1d963eda0933020b35c82b3b0ffcf2ad06764fcc7d",
      weights_bytes: 213382,
      dataset_snapshot_root_sha256: "d1974a10450b85555fc83ce33d8b5191eddfd011a77ffbff78d82a3d7e7badec",
      provenance: "Dual Gradient Boosted trees (LightGBM-compatible) trained on chronological Train split",
      eligibility_state: "NO_GO_DIRECTIONAL_CASH_EQUITY",
      explicit_limitations: ["Gross forward returns fail to survive statutory charges and spread hurdle (15.5 bps floor)"],
      failure_reason: null,
      applicable_action: "Replay Verified Archive",
    },
  };

  const mockInspection: InspectionData = {
    target: "storage",
    inspected_at: "2026-11-09T01:50:00Z",
    redaction_notice: "Credentials, access tokens, and private account identifiers have been automatically redacted.",
    metadata: {
      target_path: "/Volumes/AlphaGrid Data",
      mount_state: "MOUNTED",
      write_test_result: "PASSED",
      token: "[REDACTED]",
    },
    recent_records: [
      { key: "Target Path", value: "/Volumes/AlphaGrid Data" },
      { key: "Mount State", value: "MOUNTED" },
    ],
  };

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options?: RequestInit) => {
        if (url === "/trade/evidence") {
          return {
            ok: true,
            status: 200,
            json: async () => mockEvidence,
          };
        }
        if (url.startsWith("/trade/evidence/inspect")) {
          return {
            ok: true,
            status: 200,
            json: async () => mockInspection,
          };
        }
        if (url === "/trade/storage/action" && options?.method === "POST") {
          const body = JSON.parse(options.body as string);
          return {
            ok: true,
            status: 200,
            json: async () => ({
              success: true,
              action: body.action,
              message: `Action '${body.action}' completed successfully.`,
            }),
          };
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({ error: "Not found" }),
        };
      })
    );
  });

  it("renders Storage & Session workflow panel with target metadata and status", async () => {
    render(<StorageSessionPanel />);

    expect(screen.getByText("STORAGE & LOCAL SESSION WORKFLOW")).toBeTruthy();
    expect(screen.getByText("Evidence-First Data Volume & Session Controls")).toBeTruthy();
    expect(screen.getByText("DATA_ONLY · NO TRADE")).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("/Volumes/AlphaGrid Data")).toBeTruthy();
    });
  });

  it("renders all 6 required control action buttons", () => {
    render(<StorageSessionPanel />);

    expect(screen.getByRole("button", { name: "Connect AlphaGrid Drive" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Verify Storage" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start Local Services" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Arm Read-Only Capture" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stop Safely" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Replay Verified Archive" })).toBeTruthy();
  });

  it("renders all 5 headline evidence cards with observed backend metrics", async () => {
    render(<StorageSessionPanel />);

    await waitFor(() => {
      // 1. Storage
      expect(screen.getByText("01 · STORAGE TARGET")).toBeTruthy();
      expect(screen.getByText("MOUNTED (APFS)")).toBeTruthy();

      // 2. QJ01
      expect(screen.getByText("02 · QJ01 DURABILITY")).toBeTruthy();
      expect(screen.getByText("Binary Event Journal")).toBeTruthy();

      // 3. Historical
      expect(screen.getByText("03 · HISTORICAL ARCHIVE")).toBeTruthy();
      expect(screen.getByText("325 / 325 (0 remaining)")).toBeTruthy();
      expect(screen.getByText(/45,612/)).toBeTruthy();

      // 4. Simulation
      expect(screen.getByText("04 · SIMULATION LEDGER")).toBeTruthy();
      expect(screen.getByText("Portfolio Engine")).toBeTruthy();

      // 5. Research
      expect(screen.getByText("05 · RESEARCH ARTIFACTS")).toBeTruthy();
      expect(screen.getByText("alpha-grid-m1-20261107-v1")).toBeTruthy();
      expect(screen.getByText("TRADE-STOP")).toBeTruthy();
    });
  });

  it("displays explicit reasons and applicable actions when drive is unmounted", async () => {
    const unmountedEvidence: SharedEvidenceResponse = {
      ...mockEvidence,
      storage: {
        ...mockEvidence.storage,
        mounted: false,
        mount_state: "DRIVE_UNMOUNTED",
        free_bytes: null,
        total_bytes: null,
        writable: false,
        write_test_result: "NOT_TESTED",
        failure_reason: "DRIVE_UNMOUNTED",
        applicable_action: "Connect AlphaGrid Drive",
      },
      qj01_journal: {
        ...mockEvidence.qj01_journal,
        status: "UNAVAILABLE",
        failure_reason: "DRIVE_UNMOUNTED",
        applicable_action: "Connect AlphaGrid Drive",
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string) => ({
        ok: true,
        status: 200,
        json: async () => unmountedEvidence,
      }))
    );

    render(<StorageSessionPanel />);

    await waitFor(() => {
      expect(screen.getAllByText("DRIVE_UNMOUNTED").length).toBeGreaterThan(0);
      expect(screen.getAllByText(/Connect AlphaGrid Drive/).length).toBeGreaterThan(0);
    });
  });

  it("handles storage actions and displays feedback banner", async () => {
    render(<StorageSessionPanel />);

    const verifyBtn = screen.getByRole("button", { name: "Verify Storage" });
    fireEvent.click(verifyBtn);

    await waitFor(() => {
      expect(screen.getByText("Action 'verify' completed successfully.")).toBeTruthy();
    });
  });

  it("opens inspection drawer with redacted attributes and recent records on Inspect click", async () => {
    render(<StorageSessionPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText("Inspect storage target")).toBeTruthy();
    });

    const inspectBtn = screen.getByLabelText("Inspect storage target");
    fireEvent.click(inspectBtn);

    await waitFor(() => {
      expect(screen.getByText("RAW EVIDENCE INSPECTION · STORAGE")).toBeTruthy();
      expect(screen.getByText(/All account credentials, tokens, client IDs, and authorization headers are automatically redacted/)).toBeTruthy();
      expect(screen.getByText("[REDACTED]")).toBeTruthy();
    });

    // Close modal
    const closeBtn = screen.getByLabelText("Close inspection modal");
    fireEvent.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByText("RAW EVIDENCE INSPECTION · STORAGE")).toBeNull();
    });
  });
});
