import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { mkdir, readFile, rename, open } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createAccountSync } from "./dhan-account.mjs";

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
  } = {},
) {
  let pendingBootstrap = /^[0-9a-f]{64}$/.test(bootstrapToken ?? "")
    ? bootstrapToken
    : null;
  return createServer(async (request, response) => {
    try {
      const origin = request.headers.origin;
      if (
        origin &&
        !["http://127.0.0.1:5173", "http://localhost:5173"].includes(origin)
      ) {
        return send(response, 403, { error: "Origin not allowed" });
      }
      const path = new URL(request.url, "http://127.0.0.1").pathname;
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
            : accountOffline
              ? "CACHE_ONLY"
              : "NOT_CONNECTED",
          research: "NOT_CONNECTED",
          reason:
            "Market scanner, research and broker execution are not connected; account reads remain read-only",
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
    process.env.ALPHA_GRID_ACCOUNT_SYNC === "1" ? createAccountSync() : null;
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
