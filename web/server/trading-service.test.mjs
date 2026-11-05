import assert from "node:assert/strict";
import { mkdtemp, rm, readFile, readdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import {
  createTradingService,
  readSettings,
  saveSettings,
  validateSettings,
  restMarketSnapshot,
  readSavedRestSnapshot,
  readCrossStockSimulation,
  isIstContinuousWindow,
} from "./trading-service.mjs";

test("IST REST polling window excludes closed hours and weekends", () => {
  assert.equal(isIstContinuousWindow(new Date("2026-11-03T03:44:00Z")), false);
  assert.equal(isIstContinuousWindow(new Date("2026-11-03T03:45:00Z")), true);
  assert.equal(isIstContinuousWindow(new Date("2026-11-03T09:59:00Z")), true);
  assert.equal(isIstContinuousWindow(new Date("2026-11-03T10:00:00Z")), false);
  assert.equal(isIstContinuousWindow(new Date("2026-11-08T04:00:00Z")), false);
});

test("cross-stock simulation loads only matching private source hashes", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-cross-stock-"));
  try {
    const { mkdir } = await import("node:fs/promises");
    await mkdir(join(directory, "training-artifacts"), { mode: 0o700 });
    const sources = Object.fromEntries(Array.from({ length: 25 }, (_, index) => [`S${index}`, { sha256: "a".repeat(64) }]));
    const artifact = { schema_version: "alpha-grid-cross-stock-exploratory-job-v1", source: "EXPLORATORY_ARCHIVE",
      source_responses: sources, frozen_rule_simulation: { schema_version: "alpha-grid-simulation-v1",
        available_symbols: Object.keys(sources), source_hashes: Object.fromEntries(Object.keys(sources).map((name) => [name, ["a".repeat(64)]])),
        broker_orders: 0, paper_orders: 0, qp01_writes: 0 } };
    const raw = JSON.stringify(artifact);
    const name = `${"b".repeat(32)}.json`;
    await writeFile(join(directory, "training-artifacts", name), raw, { mode: 0o600 });
    await writeFile(join(directory, "cross-stock-simulation.latest.json"), JSON.stringify({
      schema_version: "alpha-grid-cross-stock-simulation-pointer-v1", artifact_name: name,
      sha256: createHash("sha256").update(raw).digest("hex") }), { mode: 0o600 });
    assert.equal((await readCrossStockSimulation(directory)).simulation.available_symbols.length, 25);
    await writeFile(join(directory, "training-artifacts", name), `tampered${raw}`);
    await assert.rejects(readCrossStockSimulation(directory), /checksum mismatch/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

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

test("REST snapshot retains original bytes and rejects HTTP errors as prices", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-rest-snapshot-"));
  const secretDir = join(directory, "secret");
  const tokenPath = join(secretDir, "dhan-access-token");
  const universePath = join(directory, "universe.json");
  const ids = Array.from({length: 25}, (_, index) => String(index + 1));
  try {
    const { mkdir } = await import("node:fs/promises");
    await mkdir(secretDir, {mode: 0o700});
    await writeFile(tokenPath, "fixture.token\n", {mode: 0o600});
    await writeFile(join(secretDir, "dhan-client-id"), "123456\n", {mode: 0o600});
    await writeFile(`${tokenPath}.meta.json`, JSON.stringify({schema_version: "dhan-web-manual-token-v1", source: "https://web.dhan.co/",
      client_id_sha256: createHash("sha256").update("123456").digest("hex"),
      token_sha256: createHash("sha256").update("fixture.token").digest("hex"),
      expires_at_utc: new Date(Date.now() + 3600000).toISOString()}), {mode: 0o600});
    await writeFile(universePath, JSON.stringify({instruments: ids.map((security_id) => ({symbol: `S${security_id}`, security_id}))}));
    await writeFile(join(directory, "feed-scope.json"), JSON.stringify({schema_version: "alpha-grid-market-data-access-v2",
      authorization_confirmed: true, revoked_at: null, auto_connect: true, authorized_security_ids: ids}), {mode: 0o600});
    const body = Buffer.from(JSON.stringify({status: "success", data: {NSE_EQ: Object.fromEntries(ids.map((id) => [id, {
      last_price: Number(id), net_change: 0.5, average_price: 1.25, volume: 100,
      depth: {buy: [{price: 1, quantity: 2}], sell: [{price: 2, quantity: 3}]},
    }]))}}));
    const fetched = await restMarketSnapshot(directory, universePath, async (url, options) => {
      assert.equal(url, "https://api.dhan.co/v2/marketfeed/quote");
      assert.deepEqual(JSON.parse(options.body), {NSE_EQ: ids.map(Number)});
      return new Response(body, {status: 200});
    }, tokenPath);
    assert.equal(fetched.source, "REST_SNAPSHOT");
    assert.equal(fetched.quotes.length, 25);
    assert.equal(fetched.quotes[0].net_change, 0.5);
    assert.equal(fetched.quotes[0].depth.buy[0].quantity, 2);
    assert.equal(fetched.sha256, createHash("sha256").update(body).digest("hex"));
    const files = await readdir(join(directory, "rest-snapshots"));
    assert.deepEqual(await readFile(join(directory, "rest-snapshots", files.find((name) => name.endsWith(".response")))), body);
    assert.deepEqual((await readSavedRestSnapshot(directory, universePath)).quotes, fetched.quotes);
    await assert.rejects(restMarketSnapshot(directory, universePath, async () => new Response("unauthorized", {status: 401}), tokenPath),
      /DHAN_SNAPSHOT_HTTP_401/);
    const evidence = await readdir(join(directory, "rest-snapshots"));
    assert.equal(evidence.filter((name) => name.endsWith(".response")).length, 2);
    await writeFile(join(directory, "feed-scope.json"), JSON.stringify({schema_version: "alpha-grid-market-data-access-v2",
      authorization_confirmed: true, revoked_at: null, auto_connect: true,
      authorized_security_ids: ids, expires_at: new Date(Date.now() - 1000).toISOString()}), {mode: 0o600});
    await assert.rejects(restMarketSnapshot(directory, universePath, () => {
      throw new Error("EXPIRED_SCOPE_MUST_NOT_REQUEST_DHAN");
    }, tokenPath), /READ_ONLY_SCOPE_EXPIRED/);
  } finally { await rm(directory, {recursive: true, force: true}); }
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
    assert.equal(status.marketFeed, "NOT_CONNECTED");
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

test("plain loopback startup renews a signed HttpOnly session without a URL fragment", async () => {
  let issues = 0;
  const server = createTradingService("unused", {
    sessionSecret: "private-test-session-key",
    issueSession: async () => {
      issues++;
      return "alpha_grid_session=v1.fixture; Max-Age=604800; HttpOnly; SameSite=Strict; Path=/";
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/trade/session/local`;
  const post = (origin, header) => fetch(url, { method: "POST",
    headers: { Origin: origin, ...(header ? { "X-Alpha-Grid-Local": header } : {}) } });
  try {
    assert.equal((await post("https://example.com", "1")).status, 403);
    assert.equal((await post("http://127.0.0.1:5173", null)).status, 403);
    const accepted = await post("http://127.0.0.1:5173", "1");
    assert.equal(accepted.status, 204);
    assert.match(accepted.headers.get("set-cookie"), /HttpOnly; SameSite=Strict/);
    assert.equal(issues, 1);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("persistent simulation history and stop require the cockpit session", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-simulation-"));
  await writeFile(join(directory, "simulation-status.json"), JSON.stringify({
    schema_version: "alpha-grid-live-simulation-v1", state: "WAITING_FOR_APPROVED_FEED", ledger_events: 3,
  }));
  const server = createTradingService(join(directory, "settings.json"), {
    simulationDir: directory,
    verifySession: async (cookie) => cookie === "verified-fixture-cookie",
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    assert.equal((await fetch(`${base}/trade/simulation`)).status, 401);
    assert.equal((await fetch(`${base}/trade/simulation/stop`, { method: "POST" })).status, 403);
    const headers = { Cookie: "verified-fixture-cookie", Origin: "http://127.0.0.1:5173" };
    const history = await fetch(`${base}/trade/simulation`, { headers });
    assert.equal((await history.json()).ledger_events, 3);
    assert.equal((await fetch(`${base}/trade/simulation/stop`, { method: "POST", headers })).status, 200);
    assert.equal(await readFile(join(directory, "stop-requested"), "utf8"), "");
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});

test("ticker training queues only frozen-universe jobs behind local session", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-training-"));
  const universe = join(directory, "universe.json");
  await writeFile(universe, JSON.stringify({ instruments: [{ symbol: "RELIANCE" }, { symbol: "HDFCBANK" }] }));
  await writeFile(join(directory, "training-status.json"), JSON.stringify({ state: "IDLE" }));
  const server = createTradingService(join(directory, "settings.json"), {
    simulationDir: directory, universePath: universe,
    verifySession: async (cookie) => cookie === "verified-fixture-cookie",
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    assert.equal((await fetch(`${base}/trade/training`)).status, 401);
    const headers = { Cookie: "verified-fixture-cookie", Origin: "http://127.0.0.1:5173", "Content-Type": "application/json" };
    assert.equal((await fetch(`${base}/trade/training`, { method: "POST", headers, body: JSON.stringify({ ticker: "UNKNOWN" }) })).status, 400);
    const queued = await fetch(`${base}/trade/training`, { method: "POST", headers, body: JSON.stringify({ ticker: "HDFCBANK" }) });
    assert.equal(queued.status, 202);
    assert.equal((await queued.json()).state, "QUEUED");
    assert.equal(JSON.parse(await readFile(join(directory, "training-request.json"))).ticker, "HDFCBANK");
    assert.equal((await fetch(`${base}/trade/training`, { method: "POST", headers, body: JSON.stringify({ ticker: "RELIANCE" }) })).status, 409);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});
