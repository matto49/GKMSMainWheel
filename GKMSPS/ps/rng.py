# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PS randomness: every draw of a simulated produce comes from one 256-bit root."""
from __future__ import annotations

import hashlib

LABELS_VERSION = 1
MASK32 = 0xFFFFFFFF


class Stream:
    """One labelled stream; ``used`` counts the uint32 values taken (the whole state of the stream)."""

    __slots__ = ("key", "label", "used", "_block_index", "_block")

    def __init__(self, root: bytes, label: str, used: int = 0):
        self.label = label
        self.key = hashlib.sha256(root + b"\x00" + label.encode("utf-8")).digest()
        self.used = int(used)
        self._block_index, self._block = -1, ()

    def next_u32(self) -> int:
        index, offset = divmod(self.used, 8)
        if index != self._block_index:
            digest = hashlib.sha256(self.key + index.to_bytes(8, "big")).digest()
            self._block = tuple(int.from_bytes(digest[i:i + 4], "big") for i in range(0, 32, 4))
            self._block_index = index
        self.used += 1
        return self._block[offset]

    def uniform(self, n: int) -> int:
        """0 <= x < n, by rejection (no modulo bias)."""
        n = int(n)
        if n <= 0:
            raise ValueError("uniform(n) needs n >= 1")
        if n == 1:
            return 0
        limit = (1 << 32) - ((1 << 32) % n)
        while True:
            x = self.next_u32()
            if x < limit:
                return x % n

    def chance(self, permil: int) -> bool:
        """True with probability permil / 1000 (master rates such as activationRatePermil are used as they are)."""
        return self.uniform(1000) < int(permil)

    def weighted(self, weights) -> int:
        """Index i with probability w_i / sum(w); integer weights >= 0, at least one > 0."""
        ws = [int(w) for w in weights]
        total = sum(ws)
        if total <= 0 or min(ws) < 0:
            raise ValueError("weighted() needs non-negative integer weights with a positive sum")
        r = self.uniform(total)
        for i, w in enumerate(ws):
            if r < w:
                return i
            r -= w
        raise AssertionError("unreachable")

    def choice(self, seq):
        return seq[self.uniform(len(seq))]

    def shuffle(self, items: list) -> list:
        """Fisher-Yates in place (from the end); returns the list."""
        for i in range(len(items) - 1, 0, -1):
            j = self.uniform(i + 1)
            items[i], items[j] = items[j], items[i]
        return items

    def sample(self, items, k: int, weights=None) -> list:
        """k items without replacement, each pick weighted (equal weights when None); fewer when items run out."""
        pool = list(items)
        ws = [1] * len(pool) if weights is None else [int(w) for w in weights]
        out = []
        while pool and len(out) < k and sum(ws) > 0:
            i = self.weighted(ws)
            out.append(pool.pop(i))
            ws.pop(i)
        return out

    def exam_seed(self) -> int:
        """A seed for the native exam core: the first non-zero value below 2^31 (every server exam seed observed is below
        2^31, the research data)."""
        while True:
            x = self.next_u32() & 0x7FFFFFFF
            if x:
                return x


class Rng:
    """The streams of one produce; ``state()`` / ``Rng(root, state)`` save and restore every stream exactly."""

    def __init__(self, root: bytes, state: dict | None = None):
        if len(root) != 32:
            raise ValueError("root must be 32 bytes")
        self.root = root
        self._streams = {}
        for label, used in (state or {}).items():
            self._streams[label] = Stream(root, label, used)

    def __call__(self, label: str) -> Stream:
        s = self._streams.get(label)
        if s is None:
            s = self._streams[label] = Stream(self.root, label)
        return s

    def state(self) -> dict:
        return {k: s.used for k, s in sorted(self._streams.items()) if s.used}
