# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The exam session: a round in the native core (the core, core_*.h)."""
from __future__ import annotations

import copy
import ctypes as C
import hashlib
import io
import json
import os

from GakumasCLI import paths as gkms_paths
from GakumasCLI.native import layout as L, portable as P

API_VERSION = 6
FAILURES = []
HANDBACKS = []
OPENS = []
ZONE = {"deck": 0, "hand": 1, "grave": 2, "hold": 3, "lost": 4}
GK_BIND_EVENTS, GK_BIND_SELECTS, GK_BIND_CANDS = 64, 8, 80
GK_SPLIT_SOURCES = 32
REASONS = {1: "本回合没有剩余行动", 2: "当前状态禁止使用", 3: "使用条件未满足"}
STATUS_LABELS = {5: "集中", 6: "好印象", 7: "やる気", 8: "好調", 9: "絶好調"}
COST_TYPE_NAMES = {}


def backend(options=None):
    """The engine the follower's sessions run on: ``native``."""
    return "native"


class NativeUnsupported(RuntimeError):
    """The core refused (UNSUP) or the binding cannot carry what was asked."""

    def __init__(self, where, detail=""):
        super().__init__(f"{where}: {detail}" if detail else where)
        self.where, self.detail = where, detail


def _fail(where, detail=""):
    """A refusal in the middle of a battle (FAILURES)."""
    FAILURES.append((where, str(detail)))
    raise NativeUnsupported(where, detail)


class BindEvent(C.Structure):
    _fields_ = [("kind", C.c_int32), ("ref", C.c_int32), ("upgrade", C.c_int32), ("stance", C.c_int32),
                ("turn", C.c_int64), ("score", C.c_int64), ("stamina", C.c_int64), ("block", C.c_int64),
                ("fpp", C.c_int64)]


class BindSelect(C.Structure):
    _fields_ = [("effect", C.c_int32), ("search", C.c_int32), ("pick_min", C.c_int64), ("pick_max", C.c_int64),
                ("n_cand", C.c_int32), ("n_picked", C.c_int32), ("cand", L.CardNo * GK_BIND_CANDS),
                ("picked", L.CardNo * GK_BIND_CANDS)]


class BindSource(C.Structure):
    """core_base.h BindSource -- one source of a split's score adds."""
    _fields_ = [("kind", C.c_int32), ("source", C.c_int32), ("settled", C.c_int32), ("pad", C.c_int32),
                ("value", C.c_int64)]


class BindTrace(C.Structure):
    _fields_ = [("turn", C.c_int32), ("effect", C.c_int32), ("type", C.c_int32), ("kind", C.c_int32),
                ("source", C.c_int32), ("indirect", C.c_int32), ("v1", C.c_int64)]


OPEN_HOOK = None
TRACE_CAP = 4096
TRACE = {"on": False, "rows": []}


class BindLog(C.Structure):
    _fields_ = [("overflow", C.c_int32), ("ts_valid", C.c_int32), ("ts_hand_n", C.c_int32),
                ("ts_turn", C.c_int64), ("ts_score", C.c_int64), ("ts_stamina", C.c_int64), ("ts_review", C.c_int64),
                ("ts_hand", C.c_int32 * L.MAX_ZONE),
                ("n_events", C.c_int32), ("events", BindEvent * GK_BIND_EVENTS),
                ("n_selects", C.c_int32), ("selects", BindSelect * GK_BIND_SELECTS),
                ("split_on", C.c_int32), ("split_pad", C.c_int32), ("split_direct", C.c_int64),
                ("split_indirect", C.c_int64),
                ("trace", C.POINTER(BindTrace)), ("trace_cap", C.c_int32), ("trace_n", C.c_int32),
                ("trace_overflow", C.c_int32), ("trace_mode", C.c_int32),
                ("te_valid", C.c_int32), ("te_pad", C.c_int32), ("te_turn", C.c_int64), ("te_score", C.c_int64),
                ("te_stamina", C.c_int64), ("te_block", C.c_int64), ("te_addition_block", C.c_int64),
                ("te_consumed", C.c_int64),
                ("ss_n", C.c_int32), ("ss_pad", C.c_int32), ("ss_rest", C.c_int64),
                ("ss", BindSource * GK_SPLIT_SOURCES)]


class OpenStatus(C.Structure):
    _fields_ = [("type", C.c_int32), ("sid", C.c_int32), ("a0", C.c_int64)]


class OpenDesc(C.Structure):
    """core_base.h OpenDesc -- what ExamSession.start_turn reads at turn 1 beyond the state."""
    _fields_ = [("n_status", C.c_int32), ("pad", C.c_int32), ("status", OpenStatus * 32),
                ("has_param_values", C.c_int32), ("n_values", C.c_int32),
                ("value_type", C.c_int64 * 3), ("value_value", C.c_int64 * 3),
                ("audition_limit_turn", C.c_int64), ("npc_advances", C.c_int64), ("prefix_advances", C.c_int64),
                ("initial_mode", C.c_int32), ("n_initial", C.c_int32), ("initial", C.c_int32 * 16)]


# the fields an unopened session (exam_session.deferred_open) has not set yet; the core's opening sets each
_OPEN_UNSET = (("deck", "current_turn", 0), ("deck", "current_parameter_type", 0),
               ("state", "current_parameter_type", 0), ("state", "turn_playable_start", 0),
               ("state", "turn_start_hand_numbers", []), ("state", "_running_trigger_state", None))


def _pack_unopened(session, rnd):
    """The loading of ``session`` (turn 0) as a GkmsState, the unset fields packed as zero (restored afterwards)."""
    saved = []
    for where, name, value in _OPEN_UNSET:
        obj = session if where == "session" else getattr(session, where)
        saved.append((obj, name, obj.__dict__[name] if name in obj.__dict__ else _OPEN_UNSET))
        if getattr(obj, name, None) is None:
            setattr(obj, name, value)
    try:
        v, _ = P.decode(P.encode_session(session, rnd.rm))
        return L.pack(v, rnd.ctx)
    except L.Unsupported as exc:
        raise NativeUnsupported("open", f"pack: {str(exc)[:160]}") from None
    finally:
        for obj, name, old in saved:
            if old is _OPEN_UNSET:
                obj.__dict__.pop(name, None)
            else:
                obj.__dict__[name] = old


def open_desc(session, rnd, g):
    """The OpenDesc of an unopened ``session`` (g: its packed state, for the 初期手札's card slots)."""
    d = OpenDesc()
    if getattr(session.config, "opening_statuses_are_observed", False):
        raise NativeUnsupported("open", "opening statuses observed (a replay runner)")
    rows = session.opening_statuses()
    if len(rows) > 32:
        raise NativeUnsupported("open", f"{len(rows)} opening statuses")
    for i, row in enumerate(rows):
        if row.get("status_type") != "TriggerEffect":
            raise NativeUnsupported("open", f"opening status {row.get('status_type')}")
        args = row["args"]
        s = d.status[i]
        s.type = 1
        try:
            s.sid = rnd.ctx.sid(args[1]) if args[1] else -1
        except L.Unsupported as exc:
            raise NativeUnsupported("open", str(exc)[:160]) from None
        s.a0 = int(args[0]) if int(args[0]) >= 0 else -1
    d.n_status = len(rows)
    dk = session.deck
    values = dk.audition_parameter_values
    if values is not None:
        items = list(values.items())
        try:
            if len(items) != 3:
                raise ValueError(len(items))
            for i, (k, v) in enumerate(items):
                d.value_type[i], d.value_value[i] = int(k), int(v)
        except (TypeError, ValueError) as exc:
            raise NativeUnsupported("open", f"audition_parameter_values {exc}") from None
        d.has_param_values, d.n_values = 1, 3
        d.audition_limit_turn = int(dk.audition_limit_turn)
    d.npc_advances = int(dk.audition_npc_rng_advances or 0)
    d.prefix_advances = int(dk.segment_start_rng_advances or 0)
    ids = tuple(dk.initial_card_ids or ())
    if ids:
        objs = dk._initial_card_object_ids
        if objs is not None:
            want = [c for c in dk.deck if id(c) in objs]
            slots = {}
            tmp = L.CardNo()
            for c in want:
                L._cardno_pack(c["number"], tmp, rnd.ctx, "initial")
                hit = [i for i in range(g.n_cards) if g.cards[i].number.n == tmp.n and g.cards[i].number.g == tmp.g]
                if len(hit) != 1:
                    raise NativeUnsupported("open", f"initial card {c['number']!r}: {len(hit)} slots")
                slots[hit[0]] = True
            d.initial_mode, vals = 1, list(slots)
        else:
            try:
                d.initial_mode, vals = 2, [rnd.ctx.sid(x) for x in ids]
            except L.Unsupported as exc:
                raise NativeUnsupported("open", str(exc)[:160]) from None
        if len(vals) > 16:
            raise NativeUnsupported("open", f"{len(vals)} initial cards")
        d.n_initial = len(vals)
        for i, x in enumerate(vals):
            d.initial[i] = x
    return d


