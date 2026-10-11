# Alpha Grid

Python 3.12+ foundation for a cloud-native NSE research and trading platform. This
repository contains only configuration, normalized market-event contracts, logging,
feed/storage interfaces, and a feed-health helper. It has no strategy or order API.

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

## Layout and contracts

- `quant.domain`: immutable instrument, depth, trade, and feed-health records.
- `quant.config`: typed settings loaded from `QUANT_` environment variables.
- `quant.logging`: UTC JSON logs for applications and services.
- `quant.feeds`: asynchronous normalized market-event source protocol.
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
