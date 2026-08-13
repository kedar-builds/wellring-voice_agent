"""
ratelimit.py
============
Lightweight, dependency-free in-memory rate limiting for the WellRing API.

Two tiers, both keyed by client IP (honours X-Forwarded-For so it works
behind the Railway/Vercel proxy):

1. General cap    — max requests per minute per IP on protected routes
                    (protects the API-key + Clerk-login endpoints from
                    hammering).
2. Auth-failure   — max failed (401) requests per window per IP; once an IP
                    trips the threshold it is BLOCKED for a cooldown window
                    (brute-force protection for the static API key).

In-memory (per-process) by design: the deployment is a single Railway
replica. If replicas are ever scaled horizontally, move the counters to
Redis (or the shared Postgres).

Config (env, all optional):
    RATE_LIMIT_ENABLED              = true|false       (default true)
    RATE_LIMIT_REQUESTS_PER_MINUTE  = int              (default 600)
    RATE_LIMIT_FAILURES_PER_WINDOW  = int              (default 20)
    RATE_LIMIT_FAILURE_WINDOW_SECONDS = int            (default 600)
    RATE_LIMIT_BLOCK_SECONDS        = int              (default 900)
"""

import asyncio
import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class RateLimiter:
    """Sliding-window request limiter with a 401-triggered block list."""

    def __init__(
        self,
        max_requests: int,
        request_window_seconds: float,
        max_failures: int,
        failure_window_seconds: float,
        block_seconds: float,
    ) -> None:
        self.max_requests = max_requests
        self.request_window_seconds = request_window_seconds
        self.max_failures = max_failures
        self.failure_window_seconds = failure_window_seconds
        self.block_seconds = block_seconds

        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._failures: Dict[str, Deque[float]] = defaultdict(deque)
        self._blocked_until: Dict[str, float] = {}

    # -- internals ----------------------------------------------------------

    def _prune(self, q: Deque[float], now: float, window: float) -> None:
        while q and now - q[0] > window:
            q.popleft()

    def _count(self, q: Deque[float], now: float, window: float) -> int:
        self._prune(q, now, window)
        return len(q)

    # -- public API ----------------------------------------------------------

    def is_blocked(self, ip: str) -> bool:
        with self._lock:
            until = self._blocked_until.get(ip, 0.0)
            return time.time() < until

    def remaining_block_seconds(self, ip: str) -> int:
        with self._lock:
            until = self._blocked_until.get(ip, 0.0)
            return max(0, int(until - time.time()))

    def check_and_record(self, ip: str) -> Tuple[bool, Optional[str]]:
        """
        Record a request for this IP and report whether it is allowed.

        Returns (True, None) when allowed, or (False, reason) when the IP is
        blocked or over the per-minute cap.
        """
        now = time.time()
        with self._lock:
            # NOTE: never call other lock-taking helpers (e.g.
            # remaining_block_seconds) while holding the lock — threading.Lock
            # is not re-entrant and this deadlocks the request.
            until = self._blocked_until.get(ip, 0.0)
            if until > now:
                return False, (
                    "IP temporarily blocked after too many failed auth attempts "
                    f"({max(0, int(until - now))}s remaining)"
                )
            q = self._hits[ip]
            self._prune(q, now, self.request_window_seconds)
            if len(q) >= self.max_requests:
                return False, "Too many requests — slow down"
            q.append(now)
            return True, None

    def record_failure(self, ip: str) -> bool:
        """
        Record a 401 (auth failure) for this IP. Returns True if the IP has
        now crossed the threshold and been blocked.
        """
        now = time.time()
        blocked = False
        with self._lock:
            q = self._failures[ip]
            self._prune(q, now, self.failure_window_seconds)
            q.append(now)
            if len(q) >= self.max_failures:
                self._blocked_until[ip] = now + self.block_seconds
                self._failures[ip].clear()  # fresh start after the block lifts
                blocked = True
        if blocked:
            logger.warning(
                f"[RATELIMIT] IP {ip} blocked for {int(self.block_seconds)}s after "
                f"{self.max_failures} failed auth attempts."
            )
            try:
                from src.database import log_auth_event
                log_auth_event(
                    "rate_limit_block",
                    f"IP {ip} blocked for {int(self.block_seconds)}s after "
                    f"{self.max_failures} failed auth attempts",
                    ip=ip,
                )
            except Exception as exc:  # monitoring must never break auth
                logger.error(f"[RATELIMIT] Failed to record auth event: {exc}")
        return blocked

    def failure_count(self, ip: str) -> int:
        with self._lock:
            return self._count(self._failures[ip], time.time(), self.failure_window_seconds)


def make_limiter_from_env() -> Optional[RateLimiter]:
    """Build a RateLimiter from env config (None when rate limiting is disabled)."""
    if not _env_bool("RATE_LIMIT_ENABLED", True):
        return None
    return RateLimiter(
        max_requests=_env_int("RATE_LIMIT_REQUESTS_PER_MINUTE", 600),
        request_window_seconds=60.0,
        max_failures=_env_int("RATE_LIMIT_FAILURES_PER_WINDOW", 20),
        failure_window_seconds=float(_env_int("RATE_LIMIT_FAILURE_WINDOW_SECONDS", 600)),
        block_seconds=float(_env_int("RATE_LIMIT_BLOCK_SECONDS", 900)),
    )


class RateLimitMiddleware:
    """
    Starlette/ASGI middleware applying the limiter to every request except
    exempt paths (health checks and the Twilio/Bolna webhooks, which are
    called from provider server IPs and must never be throttled).

    401 responses are fed back into the limiter as auth failures.
    """

    def __init__(self, app, limiter: Optional[RateLimiter], exempt_paths: Tuple[str, ...] = ()) -> None:
        self.app = app
        self.limiter = limiter
        self.exempt_paths = exempt_paths

    def _client_ip(self, scope: dict, headers) -> str:
        # Prefer the LAST entry of X-Forwarded-For. A trusted proxy (Railway)
        # appends the socket peer to the right; the LEFT entries are client-
        # controllable, so using the leftmost would let an attacker rotate the
        # header and trivially bypass every limit.
        forwarded = headers.get(b"x-forwarded-for")
        if forwarded:
            entries = [e.strip() for e in forwarded.decode("latin-1").split(",") if e.strip()]
            if entries:
                return entries[-1]
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self.limiter is None:
            await self.app(scope, receive, send)
            return

        from starlette.responses import JSONResponse

        path = scope.get("path", "")
        if path in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        ip = self._client_ip(scope, dict(scope.get("headers") or []))
        allowed, reason = self.limiter.check_and_record(ip)
        if not allowed:
            retry_after = self.limiter.remaining_block_seconds(ip) if self.limiter.is_blocked(ip) else 60
            response = JSONResponse(
                {"detail": reason},
                status_code=429,
                headers={"Retry-After": str(max(1, retry_after))},
            )
            await response(scope, receive, send)
            return

        # Capture the downstream response to count auth failures.
        status_holder: Dict[str, int] = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)
        if status_holder.get("status") == 401:
            # record_failure → log_auth_event does a blocking DB round-trip
            # (SQLite or psycopg2). Offload it so a slow write can't stall the
            # event loop for concurrent requests.
            await asyncio.to_thread(self.limiter.record_failure, ip)
