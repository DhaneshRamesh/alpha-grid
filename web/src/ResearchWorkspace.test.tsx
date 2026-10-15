import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ResearchWorkspace from "./ResearchWorkspace";

afterEach(() => vi.unstubAllGlobals());

describe("offline research status", () => {
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
