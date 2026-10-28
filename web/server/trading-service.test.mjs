import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import {
  createTradingService,
  readSettings,
  saveSettings,
  validateSettings,
} from "./trading-service.mjs";

const values = {
  maxTrade: { unit: "PERCENT", value: "10.5" },
  maxDailyLoss: { unit: "INR", value: "1500" },
  stopLoss: { unit: "PERCENT", value: "2.25" },
  profitTarget: { unit: "INR_PER_SHARE", value: "12.50" },
};

test("requires every positive limit and rejects percentages above 100", () => {
  assert.deepEqual(validateSettings(values), values);
  assert.throws(() =>
    validateSettings({ ...values, maxTrade: { unit: "INR", value: "0" } }),
  );
  assert.throws(() =>
    validateSettings({
      ...values,
      stopLoss: { unit: "PERCENT", value: "100.01" },
    }),
  );
  assert.throws(() =>
    validateSettings({
      ...values,
      profitTarget: { unit: "INR_PER_SHARE", value: "12.345" },
    }),
  );
  assert.throws(() => validateSettings({ ...values, maxDailyLoss: null }));
});

test("saves exact entered values and rejects invalid updates without replacing them", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-settings-"));
  const path = join(directory, "settings.json");
  const server = createTradingService(path);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  after(async () => {
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  });
  const url = `http://127.0.0.1:${server.address().port}`;
  const status = await (await fetch(`${url}/trade/status`)).json();
  assert.equal(status.execution, "UNAVAILABLE");
  assert.equal(
    (await fetch(`${url}/trade/order`, { method: "POST" })).status,
    404,
  );
  assert.equal((await fetch(`${url}/trade/account`)).status, 503);
  assert.equal(
    (await (await fetch(`${url}/trade/settings`)).json()).settings,
    null,
  );
  const saved = await fetch(`${url}/trade/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Origin: "http://127.0.0.1:5173",
    },
    body: JSON.stringify(values),
  });
  assert.equal(saved.status, 200);
  assert.equal((await saved.json()).settings.stopLoss.value, "2.25");
  const rejected = await fetch(`${url}/trade/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...values,
      stopLoss: { unit: "PERCENT", value: "101" },
    }),
  });
  assert.equal(rejected.status, 400);
  assert.equal((await readSettings(path)).stopLoss.value, "2.25");
  const revised = { ...values, maxTrade: { unit: "INR", value: "7500.00" } };
  const updated = await fetch(`${url}/trade/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(revised),
  });
  assert.equal(updated.status, 200);
  assert.equal((await readSettings(path)).maxTrade.value, "7500.00");
  assert.equal(
    (
      await fetch(`${url}/trade/settings`, {
        headers: { Origin: "https://example.com" },
      })
    ).status,
    403,
  );
});

test("account panels require the existing authenticated cockpit session", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-account-route-"));
  const server = createTradingService(join(directory, "settings.json"), {
    accountSync: {
      snapshot: () => ({
        status: "READ_ONLY",
        panels: { funds: { status: "AVAILABLE" } },
      }),
    },
    verifySession: async (cookie) => cookie === "verified-fixture-cookie",
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/trade/account`;
    assert.equal((await fetch(url)).status, 401);
    const accepted = await fetch(url, {
      headers: { Cookie: "verified-fixture-cookie" },
    });
    assert.equal(accepted.status, 200);
    assert.equal((await accepted.json()).panels.funds.status, "AVAILABLE");
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});

test("offline account panel labels verified cache without a live refresh claim", async () => {
  const server = createTradingService("unused", {
    accountOffline: true,
    accountSync: {
      snapshot: () => ({
        status: "READ_ONLY",
        panels: { holdings: { status: "DATA_UNAVAILABLE" } },
      }),
    },
    verifySession: async () => true,
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    const status = await (await fetch(`${base}/trade/status`)).json();
    assert.equal(status.marketFeed, "CACHE_ONLY");
    assert.equal(status.account, "CACHE_ONLY");
    const result = await fetch(`${base}/trade/account`);
    assert.equal(result.status, 200);
    assert.equal((await result.json()).status, "CACHE_ONLY");
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("online request is a data mode and never enables broker execution", async () => {
  const server = createTradingService("unused", { onlineRequested: true });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}`;
    const status = await (await fetch(`${url}/trade/status`)).json();
    assert.equal(status.marketFeed, "ONLINE_REQUESTED");
    assert.equal(status.execution, "UNAVAILABLE");
    assert.equal(
      (await fetch(`${url}/trade/order`, { method: "POST" })).status,
      404,
    );
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("local launcher bootstrap obtains one existing signed session without exposing its key", async () => {
  const token = "a".repeat(64);
  const key = "private-test-session-key";
  let issues = 0;
  const server = createTradingService("unused", {
    bootstrapToken: token,
    sessionSecret: key,
    issueSession: async (received) => {
      assert.equal(received, key);
      issues += 1;
      return "alpha_grid_session=v1.fixture; Max-Age=604800; HttpOnly; SameSite=Strict; Path=/";
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/trade/session/bootstrap`;
  const post = (candidate, origin = "http://127.0.0.1:5173") =>
    fetch(url, {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ token: candidate }),
    });
  try {
    assert.equal((await post(token, "https://example.com")).status, 403);
    assert.equal((await post("b".repeat(64))).status, 403);
    const accepted = await post(token);
    assert.equal(accepted.status, 204);
    const cookie = accepted.headers.get("set-cookie");
    assert.match(cookie, /^alpha_grid_session=/);
    assert.doesNotMatch(cookie, new RegExp(token));
    assert.doesNotMatch(cookie, new RegExp(key));
    assert.equal((await post(token)).status, 403);
    assert.equal(issues, 1);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
