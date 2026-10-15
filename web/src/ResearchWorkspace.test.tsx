import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ResearchWorkspace from "./ResearchWorkspace";

afterEach(() => vi.unstubAllGlobals());

describe("offline research status", () => {
  it("shows verified checksums and blocked models from read-only acquisition status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () =>
            url === "/api/acquisition"
              ? {
                  schema_version: "acquisition-status-v1",
                  sources_configured: ["NSE_LOCAL_IMPORT"],
                  access_status: "UNVERIFIED",
                  first_date: "2025-01-01",
                  last_date: "2025-02-01",
                  instrument_count: 5,
                  independent_sessions: 20,
                  missing_bars: 2,
                  quality_warning_count: 3,
                  partition_count: 12,
                  partition_bytes: 1048576,
                  checksum_verified: true,
                  collection_mode: "LOCAL_FILE",
                  research_eligibility: "NOT_TRAINED",
                  blocked_model_families: ["DEPTH", "HAWKES"],
                  block_reasons: ["historical_candles_do_not_supply_depth"],
                }
              : {
                  schema_version: "research-v1",
                  status: "NOT_TRAINED",
                  source_kind: null,
                  feature_rows: 0,
                  label_rows: 0,
                  independent_sessions: 0,
                  label_fraction: 0,
                  quality_fraction: 0,
                  sufficiency_reasons: [],
                },
        }),
      ),
    );
    render(<ResearchWorkspace />);
    expect(await screen.findByText("5 instruments")).toBeTruthy();
    expect(screen.getByText(/checksum verified/)).toBeTruthy();
    expect(screen.getByText(/Blocked DEPTH, HAWKES/)).toBeTruthy();
  });

  it("labels a synthetic one-session fixture as unvalidated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          schema_version: "research-v1",
          status: "NOT_TRAINED",
          source_kind: "SYNTHETIC",
          feature_rows: 240,
          label_rows: 1200,
          independent_sessions: 1,
          label_fraction: 0.4,
          quality_fraction: 1,
          dataset_sha256: "abcd",
          calibration: [{ x: 0.5, observed: 0.8, predicted: 0.9 }],
          sufficiency_reasons: [
            "source_is_not_verified_real_market_data",
            "insufficient_independent_sessions",
          ],
        }),
      }),
    );
    render(<ResearchWorkspace />);
    expect(await screen.findByText(/TEST FIXTURE \/ UNVALIDATED/)).toBeTruthy();
    expect(screen.getByText("insufficient independent sessions")).toBeTruthy();
    expect(screen.getAllByText(/N\/A/).length).toBeGreaterThan(1);
    expect(screen.queryByRole("img", { name: "Calibration curve" })).toBeNull();
  });
});