_LIB = {}


def _lib():
    """The core (``GKMS_NATIVE_LIB``, else this tree's build) with the binding's calls typed; one per process."""
    if "nat" in _LIB:
        return _LIB["nat"]
    from GakumasCLI.native.diffrun import Native, MIN_EXAM_RULES
    path = os.environ.get("GKMS_NATIVE_LIB") or str(
        gkms_paths.NATIVE_DLL if os.name == "nt" else gkms_paths.NATIVE_DIR / "libgkmscore.so")
    nat = Native(path)
    lib = nat.lib
    if nat.exam_rules_version < MIN_EXAM_RULES:
        raise NativeUnsupported("core", f"rules {nat.exam_rules_version} < {MIN_EXAM_RULES}")
    if not hasattr(lib, "gk_api_version") or lib.gk_api_version() < API_VERSION:
        raise NativeUnsupported("core", "no binding calls (gk_api_version)")
    if lib.gk_bind_log_size() != C.sizeof(BindLog):
        raise NativeUnsupported("core", f"BindLog {lib.gk_bind_log_size()} != {C.sizeof(BindLog)}")
    vp = C.c_void_p
    lib.gk_set_bind_log.argtypes = [vp, C.POINTER(BindLog)]
    for name in ("gk_play", "gk_use_drink", "gk_end_turn"):
        getattr(lib, name).argtypes = [vp, C.c_int]
    lib.gk_start_turn.argtypes = [vp]
    if not hasattr(lib, "gk_open") or lib.gk_open_desc_size() != C.sizeof(OpenDesc):
        raise NativeUnsupported("core", "no gk_open / OpenDesc size")
    lib.gk_open.argtypes = [vp, C.POINTER(OpenDesc)]
    lib.gk_new_card.argtypes = [vp, C.c_int32, C.c_int64]
    lib.gk_select_queue.argtypes = [vp, C.POINTER(C.c_int32), C.POINTER(C.c_int64)]
    lib.gk_set_select_queue.argtypes = [vp, C.c_int, C.POINTER(C.c_int32), C.POINTER(C.c_int64)]
    lib.gk_card_info.argtypes = [vp, C.c_int, C.c_int, C.POINTER(C.c_int64)]
    lib.gk_shuffle.argtypes = [C.POINTER(C.c_uint32), C.POINTER(C.c_int32), C.c_int]
    lib.gk_debuff_count.argtypes = [vp]
    nat.strings = json.load(open(os.environ.get("GKMS_NATIVE_STRINGS") or gkms_paths.NATIVE_STRINGS, encoding="utf-8"))
    _LIB["nat"] = nat
    _LIB["masters"] = {}
    return nat


def gk_shuffle(items, state):
    """the core's deck shuffle (core_deck.h deck_shuffle, exported as gk_shuffle) of ``items`` from the RNG state
    ``state``: (the items in their shuffled order, the RNG state after it) -- for the seed tools
    (tools/hif_seed_solve.py), which have no exam session at that point."""
    items = list(items)
    lib = _lib().lib
    order = (C.c_int32 * max(1, len(items)))(*range(len(items)))
    st = C.c_uint32(int(state) & 0xFFFFFFFF)
    lib.gk_shuffle(C.byref(st), order, len(items))
    return [items[i] for i in order[:len(items)]], int(st.value)


def _key(k):
    """A map key or set member as the engine keys it: a list is a tuple (debuff_since's ("play_card_limit", search))."""
    return tuple(_key(x) for x in k) if isinstance(k, list) else k


_STATE_STATICS = ("exam_effects", "status_enchants", "exam_triggers", "exam_setting", "gimmick_rows", "card_searches",
                  "card_pools", "idol_unique_item_enchant_ids", "audition_permils", "card_status_enchants")


_DECK_STATICS = ("card_customizes", "card_grow_effects", "upgrade_tiers", "eligible_supports", "initial_card_ids",
                 "initial_card_id", "audition_parameter_values", "audition_limit_turn", "audition_npc_rng_advances",
                 "segment_start_rng_advances", "play_move_position_type_enabled", "recycle_order")


def _deck_statics(deck):
    """the loading deck's fixed inputs and tables (references)."""
    return {name: getattr(deck, name) for name in _DECK_STATICS if hasattr(deck, name)}


def _state_statics(st):
    """the loading state's fixed tables (solver._SEARCH_STATIC_STATE_FIELDS), read by the panel and the search side, kept
    as they are (references)."""
    return {name: getattr(st, name) for name in _STATE_STATICS if hasattr(st, name)}


def _resolve_refs(v, rnd):
    """``("$ref", "<table>|<key>")`` of a decoded value as the object it names (the round's refmap)."""
    if isinstance(v, tuple) and len(v) == 2 and v[0] == "$ref":
        names = getattr(rnd, "_by_name", None)
        if names is None:
            names = rnd._by_name = {name: obj for obj in rnd.rm.keep for name in (rnd.rm.by_id.get(id(obj)),) if name}
        return names.get(v[1], v)
    if isinstance(v, list):
        return [_resolve_refs(x, rnd) for x in v]
    if isinstance(v, dict):
        return {k: _resolve_refs(x, rnd) for k, x in v.items()}
    return v


def _plain(v):
    """A decoded portable value as the engine's Python objects (records and maps as dicts, sets as sets)."""
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) and v[0].startswith("$"):
        kind, body = v
        if kind in ("$rec", "$map"):
            return {_key(k): _plain(x) for k, x in body}
        if kind == "$set":
            return set(_key(_plain(x)) for x in body)
        return body
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


class _Round:
    """A round's compiled description, shared by a session and all its clones."""

    def __init__(self, session):
        from GakumasCLI.native.diffrun import python_only_cards
        nat = _lib()
        why = python_only_cards(session)
        if why:
            raise NativeUnsupported("python_only_card", why)
        masters = _LIB["masters"]
        key = id(session.master)
        hit = masters.get(key)
        if hit is None or hit[4] is not session.master:
            nat.load_master(session.master, nat.strings)
            hit = (nat.md, nat.strings, nat.index, nat._keep, session.master)
            while len(masters) >= 2:
                masters.pop(next(iter(masters)))
            masters[key] = hit
        self.md, self.strings, self.index, self._keep, self._master = hit
        try:
            self.rd, self._mask = nat.MC.compile_round(session, self.index, len(self.strings))
        except (ValueError, KeyError, L.Unsupported) as exc:
            raise NativeUnsupported("compile", f"{type(exc).__name__}: {str(exc)[:160]}") from None
        self.ctx = L.Ctx(self.strings, round_seed=int(session.config.seed), master=session.master)
        self.rm = P.session_refmap(session, master_refmap(session.master))
        self.lib = nat.lib
        if not COST_TYPE_NAMES:
            for row in session.master.cards.values():
                ct = row.get("costType") or {}
                if isinstance(ct, dict) and ct.get("number") is not None:
                    COST_TYPE_NAMES.setdefault(int(ct["number"]), ct.get("name"))


class _FrozenList(list):
    """A pile as the core holds it: reading works as on a list, writing would not reach the core."""

    def _no(self, *a, **k):
        _fail("write", "a pile of a native session")

    append = extend = insert = remove = pop = clear = sort = reverse = __setitem__ = __delitem__ = __iadd__ = _no


class _DeckView:
    def __init__(self, session):
        object.__setattr__(self, "_s", session)

    def __getattr__(self, name):
        s = self._s
        if name in ZONE:
            return s._zone(name)
        if name == "select_choices":
            return s._select_choices
        if name == "select_policy":
            return s._select_policy
        if name == "card_master":
            return s._card_master
        if name == "hold_limit":
            return int(s._export().d_hold_limit)
        if name == "hand_limit":
            return int(s._export().d_hand_limit)
        if name == "rng":
            return _RngView(s)
        if name in s._deck_static:
            return s._deck_static[name]
        return s._decoded_part("deck", name)

    def __setattr__(self, name, value):
        s = self._s
        if name == "select_choices":
            s._select_choices = value
        elif name == "select_policy":
            s._select_policy = value
            want = int(getattr(value, "rule_name", None) == "greedy_select_policy")
            if want != int(s._round.rd.select_greedy):
                s._set_select_greedy(want)
        else:
            _fail("write", f"deck.{name}")

    def refresh_card(self, instance):
        return self._s._resolved(instance)


_SCALARS = {spec[0]: spec for spec in L.STATE_SCALARS}


