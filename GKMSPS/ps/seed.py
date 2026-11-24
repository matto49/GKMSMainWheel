# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The root of a simulated produce."""
from __future__ import annotations

import base64
import hashlib
import json

DOMAIN = b"GKMS-PS/1\x00"
PS_RULES = 1


def canonical(obj) -> bytes:
    def check(v, path="$"):
        if isinstance(v, float):
            raise ValueError(f"{path}: floats are not allowed in the formation document")
        if isinstance(v, dict):
            for k, x in v.items():
                if not isinstance(k, str):
                    raise ValueError(f"{path}: keys must be strings")
                check(x, f"{path}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                check(x, f"{path}[{i}]")
    check(obj)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def memory_digest(memory: dict) -> str:
    return hashlib.sha256(canonical(memory)).hexdigest()


def formation_document(formation: dict, ruleset: dict, timestamp_ms: int) -> dict:
    """``formation``: idol {id, level_limit_rank, potential_rank[, prima_stella]}, supports [6 x {id, level, limit_rank, rental}],
    memories [4 x memory document or None], account {producer_level, dearness_level, growth_panel}."""
    idol = formation["idol"]
    sups = formation["supports"]
    if len(sups) != 6:
        raise ValueError("a formation has 6 support cards")
    mems = formation.get("memories") or []
    if len(mems) > 4:
        raise ValueError("a formation has at most 4 memories")
    acc = formation.get("account") or {}
    idol_doc = {"id": str(idol["id"]), "level_limit_rank": int(idol.get("level_limit_rank", 0)),
                "potential_rank": int(idol.get("potential_rank", 0))}
    if idol.get("prima_stella"):
        idol_doc["prima_stella"] = True
    return {
        "ps": 1,
        "ruleset": {k: ruleset[k] for k in sorted(ruleset)},
        "idol": idol_doc,
        "supports": [{"id": str(s["id"]), "level": int(s["level"]), "limit_rank": int(s.get("limit_rank", 0)),
                      "rental": bool(s.get("rental", False))} for s in sups],
        "memories": [memory_digest(m) if m else "" for m in mems] + [""] * (4 - len(mems)),
        "account": {"producer_level": int(acc.get("producer_level", 0)),
                    "dearness_level": int(acc.get("dearness_level", 0)),
                    "true_ends": sorted(str(x) for x in acc.get("true_ends") or ()),
                    "growth_panel": {str(k): int(v) for k, v in sorted((acc.get("growth_panel") or {}).items())}},
        "timestamp_ms": int(timestamp_ms),
    }


def root_of(document: dict) -> bytes:
    return hashlib.sha256(DOMAIN + canonical(document)).digest()


def short_code(root: bytes) -> str:
    """16 characters of base32 over the first 10 bytes, for display."""
    return base64.b32encode(root[:10]).decode("ascii")


def parse_root(text: str) -> bytes:
    """A root from its 64-hex-digit code (the fixed-root mode)."""
    t = text.strip().lower()
    if len(t) != 64 or any(c not in "0123456789abcdef" for c in t):
        raise ValueError("种子码须为 64 位十六进制")
    return bytes.fromhex(t)
