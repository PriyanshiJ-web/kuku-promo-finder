"""Per-profile limiter: sliding 60 s window for rpm and tpm, plus a concurrency semaphore."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class Handle:
    """One recorded request in the window. settle() swaps its estimate for the real count."""

    ts: float
    tokens: int


class Limiter:
    def __init__(
        self,
        rpm: int,
        tpm: int,
        concurrency: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.rpm, self.tpm = rpm, tpm
        self.clock, self.sleep = clock, sleep
        self._win: deque[Handle] = deque()
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(max(1, concurrency))

    def slot(self) -> asyncio.Semaphore:
        return self._sem

    def settle(self, handle: Handle, actual_tokens: int) -> None:
        handle.tokens = actual_tokens

    async def acquire(self, tokens: int = 0) -> Handle:
        """Wait until one more request of `tokens` fits in the window, then record it."""
        async with self._lock:
            while True:
                now = self.clock()
                while self._win and now - self._win[0].ts >= 60 - 1e-9:
                    self._win.popleft()
                used = sum(h.tokens for h in self._win)
                rpm_ok = not self.rpm or len(self._win) < self.rpm
                # an oversize request is let through when the window is empty
                tpm_ok = not self.tpm or not self._win or used + tokens <= self.tpm
                if rpm_ok and tpm_ok:
                    handle = Handle(now, tokens)
                    self._win.append(handle)
                    return handle
                await self.sleep(max(self._win[0].ts + 60 - now, 0.01))
