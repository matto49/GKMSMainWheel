# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server-supplied produce draws."""
from __future__ import annotations

from typing import Callable, Hashable


MASK32 = 0xFFFFFFFF
_FNV_OFFSET_BASIS = 2166136261
_FNV_PRIME = 16777619


def _derive(run_seed: int, kind: str) -> int:
    """Return a stable unsigned 32-bit seed for one server-draw kind."""

    value = _FNV_OFFSET_BASIS
    seed_bytes = (int(run_seed) & MASK32).to_bytes(4, "little", signed=False)
    for byte in seed_bytes + str(kind).encode("utf-8"):
        value ^= byte
        value = (value * _FNV_PRIME) & MASK32
    value ^= int(run_seed) & MASK32
    value &= MASK32
    return value or 1


class ProduceRandom:
    """One deterministic xorshift32 stream for a server-draw kind."""

    def __init__(self, run_seed: int, kind: str):
        self.run_seed = int(run_seed) & MASK32
        self.kind = str(kind)
        self.state = _derive(self.run_seed, self.kind)
        self.state_before = self.state
        self.state_after = self.state

    def next_u32(self) -> int:
        """Advance and return the next unsigned 32-bit value."""

        self.state_before = self.state
        value = self.state
        value ^= (value << 13) & MASK32
        value ^= value >> 17
        value ^= (value << 5) & MASK32
        self.state = value & MASK32
        self.state_after = self.state
        return self.state

    def range(self, lo: int, hi: int) -> int:
        """Map the current state into ``[lo, hi)`` like ``ExamRandom.range``."""

        old_state = self.state
        width = (int(hi) - int(lo)) & MASK32
        result = int(lo) + ((old_state * width) >> 32)
        self.next_u32()
        self.state_before = old_state
        self.state_after = self.state
        return result


class ServerDraw:
    """Resolve observed server values or roll an isolated fallback stream."""

    def __init__(self, run_seed: int, observed: dict[tuple[str, Hashable], int] | None = None):
        self.run_seed = int(run_seed) & MASK32
        self.observed = dict(observed or {})
        self._streams: dict[str, ProduceRandom] = {}
        self.log: list[dict] = []

    def _stream(self, kind: str) -> ProduceRandom:
        kind = str(kind)
        stream = self._streams.get(kind)
        if stream is None:
            stream = ProduceRandom(self.run_seed, kind)
            self._streams[kind] = stream
        return stream

    def resolve(self, kind: str, key: Hashable, roll: Callable[[ProduceRandom], int]) -> int:
        """Return an observed value or roll the kind's independent stream."""

        kind = str(kind)
        observed_key = (kind, key)
        if observed_key in self.observed:
            value = self.observed[observed_key]
            self.log.append({"kind": kind, "key": key, "source": "observed", "value": value})
            return value

        value = roll(self._stream(kind))
        self.log.append({"kind": kind, "key": key, "source": "rolled", "value": value})
        return value
