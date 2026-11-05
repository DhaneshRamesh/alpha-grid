import { createServer } from "node:http";
import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, readdir, rename, open, stat, lstat } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { boundedResponseBytes, createAccountSync, defaultTokenPath, loadDhanCredentials } from "./dhan-account.mjs";
import { changeMarketDataAccess, readMarketDataAccess } from "./market-data-access.mjs";

const FIELDS = ["maxTrade", "maxDailyLoss", "stopLoss", "profitTarget"];
const UNITS = {
  maxTrade: ["INR", "PERCENT"],
  maxDailyLoss: ["INR", "PERCENT"],
  stopLoss: ["INR_PER_SHARE", "PERCENT"],
  profitTarget: ["INR_PER_SHARE", "PERCENT"],
};

export function validateSettings(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("Settings must be an object");
  }
  if (Object.keys(input).sort().join(",") !== FIELDS.toSorted().join(",")) {
    throw new Error("Expected four trading limits");
  }
  const output = {};
  for (const field of FIELDS) {
    const item = input[field];
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new Error(`${field} is required`);
    }
    if (Object.keys(item).sort().join(",") !== "unit,value") {
      throw new Error(`${field} needs unit and value`);
    }
    if (!UNITS[field].includes(item.unit)) {
      throw new Error(`${field} has an invalid unit`);
    }
    if (
      typeof item.value !== "string" ||
      !/^(?:0|[1-9]\d{0,10})(?:\.\d{1,2})?$/.test(item.value)
    ) {
      throw new Error(
        `${field} needs a positive decimal with at most two places`,
      );
    }
    const hundredths =
      BigInt(item.value.replace(".", "")) *
      (item.value.includes(".")
        ? 10n ** BigInt(2 - item.value.split(".")[1].length)
        : 100n);
    if (hundredths <= 0n || (item.unit === "PERCENT" && hundredths > 10000n)) {
      throw new Error(
        `${field} must be positive and percentage cannot exceed 100`,
      );
    }
    output[field] = { unit: item.unit, value: item.value };
  }
  return output;
}

export async function readSettings(path) {
  try {
    const stored = JSON.parse(await readFile(path, "utf8"));
    return {
      ...validateSettings(stored.settings),
      updatedAt: stored.updatedAt,
    };
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

export async function saveSettings(path, settings) {
  const validated = validateSettings(settings);
  const updatedAt = new Date().toISOString();
  await mkdir(dirname(path), { recursive: true });
  const temp = `${path}.${process.pid}.tmp`;
  const handle = await open(temp, "wx", 0o600);
  try {
    await handle.writeFile(
      JSON.stringify({ settings: validated, updatedAt }) + "\n",
    );
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(temp, path);
  return { ...validated, updatedAt };
}

function send(response, status, body) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(JSON.stringify(body));
}

export async function readCrossStockSimulation(privateDir) {
  const pointerFile = resolve(privateDir, "cross-stock-simulation.latest.json");
  const pointerInfo = await lstat(pointerFile);
  if (!pointerInfo.isFile() || pointerInfo.isSymbolicLink() || pointerInfo.uid !== process.getuid?.() ||
      pointerInfo.size > 4096 || (pointerInfo.mode & 0o077))
    throw new Error("Cross-stock simulation pointer storage invalid");
  const pointer = JSON.parse(await readFile(pointerFile, "utf8"));
  if (pointer.schema_version !== "alpha-grid-cross-stock-simulation-pointer-v1" ||
      !/^[a-f0-9]{32}\.json$/.test(pointer.artifact_name) ||
      !/^[a-f0-9]{64}$/.test(pointer.sha256))
    throw new Error("Cross-stock simulation pointer invalid");
  const file = resolve(privateDir, "training-artifacts", pointer.artifact_name);
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink() || info.uid !== process.getuid?.() ||
      info.size > 2_000_000 || (info.mode & 0o077))
    throw new Error("Cross-stock simulation artifact invalid");
  const raw = await readFile(file);
  if (createHash("sha256").update(raw).digest("hex") !== pointer.sha256)
    throw new Error("Cross-stock simulation checksum mismatch");
  const artifact = JSON.parse(raw.toString("utf8"));
  const report = artifact.frozen_rule_simulation;
  if (artifact.schema_version !== "alpha-grid-cross-stock-exploratory-job-v1" ||
      artifact.source !== "EXPLORATORY_ARCHIVE" ||
      Object.keys(artifact.source_responses ?? {}).length !== 25 ||
      report?.schema_version !== "alpha-grid-simulation-v1" ||
      report.available_symbols?.length !== 25 ||
      report.broker_orders !== 0 || report.paper_orders !== 0 || report.qp01_writes !== 0 ||
      Object.entries(artifact.source_responses).some(([symbol, source]) =>
        !report.source_hashes?.[symbol]?.includes(source.sha256)))
    throw new Error("Cross-stock simulation provenance invalid");
  return { source: "EXPLORATORY_ARCHIVE", sha256: pointer.sha256, simulation: report };
}

export async function readAdaptiveResearch(privateDir) {
  const directory = resolve(privateDir, "research-cross-equity-adaptive-v2");
  const dirInfo = await lstat(directory);
  if (!dirInfo.isDirectory() || dirInfo.isSymbolicLink() ||
      dirInfo.uid !== process.getuid?.() || (dirInfo.mode & 0o077))
    throw new Error("Adaptive research storage invalid");
  async function verified(name, maxBytes, expected = null) {
    const file = resolve(directory, name);
    const info = await lstat(file);
    if (!info.isFile() || info.isSymbolicLink() || info.uid !== process.getuid?.() ||
        (info.mode & 0o077) || info.size > maxBytes)
      throw new Error("Adaptive research file invalid");
    const raw = await readFile(file);
    const sha256 = createHash("sha256").update(raw).digest("hex");
    if (expected && sha256 !== expected) throw new Error("Adaptive research checksum mismatch");
    return { value: JSON.parse(raw.toString("utf8")), sha256 };
  }
  const manifest = (await verified("manifest-v2.json", 4096)).value;
  if (manifest.schema_version !== "alpha-grid-cross-equity-research-manifest-v2" ||
      manifest.source !== "EXPLORATORY_ARCHIVE" ||
      !["adaptive_report", "fixed_audit", "all_decisions"].every((key) =>
        /^[a-f0-9]{64}$/.test(manifest.sha256?.[key] ?? "")))
    throw new Error("Adaptive research manifest invalid");
  const report = await verified("adaptive-report-v2.json", 500_000, manifest.sha256.adaptive_report);
  const audit = await verified("fixed-audit-v2.json", 500_000, manifest.sha256.fixed_audit);
  const decisionFile = resolve(directory, "all-decisions-v2.json");
  const decisionInfo = await lstat(decisionFile);
  if (!decisionInfo.isFile() || decisionInfo.isSymbolicLink() ||
      decisionInfo.uid !== process.getuid?.() || (decisionInfo.mode & 0o077) ||
      decisionInfo.size > 80_000_000 ||
      createHash("sha256").update(await readFile(decisionFile)).digest("hex") !==
        manifest.sha256.all_decisions)
    throw new Error("Adaptive decision log invalid");
  const fixed = (await readCrossStockSimulation(privateDir)).simulation;
  if (report.value.schema_version !== "alpha-grid-cross-equity-adaptive-v2" ||
      report.value.source !== "EXPLORATORY_ARCHIVE" || report.value.bars !== 45_300 ||
      report.value.ml_challenger !== null || report.value.production_eligibility !== "BLOCKED" ||
      audit.value.schema_version !== "alpha-grid-cross-equity-fixed-audit-v1" ||
      audit.value.coverage?.evaluated_rows !== 125 ||
      audit.value.rows?.length !== 125 ||
      Object.keys(report.value.variants ?? {}).length !== 6 ||
      Object.entries(fixed.source_hashes).some(([symbol, hashes]) =>
        JSON.stringify(report.value.source_hashes?.[symbol]) !== JSON.stringify(hashes)) ||
      Object.values(report.value.variants).some((row) => row.broker_orders !== 0 ||
        row.qp01_writes !== 0 || row.reference_prices_are_fills !== false))
    throw new Error("Adaptive research provenance invalid");
  return { source: "EXPLORATORY_ARCHIVE", sha256: report.sha256,
    decision_log_sha256: manifest.sha256.all_decisions,
    report: report.value, fixed_audit: audit.value };
}

export function isIstContinuousWindow(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {timeZone: "Asia/Kolkata",
    weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23"})
    .formatToParts(now).map((part) => [part.type, part.value]));
  const minute = Number(parts.hour) * 60 + Number(parts.minute);
  return !["Sat", "Sun"].includes(parts.weekday) && minute >= 9 * 60 + 15 && minute < 15 * 60 + 30;
}

function profileCheck(privateDir) {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const python = process.env.ALPHA_GRID_PYTHON ?? resolve(root, ".venv/bin/python");
  return new Promise((finish) => {
    const child = spawn(python, ["-m", "quant.apps.live_feed_credentials", "--root", root,
      "--private-dir", privateDir, "--check-profile"], {
      cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, "src") }, stdio: ["ignore", "pipe", "ignore"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk.toString("utf8").slice(0, 100); });
    child.on("error", () => finish("PROFILE_CHECK_UNAVAILABLE"));
    child.on("close", () => finish(/^[A-Z_0-9]+$/.test(output.trim()) ? output.trim() : "PROFILE_CHECK_UNAVAILABLE"));
  });
}

