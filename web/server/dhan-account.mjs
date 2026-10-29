import { readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export async function loadDhanCredentials(tokenPath) {
  const clientId = process.env.DHAN_CLIENT_ID?.trim();
  let token = process.env.DHAN_ACCESS_TOKEN?.trim();
  if (!token) {
    try {
      token = (await readFile(tokenPath, "utf8")).trim();
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  return clientId && token ? { clientId, token } : null;
}

function amount(value, name) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value >= 1e12
  ) {
    throw new Error(`Invalid Dhan ${name}`);
  }
  return value.toFixed(2);
}

export async function fetchDhanAccount(credentials, fetcher = fetch) {
  const headers = {
    "access-token": credentials.token,
    Accept: "application/json",
  };
  const endpoints = ["fundlimit", "holdings", "positions"];
  const responses = await Promise.all(
    endpoints.map((name) =>
      fetcher(`https://api.dhan.co/v2/${name}`, {
        headers,
        signal: AbortSignal.timeout(8000),
        cache: "no-store",
      }),
    ),
  );
  if (responses.some((response) => !response.ok)) {
    throw new Error("Dhan account request failed");
  }
  const [funds, holdings, positions] = await Promise.all(
    responses.map((response) => response.json()),
  );
  if (
    !funds ||
    funds.dhanClientId !== credentials.clientId ||
    !Array.isArray(holdings) ||
    !Array.isArray(positions)
  ) {
    throw new Error(
      "Dhan account response did not match the connected account",
    );
  }
  return {
    source: "DHAN",
    asOf: new Date().toISOString(),
    availableFundsInr: amount(
      funds.availabelBalance ?? funds.availableBalance,
      "available balance",
    ),
    utilizedFundsInr: amount(funds.utilizedAmount, "utilized amount"),
    holdingsCount: holdings.length,
    intradayPositions: positions
      .filter(
        (item) =>
          item.exchangeSegment === "NSE_EQ" && item.productType === "INTRADAY",
      )
      .map((item) => {
        if (!Number.isSafeInteger(item.netQty))
          throw new Error("Invalid Dhan position quantity");
        return {
          symbol:
            typeof item.tradingSymbol === "string"
              ? item.tradingSymbol
              : "UNKNOWN",
          securityId: String(item.securityId),
          netQty: item.netQty,
        };
      }),
  };
}

export function defaultTokenPath() {
  return resolve(
    dirname(fileURLToPath(import.meta.url)),
    "../../data/.secrets/dhan-access-token",
  );
}

export const APPROVED_ACCOUNT_ENDPOINTS = [
  "fundlimit",
  "holdings",
  "positions",
  "orders",
  "trades",
];

export function tokenExpiryFuture(token, nowSeconds = Date.now() / 1000) {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return false;
    const claims = JSON.parse(
      Buffer.from(parts[1], "base64url").toString("utf8"),
    );
    return Number.isSafeInteger(claims.exp) && claims.exp > nowSeconds + 60;
  } catch {
    return false;
  }
}

async function boundedResponseBytes(response) {
  const limit = 2 * 1024 * 1024;
  if (!response.body) {
    const raw = Buffer.from(await response.arrayBuffer());
    if (raw.length > limit) throw new Error("Account response too large");
    return raw;
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.byteLength;
    if (size > limit) {
      await response.body.cancel().catch(() => {});
      throw new Error("Account response too large");
    }
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks, size);
}

