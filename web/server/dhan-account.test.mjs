import assert from "node:assert/strict";
import { test } from "node:test";
import {
  fetchDhanAccount,
  createAccountSync,
  tokenExpiryFuture,
} from "./dhan-account.mjs";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const credentials = { clientId: "client-1", token: "test-token" };

test("reads Dhan funds and only NSE intraday positions without exposing the token", async () => {
  const seen = [];
  const payloads = {
    fundlimit: {
      dhanClientId: "client-1",
      availabelBalance: 12345.6,
      utilizedAmount: 200,
    },
    holdings: [{ tradingSymbol: "ABC" }],
    positions: [
      {
        exchangeSegment: "NSE_EQ",
        productType: "INTRADAY",
        tradingSymbol: "ABC",
        securityId: "123",
        netQty: 2,
      },
      {
        exchangeSegment: "NSE_EQ",
        productType: "CNC",
        tradingSymbol: "XYZ",
        securityId: "456",
        netQty: 3,
      },
    ],
  };
  const fetcher = async (url, options) => {
    seen.push({ url, token: options.headers["access-token"] });
    return { ok: true, json: async () => payloads[url.split("/").at(-1)] };
  };
  const account = await fetchDhanAccount(credentials, fetcher);
  assert.equal(account.availableFundsInr, "12345.60");
  assert.equal(account.holdingsCount, 1);
  assert.deepEqual(account.intradayPositions, [
    { symbol: "ABC", securityId: "123", netQty: 2 },
  ]);
  assert.equal(JSON.stringify(account).includes(credentials.token), false);
  assert.deepEqual(
    seen.map(({ url }) => url.split("/").at(-1)),
    ["fundlimit", "holdings", "positions"],
  );
  assert(seen.every(({ token }) => token === credentials.token));
});

test("rejects a response for a different Dhan client", async () => {
  const fetcher = async (url) => ({
    ok: true,
    json: async () =>
      url.endsWith("fundlimit")
        ? {
            dhanClientId: "another-client",
            availabelBalance: 1,
            utilizedAmount: 0,
          }
        : [],
  });
  await assert.rejects(fetchDhanAccount(credentials, fetcher), /did not match/);
});

test("approved account refreshes are independent, reserved, and stop on 401", async () => {
  const directory = await mkdtemp(join(tmpdir(), "alpha-grid-account-test-"));
  const path = join(directory, "token");
  const previous = process.env.DHAN_CLIENT_ID;
  process.env.DHAN_CLIENT_ID = "test-client";
  const token = `x.${Buffer.from(JSON.stringify({ exp: 9999999999 })).toString("base64url")}.x`;
  await writeFile(path, token);
  const requests = [];
  const reserved = new Set();
  const panels = new Map();
  const cacheCommand = async (operation, name, payload) => {
    if (operation === "credential")
      return {
        private_token_present: true,
        metadata_valid: true,
        token_mtime_ns: "100",
      };
    if (operation === "reserve") {
      if (reserved.has(name)) return { reserved: false };
      reserved.add(name);
      return { reserved: true };
    }
    if (operation === "save") {
      panels.set(name, {
        endpoint: name,
        status: payload.status === 200 ? "AVAILABLE" : "DATA_UNAVAILABLE",
        last_http_status: payload.status,
        reauthentication_required: payload.status === 401,
        blocked_token_mtime_ns: payload.status === 401 ? "100" : null,
      });
      return {};
    }
    return panels.get(name) ?? { endpoint: name, status: "DATA_UNAVAILABLE" };
  };
  const fetcher = async (url, options) => {
    requests.push(url.split("/").at(-1));
    assert.equal(options.headers["access-token"], token);
    const status = url.endsWith("/positions") ? 401 : 200;
    return {
      status,
      arrayBuffer: async () =>
        Buffer.from(status === 401 ? '{"errorCode":"DH-901"}' : "[]"),
    };
  };
  try {
    const sync = createAccountSync({
      tokenPath: path,
      cacheCommand,
      fetcher,
      now: () => 1_800_000_000_000,
      pause: async () => {},
    });
    await sync.load();
    await sync.refresh();
    assert.deepEqual(requests, ["fundlimit", "holdings", "positions"]);
    assert.equal(sync.snapshot().status, "REAUTHENTICATION_REQUIRED");
    assert.equal(sync.snapshot().panels.holdings.status, "AVAILABLE");
    assert.equal(JSON.stringify(sync.snapshot()).includes(token), false);
    await sync.refresh();
    assert.equal(requests.length, 3);
    const restarted = createAccountSync({
      tokenPath: path,
      cacheCommand,
      fetcher,
      now: () => 1_800_000_000_000,
      pause: async () => {},
    });
    await restarted.load();
    await restarted.refresh();
    assert.equal(requests.length, 3);
  } finally {
    if (previous === undefined) delete process.env.DHAN_CLIENT_ID;
    else process.env.DHAN_CLIENT_ID = previous;
    await rm(directory, { recursive: true, force: true });
  }
});

