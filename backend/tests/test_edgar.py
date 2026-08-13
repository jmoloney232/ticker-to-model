"""EDGAR client: etiquette, rate limiting, and the degradation ladder (spec 01 §3)."""

import gzip
import json
import time

import httpx
import pytest

from ingest.cache import SqliteCache
from ingest.edgar import EdgarClient, RateLimiter
from ingest.errors import EdgarUnavailableError

UA = "TickerToModel-tests (test@example.com)"


def test_user_agent_with_contact_required():
    with pytest.raises(ValueError):
        EdgarClient(user_agent="")
    with pytest.raises(ValueError):
        EdgarClient(user_agent="just-a-name-no-contact")


def test_rate_limiter_spaces_requests():
    rl = RateLimiter(per_second=100)
    t0 = time.monotonic()
    for _ in range(4):
        rl.wait()
    assert time.monotonic() - t0 >= 0.03    # 3 gaps at >=10ms each


def _client(tmp_path, handler, fixture_dir=None):
    return EdgarClient(
        user_agent=UA,
        cache=SqliteCache(tmp_path / "cache.sqlite"),
        fixture_dir=fixture_dir,
        rate_limiter=RateLimiter(per_second=10_000),
        transport=httpx.MockTransport(handler),
    )


def test_live_fetch_sends_user_agent_and_caches(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("User-Agent")
        seen["calls"] = seen.get("calls", 0) + 1
        return httpx.Response(200, json={"ok": True})

    client = _client(tmp_path, handler)
    payload, tier = client.get_submissions(123)
    assert payload == {"ok": True} and tier == "live"
    assert seen["ua"] == UA

    payload, tier = client.get_submissions(123)   # second call served from cache
    assert tier == "cache" and seen["calls"] == 1


def test_outage_falls_back_to_stale_cache(tmp_path, monkeypatch):
    state = {"up": True}

    def handler(request):
        if state["up"]:
            return httpx.Response(200, json={"v": 1})
        return httpx.Response(503)

    client = _client(tmp_path, handler)
    client.get_submissions(123)
    state["up"] = False
    monkeypatch.setattr("ingest.edgar.FRESH_TTL_S", -1)     # force staleness
    monkeypatch.setattr("ingest.edgar.time.sleep", lambda s: None)
    payload, tier = client.get_submissions(123)
    assert payload == {"v": 1} and tier == "stale_cache"


def test_outage_falls_back_to_snapshot(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(503)

    fixdir = tmp_path / "fixtures"
    tdir = fixdir / "syn"
    tdir.mkdir(parents=True)
    (fixdir / "manifest.json").write_text(json.dumps({"SYN": 123}))
    (tdir / "submissions.json.gz").write_bytes(
        gzip.compress(json.dumps({"snap": True}).encode()))

    monkeypatch.setattr("ingest.edgar.time.sleep", lambda s: None)
    client = _client(tmp_path, handler, fixture_dir=fixdir)
    payload, tier = client.get_submissions(123)
    assert payload == {"snap": True} and tier == "snapshot"


def test_all_tiers_exhausted_raises(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(503)

    monkeypatch.setattr("ingest.edgar.time.sleep", lambda s: None)
    client = _client(tmp_path, handler)
    with pytest.raises(EdgarUnavailableError):
        client.get_submissions(123)
