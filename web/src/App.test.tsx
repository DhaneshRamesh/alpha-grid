import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";

vi.mock("./stream", () => ({
  useEngineStream: () => ({
    snapshot: null,
    connection: "DISCONNECTED",
    messageLagMs: null,
    reconnects: 0,
  }),
}));

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => ({
      ok: url !== "/trade/account",
      json: async () =>
        url === "/trade/status"
          ? {
              execution: "UNAVAILABLE",
              account: "NOT_CONNECTED",
              marketFeed: "NOT_CONNECTED",
              research: "NOT_CONNECTED",
              reason: "Live services are unavailable",
            }
          : options?.method === "PUT"
            ? { settings: JSON.parse(options.body as string) }
            : { settings: null },
    })),
  );
});

describe("trading workspace", () => {
  it("starts with unavailable Dhan data and no fictional candidate", async () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "Trade desk." })).toBeTruthy();
    expect(screen.getAllByText(/DATA UNAVAILABLE/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/BUY 8 SHARES/)).toBeNull();
    expect(screen.queryByText(/sample price/)).toBeNull();
    await waitFor(() =>
      expect(screen.getByText(/Live services are unavailable/)).toBeTruthy(),
    );
    expect(
      vi.mocked(fetch).mock.calls.some(([url]) => url === "/trade/account"),
    ).toBe(true);
  });

  it("keeps the separate local risk-settings tab", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Trading settings/ }));
    expect(
      screen.getByRole("heading", { name: "Trading settings." }),
    ).toBeTruthy();
    for (const [label, value] of [
      ["Maximum per trade", "5000"],
      ["Maximum daily loss", "1000"],
      ["Stop-loss", "2"],
      ["Profit target", "4"],
    ]) {
      fireEvent.change(
        screen.getByLabelText(label, { exact: false, selector: "input" }),
        { target: { value } },
      );
    }
    fireEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(screen.getByText(/Limits saved/)).toBeTruthy());
    const saved = vi
      .mocked(fetch)
      .mock.calls.find(
        ([url, options]) =>
          url === "/trade/settings" && options?.method === "PUT",
      );
    expect(saved).toBeTruthy();
    expect(JSON.parse(saved![1]!.body as string)).toMatchObject({
      maxTrade: { unit: "INR", value: "5000" },
      maxDailyLoss: { unit: "INR", value: "1000" },
      stopLoss: { unit: "PERCENT", value: "2" },
      profitTarget: { unit: "PERCENT", value: "4" },
    });
  });

  it("shows blocked online data without a connected Rust feed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: url !== "/trade/account",
        json: async () =>
          url === "/trade/status"
            ? {
                execution: "UNAVAILABLE",
                account: "READ_ONLY",
                marketFeed: "ONLINE_REQUESTED",
                research: "NOT_CONNECTED",
                reason: "Feed preflight blocked",
              }
            : { settings: null },
      })),
    );
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/MARKET DATA.*BLOCKED/)).toBeTruthy(),
    );
    expect(screen.getAllByText(/NO TRADE/).length).toBeGreaterThan(0);
  });

  it("labels verified offline account cache and independent failures accurately", async () => {
    const recent = new Date().toISOString();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () =>
          url === "/trade/status"
            ? {
                execution: "UNAVAILABLE",
                account: "READ_ONLY",
                marketFeed: "CACHE_ONLY",
                research: "NOT_CONNECTED",
                reason: "Offline",
              }
            : url === "/trade/account"
              ? {
                  status: "CACHE_ONLY",
                  panels: {
                    fundlimit: {
                      endpoint: "fundlimit",
                      status: "AVAILABLE",
                      delivery: "CACHE_ONLY",
                      retrieved_at: recent,
                      last_http_status: 200,
                      data: { available_balance_inr: "123.45" },
                    },
                    holdings: {
                      endpoint: "holdings",
                      status: "DATA_UNAVAILABLE",
                      last_http_status: 500,
                    },
                    positions: {
                      endpoint: "positions",
                      status: "DATA_UNAVAILABLE",
                      reason: "CACHE_UNVERIFIABLE",
                    },
                  },
                }
              : { settings: null },
      })),
    );
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByText(/DHAN ACCOUNT: VERIFIED LOCAL CACHE/),
      ).toBeTruthy(),
    );
    expect(screen.getByText("DESK")).toBeTruthy();
    expect(screen.getByText(/MARKET DATA.*CACHE_ONLY/)).toBeTruthy();
    expect(
      screen.getByText(
        /funds CACHE_ONLY; holdings DATA UNAVAILABLE \(last HTTP 500\); positions UNVERIFIABLE/,
      ),
    ).toBeTruthy();
  });
});
