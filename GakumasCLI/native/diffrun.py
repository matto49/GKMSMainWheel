# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the native core library with the master and a round loaded (``Native``) -- NativeSearch, the tail polish,
route_search_native, produce_value, core_bench, GKMSUINet and the headless client load the core through it.
"""
from __future__ import annotations

import ctypes as C


MIN_EXAM_RULES = 69


# cards a round holding which stays on the Python core: none.
PYTHON_ONLY_CARDS = {}


def python_only_cards(session):
    """What of this round no native core runs yet (None: nothing)."""
    deck = session.deck
    for pile in (deck.deck, deck.hand, deck.grave, deck.hold, deck.lost):
        for card in pile:
            why = PYTHON_ONLY_CARDS.get(card.get("produce_card_id"))
            if why:
                return why
    return None


class Native:
    def __init__(self, lib_path):
        from GakumasCLI.native import layout as L, mcompile as MC
        self.L, self.MC = L, MC
        lib = C.CDLL(str(lib_path))
        self.exam_rules_version = lib.gk_exam_rules_version() if hasattr(lib, "gk_exam_rules_version") else 0
        lib.gk_new.restype = C.c_void_p
        lib.gk_free.argtypes = [C.c_void_p]
        lib.gk_import.argtypes = [C.c_void_p, C.POINTER(L.GkmsState), C.POINTER(MC.MasterDesc), C.POINTER(MC.RoundDesc)]
        lib.gk_export.argtypes = [C.c_void_p, C.POINTER(L.GkmsState)]
        lib.gk_copy.argtypes = [C.c_void_p, C.c_void_p]
        lib.gk_step.argtypes = [C.c_void_p, C.c_int, C.c_int]
        lib.gk_legal.argtypes = [C.c_void_p, C.POINTER(C.c_int32)]
        lib.gk_unsup.argtypes = [C.c_void_p, C.POINTER(C.c_int32)]
        lib.gk_finished.argtypes = [C.c_void_p]
        lib.gk_score.argtypes = [C.c_void_p]
        lib.gk_score.restype = C.c_int64
        lib.gk_rollouts.argtypes = [C.c_void_p, C.c_int64, C.c_uint64, C.POINTER(C.c_int64), C.POINTER(C.c_int64)]
        lib.gk_rollouts.restype = C.c_int64
        assert lib.gk_state_size() == C.sizeof(L.GkmsState), "layout / header mismatch (GkmsState)"
        if (not hasattr(lib, "gk_trigger_row_size")
                or lib.gk_trigger_row_size() != C.sizeof(MC.TriggerRow)):
            raise RuntimeError("rebuild it")
        if not hasattr(lib, "gk_effect_row_size") or lib.gk_effect_row_size() != C.sizeof(MC.EffectRow):
            raise RuntimeError("rebuild gkmscore")
        if not hasattr(lib, "gk_grow_row_size") or lib.gk_grow_row_size() != C.sizeof(MC.GrowRow):
            raise RuntimeError("rebuild gkmscore")
        if hasattr(lib, "gk_master_desc_size"):
            assert lib.gk_master_desc_size() == C.sizeof(MC.MasterDesc), "header mismatch (MasterDesc)"
            size = C.sizeof(MC.RoundDesc)
            legacy = size - 16 if self.exam_rules_version < 1 else size - 8 if self.exam_rules_version < 4 else size
            assert lib.gk_round_desc_size() == legacy, "header mismatch (RoundDesc)"
        self.lib = lib
        self.core = lib.gk_new()

    def load_master(self, master, strings):
        strings = list(strings)
        strings.extend(sorted(self.MC.strings_needed(master) - set(strings)))
        md, keep = self.MC.compile_master(master, strings)
        self.strings = strings
        self.source_master = master
        self.index = {s: i for i, s in enumerate(strings)}
        self.md, self._keep = md, keep

    def load_round(self, session):
        if self.exam_rules_version < MIN_EXAM_RULES:
            raise RuntimeError(f"原生核心的规则版本 {self.exam_rules_version} 低于 {MIN_EXAM_RULES}，请更新 DLL")
        why = python_only_cards(session)
        if why:
            raise RuntimeError(f"原生核心尚未包含{why}")
        if self.source_master is not session.master:
            self.load_master(session.master, self.strings)
        self.rd, self._mask = self.MC.compile_round(session, self.index, len(self.strings))
        self.ctx = self.L.Ctx(self.strings, round_seed=int(session.config.seed), master=session.master)

    def import_bytes(self, portable_bytes):
        from GakumasCLI.native import portable as P
        v, _ = P.decode(portable_bytes)
        g = self.L.pack(v, self.ctx)
        self.lib.gk_import(self.core, C.byref(g), C.byref(self.md), C.byref(self.rd))

    def export_bytes(self):
        from GakumasCLI.native import portable as P
        g = self.L.GkmsState()
        self.lib.gk_export(self.core, C.byref(g))
        return P.encode_decoded(self.L.unpack(g, self.ctx))

    def step(self, action):
        kind = {"play": 0, "drink": 1, "end": 2}[action[0]]
        idx = int(action[1]) if len(action) > 1 else 0
        return self.lib.gk_step(self.core, kind, idx)

    def unsup(self):
        info = C.c_int32()
        line = self.lib.gk_unsup(self.core, C.byref(info))
        return line, info.value

    def legal(self):
        buf = (C.c_int32 * 256)()
        n = self.lib.gk_legal(self.core, buf)
        out = []
        for i in range(n):
            k, x = buf[i] >> 8, buf[i] & 0xFF
            out.append(("play", x) if k == 0 else ("drink", x) if k == 1 else ("end",))
        return out
