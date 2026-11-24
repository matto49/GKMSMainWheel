# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the engine's master tables from the public vertesan/gakumasu-diff mirror alone: no client export, no
capture descriptors.

    python -B GakumasCLI/gkms.py master-bootstrap build --mirror <gakumasu-diff checkout>
    python -B GakumasCLI/gkms.py master-bootstrap schema --master GakumasCLI/ro/master/tables --out <schema.json>
    python -B GakumasCLI/gkms.py master-bootstrap compare --master <tables> --built <tables>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
MASTER = ENGINE / "ro" / "master" / "tables"
SCHEMA = Path(__file__).resolve().with_name("master_schema.json")
STRINGS = ENGINE / "data" / "strings.json"
FORMAT = "gkms.master_schema.v1"
SEP = "/"


def _mc():
    from GakumasCLI.tools import master_convert as MC
    return MC


def write_schema(master_dir, out):
    """SCHEMA from decoded tables: {"tables": {T: {"keys", "outer", "numeric", "kinds"}}, "enums": {norm: [name, n]}}.
    """
    MM = _mc()
    try:
        from GakumasCLI.tools import master_mirror as MMR
        local, groups = MMR.Local(master_dir), MMR.enum_groups()
    except ImportError:
        local, groups = MM.Tables(master_dir), _exam_groups()
    tables = {}
    used = set()
    for table, rows in sorted(local.rows.items()):
        if not rows:
            continue
        outer = list(rows[0]["outer"])
        kinds = local.kinds(table)
        tables[table] = {"keys": list(local.keys[table]), "outer": outer,
                         "numeric": [isinstance(rows[0]["outer"][k], int) for k in outer],
                         "kinds": {SEP.join(p): k for p, k in sorted(kinds.items())}}

        def walk(x):
            if isinstance(x, dict):
                if set(x) == {"name", "number"} and isinstance(x["name"], str):
                    used.add(MM.norm(x["name"]))
                    return
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        for r in rows:
            walk(r["decoded"])
    keep = set()
    for members in groups.values():
        if used & members:
            keep |= members
    exam = json.loads((ENGINE / "engine" / "data" / "exam_enums.json").read_text(encoding="utf-8"))
    for values in exam.values():
        for name in values.values():
            keep.add(MM.norm(name))
    keep |= used
    enums = {k: list(local.enums[k]) for k in sorted(keep) if k in local.enums}
    doc = {"format": FORMAT, "tables": tables, "enums": enums}
    Path(out).write_text(json.dumps(doc, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8",
                         newline="\n")
    return len(tables), len(enums)


def _exam_groups():
    MM = _mc()
    exam = json.loads((ENGINE / "engine" / "data" / "exam_enums.json").read_text(encoding="utf-8"))
    return {kind: {MM.norm(n) for n in values.values()} for kind, values in exam.items()}


def load_schema(path=SCHEMA):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} file")
    for t in doc["tables"].values():
        t["kinds"] = {tuple(p.split(SEP)) if p else (): k for p, k in t["kinds"].items()}
    doc["enums"] = {k: (v[0], v[1]) for k, v in doc["enums"].items()}
    return doc


def _source(mirror_dir):
    try:
        out = subprocess.run(["git", "-C", str(mirror_dir), "log", "-1", "--format=%h %cs"],
                             capture_output=True, text=True, timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        out = []
    return f"gakumasu-diff@{out[0]} ({out[1]})" if len(out) == 2 else "gakumasu-diff"


def build(mirror_dir, out_dir, schema, log=print):
    """Every SCHEMA table the mirror has, converted and written to OUT_DIR; {table: rows}."""
    MM = _mc()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source = _source(mirror_dir)
    done = {}
    for table, t in sorted(schema["tables"].items()):
        path = Path(mirror_dir) / f"{table}.yaml"
        if not path.exists():
            log(f"{table}: not in the mirror")
            continue
        missing, rows, seen = set(), [], set()
        for m in MM.yaml_load(path):
            c = MM.convert(m, t["kinds"], schema["enums"], missing=missing)
            outer = {}
            for name, numeric in zip(t["outer"], t["numeric"]):
                v = c.get(MM.lc(name))
                v = v.get("number") if isinstance(v, dict) else v
                outer[name] = v if v is not None else (0 if numeric else "")
            key = MM.row_key(t["keys"], c)
            if key in seen:
                raise ValueError(f"{table}: two mirror rows share the key {outer}")
            seen.add(key)
            rows.append({"decode_status": "decoded", "decoded": c, "outer": outer, "source": source})
        if missing:
            raise ValueError(f"{table}: enum members without a number {sorted(missing)[:5]}")
        text = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)
        (out_dir / f"{table}.jsonl").write_text(text, encoding="utf-8", newline="\n")
        done[table] = len(rows)
    (out_dir / "SOURCE.md").write_text(f"# Master source\n\nBuilt by tools/master_bootstrap.py from {source}.\n",
                                       encoding="utf-8", newline="\n")
    return source, done