class _RngView:
    """deck.rng of a native session: ``state`` is the core's (GkmsState.rng), set on the core (hif_degraded.recommend
    samples RNG worlds through it)."""

    def __init__(self, session):
        object.__setattr__(self, "_s", session)

    @property
    def state(self):
        return int(self._s._export().rng)

    @state.setter
    def state(self, value):
        s = self._s
        g = L.GkmsState()
        s._lib.gk_export(s._core, C.byref(g))
        g.rng = int(value) & 0xFFFFFFFF
        rnd = s._round
        s._lib.gk_import(s._core, C.byref(g), C.byref(rnd.md), C.byref(rnd.rd))
        s._dirty()


class _StateView:
    def __init__(self, session):
        object.__setattr__(self, "_s", session)

    def __getattr__(self, name):
        s = self._s
        if name == "select_index_queue":
            return s._queue
        if name in s._static:
            return s._static[name]
        spec = _SCALARS.get(name)
        if spec is None:
            return s._decoded_state(name)
        st = s._export().st
        kind = spec[1]
        if len(spec) > 2 and not getattr(st, "has_" + name):
            return None
        v = getattr(st, name)
        if kind == "status":
            return L._CODE_TO_STATUS.get(int(v))
        if kind == "bool":
            return bool(v)
        if kind in ("f64", "opt_f64"):
            return float(v)
        if kind == "opt_i64":
            return None if int(v) == L.NONE_I64 else int(v)
        return int(v)

    def __setattr__(self, name, value):
        if name == "select_index_queue":
            self._s._queue = list(value)
        else:
            _fail("write", f"state.{name}")

    def calculate_buff_cost(self, value):
        _fail("rule", "state.calculate_buff_cost")


class _Stats:
    """what one followed round's sessions (the followed one, its clones, its previews) had refused."""

    def __init__(self, record=None):
        self.record = record
        self.handbacks = []
        self.mismatch = None
        self.checked = 0
        self.edits = 0
        self.opened = None
        self.reads = {}

    def report(self):
        followed = [h for h in self.handbacks if h["followed"]]
        return {"engine": "native", "opened": self.opened, "native_fallback": len(followed), "handbacks": followed[:10],
                "probe_handbacks": len(self.handbacks) - len(followed),
                "probe_handback_first": next((h for h in self.handbacks if not h["followed"]), None),
                "shadow_steps": self.checked, "shadow_mismatch": 0 if self.mismatch is None else 1,
                "first_mismatch": self.mismatch, "edits": self.edits,
                "shadow_reads": dict(sorted(self.reads.items(), key=lambda kv: -kv[1])[:20])}


_EDIT_FIELDS = ("score", "stamina", "block")


class _EditState:
    """the numbers a follower edit writes (hif_degraded.set_numbers, LiveRound resync)."""

    def __init__(self, g):
        self.__dict__["_g"] = g

    @staticmethod
    def _kind(name):
        spec = _SCALARS.get(name)
        if name in _EDIT_FIELDS:
            return "i64"
        if spec is not None and spec[1] in ("i64", "bool") and len(spec) == 2:
            return spec[1]
        if spec is not None and spec[1] == "opt_i64":
            return "opt_has" if len(spec) > 2 else "opt_i64"
        return None

    def __getattr__(self, name):
        kind, st = self._kind(name), self.__dict__["_g"].st
        if kind == "i64":
            return int(getattr(st, name))
        if kind == "bool":
            return bool(getattr(st, name))
        if kind == "opt_has":
            return int(getattr(st, name)) if getattr(st, "has_" + name) else None
        if kind == "opt_i64":
            return None if int(getattr(st, name)) == L.NONE_I64 else int(getattr(st, name))
        raise AttributeError(f"edit: state.{name}")

    def __setattr__(self, name, value):
        kind, st = self._kind(name), self.__dict__["_g"].st
        if kind is None:
            _fail("write", f"edit: state.{name}")
        if kind == "opt_has":
            setattr(st, "has_" + name, 0 if value is None else 1)
            setattr(st, name, 0 if value is None else int(value))
        elif kind == "opt_i64":
            setattr(st, name, L.NONE_I64 if value is None else int(value))
        else:
            setattr(st, name, int(value))


class _EditDeck:
    """a native session's piles as card dicts for a follower edit (hif_degraded.realign, LiveRound resync): each card a
    dict of its slot's instance fields; a card made here (``_generated_card``) gets its slot from the core
    (gk_new_card) at write_back."""

    def __init__(self, session, g):
        self._s, self._g, self._ctx = session, g, session._round.ctx
        self.card_master = session._card_master
        self._ids = None
        self._made = int(g.d_generated_card_count)
        for name, zi in ZONE.items():
            setattr(self, name, [self._card(g.zone[zi][j]) for j in range(g.zone_n[zi])])

    def _card(self, slot):
        c, s = self._g.cards[slot], self._ctx.s
        return {"_slot": int(slot), "number": L._cardno_unpack(c.number), "produce_card_id": s(c.produce_card_id),
                "base_upgrade_count": int(c.base_upgrade_count), "current_upgrade_count": int(c.current_upgrade_count),
                "temporary_upgrade_count": int(c.temporary_upgrade_count),
                "support_upgrade_ids": [s(c.support_upgrade_ids[k]) for k in range(c.n_support_upgrade_ids)]}

    def refresh_card(self, card):
        """deck.py refresh_card's count; a card id with no master row raises KeyError there too."""
        card["current_upgrade_count"] = (int(card["base_upgrade_count"]) + int(card.get("temporary_upgrade_count", 0))
                                         + len(card.get("support_upgrade_ids", ())))
        if self.card_master:
            if self._ids is None:
                self._ids = {k[0] for k in self.card_master}
            if card["produce_card_id"] not in self._ids:
                raise KeyError(card["produce_card_id"])
        return card

    def _generated_card(self, produce_card_id, upgrade_count):
        self._made += 1
        card = {"_slot": None, "number": f"G{self._made}", "produce_card_id": produce_card_id,
                "base_upgrade_count": int(upgrade_count), "current_upgrade_count": int(upgrade_count),
                "temporary_upgrade_count": 0, "support_upgrade_ids": []}
        return self.refresh_card(card)


class _EditSession:
    """what a follower edit function reads and writes of a session, over a copy of the exported state."""
    native = False

    def __init__(self, session, g):
        self._session, self.g = session, g
        self.deck = _EditDeck(session, g)
        self.state = _EditState(g)
        self.turn = int(g.s_turn)

    def observe(self):
        return {f: int(getattr(self.g.st, f)) for f in _EDIT_FIELDS}

    def write_back(self):
        """The piles and card fields into ``g``; the cards made here first made by the core (in no zone)."""
        s, d, g = self._session, self.deck, self.g
        ctx = s._round.ctx
        made = [c for name in ZONE for c in getattr(d, name) if c.get("_slot") is None]
        if made:
            lib = s._lib
            for c in made:
                slot = lib.gk_new_card(s._core, ctx.sid(c["produce_card_id"]), int(c["base_upgrade_count"]))
                if slot < 0:
                    _fail("write", f"edit: new card {c['produce_card_id']}")
                c["_slot"] = slot
            fresh = L.GkmsState()
            lib.gk_export(s._core, C.byref(fresh))
            for c in made:
                g.cards[c["_slot"]] = fresh.cards[c["_slot"]]
            g.n_cards, g.d_generated_card_count = fresh.n_cards, fresh.d_generated_card_count
        seen = set()
        for name, zi in ZONE.items():
            pile = getattr(d, name)
            if len(pile) > L.MAX_ZONE:
                _fail("write", f"edit: {name} {len(pile)} cards")
            for j, c in enumerate(pile):
                slot = c["_slot"]
                if slot in seen:
                    _fail("write", f"edit: card slot {slot} twice")
                seen.add(slot)
                g.zone[zi][j] = slot
                card = g.cards[slot]
                support = list(c.get("support_upgrade_ids", ()))
                if len(support) > L.MAX_SUPPORT_UP:
                    _fail("write", f"edit: {len(support)} support tiers")
                card.base_upgrade_count = int(c["base_upgrade_count"])
                card.temporary_upgrade_count = int(c.get("temporary_upgrade_count", 0))
                card.current_upgrade_count = int(c["current_upgrade_count"])
                card.n_support_upgrade_ids = len(support)
                for k, x in enumerate(support):
                    card.support_upgrade_ids[k] = ctx.sid(x)
            g.zone_n[zi] = len(pile)


