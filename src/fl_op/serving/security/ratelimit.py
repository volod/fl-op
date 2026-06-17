"""In-process fixed-window rate limiter for the serving API.

A pragmatic, single-instance throttle: it counts requests per key within a
fixed wall-clock window and refuses once the budget is spent. It is a guard
against a single misbehaving client, not a distributed quota -- durable
cross-instance limits still belong at an ingress/proxy. ``max_requests <= 0``
disables limiting entirely (the default), so the limiter is opt-in.
"""

import threading
import time
from typing import Callable

from fl_op.serving.security.errors import RateLimitError


class FixedWindowRateLimiter:
    """Per-key fixed-window counter; thread-safe for the uvicorn worker pool."""

    def __init__(
        self,
        max_requests: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window_s = window_s
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (window_start, count)
        self._windows: dict[str, tuple[float, int]] = {}

    @property
    def enabled(self) -> bool:
        return self.max_requests > 0 and self.window_s > 0

    def check(self, key: str) -> None:
        """Count one request for ``key``; raise RateLimitError past the budget."""
        if not self.enabled:
            return
        now = self._clock()
        with self._lock:
            window_start, count = self._windows.get(key, (now, 0))
            if now - window_start >= self.window_s:
                window_start, count = now, 0
            count += 1
            self._windows[key] = (window_start, count)
            if count > self.max_requests:
                retry_after = max(1, int(self.window_s - (now - window_start)))
                raise RateLimitError(
                    f"rate limit exceeded ({self.max_requests}/{self.window_s:g}s)",
                    retry_after_s=retry_after,
                )
