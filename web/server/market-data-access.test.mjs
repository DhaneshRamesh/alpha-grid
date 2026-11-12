import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { createTradingService } from "./trading-service.mjs";
import { changeMarketDataAccess, readMarketDataAccess } from "./market-data-access.mjs";

test("explicit read-only approval persists, disconnects and revokes without broker routes", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-access-"));
  const privateDir = join(directory, "private");
  await mkdir(privateDir, { mode: 0o700 });
  const universe = join(directory, "universe.json");
  await writeFile(universe, JSON.stringify({ instruments: Array.from({ length: 25 }, (_, i) => ({ security_id: String(i + 1) })) }));
  const server = createTradingService(join(directory, "settings.json"), {
    simulationDir: privateDir, universePath: universe,
    verifySession: async (cookie) => cookie === "fixture-session",
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/trade/market-data-access`;
  const headers = { Origin: "http://127.0.0.1:5173", Cookie: "fixture-session", "Content-Type": "application/json" };
  try {
    assert.equal((await fetch(url)).status, 401);
    const approval = { action: "approve", confirmed: true, universe: "FROZEN_25_NSE_EQ",
      standard_feed: true, depth_feed: false, duration: "PERSISTENT", expires_at: null,
      auto_connect: true };
    assert.equal((await fetch(url, { method: "PUT", headers, body: JSON.stringify({ ...approval, depth_feed: true }) })).status, 400);
    assert.equal((await fetch(url, { method: "PUT", headers: { ...headers, Cookie: "" }, body: JSON.stringify(approval) })).status, 403);
    const accepted = await fetch(url, { method: "PUT", headers, body: JSON.stringify(approval) });
    assert.equal(accepted.status, 200);
    assert.equal((await accepted.json()).access.authorized_security_ids.length, 25);
    const stored = JSON.parse(await readFile(join(privateDir, "feed-scope.json"), "utf8"));
    assert.equal(stored.auto_connect, true);
    assert.equal(stored.authorized_feeds[0], "STANDARD_QUOTE");
    const full = await changeMarketDataAccess(join(privateDir, "feed-scope.json"), universe,
      { ...approval, standard_mode: "FULL" });
    assert.deepEqual(full.authorized_feeds, ["STANDARD_FULL"]);
    assert.equal((await readMarketDataAccess(join(privateDir, "feed-scope.json"))).authorized_feeds[0], "STANDARD_FULL");
    await assert.rejects(changeMarketDataAccess(join(privateDir, "feed-scope.json"), universe,
      { ...approval, standard_mode: "UNSUPPORTED" }), /Exact read-only standard feed scope/);
    assert.equal((await readMarketDataAccess(join(privateDir, "feed-scope.json"))).auto_connect, true);
    const disconnect = await fetch(url, { method: "PUT", headers, body: JSON.stringify({ action: "disconnect" }) });
    assert.equal((await disconnect.json()).access.auto_connect, false);
    const reconnect = await fetch(url, { method: "PUT", headers, body: JSON.stringify({ action: "connect" }) });
    assert.equal((await reconnect.json()).access.auto_connect, true);
    const revoke = await fetch(url, { method: "PUT", headers, body: JSON.stringify({ action: "revoke" }) });
    assert.equal((await revoke.json()).access.authorization_confirmed, false);
    const depth = await changeMarketDataAccess(
      join(privateDir, "feed-scope.json"), universe,
      { ...approval, depth_feed: true }, true,
    );
    assert.deepEqual(depth.authorized_feeds, ["STANDARD_QUOTE", "DEPTH_20"]);
    assert.equal(depth.depth_connections, 1);
    assert.equal((await fetch(`${url.replace("market-data-access", "order")}`, { method: "POST" })).status, 404);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});

test("approved auto-connect survives a local API restart without creating a socket", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-access-restart-"));
  const privateDir = join(directory, "private");
  await mkdir(privateDir, { mode: 0o700 });
  const universe = join(directory, "universe.json");
  await writeFile(universe, JSON.stringify({ instruments: Array.from({ length: 25 }, (_, i) =>
    ({ symbol: `FIXTURE${i}`, security_id: String(i + 1) })) }));
  const options = { simulationDir: privateDir, universePath: universe,
    verifySession: async (cookie) => cookie === "fixture-session" };
  let server = createTradingService(join(directory, "settings.json"), options);
  try {
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    let url = `http://127.0.0.1:${server.address().port}/trade/market-data-access`;
    const response = await fetch(url, { method: "PUT", headers: {
      Origin: "http://127.0.0.1:5173", Cookie: "fixture-session", "Content-Type": "application/json",
    }, body: JSON.stringify({ action: "approve", confirmed: true,
      universe: "FROZEN_25_NSE_EQ", standard_feed: true, depth_feed: false,
      duration: "PERSISTENT", expires_at: null, auto_connect: true }) });
    assert.equal(response.status, 200);
    await new Promise((resolve) => server.close(resolve));
    server = createTradingService(join(directory, "settings.json"), options);
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    url = `http://127.0.0.1:${server.address().port}/trade/market-data-access`;
    const restored = await (await fetch(url, { headers: { Cookie: "fixture-session" } })).json();
    assert.equal(restored.access.auto_connect, true);
    assert.equal(restored.access.authorized_security_ids.length, 25);
    assert.equal(restored.universe.length, 25);
  } finally {
    if (server.listening) await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});
