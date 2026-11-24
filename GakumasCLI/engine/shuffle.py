# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
def shuffle_deck(deck, rng):
    calls = []
    for size in range(len(deck), 1, -1):
        index = rng.range(0, size)
        deck[size - 1], deck[index] = deck[index], deck[size - 1]
        calls.append(
            {
                "min": 0,
                "max": size,
                "result": index,
                "state_before": rng.state_before,
                "state_after": rng.state_after,
            }
        )
    return calls
