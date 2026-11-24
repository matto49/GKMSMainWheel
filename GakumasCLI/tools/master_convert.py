# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The master conversion shared by master_mirror.py and master_bootstrap.py: a gakumasu-diff mirror row (camelCase YAML,
enums as ``Type_Member``, int64 as strings, every default written out) in the local shape of the decoded client tables
(enums ``{"name", "number"}``, defaults left out, the nested spelling the local rows use), and the reading of decoded
tables.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
DISPLAY = re.compile(r"(?i)description|^text$|^name$|assetid|voice|order$|viewstarttime|isonlyoutgame|^title|comment")
ENUM_RE = re.compile(r"^[A-Z][A-Za-z0-9]*_[A-Za-z0-9_]+$")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def snake(k):
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", k).lower()


def lc(k):
    return k[:1].lower() + k[1:]


def yaml_load(path):
    import yaml
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=loader) or []


class Tables:
    """Decoded master tables: rows, primary-key fields (from each row's ``outer``), field kinds per path, and the enum
    numbers the rows and ``engine/data/exam_enums.json`` give."""

    def __init__(self, master):
        self.dir = Path(master)
        self.rows, self.keys = {}, {}
        for f in sorted(self.dir.glob("*.jsonl")):
            rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.rows[f.stem] = rows
            if rows:
                self.keys[f.stem] = tuple(sorted(lc(k) for k in rows[0]["outer"]))
        self.enums = self._enums()

    def _enums(self):
        m = {}

        def walk(x):
            if isinstance(x, dict):
                if set(x) == {"name", "number"} and isinstance(x["name"], str):
                    m.setdefault(norm(x["name"]), (x["name"], x["number"]))
                    return
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        for rows in self.rows.values():
            for r in rows:
                walk(r["decoded"])
        for kind, values in json.loads((ENGINE / "engine" / "data" / "exam_enums.json").read_text(encoding="utf-8")).items():
            for number, name in values.items():
                m.setdefault(norm(name), (name, int(number)))
        return m

    def kinds(self, table):
        h = {}

        def walk(x, path):
            if isinstance(x, dict):
                if set(x) == {"name", "number"} and isinstance(x["name"], str):
                    h[path] = "enum"
                    return
                for k, v in x.items():
                    walk(v, path + (k,))
            elif isinstance(x, list):
                for v in x:
                    walk(v, path + ("[]",))
            elif isinstance(x, bool):
                h.setdefault(path, "bool")
            elif isinstance(x, int):
                h.setdefault(path, "int")
            else:
                h.setdefault(path, "other")
        for r in self.rows.get(table) or []:
            walk(r["decoded"], ())
        return h


def _spell(path, k, kinds):
    s = snake(k)
    if path + (k,) in kinds:
        return k
    if path + (s,) in kinds:
        return s
    sib = [p[-1] for p in kinds if len(p) == len(path) + 1 and p[:-1] == path]
    return s if sib and sum("_" in x for x in sib) * 2 > len(sib) else k


def convert(x, kinds, enums, path=(), missing=None):
    """One mirror value in the local shape (None for a default the local export leaves out)."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            k = _spell(path, k, kinds) if path else k
            c = convert(v, kinds, enums, path + (k,), missing)
            if c not in (None, "", False, 0, [], {}):
                out[k] = c
        return out
    if isinstance(x, list):
        return [convert(v, kinds, enums, path + ("[]",), missing) for v in x]
    kind = kinds.get(path)
    if isinstance(x, str):
        if kind == "enum" or (kind is None and ENUM_RE.match(x) and norm(x) in enums):
            e = enums.get(norm(x))
            if e is None:
                if missing is not None:
                    missing.add(x)
                return {"name": x, "number": None}
            return None if e[1] == 0 else {"name": e[0], "number": e[1]}
        if (kind == "int" and re.fullmatch(r"-?\d+", x)) or (kind is None and re.fullmatch(r"-?\d{6,}", x)):
            return int(x)
        return x
    if isinstance(x, float) and x.is_integer() and kind == "int":
        return int(x)
    return x


def gameplay(d):
    if isinstance(d, dict):
        return {k: gameplay(v) for k, v in d.items() if not DISPLAY.search(k)}
    if isinstance(d, list):
        return [gameplay(v) for v in d]
    return d


def row_key(keys, d):
    return tuple(d[k].get("name") if isinstance(d.get(k), dict) else json.dumps(d.get(k)) for k in keys)


def append_strings(path):
    from GakumasCLI import paths as _gkms_paths
    _gkms_paths.ensure_roots()
    from GakumasCLI.scenario.exam_session import Master
    from GakumasCLI.native.check_layout import master_strings
    master = Master.load(ENGINE / "ro" / "master" / "tables")
    raw = Path(path).read_text(encoding="utf-8")
    strings = json.loads(raw)
    have = set(strings)
    supports = [json.loads(line)["decoded"]["id"] for line in
                (ENGINE / "ro" / "master" / "tables" / "SupportCard.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    missing = sorted({s for s in list(master_strings(master)) + supports if s not in have})
    if missing:
        Path(path).write_text(json.dumps(strings + missing, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8", newline="\n")
    return len(strings), missing