class NativeExamSession:
    native = True
    shadow = None
    is_produce_run = False
    step_index = None
    _loadout = None
    _loading = None
    _seed = None
    _deck_static = {}
    _stats = None
    _watch = False

    @classmethod
    def from_unopened(cls, session):
        """the core of an unopened ``session`` (an exam_session.ExamLoading), opened by the core (gk_open)."""
        if int(session.turn) != 0 or session.deck.initialized:
            raise NativeUnsupported("open", "already opened")
        rnd = _Round(session)
        g = _pack_unopened(session, rnd)
        desc = open_desc(session, rnd, g)
        self = cls.__new__(cls)
        self._round, self._lib = rnd, rnd.lib
        self._core = rnd.lib.gk_new()
        self._attach()
        rnd.lib.gk_import(self._core, C.byref(g), C.byref(rnd.md), C.byref(rnd.rd))
        self.master, self.config, self.produce = session.master, session.config, session.produce
        self._take_statics(session)
        self.log, self.turn_start_reads, self.turn_end_reads = [], {}, {}
        self._queue = [list(x) for x in session.state.select_index_queue]
        self._select_choices = list(getattr(session.deck, "select_choices", None) or [])
        self._select_policy = getattr(session.deck, "select_policy", None)
        self._card_master = getattr(session.deck, "card_master", None)
        st = session.state
        self._static = {"plan_type": getattr(st, "plan_type", None),
                        "research_card_ids": getattr(st, "research_card_ids", None),
                        "unresolved_effects": st.unresolved_effects,
                        "unresolved_statuses": list(getattr(st, "unresolved_statuses", ()) or ())}
        self._static.update(_state_statics(st))
        self._deck_static = _deck_statics(session.deck)
        self._resolved_cache = {}
        self._stats = _Stats()
        self._open_desc = desc
        self._loading = session
        if OPEN_HOOK is not None:
            OPEN_HOOK(self)
        self._call("open", rnd.lib.gk_open, C.byref(desc))
        return self

    @classmethod
    def from_portable(cls, loading, portable):
        """The core of ``loading`` holding the state ``portable`` encodes."""
        rnd = _Round(loading)
        v, _ = P.decode(portable)
        try:
            g = L.pack(v, rnd.ctx)
        except L.Unsupported as exc:
            raise NativeUnsupported("import", str(exc)[:160]) from None
        self = cls.__new__(cls)
        self._round, self._lib = rnd, rnd.lib
        self._core = rnd.lib.gk_new()
        self._attach()
        rnd.lib.gk_import(self._core, C.byref(g), C.byref(rnd.md), C.byref(rnd.rd))
        self.master, self.config, self.produce = loading.master, loading.config, loading.produce
        self._take_statics(loading)
        self.log, self.turn_start_reads, self.turn_end_reads = [], {}, {}
        self._queue = [list(x) for x in _plain(dict(dict(v[1])["state"][1]).get("select_index_queue") or [])]
        self._select_choices = list(getattr(loading.deck, "select_choices", None) or [])
        self._select_policy = getattr(loading.deck, "select_policy", None)
        self._card_master = getattr(loading.deck, "card_master", None)
        st = loading.state
        self._static = {"plan_type": getattr(st, "plan_type", None),
                        "research_card_ids": getattr(st, "research_card_ids", None),
                        "unresolved_effects": list(st.unresolved_effects),
                        "unresolved_statuses": list(getattr(st, "unresolved_statuses", ()) or ())}
        self._static.update(_state_statics(st))
        self._deck_static = _deck_statics(loading.deck)
        self._resolved_cache = {}
        self._stats = _Stats()
        return self

    def raw_legal(self):
        """gk_legal as the core reads it (the hand's cached resolution; ``legal_actions`` refreshes it first)."""
        if self.finished:
            return []
        buf = (C.c_int32 * 256)()
        n = self._lib.gk_legal(self._core, buf)
        return [("play", buf[i] & 0xFF) if buf[i] >> 8 == 0 else ("drink", buf[i] & 0xFF) if buf[i] >> 8 == 1
                else ("end",) for i in range(n)]

    @classmethod
    def from_state(cls, twin, rest):
        """the core of ``twin`` (its loading, unopened) with the exported state of a native_dump."""
        rnd = _Round(twin)
        if rest.get("guid_prefixes"):
            rnd.ctx.__dict__["guid_prefixes"] = list(rest["guid_prefixes"])
        if rest.get("guid_table"):
            rnd.ctx.__dict__["guid_table"] = list(rest["guid_table"])
        g = L.GkmsState.from_buffer_copy(rest["state"])
        self = cls.__new__(cls)
        self._round, self._lib = rnd, rnd.lib
        self._core = rnd.lib.gk_new()
        self._attach()
        rnd.lib.gk_import(self._core, C.byref(g), C.byref(rnd.md), C.byref(rnd.rd))
        self.master, self.config = twin.master, twin.config
        self.produce = rest.get("produce", twin.produce)
        self._take_statics(twin)
        self._loadout, self._seed = rest.get("loadout"), rest.get("seed")
        self._loading = twin
        self.log = list(rest["log"])
        self.turn_start_reads = dict(rest["turn_start_reads"])
        self.turn_end_reads = dict(rest.get("turn_end_reads") or {})
        self._queue = [list(x) for x in rest["queue"]]
        self._select_choices = None if rest["select_choices"] is None else list(rest["select_choices"])
        self._select_policy = getattr(twin.deck, "select_policy", None)
        self._card_master = getattr(twin.deck, "card_master", None)
        st = twin.state
        self._static = {"plan_type": getattr(st, "plan_type", None),
                        "research_card_ids": getattr(st, "research_card_ids", None),
                        "unresolved_effects": list(rest.get("unresolved", ())),
                        "unresolved_statuses": list(getattr(st, "unresolved_statuses", ()) or ())}
        self._static.update(_state_statics(st))
        self._deck_static = _deck_statics(twin.deck)
        self._resolved_cache = {}
        self._stats = _Stats()
        return self

    def _take_statics(self, session):
        """what the session (not its state) holds and the search side reads -- ExamSession.is_produce_run / step_index
        (solver.search_state_key, entry_state_hash)."""
        self.is_produce_run = bool(getattr(session, "is_produce_run", False))
        self.step_index = getattr(session, "step_index", None)
        self._loadout = getattr(self, "_loadout", None)

    def _mirror(self, where, native_call):
        """A step on the core."""
        if not self._watch:
            return native_call()
        backup = self._lib.gk_new()
        self._lib.gk_copy(backup, self._core)
        queue = [list(x) for x in self._queue]
        try:
            return native_call()
        except NativeUnsupported as exc:
            self._lib.gk_copy(self._core, backup)
            self._lib.gk_set_bind_log(self._core, C.byref(self._blog))
            self._queue[:] = queue
            self._dirty()
            rec = {"where": where, "detail": f"{exc.where}: {exc.detail}"[:200], "turn": int(self.turn),
                   "followed": True, "restored": True}
            HANDBACKS.append((rec["where"], rec["detail"]))
            self._stats.handbacks.append(rec)
            if self._stats.record is not None:
                self._stats.record(dict(kind="hif.native_refused", **rec))
            raise
        finally:
            self._lib.gk_free(backup)

    def edit(self, fn, *args, **kwargs):
        """A follower write (hif_degraded.realign / set_numbers, LiveRound.resync): ``fn(view,...)`` edits the exported
        state through _EditSession (its piles as card dicts, its numbers), which is imported back."""
        g = L.GkmsState.from_buffer_copy(memoryview(self._export()).cast("B"))
        view = _EditSession(self, g)
        out = fn(view, *args, **kwargs)
        view.write_back()
        rnd = self._round
        self._lib.gk_import(self._core, C.byref(view.g), C.byref(rnd.md), C.byref(rnd.rd))
        self._dirty()
        self._stats.edits += 1
        return out

    def _set_select_greedy(self, want):
        """the select stand-in on or off for this session (and its clones from here): the core reads it off the round
        (RoundDesc.select_greedy, compiled from the loading deck's select_policy), so the session takes a
        copy of its round with the flag changed and imports its state against it.
        """
        rnd = copy.copy(self._round)
        rnd.rd = type(self._round.rd).from_buffer_copy(self._round.rd)
        rnd.rd.select_greedy = int(want)
        g = L.GkmsState.from_buffer_copy(memoryview(self._export()).cast("B"))
        self._round = rnd
        self._lib.gk_import(self._core, C.byref(g), C.byref(rnd.md), C.byref(rnd.rd))
        self._dirty()

    # -- the core -----------------------------------------------------------------------------------------------
    def _attach(self):
        self._blog = BindLog()
        self._lib.gk_set_bind_log(self._core, C.byref(self._blog))
        self._g = None
        self._zones = None

    @property
    def state(self):
        return _StateView(self)

    @property
    def deck(self):
        return _DeckView(self)

    def __del__(self):
        core = getattr(self, "_core", None)
        if core:
            self._lib.gk_free(core)
            self._core = None

    def _export(self):
        if self._g is None:
            g = L.GkmsState()
            self._lib.gk_export(self._core, C.byref(g))
            self._g = g
        return self._g

    def _dirty(self):
        self._g = None
        self._zones = None

    def _unsup(self, where):
        info = C.c_int32()
        line = self._lib.gk_unsup(self._core, C.byref(info))
        name = self._round.strings[info.value] if 0 <= info.value < len(self._round.strings) else ""
        _fail(where, f"line {line} info {info.value} {name}".strip())

    def watch(self):
        """This is the followed session: a step the core refuses restores it, and (TRACE on) its effects are traced."""
        self._watch = True
        if TRACE["on"] and getattr(self, "_trace_buf", None) is None:
            self._trace_buf = (BindTrace * TRACE_CAP)()
            b = self._blog
            b.trace = C.cast(self._trace_buf, C.POINTER(BindTrace))
            b.trace_cap, b.trace_n, b.trace_overflow, b.trace_mode = TRACE_CAP, 0, 0, 0

    def _drain_trace(self, where):
        b, strings, none = self._blog, self._round.strings, L.NONE_I64
        if b.trace_overflow:
            _fail("trace", f"more than {TRACE_CAP} effects in {where}")
        name = lambda i: strings[i] if 0 <= i < len(strings) else None   # noqa: E731
        for i in range(b.trace_n):
            x = b.trace[i]
            kind = int(x.kind)
            effect = ("tl70-review-turn-end" if int(x.effect) == -1 else
                      f"e_effect-exam_add_grow_effect-p_card_search-deck_all-all-0_0-{name(int(x.v1))}"
                      if int(x.effect) == -2 else name(x.effect))
            source = (name(x.source) if kind in (1, 4) else "timer" if kind == 2 else "gimmick" if kind == 3 else "")
            TRACE["rows"].append({"turn": int(x.turn), "effect_id": effect, "type": int(x.type),
                                  "source": source or "", "indirect": bool(x.indirect),
                                  "value1": None if int(x.v1) == none or int(x.effect) == -2 else int(x.v1)})
        b.trace_n = 0

    def _sync_queue(self):
        q = self._queue
        self._queue_held = [list(row) for row in q[L.MAX_SMALL:]]
        q = q[:L.MAX_SMALL]
        if any(len(row) > L.MAX_SMALL for row in self._queue):
            _fail("select queue", f"a row of {max(len(row) for row in self._queue)}")
        lens = (C.c_int32 * L.MAX_SMALL)()
        vals = (C.c_int64 * (L.MAX_SMALL * L.MAX_SMALL))()
        n = self._lib.gk_select_queue(self._core, lens, vals)
        have = [[int(vals[i * L.MAX_SMALL + j]) for j in range(lens[i])] for i in range(n)]
        if have == q:
            return
        for i, row in enumerate(q):
            lens[i] = len(row)
            for j, x in enumerate(row):
                vals[i * L.MAX_SMALL + j] = int(x)
        if self._lib.gk_set_select_queue(self._core, len(q), lens, vals):
            _fail("select queue", repr(q)[:80])
        self._dirty()

    def _read_queue(self):
        lens = (C.c_int32 * L.MAX_SMALL)()
        vals = (C.c_int64 * (L.MAX_SMALL * L.MAX_SMALL))()
        n = self._lib.gk_select_queue(self._core, lens, vals)
        held = getattr(self, "_queue_held", [])
        if held and not n:
            _fail("select queue", f"{L.MAX_SMALL} rows used in one call, {len(held)} held back")
        self._queue[:] = [[int(vals[i * L.MAX_SMALL + j]) for j in range(lens[i])] for i in range(n)] + held
        self._queue_held = []

    def _name(self, card_id, upgrade):
        cards = self.master.cards
        row = cards.get((card_id, upgrade))
        if row is None:
            tiers = [u for (k, u) in cards if k == card_id and u <= upgrade]
            row = cards.get((card_id, max(tiers))) if tiers else {}
        return (row or {}).get("name")

    def _call(self, where, fn, *args):
        """Run one core call with the bind log cleared, then read what it logged back into the Python side."""
        self._sync_queue()
        b = self._blog
        b.overflow = b.ts_valid = b.n_events = b.n_selects = b.te_valid = 0
        was_finished = self._export().s_finished
        rc = fn(self._core, *args)
        self._dirty()
        if not was_finished and rc == 0 and self._export().s_finished:
            self._finish()
        if rc == 1:
            self._unsup(where)
        if rc == 2:
            raise ValueError(f"illegal action {where}")
        if b.overflow:
            _fail("bind log", where)
        if b.trace_n or b.trace_overflow:
            self._drain_trace(where)
        s = self._round.ctx.s
        status = L._CODE_TO_STATUS
        forced = []
        self.last_forced_card_ids = forced
        for i in range(b.n_events):
            e = b.events[i]
            turn = int(e.turn)
            if e.kind == 1:
                self.log.append({"turn": turn, "play": self._name(s(e.ref), int(e.upgrade)), "score": int(e.score),
                                 "stamina": int(e.stamina), "block": int(e.block), "fpp": int(e.fpp),
                                 "stance": status.get(int(e.stance))})
            elif e.kind == 2:
                self.log.append({"turn": turn, "force_play": self._name(s(e.ref), int(e.upgrade)),
                                 "score": int(e.score), "stamina": int(e.stamina), "block": int(e.block)})
                forced.append(s(e.ref))
            elif e.kind == 3:
                self.log.append({"turn": turn, "drink": s(e.ref), "score": int(e.score), "stamina": int(e.stamina),
                                 "block": int(e.block), "fpp": int(e.fpp), "stance": status.get(int(e.stance))})
            elif e.kind == 4:
                self.log.append({"turn": turn, "move_effect": s(e.ref), "effect_ids": []})
        if b.te_valid:
            self.turn_end_reads[int(b.te_turn)] = {
                "score": int(b.te_score), "stamina": int(b.te_stamina), "block": int(b.te_block),
                "addition_block": int(b.te_addition_block), "consumed_stamina": int(b.te_consumed)}
        if b.ts_valid:
            self.turn_start_reads[int(b.ts_turn)] = {
                "score": int(b.ts_score), "stamina": int(b.ts_stamina), "review": int(b.ts_review),
                "hand": [s(b.ts_hand[i]) for i in range(b.ts_hand_n)]}
        if b.n_selects:
            rule = getattr(self._select_policy, "rule_name", "engine_select_policy")
            for i in range(b.n_selects):
                x = b.selects[i]
                if self._select_choices is not None:
                    self._select_choices.append({
                        "effect_id": s(x.effect), "produce_card_search_id": s(x.search),
                        "picked_numbers": [L._cardno_unpack(x.picked[j]) for j in range(x.n_picked)],
                        "candidate_numbers": [L._cardno_unpack(x.cand[j]) for j in range(x.n_cand)],
                        "rule": rule, "pick_min": int(x.pick_min), "pick_max": int(x.pick_max)})
        self._read_queue()

    # -- what the follower reads --------------------------------------------------------------------------------
    @property
    def turn(self):
        return int(self._export().s_turn)

    @property
    def finished(self):
        return bool(self._export().s_finished)

    @property
    def cleared(self):
        return bool(self._export().s_cleared)

    @property
    def drinks(self):
        g = self._export()
        return [self._round.ctx.s(g.drinks[i]) for i in range(g.n_drinks)]

    @drinks.setter
    def drinks(self, value):
        """the held drinks replaced -- 's kept bottles (hif_session_tail._nia_tail_request, hif_route_search) leave them
        out of a probe: the exported state with the new list, imported back."""
        ids = [str(d) for d in value]
        g = L.GkmsState.from_buffer_copy(memoryview(self._export()).cast("B"))
        if len(ids) > len(g.drinks):
            _fail("write", f"drinks: {len(ids)}")
        g.n_drinks = len(ids)
        for i, d in enumerate(ids):
            g.drinks[i] = self._round.ctx.sid(d)
        rnd = self._round
        self._lib.gk_import(self._core, C.byref(g), C.byref(rnd.md), C.byref(rnd.rd))
        self._dirty()

    def _zone(self, name):
        if self._zones is None:
            self._zones = {}
        pile = self._zones.get(name)
        if pile is None:
            g, ctx = self._export(), self._round.ctx
            zi = ZONE[name]
            pile = _FrozenList(_plain(L._unpack_card(g.cards[g.zone[zi][j]], ctx)) for j in range(g.zone_n[zi]))
            self._zones[name] = pile
        return pile

    def _find(self, instance):
        for name in ("hand", "hold"):
            pile = self._zone(name)
            for i, card in enumerate(pile):
                if card is instance:
                    return ZONE[name], i
        number = instance.get("number")
        for name in ("hand", "hold"):
            for i, card in enumerate(self._zone(name)):
                if card.get("number") == number:
                    return ZONE[name], i
        _fail("card", f"{instance.get('produce_card_id')} {number} in no hand / hold")

    def _info(self, zone, index):
        out = (C.c_int64 * 8)()
        rc = self._lib.gk_card_info(self._core, zone, index, out)
        if rc == 1:
            _fail("card_info", f"{zone} {index}")
        if rc == 2:
            _fail("card_info", f"the core cannot judge {zone} {index}")
        return list(out)

    def can_play(self, instance):
        return bool(self._info(*self._find(instance))[3])

    def playability_reason(self, instance):
        return self._reason(self._info(*self._find(instance)))

    def _reason(self, info):
        reason, need, avail = int(info[4]), int(info[5]), int(info[6])
        if reason == 0:
            return None
        if reason in REASONS:
            return REASONS[reason]
        if reason == 4:
            return f"全力値不足（需要 {need}，当前 {avail}）"
        if reason in STATUS_LABELS:
            return f"{STATUS_LABELS[reason]}不足（需要 {need}，当前 {int(avail or 0)}）"
        st = self._export().st
        if reason == 10:
            return f"体力不足（需要 {need}，当前体力 {int(st.stamina)}）"
        return f"体力不足（需要 {need}，当前体力 {int(st.stamina)}、元気 {int(st.block)}）"

    def _resolved(self, instance):
        from GakumasCLI.engine.card_data import resolve_card_customizes
        cid, upgrade = instance.get("produce_card_id"), int(instance.get("current_upgrade_count") or 0)
        key = (cid, upgrade, json.dumps(instance.get("customizes") or [], sort_keys=True),
               tuple(instance.get("dynamic_grow_effect_ids") or ()))
        hit = self._resolved_cache.get(key)
        if hit is None:
            cards = self.master.cards
            row = cards.get((cid, upgrade))
            if row is None:
                tiers = [u for (k, u) in cards if k == cid and u <= upgrade]
                row = cards[cid, max(tiers)]
            hit = resolve_card_customizes(
                {"customizes": list(instance.get("customizes") or []),
                 "dynamic_grow_effect_ids": list(instance.get("dynamic_grow_effect_ids") or [])},
                row, self.master.customizes, self.master.grow_effects)
            self._resolved_cache[key] = hit
        return hit

    def legal_actions(self):
        if self.finished:
            return []
        self._refresh_hand()
        buf = (C.c_int32 * 256)()
        n = self._lib.gk_legal(self._core, buf)
        out = []
        for i in range(n):
            k, x = buf[i] >> 8, buf[i] & 0xFF
            out.append(("play", x) if k == 0 else ("drink", x) if k == 1 else ("end",))
        return out

    def _refresh_hand(self):
        """Every hand card at its current resolution (gk_card_info refreshes its cached cost and effects)."""
        info = (C.c_int64 * 8)()
        for i in range(int(self._export().zone_n[1])):
            self._lib.gk_card_info(self._core, 1, i, info)
        self._dirty()

    # -- steps ----------------------------------------------------------------------------------------------------
    def step(self, action, observe=True):
        if self.finished:
            raise RuntimeError("segment finished")
        kind = action[0]
        if kind == "select":
            self._queue.append([int(i) for i in action[1]])
            return self.observe() if observe else None
        code = {"play": 0, "drink": 1, "end": 2}.get(kind)
        if code is None:
            raise ValueError(f"unknown action {action!r}")
        index = int(action[1]) if len(action) > 1 else 0
        where = f"step {tuple(action)}"
        if code == 0:
            self._refresh_hand()
        self._mirror(where, lambda: self._call(where, self._lib.gk_step, code, index))
        return self.observe() if observe else None

    def play(self, index):
        where = f"play {index}"
        return self._mirror(where, lambda: self._call(where, self._lib.gk_play, int(index)))

    def use_drink(self, index):
        where = f"drink {index}"
        return self._mirror(where, lambda: self._call(where, self._lib.gk_use_drink, int(index)))

    def end_turn(self, explicit):
        return self._mirror("end_turn", lambda: self._call("end_turn", self._lib.gk_end_turn, 1 if explicit else 0))

    def _finish(self):
        """the stamina goes back to the produce when the segment ends (what exam_session.py end_turn did)."""
        if self.produce is None:
            return
        self.produce.stamina = self.state.stamina
        if self.is_produce_run and isinstance(getattr(self.produce, "provenance", None), dict):
            self.produce.provenance["stamina"] = {
                "source": "c", "locator": f"current segment state: {self.config.segment_id}/end_turn"}

    def start_turn(self):
        return self._mirror("start_turn", lambda: self._call("start_turn", self._lib.gk_start_turn))

    def clone(self):
        """A copy of the core (and what the session holds beside it)."""
        return self._fork()

    def _fork(self):
        other = NativeExamSession.__new__(NativeExamSession)
        other._round, other._lib = self._round, self._lib
        other._core = self._lib.gk_new()
        self._lib.gk_copy(other._core, self._core)
        other._attach()
        other.master, other.config = self.master, self.config
        other.produce = _own_produce(self.produce)
        other.is_produce_run, other.step_index = self.is_produce_run, self.step_index
        other._loadout, other._seed, other._loading = self._loadout, self._seed, self._loading
        other.log = list(self.log)
        other.turn_start_reads = dict(self.turn_start_reads)
        other.turn_end_reads = dict(self.turn_end_reads)
        other._queue = [list(x) for x in self._queue]
        other._select_choices = None if self._select_choices is None else list(self._select_choices)
        other._select_policy, other._card_master = self._select_policy, self._card_master
        other._static, other._resolved_cache = self._static, self._resolved_cache
        other._deck_static = self._deck_static
        other._stats = self._stats
        return other

    def result(self):
        st = self.state
        return {"segment": self.config.segment_id, "turns": self.turn, "score": st.score, "cleared": self.cleared,
                "stamina": st.stamina, "block": st.block, "plays": len(self.log),
                "unresolved": list(self._static["unresolved_effects"])}

    def debuff_count(self):
        """The 低下状態 in effect (core_session.h active_debuff_count, effect 148's list): the timeline rules' ``debuffs``
        milestone (scenario/timeline.py milestone_value)."""
        return int(self._lib.gk_debuff_count(self._core))

    def opening_statuses(self):
        """The statuses the round opened with (exam_session.ExamLoading.opening_statuses: the P items' TriggerEffect
        rows, a produce run's opening enchants, the carried ones) -- loading data, read off the round's loading."""
        if self._loading is None:
            _fail("loading", "opening_statuses of a session without its loading")
        return self._loading.opening_statuses()

    def skip_turn(self):
        """Skip card play for this turn (the game's +2 stamina recovery): an explicit end."""
        return self.end_turn(True)

    def run(self, policy):
        """the policy loop exam_session.ExamSession.run was -- ``policy(session, hand)`` returns a hand index or None
        (end the turn)."""
        while not self.finished:
            choice = policy(self, list(self.deck.hand))
            self.step(("end",) if choice is None else ("play", int(choice)), False)
        return self.result()

    def __reduce__(self):
        return (_native_unpickled, (native_dump(self),))

    def search_tables(self):
        """what native_search.NativeSearch reads of a core, for a search from this session -- a copy of the core
        (``core``, freed with ``free``), the round's master tables (``md``), strings and their index."""
        core = self._lib.gk_new()
        self._lib.gk_copy(core, self._core)
        self._lib.gk_set_bind_log(core, None)
        rnd, lib = self._round, self._lib

        class _Tables:
            md, strings, index = rnd.md, rnd.strings, rnd.index

            def __init__(self):
                self.core = core

            def free(self):
                if self.core:
                    lib.gk_free(self.core)
                    self.core = None
        return _Tables()

    def _decoded_state(self, name):
        return self._decoded_part("state", name)

    def _decoded_part(self, part, name):
        """a ``state`` / ``deck`` field the views do not map, read off the decoded export (the portable record, master
        rows as the master's own objects)."""
        g = self._export()
        hit = getattr(self, "_decoded", None)
        if hit is None or hit[0] is not g:
            v = dict(L.unpack(g, self._round.ctx)[1])
            hit = self._decoded = (g, {"state": dict(v["state"][1]), "deck": dict(v["deck"][1])})
        rec = hit[1][part]
        if name not in rec:
            if os.environ.get("GKMS_SHADOW_READ_LOG"):
                with open(os.environ["GKMS_SHADOW_READ_LOG"], "a", encoding="utf-8") as f:
                    f.write(f"{part}.{name} (not in the export)\n")
            raise AttributeError(f"{part}.{name}")
        return _resolve_refs(_plain(rec[name]), self._round)

    def portable_bytes(self):
        """the state in the portable encoding."""
        g = self._export()
        if L.synchro_state(g):
            raise NativeUnsupported("synchro", "a シンクロ state has no portable form")
        return P.encode_decoded(L.unpack(g, self._round.ctx))

    def search_key(self):
        """solver.search_state_key of a native session -- a 160-bit BLAKE2b digest of the exported state (280,952 bytes,
        mostly unused capacity: as a key it would fill a search's table with 280 KB per node)."""
        return b"GKN" + hashlib.blake2b(memoryview(self._export()).cast("B"), digest_size=20).digest()

    # -- views ------------------------------------------------------------------------------------------------------
    def hand_with_bundles(self):
        """the hand's card ids with each bundle's members after them (the client's hand read lists every card it holds)."""
        g, ctx = self._export(), self._round.ctx
        out = []
        for i, card in enumerate(self._zone("hand")):
            out.append(card.get("produce_card_id"))
            top = g.zone[1][i]
            out += [ctx.s(g.cards[g.zone[6][j]].produce_card_id) for j in range(g.zone_n[6])
                    if g.cards[g.zone[6][j]].fusion_top1 == top + 1]
        return out

    def bundle_index_of(self, card_id):
        """the hand index of the bundle holding ``card_id`` as a member, else None."""
        g, ctx = self._export(), self._round.ctx
        for j in range(g.zone_n[6]):
            m = g.zone[6][j]
            if ctx.s(g.cards[m].produce_card_id) == card_id:
                top = g.cards[m].fusion_top1 - 1
                for i in range(g.zone_n[1]):
                    if g.zone[1][i] == top:
                        return i
        return None

    def _card_view(self, zone, index, instance):
        info = self._info(zone, index)
        cost_type = COST_TYPE_NAMES.get(int(info[0])) if info[0] else None
        if cost_type is None:
            ct = (self._resolved(instance).get("costType") or {})
            cost_type = ct.get("name") if isinstance(ct, dict) else ct
        return {"index": index, "number": instance["number"], "produce_card_id": instance["produce_card_id"],
                "name": self._resolved(instance).get("name"),
                "upgrade": int(instance.get("current_upgrade_count") or 0),
                "playable": bool(info[3]), "playability_reason": self._reason(info),
                "cost_type": cost_type, "cost_value": int(info[1]), "stamina_cost": int(info[2])}

    def observe(self):
        return self._observe_native()

    def _observe_native(self):
        """exam_session.py observe from the core's state (the same keys; the trouble states and timed layers from
        the decoded state)."""
        from GakumasCLI.scenario.exam_session import OBSERVE_SCHEMA_VERSION
        g = self._export()
        st = g.st
        state = self.state
        hand = [self._card_view(1, i, c) for i, c in enumerate(self._zone("hand"))]
        hold = [self._card_view(3, i, c) for i, c in enumerate(self._zone("hold"))]
        if int(getattr(self._round.rd, "synchro", 0) or 0) and g.zone_n[6]:
            ctx = self._round.ctx
            fused = [g.zone[6][j] for j in range(g.zone_n[6])]
            for i, view_card in enumerate(hand):
                top = g.zone[1][i]
                members = [m for m in fused if g.cards[m].fusion_top1 == top + 1]
                if members:
                    view_card["fusion"] = [{"produce_card_id": ctx.s(g.cards[m].produce_card_id),
                                            "upgrade": int(g.cards[m].current_upgrade_count)} for m in members]
        playable, plays = int(st.playable), int(st.turn_play_count)
        view = {
            "observe_schema_version": OBSERVE_SCHEMA_VERSION, "segment": self.config.segment_id,
            "turn": self.turn, "limit_turn": self.config.limit_turn, "finished": self.finished,
            "cleared": self.cleared, "score": int(st.score), "score_vocal": int(st.score_vocal),
            "score_dance": int(st.score_dance), "score_visual": int(st.score_visual),
            "score_cap": self.config.score_cap, "stamina": int(st.stamina), "max_stamina": int(st.max_stamina),
            "block": int(st.block), "full_power_point": int(st.full_power_point),
            "stance": state.idol_status_type, "stance_step": int(st.idol_status_step_count),
            "playable": playable, "turn_play_count": plays, "turn_playable_start": int(st.turn_playable_start),
            "extra_action_granted": max(0, int(st.turn_playable_start or 1) - 1),
            "extra_action": max(0, playable - max(0, 1 - plays)),
            "extra_action_pending": max(0, playable - max(0, 1 - plays)),
            "extra_turns": int(st.extra_turns), "lesson_buff": int(st.lesson_buff), "review": int(st.review),
            "aggressive": state.aggressive, "hand": hand, "hold": hold, "hold_limit": int(g.d_hold_limit),
            "deck_count": int(g.zone_n[0]), "grave_count": int(g.zone_n[2]),
            "unresolved_statuses": list(self._static["unresolved_statuses"]),
        }
        parameter_type = state.current_parameter_type or self.config.parameter_type
        if parameter_type is not None:
            view["parameter_type"] = parameter_type
        dec = _plain(L.unpack(g, self._round.ctx))["state"]
        turn = self.turn

        def active(until, at):
            return until is not None and (until == -1 or at <= until)

        trouble = {}
        for name, until_name in (("gimmick_parameter_debuff", "gimmick_parameter_debuff_until_turn"),
                                 ("slump", "slump_until_turn"), ("block_add_down", "block_add_down_until_turn")):
            if dec.get(name):
                trouble[name] = {"active": True, "until_turn": dec.get(until_name)}
        if dec.get("block_add_down_fix"):
            trouble["block_add_down_fix"] = dec["block_add_down_fix"]
        if dec.get("lesson_debuff"):
            trouble["lesson_debuff"] = {"value": dec["lesson_debuff"], "layers": [
                {"value": v, "until_turn": u} for v, u in (tuple(x[:2]) for x in dec["lesson_debuff_layers"])]}
        if dec.get("stamina_consumption_add_fix"):
            trouble["stamina_consumption_add_fix"] = {"value": dec["stamina_consumption_add_fix"], "layers": [
                {"value": v, "until_turn": u} for v, u in (tuple(x[:2]) for x in dec["stamina_consumption_add_fix_layers"])]}
        draw_down = sum(x[0] for x in dec.get("card_draw_down_layers") or () if active(x[1], turn))
        if draw_down:
            trouble["card_draw_down"] = draw_down
        if dec.get("play_card_limits"):
            trouble["play_card_limits"] = [{"search_id": sid, "until_turn": until}
                                           for sid, until in dec["play_card_limits"]]
        if trouble:
            view["trouble_states"] = trouble

        def left(until, fresh):
            return until - turn + (0 if fresh == turn else 1)

        def rows(layers):
            return [{"value": layer[0], "until_turn": layer[1],
                     "remaining_turns": None if layer[1] in (-1, float("inf")) else max(
                         0, left(layer[1], layer[2] if len(layer) > 2 else 0))} for layer in layers]

        add_fix = [dict(layer) for layer in (dec.get("_timed_gain_layers") or {}).get("lesson_buff_additive_fix", ())]
        for layer in add_fix:
            expire = layer.get("expire")
            layer["until_turn"] = None if expire in (None, float("inf")) else expire
            layer["remaining_turns"] = None if expire in (None, float("inf")) else max(
                0, left(int(expire), layer.get("fresh", 0)))
            layer.pop("expire", None)
            layer.pop("fresh", None)
        view["timed_layers"] = {
            "lesson_buff_multiple_layers": rows(dec["lesson_buff_multiple_layers"]),
            "review_multiple_layers": rows(dec["review_multiple_layers"]),
            "parameter_buff_remaining": dec["parameter_buff_remaining"],
            "parameter_buff_multiple_until_turn": dec.get("parameter_buff_multiple_until_turn"),
            "lesson_buff_additive_fix_layers": add_fix}
        return view

    # -- previews (exam_session.py preview_action / preview_hand) ----------------------------------------------------
    def preview_action(self, action, *, trace=False):
        return self._preview_native(action, trace)

    def _preview_native(self, action, trace):
        from GakumasCLI.scenario.exam_session import OBSERVE_PREVIEW_VERSION
        result = {"version": OBSERVE_PREVIEW_VERSION, "action": tuple(action), "legal": True, "reason": None}
        kind = action[0]
        if self.finished:
            return {**result, "legal": False, "reason": "segment_finished"}
        fork = self._fork()
        if kind == "play":
            index = int(action[1])
            if index < 0 or index >= len(self._zone("hand")):
                return {**result, "legal": False, "reason": "index_out_of_hand"}
            info = fork._info(1, index)
            if not info[3]:
                return {**result, "legal": False, "reason": fork._reason(info) or "当前不可用"}
        elif kind == "drink":
            index = int(action[1])
            if index < 0 or index >= len(self.drinks):
                return {**result, "legal": False, "reason": "index_out_of_drinks"}
        elif kind != "end":
            return {**result, "legal": False, "reason": f"unknown_action:{action!r}"}
        before = self.state.score
        fork._split(True)
        if kind == "play":
            fork.play(int(action[1]))
        elif kind == "drink":
            fork.use_drink(int(action[1]))
        direct, indirect = fork._split(False)
        fs = fork.state
        result["after_action"] = {"score": fs.score, "gain": fs.score - before, "stamina": fs.stamina,
                                  "block": fs.block, "direct": direct, "indirect": indirect}
        if trace:
            result["breakdown"] = []
        settle = fork._fork()
        settle._split(True)
        settle.end_turn(kind == "end" and settle.state.playable > 0)
        _direct, turn_end_indirect = settle._split(False)
        ss = settle.state
        result["after_settlement"] = {"score": ss.score, "gain": ss.score - before, "stamina": ss.stamina,
                                      "block": ss.block, "finished": settle.finished, "cleared": settle.cleared,
                                      "turn_end": ss.score - fs.score, "turn_end_indirect": turn_end_indirect}
        return result

    def _split(self, on):
        """Start (on) or end the gain split of the next calls; ending returns (direct, indirect)."""
        b = self._blog
        out = (int(b.split_direct), int(b.split_indirect))
        b.split_on, b.split_direct, b.split_indirect = (1 if on else 0), 0, 0
        b.ss_n, b.ss_rest = 0, 0
        return out

    SPLIT_KINDS = {0: "none", 1: "enchant", 2: "timer", 3: "gimmick", 4: "card"}

    def split_sources(self):
        """the score the calls since ``_split(True)`` added, by source (core_base.h BindLog.ss): [{kind: card /
        enchant / timer / gimmick / none, source: the card id or the enchant id (None), settled: added once
        the turn's settlement had begun, value}], in the order first added; a source past the table's 32
        rows is in one row of kind "rest".
        """
        b, s = self._blog, self._round.ctx.s
        out = []
        for i in range(int(b.ss_n)):
            x = b.ss[i]
            out.append({"kind": self.SPLIT_KINDS.get(int(x.kind), str(int(x.kind))),
                        "source": s(int(x.source)) if int(x.source) >= 0 else None,
                        "settled": bool(x.settled), "value": int(x.value)})
        if b.ss_rest:
            out.append({"kind": "rest", "source": None, "settled": True, "value": int(b.ss_rest)})
        return out

    def preview_hand(self, *, trace=False):
        rows = [self.preview_action(("play", i), trace=trace) for i in range(len(self._zone("hand")))]
        rows += [self.preview_action(("drink", i), trace=trace) for i in range(len(self.drinks))]
        rows.append(self.preview_action(("end",), trace=trace))
        return rows


