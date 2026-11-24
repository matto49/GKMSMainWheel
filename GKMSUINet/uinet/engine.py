# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the engine and the native core come from, and loading them once per process."""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = HERE.parent
PROJECT = Path(os.environ.get("UINET_PROJECT") or TREE.parent).resolve()
ENGINE = PROJECT / "GakumasCLI"
NATIVE = WORKBENCH = ENGINE
MASTER_DIR = ENGINE / "ro" / "master" / "tables"
PRIOR = WORKBENCH / "priors" / "prior_generated_only.json"

_READY = False


def setup(chdir=True):
    """Put the project root (the GakumasCLI package) on sys.path (once)."""
    global _READY
    if not _READY:
        for p in (str(PROJECT),):
            if p not in sys.path:
                sys.path.insert(0, p)
        _READY = True
    if chdir:
        os.chdir(ENGINE)


@lru_cache(maxsize=1)
def master():
    setup()
    from GakumasCLI.scenario.exam_session import Master
    return Master.load(MASTER_DIR)


def native_lib_path():
    """The Linux build `GakumasCLI/native/build.sh` writes, or UINET_NATIVE_LIB."""
    return Path(os.environ.get("UINET_NATIVE_LIB") or (NATIVE / "native" / "libgkmscore.so"))


@lru_cache(maxsize=1)
def native():
    """(Native, NativeSearch) on this process's master; None when the library is missing."""
    setup()
    lib = native_lib_path()
    if not lib.is_file():
        return None
    from GakumasCLI.native.diffrun import Native
    from GakumasCLI.native.native_search import NativeSearch
    nat = Native(str(lib))
    nat.load_master(master(), json.load(open(NATIVE / "data" / "strings.json", encoding="utf-8")))
    return nat, NativeSearch(nat)


@lru_cache(maxsize=1)
def prior():
    setup()
    from GakumasCLI.scenario import policy_search as PS
    return PS.load_prior(str(PRIOR))


def shuffle_pile(session, rnd):
    """Reorder the session's draw pile with RND's shuffle."""
    if hasattr(session, "edit"):
        session.edit(lambda v: rnd.shuffle(v.deck.deck))
    else:
        rnd.shuffle(session.deck.deck)


def build(loadout, seed=None):
    """(session, meta) of one H.I.F battle from a ``gkms.hif_loadout.v1`` document."""
    setup()
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    return build_hif_loadout_session(master(), loadout, seed)


def project_commit():
    """The checkout's HEAD, for the provenance of every output; a tree unpacked from ``git archive`` carries it in a
    ``COMMIT`` file at its root (None when neither is there)."""
    head = PROJECT / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            return (PROJECT / ".git" / ref[5:]).read_text().strip()
        return ref
    except OSError:
        pass
    try:
        return (PROJECT / "COMMIT").read_text().strip() or None
    except OSError:
        return None


def exam_rules_version():
    """gk_exam_rules_version of the native core this process uses (what an episode's play depends on)."""
    setup()
    from GakumasCLI.native.native_session import _lib
    return int(_lib().exam_rules_version)
