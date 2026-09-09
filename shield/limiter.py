"""
Per-client token bucket, denominated in estimated model milliseconds.

Bucket capacity  = burst allowance (ms of compute a client may spend at once)
Refill rate      = sustained allowance (ms of compute per wall-clock second)

A refill rate of 400 ms/s means: this client may consume 400 milliseconds
of model time per second. On a 2-worker CPU box that is ~20% of capacity,
so five such clients saturate the machine and a sixth gets throttled.
That is a far more meaningful budget than "10 requests per second".

Concurrency note: many requests can touch the same client bucket at once.
Every mutation is under an asyncio lock. Without it, a burst of concurrent
requests all read the same token count and all pass - the classic
check-then-act race that silently defeats the limiter.
"""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    tokens: float
    last_refill: float
    last_seen: float
    allowed: int = 0
    rejected: int = 0
    spent_ms: float = field(default=0.0)


class CostAwareLimiter:
    def __init__(self, capacity_ms: float, refill_ms_per_s: float, idle_expiry_s: float = 300.0,
                 new_client_fill: float = 1.0):
        # new_client_fill < 1.0 means a never-before-seen client ID starts
        # with a partially filled bucket instead of a full one. This is the
        # answer to identity rotation (Sybil): an attacker who mints a fresh
        # ID for every request never accumulates the budget standing that
        # established light users have, so budget-priority shedding drops
        # them first. New legitimate users still get served - they just rank
        # below known-good clients while the system is under pressure, and
        # they climb as they use the service normally.
        self.new_client_fill = new_client_fill
        self.capacity_ms = capacity_ms
        self.refill_ms_per_s = refill_ms_per_s
        self.idle_expiry_s = idle_expiry_s
        self._buckets: dict[str, Bucket] = {}
        self._lock = asyncio.Lock()
        # Protection mode multipliers, applied by the controller.
        self.capacity_scale = 1.0
        self.refill_scale = 1.0

    def set_scales(self, capacity_scale: float, refill_scale: float) -> None:
        self.capacity_scale = capacity_scale
        self.refill_scale = refill_scale

    def _effective(self) -> tuple[float, float]:
        return (
            self.capacity_ms * self.capacity_scale,
            self.refill_ms_per_s * self.refill_scale,
        )

    async def check(self, client_id: str, cost_ms: float) -> tuple[bool, float, float]:
        """
        Returns (allowed, retry_after_seconds, remaining_fraction).

        remaining_fraction is how full this client's bucket is AFTER the
        request, 0.0-1.0. It is the shield's cheapest fairness signal: a
        light user sits near 1.0, a client hammering the service sits near
        0.0. We use it to decide who to shed first when capacity runs out.
        """
        now = time.monotonic()
        capacity, refill = self._effective()

        async with self._lock:
            b = self._buckets.get(client_id)
            if b is None:
                b = Bucket(tokens=capacity * self.new_client_fill, last_refill=now, last_seen=now)
                self._buckets[client_id] = b

            elapsed = now - b.last_refill
            b.tokens = min(capacity, b.tokens + elapsed * refill)
            b.last_refill = now
            b.last_seen = now

            if b.tokens >= cost_ms:
                b.tokens -= cost_ms
                b.allowed += 1
                b.spent_ms += cost_ms
                return True, 0.0, b.tokens / capacity if capacity else 0.0

            b.rejected += 1
            deficit = cost_ms - b.tokens
            retry_after = deficit / refill if refill > 0 else 60.0
            # Cap so a client with an absurd request is not told to wait an hour.
            return False, min(retry_after, 60.0), 0.0

    async def sweep(self) -> int:
        """Drop idle buckets so a client-ID-rotating attacker cannot grow
        our memory without bound. Returns number evicted."""
        now = time.monotonic()
        async with self._lock:
            stale = [k for k, b in self._buckets.items() if now - b.last_seen > self.idle_expiry_s]
            for k in stale:
                del self._buckets[k]
            return len(stale)

    def snapshot(self) -> dict:
        capacity, refill = self._effective()
        return {
            "tracked_clients": len(self._buckets),
            "capacity_ms": round(capacity, 1),
            "refill_ms_per_s": round(refill, 1),
            "new_client_fill": self.new_client_fill,
        }


class InflightCap:
    """
    Per-client cap on simultaneously outstanding requests.

    This is the fairness mechanism, and it is the one that saves the flood
    scenario. The insight: a real user is closed-loop. They send a request,
    wait for the answer, think, then send another. They essentially never
    have more than one or two requests in flight.

    An attacker is open-loop. They fire at a fixed rate whether or not the
    server is coping, so their outstanding count climbs without limit as
    the server slows down.

    So "how many requests does this client have in flight right now" is a
    behavioural signal that separates humans from floods - with no attack
    label, no ML, and no packet inspection. Under a rate limiter alone,
    twelve attackers each individually inside their budget can still
    collectively bury the queue and push legitimate users into 503s. The
    in-flight cap stops that without touching the legitimate stream.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.scale = 1.0
        self._counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.rejected = 0

    def effective_limit(self) -> int:
        return max(1, int(self.limit * self.scale))

    async def acquire(self, client_id: str) -> bool:
        async with self._lock:
            cur = self._counts.get(client_id, 0)
            if cur >= self.effective_limit():
                self.rejected += 1
                return False
            self._counts[client_id] = cur + 1
            return True

    async def release(self, client_id: str) -> None:
        async with self._lock:
            cur = self._counts.get(client_id, 0)
            if cur <= 1:
                self._counts.pop(client_id, None)
            else:
                self._counts[client_id] = cur - 1

    def snapshot(self) -> dict:
        return {
            "limit": self.effective_limit(),
            "clients_in_flight": len(self._counts),
            "rejected": self.rejected,
        }
