# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""portable, language-neutral state encoding of an ExamSession."""
from __future__ import annotations

import struct
import sys

FORMAT = "gkms.native_state.v2"

T_NONE, T_FALSE, T_TRUE, T_INT, T_FLOAT, T_STR, T_LIST, T_MAP, T_REF, T_SET, T_REC = range(11)

_D = struct.Struct("<d")


def _varint(n, out):
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _zigzag(n):
    return (n << 1) if n >= 0 else ((-n << 1) - 1)


def _unzigzag(z):
    return (z >> 1) if not (z & 1) else -((z + 1) >> 1)


FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3


def fnv1a64(data: bytes) -> int:
    h = FNV_OFFSET
    for b in data:
        h ^= b
        h = (h * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


class RefMap:
    """id(obj) -> "<table>|<key>" for every shared, never-mutated master row."""

    def __init__(self):
        self.by_id = {}
        self.keep = []

    @staticmethod
    def _key(k):
        if isinstance(k, tuple):
            return ":".join(str(x) for x in k)
        return str(k)

    def add_table(self, name, table):
        if table is None:
            return
        self.keep.append(table)
        for k, row in table.items():
            if isinstance(row, (dict, list)) and id(row) not in self.by_id:
                self.by_id[id(row)] = f"{name}|{self._key(k)}"
                self.keep.append(row)

    def add_object(self, name, obj):
        if obj is not None and id(obj) not in self.by_id:
            self.by_id[id(obj)] = name
            self.keep.append(obj)


MASTER_TABLES = ("cards", "effects", "triggers", "enchants", "searches", "customizes",
                 "grow_effects", "gimmicks", "items", "item_effects", "card_pools",
                 "drinks", "drink_effects")


def master_refmap(master):
    rm = RefMap()
    for name in MASTER_TABLES:
        rm.add_table(name, getattr(master, name, None))
    rm.add_object("master|exam_setting", getattr(master, "exam_setting", None))
    rm.add_object("master|setting", getattr(master, "setting", None))
    rm.add_object("master|trigger_effect_limits", getattr(master, "trigger_effect_limits", None))
    return rm


def session_refmap(session, base=None):
    """Master refs + this round's deck card tables (rows synthesized per round)."""
    rm = RefMap()
    if base is not None:
        rm.by_id.update(base.by_id)
        rm.keep.extend(base.keep)
    else:
        b = master_refmap(session.master)
        rm.by_id.update(b.by_id)
        rm.keep.extend(b.keep)
    deck = session.deck
    rm.add_table("deck.card_master", getattr(deck, "card_master", None))
    rm.add_table("deck.card_customizes", getattr(deck, "card_customizes", None))
    rm.add_table("deck.card_grow_effects", getattr(deck, "card_grow_effects", None))
    return rm


class OMap(dict):
    """Marks a dict with identifier keys that is nevertheless a map."""
    __slots__ = ()


class Encoder:
    def __init__(self, refmap: RefMap | None = None, cards=None):
        self.refs = refmap.by_id if refmap is not None else {}
        self.cards = cards or {}

    def enc(self, v, out: bytearray):
        if isinstance(v, (dict, list)):
            r = self.refs.get(id(v))
            if r is None and isinstance(v, dict):
                r = self.cards.get(id(v))
            if r is not None:
                out.append(T_REF)
                b = r.encode("utf-8")
                _varint(len(b), out)
                out += b
                return
        if v is None:
            out.append(T_NONE)
        elif v is True:
            out.append(T_TRUE)
        elif v is False:
            out.append(T_FALSE)
        elif isinstance(v, int):
            out.append(T_INT)
            _varint(_zigzag(int(v)), out)
        elif isinstance(v, float):
            out.append(T_FLOAT)
            out += _D.pack(v)
        elif isinstance(v, str):
            out.append(T_STR)
            b = v.encode("utf-8")
            _varint(len(b), out)
            out += b
        elif isinstance(v, (list, tuple)):
            out.append(T_LIST)
            _varint(len(v), out)
            for x in v:
                self.enc(x, out)
        elif isinstance(v, dict):
            if not isinstance(v, OMap) and all(isinstance(k, str) and k.isidentifier() for k in v):
                out.append(T_REC)
                _varint(len(v), out)
                for k in sorted(v):
                    b = k.encode("utf-8")
                    _varint(len(b), out)
                    out += b
                    self.enc(v[k], out)
            else:
                out.append(T_MAP)
                _varint(len(v), out)
                for k, x in v.items():
                    self.enc(k, out)
                    self.enc(x, out)
        elif isinstance(v, (set, frozenset)):
            items = []
            for x in v:
                tmp = bytearray()
                self.enc(x, tmp)
                items.append(bytes(tmp))
            items.sort()
            out.append(T_SET)
            _varint(len(items), out)
            for b in items:
                out += b
        else:
            raise TypeError(f"portable: unencodable {type(v).__name__}")


def decode(buf: bytes, pos: int = 0):
    """Inverse of Encoder.enc into plain Python: refs -> ('$ref', name),
    sets -> ('$set', [...]), maps -> list of (k, v) pairs (order kept)."""
    t = buf[pos]
    pos += 1

    def rv(p):
        n = shift = 0
        while True:
            b = buf[p]
            p += 1
            n |= (b & 0x7F) << shift
            if not b & 0x80:
                return n, p
            shift += 7

    if t == T_NONE:
        return None, pos
    if t == T_FALSE:
        return False, pos
    if t == T_TRUE:
        return True, pos
    if t == T_INT:
        z, pos = rv(pos)
        return _unzigzag(z), pos
    if t == T_FLOAT:
        return _D.unpack_from(buf, pos)[0], pos + 8
    if t in (T_STR, T_REF):
        n, pos = rv(pos)
        s = buf[pos:pos + n].decode("utf-8")
        return (s if t == T_STR else ("$ref", s)), pos + n
    if t in (T_LIST, T_SET):
        n, pos = rv(pos)
        items = []
        for _ in range(n):
            x, pos = decode(buf, pos)
            items.append(x)
        return (items if t == T_LIST else ("$set", items)), pos
    if t == T_REC:
        n, pos = rv(pos)
        pairs = []
        for _ in range(n):
            ln, pos = rv(pos)
            k = buf[pos:pos + ln].decode("utf-8")
            pos += ln
            x, pos = decode(buf, pos)
            pairs.append((k, x))
        return ("$rec", pairs), pos
    if t == T_MAP:
        n, pos = rv(pos)
        pairs = []
        for _ in range(n):
            k, pos = decode(buf, pos)
            x, pos = decode(buf, pos)
            pairs.append((k, x))
        return ("$map", pairs), pos
    raise ValueError(f"bad tag {t} at {pos - 1}")


# --------------------------------------------------------------------------
# field selection -- mirrors scenario/solver.py search_state_key
# --------------------------------------------------------------------------

def _solver():
    from GakumasCLI.scenario import solver
    return solver


DECK_DYNAMIC = ("initialized", "current_turn", "current_parameter_type",
                "turn_parameter_types", "turn_used_support_ids",
                "get_card_upgrade_count", "generated_card_count", "is_hand_hold",
                "hold_limit", "hand_limit", "turn_start_distribute",
                "dynamic_grow_by_number",
                "pending_move_effects")
PRODUCE_FIELDS = ("stamina", "max_stamina", "vocal", "dance", "visual", "trace")
ZONES = ("deck", "hand", "grave", "hold", "lost")


DEAD_STATE_FIELDS = frozenset({
    "playing_resolved", "_play_start_snapshot", "_consume_snapshot",
    "live_hand_numbers", "last_play_count_interval", "target_card",
    "_running_trigger_state",
    "_play_card_snapshot", "_pending_card_snapshot",
})
# identifier-keyed dicts that are maps (iteration order kept)
MAP_FIELDS = ("debuff_since", "_timed_gain_layers")
CARD_CACHE_KEYS = ("resolved_card",)


def card_index(session):
    """id(card) -> "card|<guid>" for every instance in the five zones."""
    out = {}
    seen = set()
    for z in ZONES:
        for card in getattr(session.deck, z, ()):
            g = card.get("guid")
            if g in seen:
                raise ValueError(f"portable: duplicate guid {g!r}")
            seen.add(g)
            out[id(card)] = f"card|{g}"
    return out


def _card_record(card):
    return {k: v for k, v in card.items() if k not in CARD_CACHE_KEYS}


def portable_view(session):
    """The top-level record that is encoded (plain Python values; shared
    master rows and zone card instances are left as the same objects so the
    encoder can emit them as refs)."""
    solver = _solver()
    deck = session.deck
    zones = [[_card_record(card) for card in getattr(deck, z, ())] for z in ZONES]
    skip = solver._SEARCH_STATIC_STATE_FIELDS | solver._SEARCH_AUDIT_STATE_FIELDS | DEAD_STATE_FIELDS
    in_zone = {id(c) for z in ZONES for c in getattr(deck, z, ())}

    def detach(card):
        return card if card is None or id(card) in in_zone else _card_record(card)

    state = {}
    for k, v in vars(session.state).items():
        if k in skip:
            continue
        if k in MAP_FIELDS and isinstance(v, dict):
            v = OMap(v)
        elif k == "playing_card":
            v = detach(v)
        elif k == "trigger_effect_states":
            v = [dict(t, owner_card=detach(t["owner_card"])) if t.get("owner_card") is not None
                 and id(t["owner_card"]) not in in_zone else t for t in v]
        state[k] = v
    deck_dyn = {n: getattr(deck, n) for n in DECK_DYNAMIC if hasattr(deck, n)}
    produce = {n: getattr(session.produce, n) for n in PRODUCE_FIELDS if hasattr(session.produce, n)}
    return {
        "format": FORMAT,
        "session": {"turn": session.turn, "step_index": session.step_index,
                    "finished": session.finished, "cleared": session.cleared,
                    "drinks": list(getattr(session, "drinks", ()) or ())},
        "rng": getattr(deck.rng, "state", None),
        "zones": zones,
        "deck": deck_dyn,
        "produce": produce,
        "state": state,
    }


def encode_session(session, refmap: RefMap | None = None) -> bytes:
    if getattr(session, "native", False):
        return session.portable_bytes()
    if refmap is None:
        refmap = session_refmap(session)
    out = bytearray()
    Encoder(refmap, card_index(session)).enc(portable_view(session), out)
    return bytes(out)


def encode_view(session, view, refmap):
    out = bytearray()
    Encoder(refmap, card_index(session)).enc(view, out)
    return bytes(out)


def digest_session(session, refmap: RefMap | None = None) -> str:
    return "%016x" % fnv1a64(encode_session(session, refmap))


# --------------------------------------------------------------------------
# structural diff of two canonical byte streams (for first-mismatch reports)
# --------------------------------------------------------------------------

def _walk(v, path, out):
    if isinstance(v, tuple) and v and v[0] in ("$map", "$rec"):
        for i, (k, x) in enumerate(v[1]):
            kk = k[1] if isinstance(k, tuple) and k and k[0] == "$ref" else k
            _walk(x, f"{path}.{kk}" if isinstance(kk, str) else f"{path}[{kk!r}]", out)
            out.append((f"{path}#key{i}", k))
        out.append((f"{path}#len", len(v[1])))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _walk(x, f"{path}[{i}]", out)
        out.append((f"{path}#len", len(v)))
    elif isinstance(v, tuple) and v and v[0] == "$set":
        out.append((path, ("$set", repr(v[1]))))
    else:
        out.append((path, v))


def diff(a: bytes, b: bytes, limit=20):
    """List of (path, a_value, b_value) leaves that differ."""
    va, _ = decode(a)
    vb, _ = decode(b)
    fa, fb = [], []
    _walk(va, "$", fa)
    _walk(vb, "$", fb)
    da, db = dict(fa), dict(fb)
    out = []
    for p in list(da) + [p for p in db if p not in da]:
        x, y = da.get(p, "<absent>"), db.get(p, "<absent>")
        if x != y or type(x) is not type(y):
            out.append((p, x, y))
            if len(out) >= limit:
                break
    return out


if __name__ == "__main__":
    print(FORMAT, file=sys.stderr)


# --------------------------------------------------------------------------
# encoder for the decoded form (inverse of ``decode``)
# --------------------------------------------------------------------------

def encode_decoded(v, out: bytearray | None = None) -> bytes:
    top = out is None
    if top:
        out = bytearray()
    if isinstance(v, tuple) and v and isinstance(v[0], str) and v[0].startswith("$"):
        tag = v[0]
        if tag == "$ref":
            out.append(T_REF)
            b = v[1].encode("utf-8")
            _varint(len(b), out)
            out += b
        elif tag == "$rec":
            pairs = sorted(v[1], key=lambda kv: kv[0])
            out.append(T_REC)
            _varint(len(pairs), out)
            for k, x in pairs:
                b = k.encode("utf-8")
                _varint(len(b), out)
                out += b
                encode_decoded(x, out)
        elif tag == "$map":
            out.append(T_MAP)
            _varint(len(v[1]), out)
            for k, x in v[1]:
                encode_decoded(k, out)
                encode_decoded(x, out)
        elif tag == "$set":
            items = sorted(encode_decoded(x) for x in v[1])
            out.append(T_SET)
            _varint(len(items), out)
            for b in items:
                out += b
        else:
            raise ValueError(tag)
    elif v is None:
        out.append(T_NONE)
    elif v is True:
        out.append(T_TRUE)
    elif v is False:
        out.append(T_FALSE)
    elif isinstance(v, int):
        out.append(T_INT)
        _varint(_zigzag(v), out)
    elif isinstance(v, float):
        out.append(T_FLOAT)
        out += _D.pack(v)
    elif isinstance(v, str):
        out.append(T_STR)
        b = v.encode("utf-8")
        _varint(len(b), out)
        out += b
    elif isinstance(v, list):
        out.append(T_LIST)
        _varint(len(v), out)
        for x in v:
            encode_decoded(x, out)
    else:
        raise TypeError(type(v).__name__)
    return bytes(out) if top else None
