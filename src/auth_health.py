"""
auth_health.py
==============
Background auth-health watchdog + shared Clerk rejection counters.

Two alert conditions, both delivered to DEV_ALERT_WEBHOOK_URL (Slack /
Discord / ntfy — the same webhook the Twilio-quota alert uses), throttled to
at most one notification per hour:

1. Missing secret    — the backend is running in a production-like
                       environment (ENV / RAILWAY_ENVIRONMENT / VERCEL_ENV in
                       production/prod/preview/staging) without
                       CLERK_SECRET_KEY. Clerk session verification is then
                       disabled and anyone holding the static WELLRING_API_KEY
                       can read every account's data.
2. Rejection spike   — a sustained burst of rejected Clerk session tokens
                       (>= REJECTION_SPIKE_THRESHOLD in the last 10 minutes).
                       Indicates a broken frontend auth flow (missing/expired
                       tokens) or a brute-force attempt.

The counters are shared: get_verified_clerk_uid() calls
record_clerk_rejection() on every rejected token, and the watchdog reads them
periodically. All state is in-memory (single Railway replica).
"""

import asyncio
import logging
import os
import threading
import time
from collections import deque
from typing import Deque

logger = logging.getLogger(__name__)

ALERT_COOLDOWN_SECONDS = 3600.0          # at most one alert per hour
REJECTION_SPIKE_THRESHOLD = 50           # >= N rejected tokens ...
REJECTION_SPIKE_WINDOW_SECONDS = 600.0   # ... within the last 10 minutes
WATCH_INTERVAL_SECONDS = 60.0

_PRODUCTION_LIKE_ENVS = ("production", "prod", "preview", "staging")

_lock = threading.Lock()
_rejections: Deque[float] = deque()
_last_alert_ts = 0.0  # 0 → first alert fires on the first watch tick


def current_env() -> str:
    """First non-empty of ENV / RAILWAY_ENVIRONMENT / VERCEL_ENV (lowercased)."""
    return next(
        (
            v.strip().lower()
            for v in (
                os.environ.get("ENV", ""),
                os.environ.get("RAILWAY_ENVIRONMENT", ""),
                os.environ.get("VERCEL_ENV", ""),
            )
            if v
        ),
        "",
    )


def production_like_env() -> bool:
    return current_env() in _PRODUCTION_LIKE_ENVS


def rejection_count(window_seconds: float = REJECTION_SPIKE_WINDOW_SECONDS) -> int:
    now = time.time()
    with _lock:
        while _rejections and now - _rejections[0] > window_seconds:
            _rejections.popleft()
        return len(_rejections)


def record_clerk_rejection() -> None:
    """
    Called whenever get_verified_clerk_uid() rejects a session token.

    NOTE: only Clerk session rejections feed this counter (API-key 401s from
    get_api_key do NOT) — the rejection-spike alert is specifically about a
    broken frontend login flow / Clerk brute force, not generic bad keys.
    """
    with _lock:
        _rejections.append(time.time())


def _notify_dev(title: str, message: str) -> None:
    try:
        from src.notifications import _notify_dev_via_webhook
        _notify_dev_via_webhook(
            message,
            title=f"WellRing Auth Health: {title}",
            footer="Check the Clerk configuration in the backend's environment.",
        )
    except Exception as exc:  # never let alerting crash the watchdog
        logger.error(f"[AUTH-HEALTH] Webhook notification failed: {exc}")


def _record_event(event_type: str, detail: str = "") -> None:
    """Persist an auth event for the ops dashboard (GET /auth/events)."""
    try:
        from src.database import log_auth_event
        log_auth_event(event_type, detail)
    except Exception as exc:  # monitoring must never break the watchdog
        logger.error(f"[AUTH-HEALTH] Failed to record auth event: {exc}")


def current_alerts() -> list:
    """Return the list of currently-true alert conditions (for tests/status)."""
    alerts = []
    if production_like_env() and not os.environ.get("CLERK_SECRET_KEY"):
        alerts.append("missing-secret")
    if rejection_count() >= REJECTION_SPIKE_THRESHOLD:
        alerts.append("rejection-spike")
    return alerts


async def run_auth_health_watchdog() -> None:
    """
    Periodic loop: raise critical alerts when the auth layer is broken.
    At most one webhook notification per hour (all conditions batched).
    """
    global _last_alert_ts
    while True:
        try:
            await asyncio.sleep(WATCH_INTERVAL_SECONDS)
            alerts = current_alerts()
            if not alerts:
                continue

            now = time.time()
            with _lock:
                can_alert = now - _last_alert_ts >= ALERT_COOLDOWN_SECONDS
                if can_alert:
                    _last_alert_ts = now

            if not can_alert:
                continue

            if "missing-secret" in alerts:
                logger.critical(
                    "[AUTH-HEALTH] ⛔ CLERK_SECRET_KEY is not set in a production-like "
                    "environment — Clerk session verification is DISABLED. Anyone holding "
                    "the static WELLRING_API_KEY can read every account's data. "
                    "Set CLERK_SECRET_KEY immediately."
                )
                _record_event("missing_clerk_secret", "Clerk verification disabled in a production-like environment")
                _notify_dev(
                    "CLERK_SECRET_KEY missing",
                    "The backend is running in a production-like environment without "
                    "CLERK_SECRET_KEY. Clerk session verification is DISABLED — anyone "
                    "holding the static WELLRING_API_KEY can read every account's data.",
                )
            if "rejection-spike" in alerts:
                count = rejection_count()
                logger.critical(
                    f"[AUTH-HEALTH] {count} Clerk session-token rejections in the last "
                    "10 minutes — the frontend auth flow may be broken (missing/expired "
                    "tokens) or a brute-force attempt is underway."
                )
                _record_event("clerk_rejection_spike", f"{count} rejections in 10 minutes")
                _notify_dev(
                    "Repeated login rejections",
                    f"{count} Clerk session-token rejections in the last 10 minutes. "
                    "The frontend auth flow may be broken (missing/expired tokens) or a "
                    "brute-force attempt is underway.",
                )
        except asyncio.CancelledError:
            logger.info("[AUTH-HEALTH] Watchdog task cancelled.")
            break
        except Exception as exc:
            logger.error(f"[AUTH-HEALTH] Watchdog error: {exc}")
