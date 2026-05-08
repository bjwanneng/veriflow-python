#!/usr/bin/env python3
"""VeriFlow Pipeline Initialization - zero external dependencies.

Usage: python init.py <project_dir>

Creates workspace directories, discovers tools, writes eda_env.sh,
initializes stage journal, and outputs structured JSON result.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def discover_python() -> str:
    """Find a working Python executable."""
    candidates = []
    # PATH-based candidates
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p:
            candidates.append(p)
    # Windows: use winreg for reliable Python discovery (faster than glob)
    if sys.platform == "win32":
        try:
            import winreg
            for key_path in (
                r"SOFTWARE\Python\PythonCore",
                r"SOFTWARE\WOW6432Node\Python\PythonCore",
            ):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as hkey:
                        i = 0
                        while True:
                            try:
                                version = winreg.EnumKey(hkey, i)
                                i += 1
                                try:
                                    with winreg.OpenKey(hkey, f"{version}\\InstallPath") as ip_key:
                                        install_path, _ = winreg.QueryValueEx(ip_key, "")
                                        exe_path = Path(install_path) / "python.exe"
                                        if exe_path.exists():
                                            candidates.append(str(exe_path))
                                except (OSError, FileNotFoundError):
                                    pass
                            except OSError:
                                break
                except (OSError, FileNotFoundError):
                    pass
            # Also check per-user installations
            for key_path in (r"SOFTWARE\Python\PythonCore",):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as hkey:
                        i = 0
                        while True:
                            try:
                                version = winreg.EnumKey(hkey, i)
                                i += 1
                                try:
                                    with winreg.OpenKey(hkey, f"{version}\\InstallPath") as ip_key:
                                        install_path, _ = winreg.QueryValueEx(ip_key, "")
                                        exe_path = Path(install_path) / "python.exe"
                                        if exe_path.exists():
                                            candidates.append(str(exe_path))
                                except (OSError, FileNotFoundError):
                                    pass
                            except OSError:
                                break
                except (OSError, FileNotFoundError):
                    pass
        except ImportError:
            # winreg not available (non-Windows), fall back to common paths
            for p in Path(os.path.expanduser("~")).glob(
                "AppData/Local/Programs/Python/*/python.exe"
            ):
                candidates.append(str(p))
    # Unix locations (harmless on Windows — paths won't exist)
    if sys.platform != "win32":
        for d in ("/opt/homebrew/bin", "/usr/bin", "/usr/local/bin"):
            p = Path(d) / "python3"
            if p.exists():
                candidates.append(str(p))

    for p in candidates:
        # Skip Windows Store stub
        if "WindowsApps" in p:
            continue
        try:
            result = subprocess.run(
                [p, "--version"], capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return p
        except (OSError, subprocess.TimeoutExpired):
            continue
    return ""


def discover_eda() -> tuple[str, str]:
    """Find EDA tools (iverilog, yosys). Returns (eda_bin, eda_lib)."""
    # Check existing EDA_BIN env var first (user override or previous run).
    existing = os.environ.get("EDA_BIN", "")
    if existing:
        # Normalize MSYS paths on Windows
        if sys.platform == "win32" and existing.startswith("/"):
            import re
            existing = re.sub(r'^/([A-Za-z])/', lambda m: f'{m.group(1).upper()}:/', existing)
        bin_dir = Path(existing)
        if bin_dir.is_dir() and any((bin_dir / n).exists() for n in ("iverilog", "iverilog.exe")):
            eda_bin = str(bin_dir)
            eda_lib = ""
            lib_dir = bin_dir.parent / "lib"
            if lib_dir.is_dir():
                eda_lib = str(lib_dir)
            ivl_dir = bin_dir.parent / "lib" / "ivl"
            if ivl_dir.is_dir():
                eda_lib = f"{eda_lib}{os.pathsep}{ivl_dir}" if eda_lib else str(ivl_dir)
            return eda_bin, eda_lib

    # Platform-specific search paths.
    # On Windows native Python, Unix-style /c/ paths resolve incorrectly
    # (e.g., /c/oss-cad-suite → C:\c\oss-cad-suite instead of C:\oss-cad-suite).
    # Only include paths appropriate for the current platform.
    search_dirs = []
    if sys.platform == "win32":
        search_dirs.extend([
            r"C:\oss-cad-suite",
            r"C:\Program Files\iverilog",
            r"C:\Program Files (x86)\iverilog",
        ])
    else:
        search_dirs.extend([
            "/opt/homebrew",        # macOS Apple Silicon (Homebrew default)
            "/opt/oss-cad-suite",
            "/usr/local",           # macOS Intel / Linux
            "/usr",
        ])
    search_dirs.append(os.path.expanduser("~/.local"))
    search_dirs.append(os.path.expanduser("~/oss-cad-suite"))

    for base in search_dirs:
        base_path = Path(base)
        bin_dir = base_path / "bin"
        # Check for iverilog or iverilog.exe in bin
        iverilog_path = bin_dir / "iverilog"
        iverilog_exe_path = bin_dir / "iverilog.exe"
        if not ((bin_dir.is_dir() and iverilog_path.exists())
                or iverilog_exe_path.exists()):
            continue
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
        # Also look for lib relative to bin
        eda_lib = ""
        lib_dir = Path(eda_bin).parent / "lib"
        if lib_dir.is_dir():
            eda_lib = str(lib_dir)
        return eda_bin, eda_lib
    return "", ""


def verify_iverilog(iverilog_path: str) -> tuple[bool, str]:
    """Actually run iverilog -V to verify it works (catches DLL issues on Windows).

    Returns (success, version_or_error).
    """
    if not iverilog_path:
        return False, "iverilog path is empty"
    try:
        from agent.eda_paths import build_subprocess_env
        result = subprocess.run(
            [iverilog_path, "-V"],
            capture_output=True, text=True, timeout=10,
            env=build_subprocess_env(),
        )
        if result.returncode == 0:
            version = (result.stdout + result.stderr).split("\n")[0]
            return True, version
        else:
            return False, f"iverilog -V returned code {result.returncode}: {(result.stderr or '')[:200]}"
    except FileNotFoundError:
        return False, f"iverilog not found at {iverilog_path}"
    except OSError as e:
        # On Windows, this catches DLL loading failures (e.g., 0xC0000139)
        return False, f"Cannot execute iverilog (shared library error?): {e}"
    except subprocess.TimeoutExpired:
        return False, "iverilog -V timed out"


def smoke_test_vvp(iverilog_path: str, timeout: int = 15) -> tuple[bool, str]:
    """Compile and run a minimal testbench to verify vvp works end-to-end.

    This catches issues that iverilog -V alone cannot:
    - vvp.exe missing DLLs (e.g., STATUS_ENTRYPOINT_NOT_FOUND on Windows)
    - vvp runtime library not in PATH
    - Corrupted installation where compiler works but simulator doesn't

    Returns (success, detail_message).
    """
    vvp_path = str(Path(iverilog_path).parent / ("vvp.exe" if sys.platform == "win32" else "vvp"))
    if not Path(vvp_path).exists():
        vvp_path = str(Path(iverilog_path).parent / "vvp")

    smoke_tb = (
        'module smoke_tb;\n'
        '    reg clk = 0;\n'
        '    integer cycle = 0;\n'
        '    always #5 clk = ~clk;\n'
        '    always @(posedge clk) begin\n'
        '        cycle <= cycle + 1;\n'
        '        if (cycle == 3) begin $display("SMOKE_PASS"); $finish; end\n'
        '    end\n'
        'endmodule\n'
    )

    import tempfile
    with tempfile.TemporaryDirectory(prefix="vf_smoke_") as tmpdir:
        tb_file = Path(tmpdir) / "smoke_tb.v"
        vvp_file = Path(tmpdir) / "smoke_tb.vvp"
        tb_file.write_text(smoke_tb)

        try:
            from agent.eda_paths import build_subprocess_env
            smoke_env = build_subprocess_env()
            comp = subprocess.run(
                [iverilog_path, "-o", str(vvp_file), str(tb_file)],
                capture_output=True, text=True, timeout=10,
                cwd=tmpdir, env=smoke_env,
            )
            if comp.returncode != 0:
                return False, f"Smoke compile failed: {comp.stderr[:200]}"
        except Exception as e:
            return False, f"Smoke compile error: {e}"

        try:
            sim = subprocess.run(
                [vvp_path, str(vvp_file)],
                capture_output=True, text=True, timeout=timeout,
                cwd=tmpdir, env=smoke_env,
            )
            if sim.returncode != 0:
                return False, f"vvp exited code {sim.returncode}: {(sim.stderr or '')[:200]}"
            if "SMOKE_PASS" not in sim.stdout:
                return False, f"vvp ran but no SMOKE_PASS output: {(sim.stdout or '')[:200]}"
            return True, "vvp smoke test passed"
        except FileNotFoundError:
            return False, f"vvp not found at {vvp_path}"
        except OSError as e:
            return False, f"Cannot execute vvp (shared library error?): {e}"
        except subprocess.TimeoutExpired:
            return False, f"vvp smoke test timed out after {timeout}s (possible runtime error)"


def discover_yosys(eda_bin_hint: str = "") -> str:
    """Find yosys executable. Returns path or empty string."""
    # Use provided hint first (already normalized, from discover_eda())
    if eda_bin_hint:
        for name in ("yosys", "yosys.exe"):
            p = Path(eda_bin_hint) / name
            if p.exists():
                return str(p)
    # Check EDA_BIN env var (may need MSYS normalization)
    eda_bin = os.environ.get("EDA_BIN", "")
    if eda_bin:
        # Normalize MSYS paths on Windows
        if sys.platform == "win32" and eda_bin.startswith("/"):
            import re
            eda_bin = re.sub(r'(^|[:;])/([A-Za-z])/', lambda m: f'{m.group(2).upper()}:/', eda_bin)
        for name in ("yosys", "yosys.exe"):
            p = Path(eda_bin) / name
            if p.exists():
                return str(p)
    for name in ("yosys", "yosys.exe"):
        found = shutil.which(name)
        if found:
            return found
    # Check common installation directories
    bases = []
    if sys.platform == "win32":
        bases.append(r"C:\oss-cad-suite")
    else:
        bases.extend(["/opt/homebrew", "/opt/oss-cad-suite", "/usr/local", "/usr"])
    for base in bases:
        for name in ("yosys", "yosys.exe"):
            p = Path(base) / "bin" / name
            if p.exists():
                return str(p)
    return ""


def check_cocotb(python_exe: str) -> bool:
    """Check if cocotb is available."""
    if not python_exe:
        return False
    try:
        result = subprocess.run(
            [python_exe, "-c", "import cocotb; import cocotb_tools.runner"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def init_journal(project_dir: Path, is_resume: bool) -> None:
    """Initialize or append to stage journal."""
    journal_path = project_dir / "workspace" / "docs" / "stage_journal.md"
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    from datetime import datetime

    ts = datetime.now().isoformat()

    if is_resume:
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(f"\n---\n\n**Session resumed** at {ts}\n\n")
    elif not journal_path.exists():
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write(
                "# VeriFlow Pipeline Stage Journal\n\n"
                "This file records the progress, outputs, and key decisions for each pipeline stage.\n"
            )
            f.write(f"\n## Pipeline Start\n**Timestamp**: {ts}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python init.py <project_dir>", file=sys.stderr)
        sys.exit(1)

    project_dir = Path(sys.argv[1]).resolve()

    # Validate requirement.md
    req_file = project_dir / "requirement.md"
    if not req_file.exists():
        print(f"[FATAL] requirement.md not found in {project_dir}", file=sys.stderr)
        sys.exit(1)

    # Create directories
    for d in [
        "workspace/docs",
        "workspace/rtl",
        "workspace/tb",
        "workspace/sim",
        "workspace/synth",
        ".veriflow",
        "logs",
    ]:
        (project_dir / d).mkdir(parents=True, exist_ok=True)

    # Report input files
    inputs = {}
    for name in ("requirement.md", "constraints.md", "design_intent.md"):
        exists = (project_dir / name).exists()
        inputs[name] = exists
        print(f"[INPUT] {name}: {'YES' if exists else 'NO'}")

    context_dir = project_dir / "context"
    if context_dir.exists():
        ctx_files = list(context_dir.glob("*.md"))
        print(f"[INPUT] context/: {len(ctx_files)} file(s)")
        for f in ctx_files:
            print(f"  - {f.name}")
        inputs["context_files"] = [f.name for f in ctx_files]
    else:
        print("[INPUT] context/: 0 file(s)")
        inputs["context_files"] = []

    # Discover tools
    python_exe = discover_python()
    print(f"[ENV] Python: {python_exe or 'NOT FOUND'}")

    cocotb_available = check_cocotb(python_exe)
    print(f"[ENV] cocotb: {'AVAILABLE' if cocotb_available else 'NOT AVAILABLE'}")

    eda_bin, eda_lib = discover_eda()
    print(f"[ENV] EDA_BIN={eda_bin}  EDA_LIB={eda_lib}")

    yosys_exe = discover_yosys(eda_bin_hint=eda_bin)
    print(f"[ENV] Yosys: {yosys_exe or 'NOT FOUND'}")

    # Detect IVL_HOME (iverilog needs this for its internal preprocessor)
    ivl_home = ""
    if eda_bin:
        eda_base = str(Path(eda_bin).parent)
        for candidate in [
            Path(eda_base) / "lib" / "ivl",
            Path(eda_bin) / ".." / "lib" / "ivl",
        ]:
            if candidate.is_dir():
                ivl_home = str(candidate.resolve())
                break

    # ── Write eda_env.sh and eda_env.json ──────────────────────────────
    veriflow_dir = project_dir / ".veriflow"

    # Preserve native platform paths for JSON output (never overwrite originals).
    python_exe_native = python_exe
    eda_bin_native = eda_bin
    eda_lib_native = eda_lib
    ivl_home_native = ivl_home
    yosys_exe_native = yosys_exe

    # Convert EDA_LIB separator to ":" for bash scripts (semicolon on Windows).
    eda_lib_bash = eda_lib.replace(os.pathsep, ":") if os.pathsep != ":" else eda_lib

    # Build PATH for bash (colon-separated, bash convention).
    path_entries = [p for p in [eda_bin, eda_lib_bash] if p]
    path_entries.append("$PATH")
    path_str = ":".join(path_entries)

    # On Windows, convert native paths to forward-slash C:/path format.
    # IMPORTANT: Use C:/path (not MSYS /c/path) so that:
    #   - Git Bash can still find executables (handles C:/ correctly)
    #   - Python child processes can resolve paths (Path("C:/...") works on Windows)
    #   - MSYS /c/path breaks Python: Path("/c/...") → C:\c\... (wrong!)
    def _to_bash_path(p):
        """Convert Windows backslashes to forward slashes and semicolons to colons."""
        return p.replace("\\", "/").replace(";", ":") if p else p

    if sys.platform == "win32":
        python_exe_bash = _to_bash_path(python_exe)
        eda_bin_bash = _to_bash_path(eda_bin)
        eda_lib_bash_out = _to_bash_path(eda_lib_bash)
        ivl_home_bash = _to_bash_path(ivl_home)
        yosys_exe_bash = _to_bash_path(yosys_exe)
        path_str = _to_bash_path(path_str)
    else:
        python_exe_bash = python_exe
        eda_bin_bash = eda_bin
        eda_lib_bash_out = eda_lib_bash
        ivl_home_bash = ivl_home
        yosys_exe_bash = yosys_exe

    # Write eda_env.sh (sourced by bash at each pipeline stage)
    eda_env_path = veriflow_dir / "eda_env.sh"
    with open(eda_env_path, "w", encoding="utf-8") as f:
        f.write(f'export PYTHON_EXE="{python_exe_bash}"\n')
        f.write('export PYTHONUTF8=1\n')
        f.write(f'export EDA_BIN="{eda_bin_bash}"\n')
        f.write(f'export EDA_LIB="{eda_lib_bash_out}"\n')
        f.write(f'export IVL_HOME="{ivl_home_bash}"\n')
        f.write(f'export COCOTB_AVAILABLE="{"true" if cocotb_available else "false"}"\n')
        f.write(f'export YOSYS_EXE="{yosys_exe_bash}"\n')
        f.write(f'export PATH="{path_str}"\n')
    print(f"[ENV] Wrote {eda_env_path}")

    # Write eda_env.json (native platform paths, consumed by Python runners)
    eda_env_json = {
        "python_exe": python_exe_native,
        "eda_bin": eda_bin_native,
        "eda_lib": eda_lib_native,
        "ivl_home": ivl_home_native,
        "yosys_exe": yosys_exe_native,
        "cocotb_available": cocotb_available,
    }
    eda_json_path = veriflow_dir / "eda_env.json"
    eda_json_path.write_text(json.dumps(eda_env_json, indent=2), encoding="utf-8")
    print(f"[ENV] Wrote {eda_json_path}")

    if ivl_home_native:
        print(f"[ENV] IVL_HOME={ivl_home_native}")

    # Verify iverilog actually runs (catches DLL issues on Windows)
    if eda_bin:
        iverilog_path = ""
        for name in ("iverilog", "iverilog.exe"):
            p = Path(eda_bin) / name
            if p.exists():
                iverilog_path = str(p)
                break

        if iverilog_path:
            ok, msg = verify_iverilog(iverilog_path)
            if ok:
                print(f"[ENV] iverilog: {msg}")
                vvp_ok, vvp_msg = smoke_test_vvp(iverilog_path)
                if vvp_ok:
                    print(f"[ENV] vvp: {vvp_msg}")
                else:
                    print(f"[WARN] vvp smoke test failed: {vvp_msg}")
                    print("[WARN] Simulation will likely fail at runtime even if compilation succeeds.")
                    if sys.platform == "win32":
                        print("[HINT] Ensure oss-cad-suite lib/ivl/ directory is on PATH or set IVL environment variable")
            else:
                print(f"[WARN] iverilog found but verification failed: {msg}")
                print("[WARN] Simulation may fail. Check DLL/library dependencies.")
                if sys.platform == "win32":
                    print("[HINT] On Windows, ensure oss-cad-suite bin/ and lib/ are both on PATH")
        else:
            print("[WARN] iverilog binary not found in EDA_BIN")

    # Windows DLL check
    if sys.platform == "win32" and eda_bin:
        eda_bin_path = Path(eda_bin)
        dll_files = list(eda_bin_path.glob("*.dll"))
        if dll_files:
            print(f"[ENV] DLLs found in EDA_BIN: {len(dll_files)}")
        else:
            lib_dlls = []
            for lib_dir_str in (eda_lib.split(os.pathsep) if eda_lib else []):
                lib_dir = Path(lib_dir_str)
                if lib_dir.is_dir():
                    lib_dlls.extend(lib_dir.glob("*.dll"))
            if lib_dlls:
                print(f"[WARN] DLLs found in EDA_LIB ({len(lib_dlls)}) but not in EDA_BIN")
                print("[HINT] Ensure EDA_LIB directories are on PATH for Windows DLL resolution")
            else:
                print("[WARN] No DLLs found near iverilog — may fail on Windows")

    # macOS shared library check
    if sys.platform == "darwin" and eda_bin:
        eda_base = str(Path(eda_bin).parent)
        dylib_dirs = []
        for lib_dir_str in (eda_lib.split(os.pathsep) if eda_lib else []):
            lib_dir = Path(lib_dir_str)
            if lib_dir.is_dir() and (list(lib_dir.glob("*.dylib")) or list(lib_dir.glob("*.so"))):
                dylib_dirs.append(str(lib_dir))
        if dylib_dirs:
            print(f"[ENV] Shared libraries found in: {dylib_dirs}")
        elif eda_lib:
            print(f"[WARN] EDA_LIB set but no .dylib/.so found — vvp may fail to start")
            print(f"[HINT] Set DYLD_LIBRARY_PATH={eda_lib} or install via Homebrew: brew install iverilog")

    # Check existing state
    state_path = veriflow_dir / "pipeline_state.json"
    state = {"is_resume": False, "stages_completed": [], "next_stage": "design_spec"}
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            state["is_resume"] = True
            state["stages_completed"] = data.get("stages_completed", [])
            completed = state["stages_completed"]
            stages = ["design_spec", "codegen", "verify_fix", "lint_synth"]
            for s in stages:
                if s not in completed:
                    state["next_stage"] = s
                    break
            else:
                state["next_stage"] = "complete"
            print(f"[STATUS] Resuming — next stage: {state['next_stage']}")
            print(f"[STATUS] Completed: {completed}")
        except json.JSONDecodeError:
            print("[STATUS] Corrupted state, starting fresh")
    else:
        print("[STATUS] New project, starting from Stage 1.")

    # Init journal
    init_journal(project_dir, state["is_resume"])

    # Output structured JSON for main session (always native platform paths)
    result = {
        "project_dir": str(project_dir),
        "python_exe": python_exe_native,
        "eda_bin": eda_bin_native,
        "eda_lib": eda_lib_native,
        "cocotb_available": cocotb_available,
        "yosys_exe": yosys_exe_native,
        "is_resume": state["is_resume"],
        "stages_completed": state["stages_completed"],
        "next_stage": state["next_stage"],
        "inputs": inputs,
    }

    result_path = veriflow_dir / "init_result.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n[INIT] Complete. Result: {result_path}")


if __name__ == "__main__":
    main()
