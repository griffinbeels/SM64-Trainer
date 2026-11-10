"""Deterministic, bounded simulated loopback faults; no transport or resources."""
from collections import deque
from dataclasses import dataclass, field
import heapq
import random


@dataclass(frozen=True)
class Faults:
    seats: int = 1
    delay_ms: int = 0
    jitter_ms: int = 0
    loss_percent: int = 0
    reorder_percent: int = 0
    duplicate_percent: int = 0
    seed: int = 42
    outages: tuple[bool, bool, bool] = (False, False, False)

    def __post_init__(self):
        limits = {"seats": (1, 3), "delay_ms": (0, 1500), "jitter_ms": (0, 500),
                  "loss_percent": (0, 100), "reorder_percent": (0, 100),
                  "duplicate_percent": (0, 100), "seed": (0, (1 << 32) - 1)}
        if any(type(getattr(self, key)) is not int or not low <= getattr(self, key) <= high
               for key, (low, high) in limits.items()):
            raise ValueError("Lab settings are outside their bounds.")
        if type(self.outages) is not tuple or len(self.outages) != 3 or any(type(value) is not bool for value in self.outages):
            raise ValueError("Lab outages are invalid.")


@dataclass(order=True, frozen=True)
class Arrival:
    due_ns: int
    serial: int
    epoch: tuple[int, int] = field(compare=False)
    peer: int = field(compare=False)
    packet: bytes = field(compare=False, repr=False)
    source_received_ns: int = field(compare=False)
    enqueued_ns: int = field(compare=False)


class FaultQueue:
    LIMIT = 512

    def __init__(self, faults: Faults, epoch: tuple[int, int]):
        self.faults, self.epoch = faults, epoch
        self._random, self._heap, self._serial = random.Random(faults.seed), [], 0
        self.trace = deque(maxlen=128)
        self.counts = dict.fromkeys(("enqueued", "arrivals", "loss", "outage", "overflow", "cleared", "duplicates"), 0)

    def reset(self, epoch):
        self.counts["cleared"] += len(self._heap)
        self._heap.clear()
        self.epoch = epoch

    def _trace(self, peer, event, **extra):
        self.trace.append({"peer": peer + 1, "event": event, **extra})

    def put(self, peer, packet, *, now_ns, source_received_ns, epoch):
        if epoch != self.epoch:
            return 0
        if type(peer) is not int or not 0 <= peer < self.faults.seats or type(packet) is not bytes or not 0 < len(packet) <= 1024:
            raise ValueError("Lab packet is outside its bounds.")
        if self.faults.outages[peer]:
            self.counts["outage"] += 1; self._trace(peer, "outage"); return 0
        if self._random.randrange(100) < self.faults.loss_percent:
            self.counts["loss"] += 1; self._trace(peer, "loss"); return 0
        delay = max(0, self.faults.delay_ms + self._random.randint(-self.faults.jitter_ms, self.faults.jitter_ms))
        if self._random.randrange(100) < self.faults.reorder_percent:
            delay += 100  # An explicit extra hold, not a claim that reordering occurred.
        copies = 2 if self._random.randrange(100) < self.faults.duplicate_percent else 1
        self.counts["duplicates"] += copies - 1
        added = 0
        for copy in range(copies):
            if len(self._heap) >= self.LIMIT:
                self.counts["overflow"] += 1; self._trace(peer, "overflow"); continue
            self._serial += 1
            heapq.heappush(self._heap, Arrival(now_ns + (delay + copy) * 1_000_000, self._serial,
                epoch, peer, packet, source_received_ns, now_ns))
            self.counts["enqueued"] += 1; added += 1
            self._trace(peer, "queued", packet=self._serial, hold_ms=delay + copy)
        return added

    def take_due(self, *, now_ns, epoch):
        if epoch != self.epoch:
            self.reset(epoch)
            return []
        result = []
        while self._heap and self._heap[0].due_ns <= now_ns and len(result) < 64:
            item = heapq.heappop(self._heap)
            if item.epoch == epoch:
                result.append(item); self.counts["arrivals"] += 1
                self._trace(item.peer, "arrival", packet=item.serial)
        return result

    def snapshot(self, now_ns):
        age = max((now_ns - item.enqueued_ns for item in self._heap), default=0)
        return {"queued": len(self._heap), "oldest_queued_age_ms": max(0, age) / 1_000_000,
                "counts": dict(self.counts), "trace": list(self.trace)}
