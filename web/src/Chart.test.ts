import { describe, expect, it } from "vitest";
import { TickMarkType, type UTCTimestamp } from "lightweight-charts";
import { formatIstChartTick } from "./Chart";

describe("historical chart time axis", () => {
  it("labels actual UTC instants in IST without changing source timestamps", () => {
    const noon = Date.parse("2026-11-04T06:30:00Z") / 1000 as UTCTimestamp;
    const close = Date.parse("2026-11-04T09:41:00Z") / 1000 as UTCTimestamp;
    expect(formatIstChartTick(noon, TickMarkType.Time)).toBe("12:00");
    expect(formatIstChartTick(close, TickMarkType.Time)).toBe("15:11");
    expect(formatIstChartTick(noon, TickMarkType.DayOfMonth)).toContain("Nov");
  });
});
