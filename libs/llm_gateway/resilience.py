"""Shared rate limiter and circuit breaker (TSD §9 "Model provider errors").

In-process per worker for the pilot; swap the bucket for Redis when workers scale out.
Clock is injectable so both are unit-testable without sleeping.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Keeps requests and tokens ~10% under the provider's RPM/TPM caps."""

    rpm: int
    tpm: int
    headroom: float = 0.9
    clock: Callable[[], float] = time.monotonic
    _req: float = field(init=False)
    _tok: float = field(init=False)
    _t: float = field(init=False)

    def __post_init__(self) -> None:
        self._req = self.rpm * self.headroom
        self._tok = self.tpm * self.headroom
        self._t = self.clock()

    def _refill(self) -> None:
        now = self.clock()
        dt = now - self._t
        self._t = now
        self._req = min(self.rpm * self.headroom, self._req + dt * self.rpm * self.headroom / 60)
        self._tok = min(self.tpm * self.headroom, self._tok + dt * self.tpm * self.headroom / 60)

    def try_acquire(self, tokens: int) -> float:
        """0.0 if acquired, otherwise seconds to wait."""
        self._refill()
        if self._req >= 1 and self._tok >= tokens:
            self._req -= 1
            self._tok -= tokens
            return 0.0
        need_r = max(0.0, 1 - self._req) / (self.rpm * self.headroom / 60)
        need_t = max(0.0, tokens - self._tok) / (self.tpm * self.headroom / 60)
        return max(need_r, need_t, 0.01)

    async def acquire(self, tokens: int) -> None:
        while (wait := self.try_acquire(tokens)) > 0:
            await asyncio.sleep(wait)


class CircuitOpen(Exception):
    pass


@dataclass
class CircuitBreaker:
    """Opens at >=50% failures over the last 20 calls (or 60 s); half-open probe every 30 s."""

    window: int = 20
    window_s: float = 60.0
    threshold: float = 0.5
    probe_after_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _calls: deque[tuple[float, bool]] = field(default_factory=deque)
    _opened_at: float | None = None
    _probing: bool = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self.clock() - self._opened_at >= self.probe_after_s:
            return "half_open"
        return "open"

    def before(self) -> None:
        st = self.state
        if st == "open" or (st == "half_open" and self._probing):
            raise CircuitOpen("circuit open")
        if st == "half_open":
            self._probing = True

    def record(self, ok: bool) -> None:
        now = self.clock()
        if self._opened_at is not None:  # result of a half-open probe
            self._probing = False
            if ok:
                self._opened_at = None
                self._calls.clear()
            else:
                self._opened_at = now
            return
        self._calls.append((now, ok))
        while len(self._calls) > self.window or (
            self._calls and now - self._calls[0][0] > self.window_s
        ):
            self._calls.popleft()
        fails = sum(1 for _, k in self._calls if not k)
        if len(self._calls) >= min(self.window, 10) and fails / len(self._calls) >= self.threshold:
            self._opened_at = now


def backoff_s(attempt: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff with full jitter: 2, 4, 8 ... capped at 300 s."""
    return random.uniform(0, min(cap, base * (2 ** max(0, attempt - 1))))