test("expired or malformed token never starts an account request", async () => {
  assert.equal(tokenExpiryFuture("not-a-token"), false);
  const directory = await mkdtemp(
    join(tmpdir(), "alpha-grid-account-expired-"),
  );
  const path = join(directory, "token");
  const previous = process.env.DHAN_CLIENT_ID;
  process.env.DHAN_CLIENT_ID = "test-client";
  await writeFile(
    path,
    `x.${Buffer.from('{"exp":1}').toString("base64url")}.x`,
  );
  let requests = 0;
  try {
    const sync = createAccountSync({
      tokenPath: path,
      cacheCommand: async (operation, name) =>
        operation === "credential"
          ? { private_token_present: true, metadata_valid: true }
          : { endpoint: name, status: "DATA_UNAVAILABLE" },
      fetcher: async () => {
        requests++;
        throw Error("unexpected");
      },
    });
    await sync.refresh();
    assert.equal(requests, 0);
    assert.equal(sync.snapshot().status, "REAUTHENTICATION_REQUIRED");
  } finally {
    if (previous === undefined) delete process.env.DHAN_CLIENT_ID;
    else process.env.DHAN_CLIENT_ID = previous;
    await rm(directory, { recursive: true, force: true });
  }
});

test("a transport failure leaves other approved panels refreshable", async () => {
  const directory = await mkdtemp(
    join(tmpdir(), "alpha-grid-account-independent-"),
  );
  const path = join(directory, "token");
  const previous = process.env.DHAN_CLIENT_ID;
  process.env.DHAN_CLIENT_ID = "test-client";
  await writeFile(
    path,
    `x.${Buffer.from('{"exp":9999999999}').toString("base64url")}.x`,
  );
  const called = [];
  const cacheCommand = async (operation, name, payload) => {
    if (operation === "credential")
      return { private_token_present: true, metadata_valid: true };
    if (operation === "reserve") return { reserved: true };
    if (operation === "load")
      return { endpoint: name, status: "DATA_UNAVAILABLE" };
    assert.equal(operation === "failure" || operation === "save", true);
    assert(payload.received_at);
    return {};
  };
  try {
    const sync = createAccountSync({
      tokenPath: path,
      cacheCommand,
      now: () => 1_800_000_000_000,
      pause: async () => {},
      fetcher: async (url) => {
        const name = url.split("/").at(-1);
        called.push(name);
        if (name === "fundlimit")
          throw Error("offline fixture transport failure");
        return { status: 200, arrayBuffer: async () => Buffer.from("[]") };
      },
    });
    await sync.refresh();
    assert.deepEqual(called, [
      "fundlimit",
      "holdings",
      "positions",
      "orders",
      "trades",
    ]);
    assert.equal(
      sync.snapshot().panels.fundlimit.reason,
      "REQUEST_OR_STORAGE_FAILED",
    );
  } finally {
    if (previous === undefined) delete process.env.DHAN_CLIENT_ID;
    else process.env.DHAN_CLIENT_ID = previous;
    await rm(directory, { recursive: true, force: true });
  }
});

test("failed cache re-verification removes previously displayed account data", async () => {
  const directory = await mkdtemp(
    join(tmpdir(), "alpha-grid-account-integrity-"),
  );
  const path = join(directory, "token");
  const previous = process.env.DHAN_CLIENT_ID;
  process.env.DHAN_CLIENT_ID = "test-client";
  await writeFile(
    path,
    `x.${Buffer.from('{"exp":9999999999}').toString("base64url")}.x`,
  );
  let fundsLoads = 0;
  try {
    const sync = createAccountSync({
      tokenPath: path,
      now: () => 1_800_000_000_000,
      pause: async () => {},
      cacheCommand: async (operation, name) => {
        if (operation === "credential")
          return { private_token_present: true, metadata_valid: true };
        if (operation === "reserve") return { reserved: true };
        if (operation === "load" && name === "fundlimit") {
          if (++fundsLoads > 1) throw Error("fixture integrity failure");
          return {
            endpoint: name,
            status: "AVAILABLE",
            data: { available_balance_inr: "123.45" },
          };
        }
        return { endpoint: name, status: "DATA_UNAVAILABLE" };
      },
      fetcher: async () => ({
        status: 200,
        arrayBuffer: async () => Buffer.from("{}"),
      }),
    });
    await sync.load();
    assert.equal(sync.snapshot().panels.fundlimit.status, "AVAILABLE");
    await sync.refresh();
    const funds = sync.snapshot().panels.fundlimit;
    assert.equal(funds.status, "DATA_UNAVAILABLE");
    assert.equal(funds.reason, "CACHE_UNVERIFIABLE");
    assert.equal(funds.data, undefined);
  } finally {
    if (previous === undefined) delete process.env.DHAN_CLIENT_ID;
    else process.env.DHAN_CLIENT_ID = previous;
    await rm(directory, { recursive: true, force: true });
  }
});
