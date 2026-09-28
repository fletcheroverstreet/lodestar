"""Tests for the News bucket protocol and null provider."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lodestar.config import load_config
from lodestar.metrics.base import TickerContext
from lodestar.metrics.news import NewsSignal, NullNewsProvider, news_sentiment

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


def _ctx() -> TickerContext:
    return TickerContext(ticker="TEST", as_of="2026-01-01", cik=1,
                          fundamentals=pd.DataFrame(), prices=pd.DataFrame(),
                          sector="s", industry="i")


def test_null_provider_reports_zero_coverage_for_every_ticker():
    provider = NullNewsProvider()
    result = provider.get_signal(["AAPL", "MSFT"], as_of="2026-01-01")
    assert set(result) == {"AAPL", "MSFT"}
    for signal in result.values():
        assert signal.coverage == 0.0
        assert signal.score is None
        assert signal.event_count == 0


def test_news_sentiment_unavailable_with_null_provider_signal(cfg):
    provider = NullNewsProvider()
    signal = provider.get_signal(["TEST"], as_of="2026-01-01")["TEST"]
    result = news_sentiment(_ctx(), cfg, signal=signal)
    assert result.value is None


def test_news_sentiment_unavailable_with_no_signal_passed(cfg):
    result = news_sentiment(_ctx(), cfg)
    assert result.value is None


def test_news_sentiment_reports_score_when_a_real_signal_is_provided(cfg):
    signal = NewsSignal(ticker="TEST", score=0.42, event_count=17, coverage=0.9)
    result = news_sentiment(_ctx(), cfg, signal=signal)
    assert result.value == 0.42
    assert result.raw_inputs["event_count"] == 17
    assert result.raw_inputs["coverage"] == 0.9


# ---------------------------------------------------------------------------
# The real provider (finlake 0.6.0+). DEC-008 predicted wiring one in would
# cost a class and a line of config; these guard that it stayed that cheap
# AND that it did not quietly trade honesty for coverage.
# ---------------------------------------------------------------------------
import dataclasses  # noqa: E402

from lodestar.metrics.news import (  # noqa: E402
    FinlakeNewsProvider, NullNewsProvider, build_provider,
)


class _FakeAdapter:
    """Stands in for finlake so these stay offline and deterministic."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_news_signal(self, tickers, *, as_of, lookback_days, half_life_days):
        self.calls.append((tuple(tickers), as_of, lookback_days, half_life_days))
        return self.payload


def _with_adapter(monkeypatch, payload):
    from lodestar.metrics import news as news_module

    fake = _FakeAdapter(payload)
    monkeypatch.setattr(news_module, "adapter", fake)
    return fake


def test_provider_translates_finlake_signal(monkeypatch):
    _with_adapter(monkeypatch, {
        "AAPL": {"score": -0.21, "articles": 114, "coverage": 1.0},
    })
    out = FinlakeNewsProvider().get_signal(["AAPL"], as_of="2026-08-09")
    assert out["AAPL"].score == -0.21
    assert out["AAPL"].event_count == 114
    assert out["AAPL"].coverage == 1.0


def test_a_ticker_with_no_news_is_not_given_a_neutral_score(monkeypatch):
    """The whole reason the bucket was left empty rather than filled with
    zeros. A fabricated 0.0 places a name we know nothing about at exactly
    the peer-group mean, which reads as a real finding rather than as an
    absence of one."""
    _with_adapter(monkeypatch, {})            # finlake returned nothing at all
    out = FinlakeNewsProvider().get_signal(["QUIET"], as_of="2026-08-09")
    assert out["QUIET"].score is None, "a no-news ticker was scored as neutral"
    assert out["QUIET"].coverage == 0.0
    assert out["QUIET"].event_count == 0


def test_provider_asks_finlake_once_for_the_whole_universe(monkeypatch):
    """Batched by design: a per-ticker call would mean 500 passes over the
    news table for one run."""
    fake = _with_adapter(monkeypatch, {})
    FinlakeNewsProvider().get_signal(["A", "B", "C"], as_of="2026-08-09")
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == ("A", "B", "C")


def test_provider_passes_the_configured_windows(monkeypatch):
    fake = _with_adapter(monkeypatch, {})
    FinlakeNewsProvider(lookback_days=30, half_life_days=5).get_signal(
        ["A"], as_of="2026-08-09")
    _tickers, _as_of, lookback, half_life = fake.calls[0]
    assert lookback == 30 and half_life == 5


def test_a_missing_score_survives_a_present_entry(monkeypatch):
    """finlake returns an entry with score=None for a ticker whose articles
    all lacked sentiment vocabulary. That must stay None, not become 0.0 —
    the articles count as coverage volume but not as tone."""
    _with_adapter(monkeypatch, {
        "T": {"score": None, "articles": 4, "coverage": 0.0},
    })
    out = FinlakeNewsProvider().get_signal(["T"], as_of="2026-08-09")
    assert out["T"].score is None
    assert out["T"].event_count == 4


def test_build_provider_selects_from_config():
    cfg = load_config(REPO_CONFIG)
    assert isinstance(build_provider(cfg), FinlakeNewsProvider)

    nulled = dataclasses.replace(cfg, buckets={
        **cfg.buckets,
        "news": dataclasses.replace(cfg.bucket("news"),
                                    params={**cfg.bucket("news").params,
                                            "provider": None}),
    })
    assert isinstance(build_provider(nulled), NullNewsProvider)


def test_an_unknown_provider_name_fails_loudly():
    """A typo'd provider that silently fell back to zero coverage would be
    indistinguishable from having no news at all, and would look like a data
    problem for as long as it took someone to notice."""
    cfg = load_config(REPO_CONFIG)
    broken = dataclasses.replace(cfg, buckets={
        **cfg.buckets,
        "news": dataclasses.replace(cfg.bucket("news"),
                                    params={**cfg.bucket("news").params,
                                            "provider": "finlakke"}),
    })
    with pytest.raises(ValueError, match="unknown news.provider"):
        build_provider(broken)
