#!/usr/bin/env python3
"""Centralized EDA tool path resolution for VeriFlow.

Single source of truth for finding iverilog, vvp, yosys, and Python.
Used by init.py, iverilog_runner.py, and cocotb_runner.py.

Resolution order:
  1. eda_env.json (native paths written by init.py, most reliable)
  2. Environment variables (with MSYS→Windows conversion on Windows)
  3. Discovery (scan common installation directories + PATH)
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path


# ── Path normalization ──────────────────────────────────────────────

def _msys_to_native(path: str) -> str:
    """Convert MSYS paths (/c/path) to native paths (C:/path) on Windows.

    MSYS/Git Bash uses /c/path internally but Python on Windows cannot
    resolve these — Path('/c/oss-cad-suite') becomes C:\\c\\oss-cad-suite.
    Convert to C:/path which both Git Bash and Python understand.

    Handles multi-path strings like /c/a:/d/b → C:/a;D:/b.
    """
    if sys.platform != "win32":
        return path
    # Convert /X/ at start or after path separator (: or ;)
    return re.sub(
        r'(^|[:;])/([A-Za-z])/',
        lambda m: f'{m.group(1)}{m.group(2).upper()}:/',
        path,
    )


def _normalize_path(path: str, multi: bool = False) -> str:
    """Normalize a path: convert MSYS paths, then resolve to platform native.

    Args:
        path: Path string to normalize.
        multi: If True, treat as multi-path string (e.g. EDA_LIB with
               multiple dirs).  Splits into individual paths, normalizes
               each, and rejoins with platform-native separator.
    """
    if not path:
        return path
    path = _msys_to_native(path)
    if multi:
        if sys.platform == "win32":
            # Convert bash : separators to Windows ; while preserving
            # drive-letter colons (drive : is always followed by / or \).
            path = re.sub(r':(?!/|\\)', ';', path)
            parts = [str(Path(p)) for p in path.split(";") if p]
        else:
            parts = [str(Path(p)) for p in path.split(":") if p]
        return os.pathsep.join(parts)
    if path.startswith("$"):
        return path
    return str(Path(path))


# ── Cache ───────────────────────────────────────────────────────────

_cache: dict = {}


def clear_cache():
    """Clear the path cache. Call after init.py writes new eda_env files."""
    _cache.clear()


# ── eda_env.json loader ─────────────────────────────────────────────

def _find_project_dir() -> str:
    """Find project directory by walking up from CWD looking for .veriflow/."""
    p = Path.cwd()
    for _ in range(10):
        if (p / ".veriflow").is_dir():
            return str(p)
        parent = p.parent
        if parent == p:
            break
        p = parent
    return ""


def _load_eda_env_json() -> dict:
    """Load native paths from eda_env.json. Returns empty dict if not found."""
    if "_env_json" in _cache:
        return _cache["_env_json"]

    project = _find_project_dir()
    if project:
        json_path = Path(project) / ".veriflow" / "eda_env.json"
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                _cache["_env_json"] = data
                return data
            except (json.JSONDecodeError, OSError):
                pass

    _cache["_env_json"] = {}
    return {}


# ── Discovery (fallback when env/json unavailable) ──────────────────

_EDA_SEARCH_DIRS_WIN = [
    r"C:\oss-cad-suite",
    r"C:\Program Files\iverilog",
    r"C:\Program Files (x86)\iverilog",
]

_EDA_SEARCH_DIRS_UNIX = [
    "/opt/oss-cad-suite",
    "/usr/local",
    "/usr",
]


def _discover_eda() -> tuple[str, str]:
    """Scan common directories for iverilog. Returns (eda_bin, eda_lib)."""
    search_dirs = []
    if sys.platform == "win32":
        search_dirs.extend(_EDA_SEARCH_DIRS_WIN)
    else:
        search_dirs.extend(_EDA_SEARCH_DIRS_UNIX)
    search_dirs.append(os.path.expanduser("~/.local"))

    for base in search_dirs:
        base_path = Path(base)
        bin_dir = base_path / "bin"
        for name in ("iverilog", "iverilog.exe"):
            if (bin_dir / name).exists():
                eda_bin = str(bin_dir)
                eda_lib = ""
                lib_dir = base_path / "lib"
                if lib_dir.is_dir():
                    eda_lib = str(lib_dir)
                ivl_dir = base_path / "lib" / "ivl"
                if ivl_dir.is_dir():
                    eda_lib = f"{eda_lib}{os.pathsep}{ivl_dir}" if eda_lib else str(ivl_dir)
                return eda_bin, eda_lib

    # Fallback: check PATH
    iverilog = shutil.which("iverilog")
    if iverilog:
        eda_bin = str(Path(iverilog).parent)
        eda_lib = ""
        lib_dir = Path(eda_bin).parent / "lib"
        if lib_dir.is_dir():
            eda_lib = str(lib_dir)
        return eda_bin, eda_lib

    return "", ""


def _discover_yosys(eda_bin: str = "") -> str:
    """Find yosys executable. Returns path or empty string."""
    if eda_bin:
        for name in ("yosys", "yosys.exe"):
            p = Path(eda_bin) / name
            if p.exists():
                return str(p)
    for name in ("yosys", "yosys.exe"):
        found = shutil.which(name)
        if found:
            return found
    bases = (
        [r"C:\oss-cad-suite"]
        if sys.platform == "win32"
        else ["/opt/oss-cad-suite", "/usr/local", "/usr"]
    )
    for base in bases:
        for name in ("yosys", "yosys.exe"):
            p = Path(base) / "bin" / name
            if p.exists():
                return str(p)
    return ""


# ── Public API ──────────────────────────────────────────────────────

def get_eda_bin() -> str:
    """Get EDA_BIN directory (contains iverilog, vvp, etc.)."""
    if "eda_bin" in _cache:
        return _cache["eda_bin"]

    # 1. eda_env.json
    data = _load_eda_env_json()
    if data.get("eda_bin"):
        p = _normalize_path(data["eda_bin"])
        if Path(p).is_dir():
            _cache["eda_bin"] = p
            return p

    # 2. Env var (may be MSYS path from eda_env.sh)
    env_val = os.environ.get("EDA_BIN", "")
    if env_val:
        p = _normalize_path(env_val)
        if Path(p).is_dir():
            _cache["eda_bin"] = p
            return p

    # 3. Discover
    eda_bin, _ = _discover_eda()
    _cache["eda_bin"] = eda_bin
    return eda_bin


def get_eda_lib() -> str:
    """Get EDA_LIB directories (platform-native separator, may be multi-path)."""
    if "eda_lib" in _cache:
        return _cache["eda_lib"]

    # 1. eda_env.json
    data = _load_eda_env_json()
    if data.get("eda_lib"):
        p = _normalize_path(data["eda_lib"], multi=True)
        _cache["eda_lib"] = p
        return p

    # 2. Env var
    env_val = os.environ.get("EDA_LIB", "")
    if env_val:
        p = _normalize_path(env_val, multi=True)
        _cache["eda_lib"] = p
        return p

    # 3. Discover
    _, eda_lib = _discover_eda()
    _cache["eda_lib"] = eda_lib
    return eda_lib


def get_ivl_home() -> str:
    """Get IVL_HOME directory for iverilog preprocessor."""
    if "ivl_home" in _cache:
        return _cache["ivl_home"]

    # 1. eda_env.json
    data = _load_eda_env_json()
    if data.get("ivl_home"):
        p = _normalize_path(data["ivl_home"])
        if p and Path(p).is_dir():
            _cache["ivl_home"] = p
            return p

    # 2. Env var
    env_val = os.environ.get("IVL_HOME", "")
    if env_val:
        p = _normalize_path(env_val)
        if p and Path(p).is_dir():
            _cache["ivl_home"] = p
            return p

    # 3. Derive from eda_bin
    eda_bin = get_eda_bin()
    if eda_bin:
        eda_base = str(Path(eda_bin).parent)
        for candidate in [
            Path(eda_base) / "lib" / "ivl",
            Path(eda_bin) / ".." / "lib" / "ivl",
        ]:
            candidate = candidate.resolve()
            if candidate.is_dir():
                p = str(candidate)
                _cache["ivl_home"] = p
                return p

    _cache["ivl_home"] = ""
    return ""


def get_python_exe() -> str:
    """Get Python executable path."""
    if "python_exe" in _cache:
        return _cache["python_exe"]

    # 1. eda_env.json
    data = _load_eda_env_json()
    if data.get("python_exe"):
        p = _normalize_path(data["python_exe"])
        if p and Path(p).exists():
            _cache["python_exe"] = p
            return p

    # 2. Env var
    env_val = os.environ.get("PYTHON_EXE", "")
    if env_val:
        p = _normalize_path(env_val)
        if p and Path(p).exists():
            _cache["python_exe"] = p
            return p

    # 3. Current interpreter
    _cache["python_exe"] = sys.executable
    return sys.executable


def get_yosys_exe() -> str:
    """Get yosys executable path."""
    if "yosys_exe" in _cache:
        return _cache["yosys_exe"]

    # 1. eda_env.json
    data = _load_eda_env_json()
    if data.get("yosys_exe"):
        p = _normalize_path(data["yosys_exe"])
        if p and Path(p).exists():
            _cache["yosys_exe"] = p
            return p

    # 2. Env var
    env_val = os.environ.get("YOSYS_EXE", "")
    if env_val:
        p = _normalize_path(env_val)
        if p and Path(p).exists():
            _cache["yosys_exe"] = p
            return p

    # 3. Discover
    eda_bin = get_eda_bin()
    yosys_exe = _discover_yosys(eda_bin)
    _cache["yosys_exe"] = yosys_exe
    return yosys_exe


def find_executable(names: list[str]) -> str:
    """Find an executable by name, checking EDA_BIN then system PATH.

    Args:
        names: Candidate names, e.g. ["iverilog", "iverilog.exe"].

    Returns:
        Full path to executable, or empty string if not found.
    """
    eda_bin = get_eda_bin()
    if eda_bin:
        for name in names:
            p = Path(eda_bin) / name
            if p.exists():
                return str(p)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def build_subprocess_env(base_env: dict | None = None) -> dict:
    """Build subprocess env with EDA paths set for DLL / library resolution.

    Sets EDA_BIN, EDA_LIB, IVL_HOME, YOSYS_EXE, PYTHON_EXE and prepends
    EDA directories to PATH so that child processes can locate tools and
    their runtime dependencies (DLLs on Windows, .so on Linux).

    Args:
        base_env: Base dict (defaults to os.environ.copy()).

    Returns:
        New env dict — safe to pass to subprocess.run(env=…).
    """
    env = dict(base_env) if base_env else dict(os.environ)

    eda_bin = get_eda_bin()
    eda_lib = get_eda_lib()
    ivl_home = get_ivl_home()
    yosys_exe = get_yosys_exe()
    python_exe = get_python_exe()

    if eda_bin:
        env["EDA_BIN"] = eda_bin
    if eda_lib:
        env["EDA_LIB"] = eda_lib
    if ivl_home:
        env["IVL_HOME"] = ivl_home
    if yosys_exe:
        env["YOSYS_EXE"] = yosys_exe
    if python_exe:
        env["PYTHON_EXE"] = python_exe

    # Prepend EDA directories to PATH
    lib_dirs = eda_lib.split(os.pathsep) if eda_lib else []
    extra = os.pathsep.join(p for p in [eda_bin] + lib_dirs if p)
    if extra:
        env["PATH"] = extra + os.pathsep + env.get("PATH", "")

    return env