def _own_produce(produce):
    """A fork's own produce object: exam_session.py clone deep-copies it, and a fork that finishes writes its stamina
    (and, for a produce run, the provenance) back to it."""
    if produce is None:
        return None
    other = copy.copy(produce)
    if isinstance(getattr(other, "provenance", None), dict):
        other.provenance = dict(other.provenance)
    return other


_NATIVE_MAGIC = b"GKMSNATIVE1\n"


def native_dump(session):
    """a native session as bytes -- its loadout (the round is built again from it), its exported state and what the
    session holds beside the core.
    """
    loading = None
    if session._loadout is None:
        if session._loading is None:
            _fail("pickle", "a native session without a loadout or a loading")
        loading = session._loading
    rest = {"state": bytes(session._export()), "loadout": session._loadout, "loading": loading, "seed": session._seed,
            "log": session.log,
            "turn_start_reads": session.turn_start_reads, "turn_end_reads": session.turn_end_reads,
            "queue": session._queue,
            "select_choices": session._select_choices, "produce": session.produce,
            "unresolved": list(session._static["unresolved_effects"]),
            "guid_prefixes": list(session._round.ctx.__dict__.get("guid_prefixes", [])),
            "guid_table": list(session._round.ctx.__dict__.get("guid_table", [])),
            "select_greedy": int(session._round.rd.select_greedy)}
    from GakumasCLI.scenario import hif_live
    buf = io.BytesIO()
    saved = None
    if loading is not None:
        dk, missing = loading.deck, object()
        saved = (loading.master, dk.__dict__.get("select_policy", missing), dk.__dict__.get("select_choices", missing))
        loading.master, dk.select_policy, dk.select_choices = None, None, []
    try:
        hif_live._MasterPickler(buf, hif_live._master_refs(session.master)).dump(rest)
    finally:
        if saved is not None:
            loading.master = saved[0]
            for name, value in (("select_policy", saved[1]), ("select_choices", saved[2])):
                if value is missing:
                    dk.__dict__.pop(name, None)
                else:
                    setattr(dk, name, value)
    return _NATIVE_MAGIC + buf.getvalue()


