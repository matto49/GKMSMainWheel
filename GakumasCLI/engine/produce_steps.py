# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""neu.produce_steps - ProduceSetting-backed helpers for produce steps."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .card_data import _f32, _f32_div


_PRODUCE_SETTING_FIELDS = (
    "refreshStaminaRecoveryPermil",
    "stepSkipStaminaRecoveryPermil",
    "beforeAuditionRefreshStaminaRecoveryPermil",
)


def _wrap_int32(value):
    return ((int(value) + 2**31) % 2**32) - 2**31


def refresh_stamina_delta(max_stamina, current_stamina, permil):
    """Return the native 休む stamina increase for one ProduceSetting rate."""
    maximum = int(max_stamina)
    current = int(current_stamina)
    product = _wrap_int32(maximum * int(permil))
    scaled = _f32_div(_f32(product), _f32(1000.0))
    raw = math.floor(_f32(scaled + _f32(0.0001)))
    return min(maximum - current, raw)


def load_produce_setting(master_dir, setting_id="p_setting-2"):
    """Load the three stamina permils for one decoded ProduceSetting row."""
    path = Path(master_dir) / "ProduceSetting.jsonl"
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("decode_status", "decoded") != "decoded":
                continue
            decoded = row.get("decoded", row)
            if decoded.get("id") == setting_id:
                return {field: decoded[field] for field in _PRODUCE_SETTING_FIELDS}
    raise KeyError(setting_id)