function localArchive(privateDir, symbol) {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const python = process.env.ALPHA_GRID_PYTHON ?? resolve(root, ".venv/bin/python");
  return new Promise((finish, fail) => {
    const child = spawn(python, ["-m", "quant.apps.cockpit_cache", "--cross-stock-symbol", symbol,
      "--private-dir", privateDir], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, "src") },
      stdio: ["ignore", "pipe", "ignore"] });
    let output = "";
    child.stdout.on("data", (part) => { output += part.toString("utf8"); if (output.length > 2_000_000) child.kill(); });
    child.on("error", () => fail(new Error("LOCAL_ARCHIVE_UNAVAILABLE")));
    child.on("close", (code) => { if (code !== 0 || output.length > 2_000_000) return fail(new Error("LOCAL_ARCHIVE_UNAVAILABLE"));
      try { finish(JSON.parse(output)); } catch { fail(new Error("LOCAL_ARCHIVE_INVALID")); } });
  });
}

async function saveRawSnapshot(privateDir, raw, metadata) {
  const directory = resolve(privateDir, "rest-snapshots");
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid?.() || (info.mode & 0o077))
    throw new Error("Private REST snapshot storage invalid");
  const stem = `${Date.now()}-${randomUUID()}`;
  const path = resolve(directory, `${stem}.response`);
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(raw); await handle.sync(); }
  finally { await handle.close(); }
  const meta = await open(resolve(directory, `${stem}.meta.json`), "wx", 0o600);
  try { await meta.writeFile(JSON.stringify(metadata) + "\n"); await meta.sync(); }
  finally { await meta.close(); }
}

function parseQuoteSnapshot(raw, universe, ids, metadata) {
  const value = JSON.parse(raw.toString("utf8"));
  if (value.status !== "success" || !value.data?.NSE_EQ) throw new Error("DHAN_SNAPSHOT_SCHEMA_INVALID");
  const quotes = universe.instruments.flatMap((item) => {
    const row = value.data.NSE_EQ[item.security_id];
    if (!row) return [];
    if (typeof row.last_price !== "number" || !Number.isFinite(row.last_price) || row.last_price <= 0)
      throw new Error("DHAN_SNAPSHOT_PRICE_INVALID");
    const observed = {};
    for (const key of ["net_change", "average_price", "last_quantity", "buy_quantity",
      "sell_quantity", "upper_circuit_limit", "lower_circuit_limit", "52_week_high", "52_week_low"]) {
      if (typeof row[key] === "number" && Number.isFinite(row[key])) observed[key] = row[key];
    }
    return [{ symbol: item.symbol, security_id: item.security_id, last_price: row.last_price,
      ohlc: row.ohlc ?? null, volume: row.volume ?? null, last_trade_time: row.last_trade_time ?? null,
      depth: row.depth ?? null, ...observed }];
  });
  if (Object.keys(value.data.NSE_EQ).some((id) => !ids.includes(id)))
    throw new Error("DHAN_SNAPSHOT_WRONG_INSTRUMENT");
  return { source: "REST_SNAPSHOT", received_at: metadata.received_at, sha256: metadata.sha256,
    http_status: metadata.http_status, response_bytes: raw.length, quotes,
    missing_security_ids: ids.filter((id) => !quotes.some((q) => q.security_id === id)) };
}

