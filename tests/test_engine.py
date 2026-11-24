# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
import random

from GakumasCLI.scenario.hif import build_hif_loadout_session


def test_every_plan_plays_to_the_end(master, loadouts):
    for kind, lo in loadouts.items():
        for seed in range(3):
            session, _meta = build_hif_loadout_session(master, lo, seed=seed)
            rng = random.Random(seed)
            while not session.finished:
                legal = sorted(session.legal_actions())
                plays = [a for a in legal if a[0] == "play"]
                session.step(rng.choice(plays) if plays and rng.random() < 0.8 else rng.choice(legal))
            assert session.state.score > 0, kind


def test_a_seed_replays_the_same(master, loadouts):
    lo = next(iter(loadouts.values()))
    scores = []
    for _ in range(2):
        session, _meta = build_hif_loadout_session(master, lo, seed=11)
        while not session.finished:
            session.step(sorted(session.legal_actions())[0])
        scores.append(int(session.state.score))
    assert scores[0] == scores[1]