def native_load(blob, master):
    """The session of ``native_dump(blob)``, built again against ``master``."""
    if not blob.startswith(_NATIVE_MAGIC):
        raise ValueError("not a native session blob")
    from GakumasCLI.scenario.exam_session import deferred_open
    from GakumasCLI.scenario import hif_live
    rest = hif_live._MasterUnpickler(io.BytesIO(blob[len(_NATIVE_MAGIC):]), master).load()
    if rest.get("loading") is not None:
        twin = rest["loading"]
        twin.master = master
    else:
        from GakumasCLI.scenario.hif import build_hif_loadout_session
        with deferred_open():
            twin, _ = build_hif_loadout_session(master, rest["loadout"], rest.get("seed"))
    if getattr(twin.deck, "select_policy", None) is None and rest.get("select_greedy", 1):
        from GakumasCLI.engine.card_data import greedy_select_policy
        twin.deck.select_policy = greedy_select_policy(master.cards)
    nat = NativeExamSession.from_state(twin, rest)
    if "select_greedy" in rest and int(nat._round.rd.select_greedy) != int(rest["select_greedy"]):
        nat._set_select_greedy(int(rest["select_greedy"]))
    return nat


def _native_unpickled(blob):
    """pickle's side of native_load: this process's master (the pool worker's, else the default tables)."""
    master = _PROCESS_MASTER.get("master")
    if master is None:
        from GakumasCLI.scenario.exam_session import Master
        master = _PROCESS_MASTER["master"] = Master.load(gkms_paths.MASTER_DIR)
    return native_load(blob, master)