export async function readSavedRestSnapshot(privateDir, universePath) {
  const directory = resolve(privateDir, "rest-snapshots");
  let names;
  try { names = (await readdir(directory)).filter((name) => /^\d{13}-[a-f0-9-]+\.meta\.json$/.test(name)).sort(); }
  catch (error) { if (error.code === "ENOENT") return null; throw error; }
  if (!names.length) return null;
  const universe = JSON.parse(await readFile(universePath, "utf8"));
  const ids = universe.instruments.map((item) => item.security_id);
  if (ids.length !== 25) throw new Error("SAVED_REST_SNAPSHOT_UNIVERSE_INVALID");
  for (const name of names.toReversed()) {
    const metaPath = resolve(directory, name);
    const metaInfo = await lstat(metaPath);
    if (!metaInfo.isFile() || metaInfo.isSymbolicLink() ||
        metaInfo.uid !== process.getuid?.() || (metaInfo.mode & 0o077) || metaInfo.size > 1_048_576)
      throw new Error("SAVED_REST_SNAPSHOT_STORAGE_INVALID");
    const metadata = JSON.parse(await readFile(metaPath, "utf8"));
    if (metadata.http_status !== 200) continue;
    const rawPath = resolve(directory, name.replace(/\.meta\.json$/, ".response"));
    const rawInfo = await lstat(rawPath);
    if (!rawInfo.isFile() || rawInfo.isSymbolicLink() ||
        rawInfo.uid !== process.getuid?.() || (rawInfo.mode & 0o077) || rawInfo.size > 1_048_576)
      throw new Error("SAVED_REST_SNAPSHOT_STORAGE_INVALID");
    const raw = await readFile(rawPath);
    if (metadata.schema_version !== "alpha-grid-dhan-rest-snapshot-v1" ||
        metadata.endpoint !== "https://api.dhan.co/v2/marketfeed/quote" ||
        metadata.method !== "POST" || metadata.response_bytes !== raw.length ||
        raw.length === 0 || metadata.sha256 !== createHash("sha256").update(raw).digest("hex") ||
        !Number.isFinite(Date.parse(metadata.received_at)) ||
        Date.parse(metadata.received_at) > Date.now() + 60_000 ||
        !Array.isArray(metadata.request?.NSE_EQ) ||
        metadata.request.NSE_EQ.length !== 25 ||
        !ids.every((id, index) => metadata.request.NSE_EQ[index] === Number(id)))
      throw new Error("SAVED_REST_SNAPSHOT_INTEGRITY_FAILED");
    return parseQuoteSnapshot(raw, universe, ids, metadata);
  }
  return null;
}

