"""
test_ratelimit.py
=================
Tests for the dependency-free in-memory rate limiter (src/ratelimit.py):
per-IP request caps, 401-triggered blocking, window pruning, and the ASGI
middleware integration.
"""

import time

from src.ratelimit import RateLimiter, RateLimitMiddleware


def _limiter():
    return RateLimiter(
        max_requests=3,
        request_window_seconds=60.0,
        max_failures=2,
        failure_window_seconds=600.0,
        block_seconds=60.0,
    )


# ---------------------------------------------------------------------------
# RateLimiter unit tests
# ---------------------------------------------------------------------------

def test_general_cap_allows_under_limit():
    lim = _limiter()
    assert lim.check_and_record("1.2.3.4") == (True, None)
    assert lim.check_and_record("1.2.3.4") == (True, None)
    assert lim.check_and_record("1.2.3.4") == (True, None)
    ok, reason = lim.check_and_record("1.2.3.4")
    assert ok is False
    assert "Too many requests" in reason


def test_general_cap_is_per_ip():
    lim = _limiter()
    for _ in range(5):
        lim.check_and_record("1.2.3.4")
    assert lim.check_and_record("5.6.7.8") == (True, None)


def test_window_prunes_old_hits():
    lim = RateLimiter(2, 0.05, 10, 600.0, 60.0)
    lim.check_and_record("1.1.1.1")
    time.sleep(0.1)
    assert lim.check_and_record("1.1.1.1") == (True, None)  # old hit pruned


def test_auth_failures_block_ip():
    lim = _limiter()
    assert lim.record_failure("9.9.9.9") is False
    assert lim.record_failure("9.9.9.9") is True  # threshold crossed → blocked
    assert lim.is_blocked("9.9.9.9")
    ok, reason = lim.check_and_record("9.9.9.9")
    assert ok is False
    assert "blocked" in reason.lower() or "failed auth" in reason.lower()


def test_block_expires():
    lim = RateLimiter(10, 60.0, 1, 600.0, 0.3)
    lim.record_failure("9.9.9.9")
    assert lim.is_blocked("9.9.9.9")
    # Poll until the block lifts (bounded) so slow CI machines don't flake.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not lim.is_blocked("9.9.9.9"):
            break
        time.sleep(0.05)
    assert not lim.is_blocked("9.9.9.9")
    assert lim.check_and_record("9.9.9.9")[0] is True


def test_failures_window_prunes_old():
    lim = RateLimiter(10, 60.0, 2, 0.05, 60.0)
    lim.record_failure("1.1.1.1")
    time.sleep(0.1)
    assert lim.failure_count("1.1.1.1") == 0  # old failure pruned


# ---------------------------------------------------------------------------
# Middleware integration tests (tiny standalone Starlette app)
# ---------------------------------------------------------------------------

def _make_app(limiter, exempt=()):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    async def home(request):
        return PlainTextResponse("ok")

    async def unauth(request):
        return JSONResponse({"detail": "nope"}, status_code=401)

    app = Starlette(routes=[Route("/", home), Route("/secret", unauth)])
    app.add_middleware(RateLimitMiddleware, limiter=limiter, exempt_paths=exempt)
    return app


def test_middleware_429s_over_limit():
    from starlette.testclient import TestClient
    app = _make_app(RateLimiter(2, 60.0, 10, 600.0, 60.0))
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/").status_code == 200
        assert c.get("/").status_code == 429


def test_middleware_exempt_paths_not_limited():
    from starlette.testclient import TestClient
    app = _make_app(RateLimiter(1, 60.0, 10, 600.0, 60.0), exempt=("/health",))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 404  # route doesn't exist, but NOT rate-limited
        # the real route still gets capped
        assert c.get("/").status_code == 200
        assert c.get("/").status_code == 429


def test_middleware_401_burst_blocks_ip():
    from starlette.testclient import TestClient
    app = _make_app(RateLimiter(100, 60.0, 2, 600.0, 60.0))
    with TestClient(app) as c:
        assert c.get("/secret").status_code == 401
        assert c.get("/secret").status_code == 401  # threshold crossed here
        # the third request is already blocked → 429
        assert c.get("/secret").status_code == 429
