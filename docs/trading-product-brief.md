# Alpha Grid trading product brief

Captured 2026-10-21 from the owner's stated goal. This describes the intended
product and the first settings implementation. Further requirements are
expected. No profitable stock pick or order outcome can be guaranteed.

## Primary workflow

1. Connect the owner's brokerage account and show available trading funds,
   holdings, open positions and orders with their source and last refresh time.
2. Examine current NSE cash-equity data across a defined eligible universe and
   rank possible trades. Display the market evidence, costs, risks, data age and
   an OpenAI-assisted research explanation for each candidate. Clearly separate
   broker quotes from web/news research, and show when evidence is missing.
3. With automation enabled and configured limits satisfied, choose a candidate
   and place a BUY or SELL order using account funds or owned shares. Accept
   stop-loss and profit-target settings as an absolute INR price or a percentage
   of a clearly identified entry/reference price. Record the resulting prices,
   quantity, estimated capital/charges, input evidence and reason for the
   decision. Reconcile broker order status, fills and positions afterward.

The owner wants the service to choose stocks and place trades automatically
through Dhan, intraday only. The owner will configure per-trade and daily-loss
limits in a separate tab instead of choosing fixed values now. Further
requirements are expected. The equity universe, position sizing, activation
controls and permissible order types remain open. Automatic execution remains
disabled until required limits are configured and account, broker access,
market-data freshness and reconciliation are verified.

## Current implementation gap

The UI now opens on a Trade desk that explicitly reports unavailable live
account, market, research and execution services. A separate Trading settings
tab saves four required positive limits using a local settings API and displays
that saving does not activate trading. The old two-instrument historical
synthetic replay is disabled outside software tests. The Rust paper OMS simulates
fills against QJ01 and writes QP01; it does not transmit orders. Python Dhan
acquisition is offline/read-only. The separate API-key callback service stores
a Dhan token and quarantines postbacks. A separate read-only route can fetch
Dhan funds, holdings count and NSE intraday positions with an exported client
ID and access token, or the saved callback token. It fails closed when those
are absent, the broker request fails or the client ID does not match. No order
route, OpenAI-backed research integration or live candidate scanner exists.

Superseding status, 2026-10-24: the account route now returns 503 without
calling Dhan, and the trade desk shows `DATA UNAVAILABLE / NO TRADE`. The
preceding read-only account-route description is historical, not active.

## Contract notes for implementation

- Dhan's [fund limits](https://dhanhq.co/docs/v2/funds/) endpoint reads available
  balance; [portfolio](https://dhanhq.co/docs/v2/portfolio/) reads holdings and
  positions. These must be fetched from the broker, not inferred from paper cash.
- Dhan's [market quote](https://dhanhq.co/docs/v2/market-quote/) and
  [live feed](https://dhanhq.co/docs/v2/live-market-feed/) support current prices.
  The [instrument list](https://dhanhq.co/docs/v2/instruments/) maps symbols to
  security IDs. The current replay's two symbols are not a stock universe.
- Dhan's [Super Order](https://dhanhq.co/docs/v2/super-order/) accepts an entry,
  targetPrice and stopLossPrice together. The broker documents static IP
  whitelisting for placement; [authentication](https://dhanhq.co/docs/v2/authentication/)
  is a separate prerequisite. Percentage inputs require conversion to permitted
  absolute prices using a fresh reference quote and the instrument's price
  increment. The broker response must be reconciled with order and trade reads;
  an unverified postback cannot prove a fill. Orders need a durable local
  intent/correlation ID, duplicate prevention, and a stop switch, because the
  workflow is automatic.
- Official [OpenAI Docs](https://developers.openai.com/api/docs/guides/tools-web-search)
  describe Responses API web search for current sourced research and require
  clickable citations when web findings are shown. The
  [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
  describes a schema for suggestions. Keep the API key on the server, as in the
  [API reference](https://developers.openai.com/api/reference/overview).
  Research output is explanatory input; broker identity, market prices, sizing
  and order limits must be checked against trusted application data. Missing,
  stale or unsupported inputs block an automatic trade.

## Acceptance path

First replace the replay-centered home workflow with a trading workspace that
distinguishes live, disconnected and unavailable states. Add read-only account
and NSE market-data integrations with source timestamps, then candidate research
with evidence and data-age checks. Build an inspectable automatic order plan
with both stop-loss and profit-target formats and test price conversion, bounds,
account availability, duplicate prevention and broker reconciliation. Enable
live order submission only after the owner resolves open limits and holding
period choices and the prerequisites have been verified. Do not mistake the
existing synthetic fixture for live market coverage or validated pick quality.