export async function restMarketSnapshot(privateDir, universePath, fetcher = fetch, tokenPath = defaultTokenPath()) {
  const access = await readMarketDataAccess(resolve(privateDir, "feed-scope.json"));
  if (!access?.authorization_confirmed || access.revoked_at || !access.auto_connect)
    throw new Error("READ_ONLY_SCOPE_UNAVAILABLE");
  const todayIST = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata",
    year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  if ((access.expires_at && Date.parse(access.expires_at) <= Date.now()) ||
      (access.duration === "SESSION_ONLY" && access.session_date_ist !== todayIST))
    throw new Error("READ_ONLY_SCOPE_EXPIRED");
  const ids = access.authorized_security_ids;
  const universe = JSON.parse(await readFile(universePath, "utf8"));
  if (!Array.isArray(ids) || ids.length !== 25 || ids.some((id, i) => id !== universe.instruments[i]?.security_id))
    throw new Error("FROZEN_UNIVERSE_MISMATCH");
  const credentials = await loadDhanCredentials(tokenPath);
  const metaPath = `${tokenPath}.meta.json`;
  const metaInfo = await lstat(metaPath);
  if (!metaInfo.isFile() || metaInfo.isSymbolicLink() || metaInfo.uid !== process.getuid?.() || (metaInfo.mode & 0o077))
    throw new Error("AUTH_REQUIRED");
  const meta = JSON.parse(await readFile(metaPath, "utf8"));
  if (!credentials || ![["dhan-consent-token-v1", "https://auth.dhan.co/app/consumeApp-consent"],
      ["dhan-web-manual-token-v1", "https://web.dhan.co/"]].some(([version, source]) =>
        meta.schema_version === version && meta.source === source) ||
      meta.token_sha256 !== createHash("sha256").update(credentials.token).digest("hex") ||
      (meta.schema_version === "dhan-web-manual-token-v1" &&
       meta.client_id_sha256 !== createHash("sha256").update(credentials.clientId).digest("hex")) ||
      Date.parse(meta.expires_at_utc) <= Date.now() + 60_000)
    throw new Error("AUTH_REQUIRED");
  const payload = { NSE_EQ: ids.map(Number) };
  const received = await fetcher("https://api.dhan.co/v2/marketfeed/quote", {
    method: "POST", headers: { "access-token": credentials.token, "client-id": credentials.clientId,
      "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload), signal: AbortSignal.timeout(8000), cache: "no-store",
  });
  const raw = await boundedResponseBytes(received);
  const at = new Date().toISOString();
  const sha256 = createHash("sha256").update(raw).digest("hex");
  const metadata = { schema_version: "alpha-grid-dhan-rest-snapshot-v1",
    endpoint: "https://api.dhan.co/v2/marketfeed/quote", method: "POST", request: payload,
    received_at: at, http_status: received.status, server_date: received.headers.get("date"),
    response_bytes: raw.length, sha256 };
  await saveRawSnapshot(privateDir, raw, metadata);
  if (!received.ok) throw new Error(`DHAN_SNAPSHOT_HTTP_${received.status}`);
  return parseQuoteSnapshot(raw, universe, ids, metadata);
}

function workerFresh(info, value) {
  return info.isFile() && Date.now() - info.mtimeMs <= (value?.state === "RUNNING" ? 125000 : 5000);
}

async function authenticatedCookie(cookie) {
  if (!cookie) return false;
  try {
    const response = await fetch("http://127.0.0.1:8765/api/session", {
      headers: { Cookie: cookie },
      signal: AbortSignal.timeout(2000),
      cache: "no-store",
    });
    return response.status === 204;
  } catch {
    return false;
  }
}

async function issueCockpitSession(secret) {
  const result = await fetch("http://127.0.0.1:8765/api/session", {
    method: "POST",
    headers: {
      Origin: "http://127.0.0.1:5173",
      Authorization: `Bearer ${secret}`,
    },
    signal: AbortSignal.timeout(2000),
    cache: "no-store",
  });
  return result.status === 204 ? result.headers.get("set-cookie") : null;
}

export function createTradingService(
  settingsPath,
  {
    accountSync = null,
    accountOffline = false,
    onlineRequested = false,
    verifySession = authenticatedCookie,
    bootstrapToken = null,
    sessionSecret = null,
    issueSession = issueCockpitSession,
    simulationDir = null,
    universePath = null,
    depthAvailable = false,
    autoRestSnapshots = false,
  } = {},
) {
  let pendingBootstrap = /^[0-9a-f]{64}$/.test(bootstrapToken ?? "")
    ? bootstrapToken
    : null;
  let historicalCapability = { state: "BLOCKED", checkedAt: 0 };
  let restSnapshotCache = null;
  let restSnapshotAt = 0;
  let restSnapshotPending = null;
  let restSnapshotError = null;
  const archivedBySymbol = new Map();
  let all25Archive = { state: "STARTING", instruments: 0, bars: 0 };
  async function warmArchivedUniverse() {
    if (!simulationDir || !universePath) return;
    try {
      const universe = JSON.parse(await readFile(universePath, "utf8"));
      if (universe.instruments?.length !== 25) throw new Error("FROZEN_UNIVERSE_INVALID");
      let bars = 0;
      for (const item of universe.instruments) {
        const value = await localArchive(simulationDir, item.symbol);
        if (value.status !== "AVAILABLE" || value.source_integrity !== "VERIFIED" ||
            value.instrument !== `NSE_EQ:${item.security_id}:${item.symbol}` ||
            !Array.isArray(value.bars) || !value.bars.length)
          throw new Error("VERIFIED_LOCAL_ARCHIVE_MISSING");
        archivedBySymbol.set(item.symbol, value);
        bars += value.bars.length;
      }
      const simulation = await readCrossStockSimulation(simulationDir);
      if (bars !== 45_300 || universe.instruments.some((item) =>
        !simulation.simulation.source_hashes?.[item.symbol]?.includes(
          archivedBySymbol.get(item.symbol).response_sha256)))
        throw new Error("FROZEN_ARCHIVE_SET_MISMATCH");
      all25Archive = { state: "READY", instruments: archivedBySymbol.size, bars };
    } catch {
      all25Archive = { state: "BLOCKED", instruments: archivedBySymbol.size,
        bars: [...archivedBySymbol.values()].reduce((total, value) => total + value.bars.length, 0) };
    }
  }
  async function currentRestSnapshot(force = false) {
    if (!force && restSnapshotCache && Date.now() - restSnapshotAt < 55_000) return restSnapshotCache;
    if (!restSnapshotPending) restSnapshotPending = restMarketSnapshot(simulationDir, universePath)
      .then((value) => { restSnapshotCache = value; restSnapshotAt = Date.now(); restSnapshotError = null; return value; })
      .catch((error) => { restSnapshotError = /^DHAN_SNAPSHOT_HTTP_\d+$|^AUTH_REQUIRED$/.test(error.message)
        ? error.message : "REST_SNAPSHOT_REFRESH_FAILED"; throw error; })
      .finally(() => { restSnapshotPending = null; });
    return restSnapshotPending;
  }
  async function pollRestSnapshot() {
    if (!simulationDir || !universePath) return;
    if (!isIstContinuousWindow()) return;
    try {
      const gateFile = resolve(simulationDir, "feed-gate-status.json");
      const gate = JSON.parse(await readFile(gateFile, "utf8"));
      if (gate.schema_version !== "alpha-grid-feed-gate-v1" ||
          Date.now() - Number(gate.checked_at_ns) / 1e6 > 70_000 ||
          ["MARKET CLOSED", "AUTH REQUIRED"].includes(gate.state)) return;
      await currentRestSnapshot();
    } catch { /* Auth, scope, network and storage errors remain visible on explicit refresh. */ }
  }
  const server = createServer(async (request, response) => {
    try {
      const origin = request.headers.origin;
      if (
        origin &&
        !["http://127.0.0.1:5173", "http://localhost:5173"].includes(origin)
      ) {
        return send(response, 403, { error: "Origin not allowed" });
      }
      const path = new URL(request.url, "http://127.0.0.1").pathname;
      if (path === "/trade/capabilities" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        let gateway = null;
        let snapshot = null;
        let feedScopeConfigured = false;
        let feedGate = null;
        let marketAccess = null;
        let depthStatus = null;
        let rawStatus = null;
        let savedRawStatus = null;
        try {
          const result = await fetch("http://127.0.0.1:8765/api/health", {
            signal: AbortSignal.timeout(1500), cache: "no-store",
          });
          if (result.ok) gateway = await result.json();
        } catch { /* Local gateway unavailable. */ }
        if (gateway) {
          try {
            const state = await fetch("http://127.0.0.1:8765/api/snapshot", { headers: { Cookie: request.headers.cookie ?? "" }, signal: AbortSignal.timeout(1500) });
            if (state.ok) snapshot = (await state.json()).payload;
          } catch { /* Source detail unavailable. */ }
          if (Date.now() - historicalCapability.checkedAt > 60000) {
            try {
              const archive = await fetch("http://127.0.0.1:8765/api/historical/reliance", { headers: { Cookie: request.headers.cookie ?? "" }, signal: AbortSignal.timeout(1500) });
              const value = archive.ok ? await archive.json() : null;
              historicalCapability = { state: value?.status === "AVAILABLE" && value?.source_integrity === "VERIFIED" ? "READY" : "BLOCKED", checkedAt: Date.now() };
            } catch { historicalCapability = { state: "UNAVAILABLE", checkedAt: Date.now() }; }
          }
        }
        if (simulationDir) {
          try {
            const file = resolve(simulationDir, "raw-capture-status.json");
            const info = await lstat(file);
            const value = JSON.parse(await readFile(file, "utf8"));
            if (info.isFile() && !info.isSymbolicLink() && info.uid === process.getuid?.() &&
                !(info.mode & 0o077) && value.schema_version === "alpha-grid-raw-capture-status-v1" &&
                Number.isSafeInteger(value.frames_received) && value.frames_received >= 0 &&
                Number.isFinite(Number(value.last_frame_received_ns))) savedRawStatus = value;
            if (savedRawStatus === value && Date.now() - Number(value.checked_at_ns) / 1e6 < 10000 &&
                value.schema_version === "alpha-grid-raw-capture-status-v1" &&
                value.feed_session_id === snapshot?.session_id) rawStatus = value;
          } catch { /* No current raw frame. */ }
          try { feedScopeConfigured = (await stat(resolve(simulationDir, "feed-scope.json"))).isFile(); }
          catch { feedScopeConfigured = false; }
          try { marketAccess = await readMarketDataAccess(resolve(simulationDir, "feed-scope.json")); }
          catch { /* Invalid approval remains blocked by the Python gate. */ }
          try {
            const file = resolve(simulationDir, "depth-status.json");
            const info = await stat(file);
            const value = JSON.parse(await readFile(file, "utf8"));
            if (info.isFile() && Date.now() - Number(value.checked_at_ns) / 1e6 < 10000 &&
                value.schema_version === "alpha-grid-depth-status-v1") depthStatus = value;
          } catch { /* Depth has no current observation. */ }
          try {
            const gatePath = resolve(simulationDir, "feed-gate-status.json");
            const info = await stat(gatePath);
            const value = JSON.parse(await readFile(gatePath, "utf8"));
            if (info.isFile() && Date.now() - Number(value.checked_at_ns) / 1e6 < 70000 &&
                value.schema_version === "alpha-grid-feed-gate-v1" &&
                ["CONNECTING", "MARKET CLOSED", "AUTH REQUIRED", "BLOCKED"].includes(value.state)) {
              feedGate = value;
            }
          } catch { /* Feed gate has not reported yet. */ }
        }
        async function workerState(name) {
          if (!simulationDir) return "UNAVAILABLE";
          try {
            const file = resolve(simulationDir, name);
            const info = await stat(file);
            const value = JSON.parse(await readFile(file, "utf8"));
            if (!workerFresh(info, value)) return "UNAVAILABLE";
            return value.state === "FAILED" ? "FAILED" : value.state === "STOPPED" ? "UNAVAILABLE" : "READY";
          } catch { return "STARTING"; }
        }
        const validationPending = gateway?.feed_connected && (
          feedGate?.reason === "RAW_CAPTURE_VALIDATION_PENDING" ||
          snapshot?.no_trade_reasons?.includes("RAW_CAPTURE_VALIDATION_PENDING"));
        return send(response, 200, {
          schema_version: "alpha-grid-capabilities-v1",
          local_service: "READY",
          gateway: gateway ? "READY" : "UNAVAILABLE",
          telemetry_websocket: gateway ? "READY" : "UNAVAILABLE",
          feed: !gateway ? "UNAVAILABLE" : validationPending ? "LIVE CAPTURE" : gateway.feed_connected ? "LIVE" : snapshot?.mode === "OBSERVE_ONLY" ? "STALE" : feedGate?.state ?? "BLOCKED",
          feed_reason: validationPending ? "VALIDATION PENDING" : gateway?.feed_connected ? null : feedGate?.reason ?? (feedScopeConfigured ? "APPROVAL_OR_PREFLIGHT_NOT_SATISFIED" : "NO_APPROVED_FEED_SCOPE"),
          market_data_access: marketAccess ? {
            authorized_feeds: marketAccess.authorized_feeds,
            authorized_security_ids: marketAccess.authorized_security_ids,
            duration: marketAccess.duration, expires_at: marketAccess.expires_at,
            auto_connect: marketAccess.auto_connect, revoked_at: marketAccess.revoked_at,
          } : null,
          standard_socket_count: gateway?.feed_connected && snapshot?.mode === "OBSERVE_ONLY" ? 1 : 0,
          depth_socket_count: !marketAccess?.revoked_at && marketAccess?.auto_connect && marketAccess?.authorized_feeds?.includes("DEPTH_20") && depthStatus?.connected ? 1 : 0,
          depth_frames_received: !marketAccess?.revoked_at && marketAccess?.authorized_feeds?.includes("DEPTH_20") ? depthStatus?.frames_received ?? 0 : 0,
          depth_reason: !marketAccess?.revoked_at && marketAccess?.authorized_feeds?.includes("DEPTH_20") && !depthStatus?.connected ? depthStatus?.reason ?? "DEPTH_NOT_CONNECTED" : null,
          connected_cockpit_clients: gateway?.counters?.connected_clients ?? 0,
          last_genuine_message_ns: gateway?.feed_connected ? rawStatus?.last_frame_received_ns ?? gateway.latest_data_timestamp_ns : null,
          last_raw_capture: savedRawStatus ? {
            feed_session_id: savedRawStatus.feed_session_id,
            frames_received: savedRawStatus.frames_received,
            last_frame_received_ns: savedRawStatus.last_frame_received_ns,
            state: gateway?.feed_connected && rawStatus ? "LIVE CAPTURE" : "SAVED CAPTURE",
          } : null,
          last_genuine_depth_frame_ns: !marketAccess?.revoked_at && marketAccess?.authorized_feeds?.includes("DEPTH_20") ? depthStatus?.last_frame_received_ns ?? null : null,
          simulation: validationPending ? "SIMULATION PAUSED" : await workerState("simulation-status.json"),
          training: await workerState("training-status.json"),
          data_source: validationPending ? "VALIDATION PENDING" : gateway?.feed_connected ? "READY" : "BLOCKED",
          historical_data: gateway ? historicalCapability.state : "UNAVAILABLE",
          all25_archive: all25Archive,
          account_sync: accountSync ? accountSync.snapshot().status : "UNAVAILABLE",
          broker_orders: "BLOCKED",
        });
      }
      if (path === "/trade/market-data-access" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        if (!simulationDir || !universePath)
          return send(response, 503, { error: "Market data controls unavailable" });
        const access = await readMarketDataAccess(resolve(simulationDir, "feed-scope.json"));
        const universe = JSON.parse(await readFile(universePath, "utf8"));
        return send(response, 200, { access, depth_available: depthAvailable,
          universe: universe.instruments.map(({ symbol, security_id }) => ({ symbol, security_id })) });
      }
      if (path === "/trade/dhan/profile" && request.method === "POST") {
        if (origin !== "http://127.0.0.1:5173" || !(await verifySession(request.headers.cookie)))
          return send(response, 403, { error: "Cockpit session required" });
        if (!simulationDir) return send(response, 503, { status: "PROFILE_CHECK_UNAVAILABLE" });
        const result = await profileCheck(simulationDir);
        return send(response, result === "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE" ? 200 : 503, { status: result });
      }
      if (path === "/trade/market-snapshot" && request.method === "POST") {
        if (origin !== "http://127.0.0.1:5173" || !(await verifySession(request.headers.cookie)))
          return send(response, 403, { error: "Cockpit session required" });
        if (!simulationDir || !universePath) return send(response, 503, { error: "REST_SNAPSHOT_UNAVAILABLE" });
        try {
          return send(response, 200, await currentRestSnapshot());
        }
        catch (error) { return send(response, 503, { error: error.message }); }
      }
      if (path === "/trade/market-snapshot" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        return restSnapshotCache
          ? send(response, 200, { ...restSnapshotCache, snapshot_state: Date.now() - restSnapshotAt > 120_000 ? "STALE" : "CURRENT_RECEIPT", refresh_error: restSnapshotError })
          : send(response, 503, { error: restSnapshotError ?? "NO_REST_SNAPSHOT_RECEIVED" });
      }
      if (path === "/trade/market-data-access" && request.method === "PUT") {
        if (origin !== "http://127.0.0.1:5173" || !(await verifySession(request.headers.cookie)))
          return send(response, 403, { error: "Cockpit session required" });
        if (!simulationDir || !universePath)
          return send(response, 503, { error: "Market data controls unavailable" });
        if (!request.headers["content-type"]?.startsWith("application/json"))
          return send(response, 415, { error: "JSON required" });
        let body = "";
        for await (const chunk of request) {
          body += chunk.toString("utf8");
          if (body.length > 1024) return send(response, 413, { error: "Request too large" });
        }
        try {
          const access = await changeMarketDataAccess(
            resolve(simulationDir, "feed-scope.json"), universePath, JSON.parse(body), depthAvailable,
          );
          if (access.auto_connect === false) {
            restSnapshotCache = null;
            restSnapshotAt = 0;
            const stopPath = resolve(simulationDir, "gateway-stop-requested");
            try {
              const handle = await open(stopPath, "wx", 0o600);
              await handle.close();
            } catch (error) {
              if (error.code !== "EEXIST") throw error;
              const info = await lstat(stopPath);
              if (!info.isFile() || info.isSymbolicLink())
                throw new Error("Gateway stop marker invalid");
            }
          }
          return send(response, 200, { access, depth_available: depthAvailable });
        } catch (error) {
          return send(response, 400, { error: error.message });
        }
      }
      if (path === "/trade/training" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        if (!simulationDir) return send(response, 503, { error: "Training worker unavailable" });
        try {
          const file = resolve(simulationDir, "training-status.json");
          const info = await stat(file);
          const value = JSON.parse(await readFile(file, "utf8"));
          if (!workerFresh(info, value)) return send(response, 503, { error: "Training worker stale" });
          return send(response, 200, value);
        } catch { return send(response, 503, { error: "Training worker unavailable" }); }
      }
      if (path === "/trade/exploratory-artifact" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        if (!simulationDir) return send(response, 503, { error: "EXPLORATORY_ARCHIVE_UNAVAILABLE" });
        try {
          const dir = resolve(simulationDir, "training-artifacts");
          const files = (await readdir(dir)).filter((name) => /^[a-f0-9]{32}\.json$/.test(name)).slice(0, 100);
          for (const name of files) {
            const raw = await readFile(resolve(dir, name));
            const artifact = JSON.parse(raw.toString("utf8"));
            if (artifact.instrument !== "NSE_EQ:2885:RELIANCE" || artifact.source_bars !== 45945 ||
                artifact.source_sessions !== 125 || artifact.source_integrity !== "VERIFIED_FOR_PINNED_RESPONSES") continue;
            return send(response, 200, { source: "EXPLORATORY_ARCHIVE", sha256: createHash("sha256").update(raw).digest("hex"),
              source_bars: artifact.source_bars, source_sessions: artifact.source_sessions,
              timing_assumptions_seconds: artifact.assumed_post_close_availability_seconds,
              promotion_eligibility: artifact.promotion_eligibility,
              primary_60m: artifact.results?.["60"]?.["60"] ?? null });
          }
        } catch { /* No verified local exploratory artifact. */ }
        return send(response, 404, { error: "EXPLORATORY_ARCHIVE_UNAVAILABLE" });
      }
      if (path.startsWith("/trade/historical/") && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        const symbol = path.slice("/trade/historical/".length);
        if (!/^[A-Z0-9&]{1,20}$/.test(symbol) || !simulationDir)
          return send(response, 400, { error: "Frozen symbol required" });
        try { return send(response, 200, archivedBySymbol.get(symbol) ?? await localArchive(simulationDir, symbol)); }
        catch { return send(response, 404, { error: "VERIFIED_LOCAL_ARCHIVE_MISSING" }); }
      }
      if (path === "/trade/training" && request.method === "POST") {
        if (origin !== "http://127.0.0.1:5173" || !(await verifySession(request.headers.cookie)))
          return send(response, 403, { error: "Cockpit session required" });
        if (!simulationDir || !universePath)
          return send(response, 503, { error: "Training worker unavailable" });
        try {
          const worker = await stat(resolve(simulationDir, "training-status.json"));
          const value = JSON.parse(await readFile(resolve(simulationDir, "training-status.json"), "utf8"));
          if (!workerFresh(worker, value))
            return send(response, 503, { error: "Training worker stale" });
        } catch { return send(response, 503, { error: "Training worker unavailable" }); }
        if (!request.headers["content-type"]?.startsWith("application/json"))
          return send(response, 415, { error: "JSON required" });
        let body = "";
        for await (const chunk of request) {
          body += chunk.toString("utf8");
          if (body.length > 256) return send(response, 413, { error: "Request too large" });
        }
        let ticker;
        try { ticker = JSON.parse(body).ticker; } catch { ticker = null; }
        const universe = JSON.parse(await readFile(universePath, "utf8"));
        if (typeof ticker !== "string" || (ticker !== "ALL_25" && !universe.instruments.some((item) => item.symbol === ticker)))
          return send(response, 400, { error: "Ticker outside frozen universe" });
        const active = resolve(simulationDir, "training-active.json");
        try { await stat(active); return send(response, 409, { error: "Training job already running" }); }
        catch (error) { if (error.code !== "ENOENT") throw error; }
        const job = { id: randomUUID().replaceAll("-", ""), ticker };
        const file = resolve(simulationDir, "training-request.json");
        let handle;
        try { handle = await open(file, "wx", 0o600); }
        catch (error) {
          if (error.code === "EEXIST") return send(response, 409, { error: "Training job already queued" });
          throw error;
        }
        try { await handle.writeFile(JSON.stringify(job) + "\n"); await handle.sync(); }
        finally { await handle.close(); }
        return send(response, 202, { ...job, state: "QUEUED" });
      }
      if (path === "/trade/simulation" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        if (!simulationDir)
          return send(response, 503, { error: "Simulation worker unavailable" });
        try {
          const file = resolve(simulationDir, "simulation-status.json");
          const info = await stat(file);
          if (!info.isFile() || Date.now() - info.mtimeMs > 5000)
            return send(response, 503, { error: "Simulation worker stale" });
          return send(response, 200, JSON.parse(await readFile(file, "utf8")));
        } catch {
          return send(response, 503, { error: "Simulation worker unavailable" });
        }
      }
      if (path === "/trade/cross-stock-simulation" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        if (!simulationDir)
          return send(response, 503, { error: "Cross-stock archive unavailable" });
        try { return send(response, 200, await readCrossStockSimulation(simulationDir)); }
        catch { return send(response, 404, { error: "VERIFIED_CROSS_STOCK_SIMULATION_MISSING" }); }
      }
      if (path === "/trade/cross-equity-adaptive" && request.method === "GET") {
        if (!(await verifySession(request.headers.cookie)))
          return send(response, 401, { error: "Cockpit session required" });
        if (!simulationDir)
          return send(response, 503, { error: "Adaptive research unavailable" });
        try { return send(response, 200, await readAdaptiveResearch(simulationDir)); }
        catch { return send(response, 404, { error: "VERIFIED_ADAPTIVE_RESEARCH_MISSING" }); }
      }
      if (path === "/trade/simulation/stop" && request.method === "POST") {
        if (origin !== "http://127.0.0.1:5173" ||
            !(await verifySession(request.headers.cookie)))
          return send(response, 403, { error: "Cockpit session required" });
        if (!simulationDir)
          return send(response, 503, { error: "Simulation worker unavailable" });
        const file = resolve(simulationDir, "stop-requested");
        const handle = await open(file, "wx", 0o600).catch((error) => {
          if (error.code === "EEXIST") return null;
          throw error;
        });
        if (handle) {
          await handle.sync();
          await handle.close();
        }
        return send(response, 200, { status: "STOP_REQUESTED" });
      }
      if (request.method === "POST" && path === "/trade/session/local") {
        if (origin !== "http://127.0.0.1:5173" ||
            request.headers["x-alpha-grid-local"] !== "1" ||
            request.socket.remoteAddress !== "127.0.0.1" || !sessionSecret)
          return send(response, 403, { error: "Local sign-in unavailable" });
        let cookie;
        try { cookie = await issueSession(sessionSecret); }
        catch { return send(response, 503, { error: "Cockpit sign-in unavailable" }); }
        if (!cookie?.startsWith("alpha_grid_session=") ||
            !cookie.includes("HttpOnly") || !cookie.includes("SameSite=Strict"))
          return send(response, 503, { error: "Cockpit sign-in unavailable" });
        response.writeHead(204, { "Set-Cookie": cookie, "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff" });
        return response.end();
      }
      if (request.method === "POST" && path === "/trade/session/bootstrap") {
        if (
          origin !== "http://127.0.0.1:5173" ||
          request.headers["content-type"] !== "application/json" ||
          !pendingBootstrap ||
          !sessionSecret
        ) {
          return send(response, 403, { error: "Local sign-in unavailable" });
        }
        let body = "";
        for await (const chunk of request) {
          body += chunk.toString("utf8");
          if (body.length > 256)
            return send(response, 413, { error: "Request too large" });
        }
        let candidate;
        try {
          candidate = JSON.parse(body).token;
        } catch {
          candidate = null;
        }
        if (
          typeof candidate !== "string" ||
          !/^[0-9a-f]{64}$/.test(candidate) ||
          !timingSafeEqual(
            Buffer.from(candidate),
            Buffer.from(pendingBootstrap),
          )
        ) {
          return send(response, 403, { error: "Local sign-in unavailable" });
        }
        pendingBootstrap = null;
        let cookie;
        try {
          cookie = await issueSession(sessionSecret);
        } catch {
          return send(response, 503, { error: "Cockpit sign-in unavailable" });
        }
        if (
          !cookie ||
          !cookie.startsWith("alpha_grid_session=") ||
          !cookie.includes("HttpOnly") ||
          !cookie.includes("SameSite=Strict")
        ) {
          return send(response, 503, { error: "Cockpit sign-in unavailable" });
        }
        response.writeHead(204, {
          "Set-Cookie": cookie,
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        });
        return response.end();
      }
      if (request.method === "GET" && path === "/trade/status") {
        return send(response, 200, {
          execution: "UNAVAILABLE",
          account:
            accountOffline && accountSync
              ? "CACHE_ONLY"
              : (accountSync?.snapshot().status ?? "NOT_CONNECTED"),
          marketFeed: onlineRequested
            ? "ONLINE_REQUESTED"
            : "NOT_CONNECTED",
          research: "NOT_CONNECTED",
          reason:
            "Trade-candidate output and broker execution are inactive; read-only market and account data are independent",
        });
      }
      if (request.method === "GET" && path === "/trade/account") {
        if (!accountSync)
          return send(response, 503, { error: "Account sync unavailable" });
        if (!(await verifySession(request.headers.cookie))) {
          return send(response, 401, { error: "Cockpit session required" });
        }
        const account = accountSync.snapshot();
        return send(
          response,
          200,
          accountOffline ? { ...account, status: "CACHE_ONLY" } : account,
        );
      }
      if (request.method === "GET" && path === "/trade/settings") {
        return send(response, 200, {
          settings: await readSettings(settingsPath),
        });
      }
      if (request.method === "PUT" && path === "/trade/settings") {
        if (!request.headers["content-type"]?.startsWith("application/json")) {
          return send(response, 415, { error: "JSON required" });
        }
        let body = "";
        for await (const chunk of request) {
          body += chunk.toString("utf8");
          if (body.length > 16384)
            return send(response, 413, { error: "Request too large" });
        }
        let settings;
        try {
          settings = validateSettings(JSON.parse(body));
        } catch (error) {
          return send(response, 400, { error: error.message });
        }
        return send(response, 200, {
          settings: await saveSettings(settingsPath, settings),
        });
      }
      send(response, 404, { error: "Not found" });
    } catch (error) {
      console.error(error);
      send(response, 500, { error: "Trading service error" });
    }
  });
  if (autoRestSnapshots) {
    server.on("listening", () => {
      void warmArchivedUniverse();
      void (async () => {
        if (simulationDir && universePath) {
          try {
            const saved = await readSavedRestSnapshot(simulationDir, universePath);
            if (saved && (!restSnapshotCache || Date.parse(saved.received_at) > restSnapshotAt)) {
              restSnapshotCache = saved;
              restSnapshotAt = Date.parse(saved.received_at);
            }
          } catch { restSnapshotError = "SAVED_REST_SNAPSHOT_INTEGRITY_FAILED"; }
        }
        // One bounded startup refresh also works after the market closes; the
        // regular one-minute poll remains confined to the session window.
        try { await currentRestSnapshot(true); } catch { /* Saved evidence remains available. */ }
      })();
      const timer = setInterval(() => void pollRestSnapshot(), 60_000);
      timer.unref();
      server.on("close", () => clearInterval(timer));
    });
  }
  return server;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const settingsPath = resolve(
    process.env.ALPHA_GRID_TRADING_SETTINGS ??
      resolve(
        dirname(fileURLToPath(import.meta.url)),
        "../../.local/trading/settings.json",
      ),
  );
  const accountSync =
    process.env.ALPHA_GRID_ACCOUNT_SYNC === "1"
      ? createAccountSync({ endpoints: process.env.ALPHA_GRID_ACCOUNT_READ_ONLY_LIMITED === "1"
        ? ["fundlimit", "holdings", "positions"] : undefined }) : null;
  const bootstrapToken = process.env.ALPHA_GRID_UI_BOOTSTRAP_TOKEN;
  const sessionSecret = process.env.ALPHA_GRID_UI_SESSION_SECRET;
  delete process.env.ALPHA_GRID_UI_BOOTSTRAP_TOKEN;
  delete process.env.ALPHA_GRID_UI_SESSION_SECRET;
  createTradingService(settingsPath, {
    accountSync,
    accountOffline: process.env.ALPHA_GRID_ACCOUNT_OFFLINE === "1",
    onlineRequested: process.env.ALPHA_GRID_ONLINE_MODE === "1",
    bootstrapToken,
    sessionSecret,
    simulationDir: process.env.ALPHA_GRID_SIMULATION_DIR ?? null,
    universePath: process.env.ALPHA_GRID_UNIVERSE_PATH ?? null,
    depthAvailable: process.env.ALPHA_GRID_DEPTH_AVAILABLE === "1",
    autoRestSnapshots: true,
  }).listen(8767, "127.0.0.1", () => {
    console.log("Trading settings service on http://127.0.0.1:8767");
  });
  if (accountSync) {
    if (process.env.ALPHA_GRID_ACCOUNT_OFFLINE === "1") {
      void accountSync.load().catch(() => {});
    } else {
      void accountSync
        .load()
        .then(() => accountSync.refresh())
        .catch(() => {});
      setInterval(() => {
        void accountSync.refresh().catch(() => {});
      }, 60_000).unref();
    }
  }
}
