"""Feed adapter contracts; concrete NSE adapters belong in later work."""

from quant.feeds.base import FeedCounters, MarketDataFeed, MarketDepthFeed

__all__ = ["FeedCounters", "MarketDataFeed", "MarketDepthFeed"]
