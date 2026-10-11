# Alpha Grid

Python 3.12+ foundation for a cloud-native NSE research and trading platform. It
now includes a Dhan 20-level depth source that emits broker-independent
`DepthEvent` records. It has no strategy or order API.

## Set up

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy
```

`quant-diagnostics` validates the environment settings and writes a JSON startup
log. Copy `.env.example` to `.env` if useful, then export its variables; the
application deliberately does not load `.env` files or embed credentials.

## Collect Dhan 20-level depth

```sh
python -m pip install -e '.[dev,dhan]'
cp .env.example .env
# Edit .env to set DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, and the instrument list.
set -a; . ./.env; set +a
python -m quant.apps.collect_depth
```

The collector emits one normalized JSON `DepthEvent` per line on stdout. Its
connection, reconnect, packet, event, malformed-packet, and disconnect counters
are available through `MarketDepthFeed.counters`; operational JSON logs go to
stderr. It reconnects with a capped exponential delay. Explicit Dhan server
rejection codes 806–809 stop the collector; connection errors retry with the
capped delay. No account is needed to run the tests.

The example config contains 20 NSE equity security IDs checked against Dhan's
[instrument master](https://dhanhq.co/docs/v2/instruments/). Replace or reduce
the list as needed; one connection supports up to 50 distinct NSE equity or
derivatives instruments. IDs should be checked again before use because the
instrument master can change.

Dhan sends separate 20-level bid and ask packets. The adapter pairs only recent
halves for the same instrument and drops malformed or incomplete packets. The
receive timestamp is captured immediately after each WebSocket receive, before
decoding. The [documented 20-level packet](https://dhanhq.co/docs/v2/full-market-depth/)
does not contain an exchange timestamp, so `exchange_timestamp` is normally
`null`; a timestamp is retained if a future decoder supplies one. The header
sequence is attached only when the two sides carry the same value. Dhan's
documentation says to ignore that sequence, so consumers should not use it as
a gap detector without live verification. Empty price levels are retained as
zero-price, zero-quantity placeholders so supplied 20-level frames remain 20
levels long.
These packet assumptions were checked against DhanHQ-py 2.3.0 and its published
protocol; they have not been checked against a live account session.

## Layout and contracts

- `quant.domain`: immutable instrument, depth, trade, and feed-health records.
- `quant.config`: typed settings loaded from `QUANT_` environment variables.
- `quant.logging`: UTC JSON logs for applications and services.
- `quant.feeds`: asynchronous normalized market-event source protocol and Dhan
  depth adapter. The adapter uses the official DhanHQ decoder and a direct
  WebSocket receive path to timestamp packets before decoding.
- `quant.storage`: asynchronous normalized market-event sink protocol.
- `quant.health`: feed freshness assessment.
- `quant.apps`: operational entry points only.

`DepthEvent` retains venue, exchange segment, instrument ID, optional exchange
timestamp, local receive timestamp, bid and ask levels, and optional sequence
number. Timestamps must include a timezone; prices use `Decimal`. Depth levels
are immutable tuples. Instrument IDs remain strings so exchange identifiers are
never silently coerced or truncated.

The intended later flow is market data → normalized events → research models →
risk and optimization → execution and OMS. Those later components are deliberately
outside this foundation. No live orders can be placed from this repository.