_PROCESS_MASTER = {}


def native_round(live, options=None, record=None):
    """The followed session of ``live`` (a scenario.hif_live LiveRound): its stats get the HifSession's journal
    (``record``: refused steps, hif.native_refused)."""
    nat = live.session
    nat._stats.record = record
    if nat._stats.opened is None:
        nat._stats.opened = "native"
    OPENS.append(nat._stats.opened)
    nat.watch()
    live.native_engine = nat._stats
    return live


def open_loading(loading, document=None, seed=None):
    """``loading`` (an exam_session.ExamLoading, turn 0) opened by the core (gk_open) -- what ExamSession and every round
    builder return.
    """
    nat = NativeExamSession.from_unopened(loading)
    nat._loadout, nat._seed = document, seed
    nat._stats.opened = "native"
    return nat


def make_session(master, document, seed=None):
    """the session of a loadout document -- since scenario.hif.build_hif_loadout_session itself (every builder opens its
    loading in the core)."""
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    return build_hif_loadout_session(master, document, seed)


make_native_session = make_session


def loading_of(master, document, seed=None):
    """the loading of a loadout document (unopened): (loading, meta)."""
    from GakumasCLI.scenario.exam_session import deferred_open
    from GakumasCLI.scenario.exam_loadout import build_produce_exam_session
    with deferred_open():
        return build_produce_exam_session(master, document, seed)


_REFMAPS = {}


def master_refmap(master):
    """``portable.master_refmap(master)``, made once per master (the last two kept, as the compiled masters are)."""
    hit = _REFMAPS.get(id(master))
    if hit is None or hit[0] is not master:
        while len(_REFMAPS) >= 2:
            _REFMAPS.pop(next(iter(_REFMAPS)))
        hit = _REFMAPS[id(master)] = (master, P.master_refmap(master))
    return hit[1]


def portable_bytes(session):
    """``session``'s state in the portable encoding -- for a native session its export, for a loading (ExamLoading)
    ``portable.encode_session``."""
    if isinstance(session, NativeExamSession):
        return session.portable_bytes()
    return P.encode_session(session, P.session_refmap(session, master_refmap(session.master)))


def engine_report(live):
    """What a round ran on, for hif.audition_open / hif.audition_end."""
    eng = getattr(live, "native_engine", None)
    if isinstance(eng, _Stats):
        out = eng.report()
        out["shadow"] = False
        out["handed_back"] = False
        return out
    return dict(eng or {"engine": "python"})