def compare(master_dir, built_dir):
    """{table: {"local", "built", "equal", "gameplay", "missing", "extra"}} keyed as SCHEMA keys the rows."""
    MM = _mc()
    local, built = MM.Tables(master_dir), {}
    for f in sorted(Path(built_dir).glob("*.jsonl")):
        built[f.stem] = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    out = {}
    for table, rows in local.rows.items():
        if table not in built or table not in local.keys:
            continue
        keys = local.keys[table]
        have = {MM.row_key(keys, r["decoded"]): r["decoded"] for r in rows}
        got = {MM.row_key(keys, r["decoded"]): r["decoded"] for r in built[table]}
        both = have.keys() & got.keys()
        out[table] = {"local": len(have), "built": len(got),
                      "equal": sum(1 for k in both if have[k] == got[k]),
                      "gameplay": sum(1 for k in both if MM.gameplay(have[k]) == MM.gameplay(got[k])),
                      "missing": len(have.keys() - got.keys()), "extra": len(got.keys() - have.keys())}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("build", "schema", "compare"))
    ap.add_argument("--mirror", help="build: a vertesan/gakumasu-diff checkout")
    ap.add_argument("--master", default=str(MASTER), help="schema / compare: decoded tables; build: where to write")
    ap.add_argument("--schema", default=str(SCHEMA))
    ap.add_argument("--out", help="schema: the file to write")
    ap.add_argument("--built", help="compare: the tables build wrote")
    ap.add_argument("--strings", default=str(STRINGS), help="build: the native core's strings.json to append to")
    ap.add_argument("--no-extra", action="store_true", help="build: leave produce/master_extra.json alone")
    a = ap.parse_args(argv)
    if a.command == "schema":
        n, e = write_schema(a.master, a.out or a.schema)
        print(f"schema {a.out or a.schema}: tables {n}, enum members {e}")
        return 0
    if a.command == "compare":
        res = compare(a.master, a.built)
        for table, r in res.items():
            print(f"{table:38s} local {r['local']:6d} built {r['built']:6d} equal {r['equal']:6d} "
                  f"gameplay {r['gameplay']:6d} missing {r['missing']:5d} extra {r['extra']:5d}")
        print(f"tables {len(res)}; equal on gameplay in every shared row: "
              f"{sum(1 for r in res.values() if r['gameplay'] == r['local'] - r['missing'])}")
        return 0
    if not a.mirror:
        ap.error("build needs --mirror")
    from GakumasCLI import paths
    paths.ensure_roots()
    source, done = build(a.mirror, a.master, load_schema(a.schema))
    print(f"built {len(done)} tables, {sum(done.values())} rows from {source} into {a.master}")
    if not a.no_extra:
        import GakumasCLI.produce.formation as formation
        formation.extra(a.mirror, formation.EXTRA)
        print("produce/master_extra.json regenerated")
        if (ENGINE.parent / "GKMSPS" / "tools" / "build_ps_data.py").exists():
            from GKMSPS.tools import build_ps_data as B
            B.DATA.mkdir(exist_ok=True)
            (B.DATA / "master_ps.json").write_text(
                json.dumps(B.master_ps(a.mirror), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8", newline="\n")
            print("GKMSPS/data/master_ps.json regenerated")
    if a.strings and Path(a.strings).exists():
        if Path(a.master).resolve() != MASTER.resolve():
            print("strings.json left alone (the tables are not the engine's)")
        else:
            n, added = _mc().append_strings(a.strings)
            print(f"strings.json {n} -> {n + len(added)} (appended {len(added)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
