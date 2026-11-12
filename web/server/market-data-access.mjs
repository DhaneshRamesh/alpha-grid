import { createHash } from "node:crypto";
import { lstat, open, readFile, rename, stat } from "node:fs/promises";
import { dirname } from "node:path";

const VERSION = "alpha-grid-market-data-access-v2";
const todayIST = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());

export async function readMarketDataAccess(path) {
  try {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink() || info.uid !== process.getuid?.() || (info.mode & 0o077))
      throw new Error("Market data approval permissions invalid");
    const value = JSON.parse(await readFile(path, "utf8"));
    if (value.schema_version !== VERSION) throw new Error("Market data approval schema invalid");
    return value;
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function writeAccess(path, value) {
  const directory = await stat(dirname(path));
  if (!directory.isDirectory() || directory.uid !== process.getuid?.() || (directory.mode & 0o077))
    throw new Error("Private market data directory invalid");
  const temporary = `${path}.${process.pid}.${Date.now()}.tmp`;
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify(value) + "\n");
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(temporary, path);
  return value;
}

export async function changeMarketDataAccess(path, universePath, input, depthAvailable = false) {
  if (!input || typeof input !== "object" || Array.isArray(input))
    throw new Error("Market data action required");
  const previous = await readMarketDataAccess(path);
  const now = new Date().toISOString();
  if (input.action === "approve") {
    if (input.confirmed !== true || input.universe !== "FROZEN_25_NSE_EQ" ||
        input.standard_feed !== true || typeof input.depth_feed !== "boolean" ||
        !["QUOTE", "FULL"].includes(input.standard_mode ?? "QUOTE") ||
        !["SESSION_ONLY", "PERSISTENT"].includes(input.duration))
      throw new Error("Exact read-only standard feed scope must be confirmed");
    if (input.depth_feed && !depthAvailable)
      throw new Error("Supervised 20-level depth collector unavailable");
    if (input.expires_at !== null && input.expires_at !== undefined &&
        (typeof input.expires_at !== "string" || !Number.isFinite(Date.parse(input.expires_at)) ||
         Date.parse(input.expires_at) <= Date.now()))
      throw new Error("Approval expiry must be a future timestamp");
    const raw = await readFile(universePath);
    const universe = JSON.parse(raw);
    const ids = universe.instruments.map((item) => item.security_id);
    if (ids.length !== 25 || new Set(ids).size !== 25)
      throw new Error("Frozen 25-stock universe invalid");
    return writeAccess(path, {
      schema_version: VERSION, purpose: "SIMULATION_ONLY", segment: "NSE_EQ",
      universe_sha256: createHash("sha256").update(raw).digest("hex"),
      authorized_security_ids: ids,
      authorized_feeds: [input.standard_mode === "FULL" ? "STANDARD_FULL" : "STANDARD_QUOTE",
        ...(input.depth_feed ? ["DEPTH_20"] : [])],
      standard_connections: 1, depth_connections: input.depth_feed ? 1 : 0,
      duration: input.duration, session_date_ist: input.duration === "SESSION_ONLY" ? todayIST() : null,
      expires_at: input.expires_at || null, auto_connect: input.auto_connect === true,
      authorization_confirmed: true, data_entitlement_confirmed: true,
      authorized_at: now, revoked_at: null,
    });
  }
  if (!previous || previous.revoked_at) throw new Error("No active approval to change");
  if (input.action === "connect" || input.action === "disconnect") {
    if (input.action === "connect" &&
        ((previous.duration === "SESSION_ONLY" && previous.session_date_ist !== todayIST()) ||
         (previous.expires_at && Date.parse(previous.expires_at) <= Date.now())))
      throw new Error("Market data approval expired; approve a new scope");
    return writeAccess(path, { ...previous, auto_connect: input.action === "connect", updated_at: now });
  }
  if (input.action === "revoke") {
    return writeAccess(path, { ...previous, auto_connect: false, authorization_confirmed: false,
      revoked_at: now, updated_at: now });
  }
  throw new Error("Unsupported market data action");
}