export function accountCacheCommand(operation, name, input) {
  if (!APPROVED_ACCOUNT_ENDPOINTS.includes(name))
    throw new Error("Unsupported account endpoint");
  return new Promise((resolvePromise, reject) => {
    const child = spawn(
      process.env.ALPHA_GRID_PYTHON ?? ".venv\\Scripts\\python.exe",
      ["-m", "quant.apps.dhan_account_cache", operation, name],
      {
        env: { ...process.env, PYTHONPATH: "src" },
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    let output = "";
    child.stdout.on("data", (part) => {
      output += part.toString("utf8");
      if (output.length > 2_000_000) child.kill();
    });
    // Never surface child stderr: private storage errors may include paths.
    child.stderr.resume();
    child.on("error", () =>
      reject(new Error("Private account cache unavailable")),
    );
    child.on("close", (code) => {
      if (code !== 0)
        return reject(new Error("Private account cache unavailable"));
      try {
        resolvePromise(JSON.parse(output));
      } catch {
        reject(new Error("Private account cache invalid"));
      }
    });
    child.stdin.end(input ? JSON.stringify(input) : "");
  });
}

export function createAccountSync({
  tokenPath = defaultTokenPath(),
  cacheCommand = accountCacheCommand,
  fetcher = fetch,
  now = () => Date.now(),
  pause = (ms) => new Promise((resolvePause) => setTimeout(resolvePause, ms)),
} = {}) {
  const panels = Object.fromEntries(
    APPROVED_ACCOUNT_ENDPOINTS.map((name) => [
      name,
      { endpoint: name, status: "DATA_UNAVAILABLE" },
    ]),
  );
  let running = false;
  let reauthenticationRequired = false;
  let blockedTokenMtimeNs = null;
  let storageBlocked = false;
  let lastRequestStarted = 0;

  async function load() {
    for (const name of APPROVED_ACCOUNT_ENDPOINTS) {
      try {
        panels[name] = {
          ...(await cacheCommand("load", name)),
          delivery: "CACHE_ONLY",
        };
        if (panels[name].reauthentication_required) {
          reauthenticationRequired = true;
          blockedTokenMtimeNs = panels[name].blocked_token_mtime_ns ?? null;
        }
      } catch {
        panels[name] = {
          endpoint: name,
          status: "DATA_UNAVAILABLE",
          reason: "CACHE_UNVERIFIABLE",
        };
      }
    }
  }

  async function refresh() {
    if (running || storageBlocked) return;
    running = true;
    try {
      const privateToken = await cacheCommand("credential", "fundlimit").catch(
        () => null,
      );
      if (
        !privateToken?.private_token_present ||
        !privateToken.metadata_valid
      ) {
        reauthenticationRequired = true;
        return;
      }
      if (
        reauthenticationRequired &&
        (!blockedTokenMtimeNs ||
          privateToken.token_mtime_ns === blockedTokenMtimeNs)
      )
        return;
      reauthenticationRequired = false;
      const credentials = await loadDhanCredentials(tokenPath);
      if (!credentials || !tokenExpiryFuture(credentials.token, now() / 1000)) {
        reauthenticationRequired = true;
        return;
      }
      for (const name of APPROVED_ACCOUNT_ENDPOINTS) {
        if (reauthenticationRequired || storageBlocked) break;
        let reserved;
        try {
          reserved = (await cacheCommand("reserve", name)).reserved;
        } catch {
          storageBlocked = true;
          panels[name] = {
            endpoint: name,
            status: "DATA_UNAVAILABLE",
            reason: "CACHE_UNVERIFIABLE",
          };
          break;
        }
        if (!reserved) continue;
        const elapsed = now() - lastRequestStarted;
        if (lastRequestStarted && elapsed < 1000) await pause(1000 - elapsed);
        lastRequestStarted = now();
        try {
          const response = await fetcher(`https://api.dhan.co/v2/${name}`, {
            method: "GET",
            headers: {
              "access-token": credentials.token,
              Accept: "application/json",
            },
            signal: AbortSignal.timeout(8000),
            cache: "no-store",
          });
          const body = await boundedResponseBytes(response);
          try {
            await cacheCommand("save", name, {
              status: response.status,
              received_at: new Date(now()).toISOString(),
              body_base64: body.toString("base64"),
            });
          } catch {
            storageBlocked = true;
            break;
          }
          if (response.status === 401) {
            reauthenticationRequired = true;
            blockedTokenMtimeNs = privateToken.token_mtime_ns ?? null;
          }
          try {
            const loaded = await cacheCommand("load", name);
            panels[name] = {
              ...loaded,
              delivery:
                response.status === 200 &&
                loaded.status === "AVAILABLE" &&
                loaded.last_http_status === 200
                  ? "LIVE"
                  : "CACHE_ONLY",
            };
          } catch {
            panels[name] = {
              endpoint: name,
              status: "DATA_UNAVAILABLE",
              reason: "CACHE_UNVERIFIABLE",
            };
          }
        } catch {
          try {
            await cacheCommand("failure", name, {
              received_at: new Date(now()).toISOString(),
            });
          } catch {
            storageBlocked = true;
          }
          panels[name] = {
            ...panels[name],
            reason: "REQUEST_OR_STORAGE_FAILED",
          };
        }
      }
    } finally {
      running = false;
    }
  }

  return {
    load,
    refresh,
    snapshot: () => ({
      status: storageBlocked
        ? "STORAGE_BLOCKED"
        : reauthenticationRequired
          ? "REAUTHENTICATION_REQUIRED"
          : "READ_ONLY",
      panels: structuredClone(panels),
      refreshed_at: new Date(now()).toISOString(),
    }),
  };
}
