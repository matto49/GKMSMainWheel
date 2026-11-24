# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
import random


def test_the_screen_encodes_and_hides_the_draw_order(master, loadouts):
    from uinet.encode import Encoder, KIND_HAND
    from uinet.env import Battle
    enc = Encoder()
    lo = next(iter(loadouts.values()))
    b1, b2 = Battle(dict(lo, seed=7)), Battle(dict(lo, seed=7))
    b2.session.edit(lambda v: random.Random(1).shuffle(v.deck.deck))
    o1, o2 = b1.obs(), b2.obs()
    assert o1["deck"] == o2["deck"]
    s = enc.compact(o1)
    assert list(s["t_kind"][:len(o1["hand"])]) == [KIND_HAND] * len(o1["hand"])
