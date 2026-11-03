"""Client identification and in-memory rate limiting for the public, login-free service.

Client IPs are never stored: they are HMAC'd with a random per-process key, so the
limiter only ever holds opaque tokens that are useless after a restart.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from collections import deque

from fastapi import Request

_PROCESS_KEY = secrets.token_bytes(32)
CLIENT_IP_HEADER = "x-client-ip"


def client_ip(request: Request, trust_header: bool) -> str:
    if trust_header:
        value = (request.headers.get(CLIENT_IP_HEADER) or "").strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def client_key(ip: str) -> str:
    """Opaque per-process token for an IP. IPv6 addresses are grouped by /64."""
    try:
        addr = ipaddress.ip_address(ip)
        if addr.version == 6:
            ip = str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
    except ValueError:
        pass
    return hmac.new(_PROCESS_KEY, ip.encode(), hashlib.sha256).hexdigest()[:32]


class RateLimiter:
    """Sliding-window limits, e.g. {3600: 20, 86400: 60} = 20 per hour and 60 per day."""

    def __init__(self, windows: dict[int, int]):
        self.windows = {w: n for w, n in windows.items() if n > 0}
        self.longest = max(self.windows, default=0)
        self.events: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def hit(self, key: str, now: float | None = None, record: bool = True) -> int | None:
        """Record an event (unless record=False, which only checks).

        Returns None if allowed, else the number of seconds until a slot frees up.
        """
        if not self.windows:
            return None
        now = time.time() if now is None else now
        with self.lock:
            events = self.events.setdefault(key, deque())
            while events and now - events[0] >= self.longest:
                events.popleft()
            for window, limit in self.windows.items():
                recent = [t for t in events if now - t < window]
                if len(recent) >= limit:
                    return max(1, int(window - (now - recent[0])) + 1)
            if record:
                events.append(now)
            return None

    def purge(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            for key in [k for k, ev in self.events.items() if not ev or now - ev[-1] >= self.longest]:
                del self.events[key]
