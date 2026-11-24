# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional shapes for the offline plain-exam experiment."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from GakumasCLI.scenario import policy_search as PS


def _action_path(path):
    actions = tuple(tuple(action) for action in path)
    if any(action[0] == "select" for action in actions):
        raise ValueError("chunk shapes do not support select actions")
    return actions


def _ranked(routes):
    return sorted(routes, key=lambda row: (-row[2], row[0]))


def _top_two(routes):
    best = {}
    for row in _ranked(routes):
        best.setdefault(row[1], row)
        if len(best) == 2:
            break
    return list(best.values())


class _Debits:
    def __init__(self, budget):
        self.budget = budget
        self.charged = 0
        self.add(0)

    def add(self, count):
        if self.charged + count >= self.budget:
            raise ValueError("replay leaves no search transitions in this chunk")
        self.charged += count

    def step(self, node, action):
        self.add(1)
        node.step(action, observe=False)


class _Teacher:
    """Capture resolved base weights on learn_prior's existing traversal."""

    def __init__(self, node, base, resolved):
        self._node = node
        self._base = base
        self._resolved = resolved

    def clone(self):
        return _Teacher(self._node.clone(), self._base, self._resolved)

    def __getattr__(self, name):
        return getattr(self._node, name)

    def step(self, action):
        if self._base:
            _, keys, weights = PS._choices(self._node, False, prior=self._base)
            for key, weight in zip(keys, weights):
                if key not in self._base:
                    self._resolved[key].append(weight)
        self._node.step(action, observe=False)


class ChunkShapes:
    """Prepare and observe each chunk in order for one 17-chunk search."""

    def __init__(self, switches, session, prior):
        self._switches = switches
        self._session = session
        self._prior = prior
        self._openings = Counter()
        self._strata = ()
        self._routes = []
        self._opening_prefix = ()

    def observe(self, index: int, full_path, score, valid):
        if not 0 <= index < 17:
            raise ValueError("chunk index must be in 0..16")
        switches = self._switches
        count_opening = switches.root_strata and index < 4
        keep_route = (switches.prefix_four or switches.elite_prior) and index < 8
        if not valid or not (count_opening or keep_route):
            return
        path = _action_path(full_path)
        if count_opening and path:
            self._openings[path[0]] += 1
        if keep_route:
            self._routes.append((index, path, score))
            if not switches.prefix_four:
                self._routes = _top_two(self._routes)

    def _two_turn_prefix(self, path, debits):
        node = self._session.clone()
        if node.turn != 1:
            return ()
        for i, action in enumerate(path, 1):
            debits.step(node, action)
            if node.finished:
                break
            if node.turn == 3:
                return path[:i]
        return ()

    def _alternative_opening(self, debits):
        if not self._routes:
            return ()
        measured = [self._two_turn_prefix(path, debits) for _, path, _ in _ranked(self._routes)]
        winner, *others = measured
        if not winner:
            return ()
        return next((prefix for prefix in others if prefix and prefix != winner), ())

    def _elite_prior(self, debits):
        teachers = _top_two(self._routes)
        if len(teachers) < 2:
            return self._prior
        debits.add(sum(len(path) for _, path, _ in teachers))
        base = self._prior
        base_weights = base.weights if base is not None else {}
        resolved = defaultdict(list)
        root = _Teacher(self._session, base_weights, resolved)
        learned = PS.learn_prior((root, path) for _, path, _ in teachers)
        weights = dict(base_weights)
        neutral = 2.0 * math.log(0.5)
        for key, weight in learned.weights.items():
            if isinstance(key[0], int) or key[0] == "dt":
                baseline = base_weights.get(key, neutral)
                if key in resolved:
                    baseline = math.fsum(resolved[key]) / len(resolved[key])
                weights[key] = 0.5 * (baseline + weight)
        sources = base.sources if base is not None else ()
        return PS.ScorePrior(weights, sources=(*sources, "gpu10:elite_prior"))

    def _late_prefix(self, incumbent_path, debits):
        path = _action_path(incumbent_path)
        node = self._session.clone()
        turn = node.turn
        previous_start = start = 0
        for i, action in enumerate(path, 1):
            debits.step(node, action)
            if not node.finished and node.turn != turn:
                previous_start, start = start, i
                turn = node.turn
        if not node.finished:
            raise ValueError("incumbent route is incomplete")
        return path[:previous_start]

    def prepare(self, index: int, chunk_budget: int, incumbent_path):
        if not 0 <= index < 17:
            raise ValueError("chunk index must be in 0..16")
        switches = self._switches
        debits = _Debits(chunk_budget)
        prefix = ()
        if switches.root_strata and 4 <= index < 8:
            if index == 4:
                actions = self._session.clone().legal_actions()
                self._strata = tuple(sorted(actions, key=self._openings.__getitem__))
                self._openings.clear()
            if self._strata:
                prefix = (self._strata[(index - 4) % len(self._strata)],)
            if index == 7:
                self._strata = ()
        elif switches.prefix_four and 8 <= index < 12:
            if index == 8:
                self._opening_prefix = self._alternative_opening(debits)
            prefix = self._opening_prefix
            if index == 11:
                self._opening_prefix = ()
        elif switches.warm_suffix and 12 <= index < 16 and incumbent_path:
            prefix = self._late_prefix(incumbent_path, debits)

        if index == 8:
            if switches.elite_prior:
                self._prior = self._elite_prior(debits)
            self._routes.clear()

        root = self._session
        if prefix:
            probe = root.clone()
            for action in prefix:
                debits.step(probe, action)
            if probe.finished:
                prefix = ()
            else:
                root = probe
        return root, prefix, self._prior, None, {"charged": debits.charged, "prefix_length": len(prefix)}
