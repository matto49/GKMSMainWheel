# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every root of GakumasCLI in one place (one package)."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

APP_ROOT = PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = APP_ROOT.parent
LOCAL_CMD = REPO_ROOT / "gkms.local.cmd"
GKMS_PY = APP_ROOT / "gkms.py"

_SET = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$', re.IGNORECASE)


def _decode_cmd(data):
    """UTF-8 as written by hand; else the console code page cmd itself reads the file in."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        for codec in ("oem", "mbcs"):
            try:
                return data.decode(codec)
            except (LookupError, UnicodeDecodeError):
                continue
        return data.decode("utf-8", errors="replace")


def _read_local():
    values = {}
    try:
        text = _decode_cmd(LOCAL_CMD.read_bytes())
    except OSError:
        return values
    for line in text.splitlines():
        match = _SET.match(line)
        if match:
            values[match.group(1).upper()] = match.group(2).strip()
    return values


_LOCAL = _read_local()


def setting(name, default=None):
    """Environment variable, else gkms.local.cmd, else ``default``."""
    value = os.environ.get(name) or _LOCAL.get(name.upper())
    return value if value else default


def _path(name, default=None):
    value = setting(name)
    if value:
        return Path(value)
    return Path(default) if default is not None else None


# inside the tree
ENGINE_ROOT = APP_ROOT
WORKBENCH_ROOT = APP_ROOT
MASTER_DIR = APP_ROOT / "ro" / "master" / "tables"
PRODUCE_DIR = PACKAGE_ROOT / "produce"
GPU_ROOT = APP_ROOT
NATIVE_DIR = APP_ROOT / "native"
NATIVE_STRINGS = APP_ROOT / "data" / "strings.json"
NATIVE_DLL = NATIVE_DIR / "build" / "gkmscore.dll"
NATIVE_SO = NATIVE_DIR / "libgkmscore.so"
NATIVE_LIB = NATIVE_DLL if os.name == "nt" else NATIVE_SO
LIVE_ROOT = _path("GKMS_LIVE_ROOT", WORKBENCH_ROOT / "data" / "live")
HIF_ROOT = _path("GKMS_HIF_ROOT", WORKBENCH_ROOT / "data" / "hif")
VENDORED_TRANS_DIR = next((p for p in (WORKBENCH_ROOT / "data" / "i18n" / "masterTrans",
                                       WORKBENCH_ROOT / "data" / "i18n" / "names") if p.is_dir()),
                          WORKBENCH_ROOT / "data" / "i18n" / "masterTrans")

CORPUS_ROOT = _path("GKMS_CORPUS_ROOT", REPO_ROOT.parent / "archive")

# outside the repository: None when gkms.local.cmd does not name them
NETWORK_ROOT = _path("GKMS_NETWORK_ROOT")
TRANS_DIR = _path("GKMS_TRANS_DIR") or (VENDORED_TRANS_DIR if VENDORED_TRANS_DIR.is_dir() else None)
TEMP_ROOT = _path("GKMS_TEMP_ROOT", tempfile.gettempdir())
# files a user picks in the panel must lie under this root
READ_SCOPE = _path("GKMS_READ_SCOPE", REPO_ROOT.anchor if os.name == "nt" else WORKBENCH_ROOT)


def temp_dir(name):
    """``TEMP_ROOT / name``, created if missing."""
    path = Path(TEMP_ROOT) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_roots():
    """The repository root (the ``GakumasCLI`` package) and the sibling trees the workbench imports (``the capture
    decoder``, ``uinet``, ``the headless client``, ``the MAA tool``, the website) on sys.path; idempotent.
    """
    import sys
    for path in reversed((REPO_ROOT, REPO_ROOT / "GKMSUINet")):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    return REPO_ROOT


def command(module, *args, python=None):
    """The argv that runs ``module`` (a ``GakumasCLI`` module or a gkms.py command) in a new process, from any cwd."""
    import sys
    return [str(python or sys.executable), "-B", str(GKMS_PY), module, *map(str, args)]
