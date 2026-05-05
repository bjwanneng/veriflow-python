#!/usr/bin/env python3
"""Shared golden model loader for VeriFlow pipeline tools.

Provides a single implementation of golden model loading used by both
iverilog_runner.py and vcd2table.py. Eliminates ~80 lines of duplicated logic.

Two strategies (tried in order):
  1. Import and call run() function directly — fast, type-safe, preserves int precision
  2. Run as standalone script and parse stdout — fallback when import fails

Usage:
    from agent.golden_loader import load_golden_cycles, run_golden_self_check

    cycles = load_golden_cycles("path/to/design_spec.py")
    result = run_golden_self_check("path/to/design_spec.py")
"""

import re
import subprocess
import sys
from pathlib import Path

_MAX_GOLDEN_OUTPUT = 2000
_MAX_FAILURES_SHOWN = 10


def load_golden_cycles(
    golden_path: str,
    test_vector_index: int = 0,
    verbose: bool = False,
) -> dict[int, dict[str, int | str]] | None:
    """Load golden model cycle trace for waveform comparison and bug classification.

    Runs the golden model to obtain per-cycle signal values.

    Args:
        golden_path: Path to design_spec.py or golden_model.py.
        test_vector_index: Which test vector to run (default 0).
        verbose: Print diagnostic messages to stderr.

    Returns:
        dict mapping cycle_num -> {signal_name: int|str}, or None on failure.
        Values preserve their original type (int from import, str from subprocess).
    """
    golden_path = str(Path(golden_path).resolve())
    if not Path(golden_path).exists():
        if verbose:
            print(f"[golden_loader] File not found: {golden_path}", file=sys.stderr)
        return None

    golden_cycles: dict[int, dict[str, int | str]] = {}

    # Strategy 1 (preferred): Import and call run() function directly.
    # Faster and type-safe — avoids stdout parsing and preserves int precision.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_golden_model", golden_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "run"):
                try:
                    golden_data = mod.run(test_vector_index=test_vector_index)
                except TypeError:
                    golden_data = mod.run()
                if isinstance(golden_data, list):
                    for i, entry in enumerate(golden_data):
                        if isinstance(entry, dict):
                            # Preserve original types — downstream consumers
                            # (normalize_for_compare, _normalize_value) handle
                            # both int and str.
                            golden_cycles[i] = dict(entry)
                    if verbose:
                        print(
                            f"[golden_loader] Imported run(), "
                            f"got {len(golden_cycles)} cycles",
                            file=sys.stderr,
                        )
    except Exception as e:
        if verbose:
            print(f"[golden_loader] Direct import failed ({e}), trying subprocess...",
                  file=sys.stderr)

    # Strategy 2 (fallback): Run as standalone script and parse stdout.
    # Used when import fails (e.g., module has unresolvable dependencies).
    if not golden_cycles:
        try:
            result = subprocess.run(
                [sys.executable, golden_path],
                capture_output=True, text=True, timeout=30,
                cwd=str(Path(golden_path).parent),
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    m = re.match(r'cycle\s+(\d+)\s*:\s+(.+)', line, re.IGNORECASE)
                    if m:
                        cycle = int(m.group(1))
                        assignments = m.group(2)
                        golden_cycles[cycle] = {}
                        for assignment in re.finditer(
                            r'(\w+)\s*=\s*(0x[0-9a-fA-F_]+|\d+)', assignments
                        ):
                            golden_cycles[cycle][assignment.group(1)] = assignment.group(2)
                if verbose:
                    print(
                        f"[golden_loader] Subprocess parsed {len(golden_cycles)} cycles",
                        file=sys.stderr,
                    )
        except subprocess.TimeoutExpired:
            if verbose:
                print("[golden_loader] Subprocess timed out after 30s", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[golden_loader] Subprocess error: {e}", file=sys.stderr)

    return golden_cycles if golden_cycles else None


def run_golden_self_check(
    golden_path: str,
    verbose: bool = False,
) -> dict:
    """Run golden model standalone and verify all test vectors pass.

    This MUST run BEFORE RTL simulation to confirm the reference model is correct.
    If the reference model itself has bugs, any RTL comparison is meaningless.

    Args:
        golden_path: Path to design_spec.py or golden_model.py.
        verbose: Print diagnostic messages to stderr.

    Returns:
        dict with keys: passed (bool), test_count, pass_count, fail_count,
            failures (list), output (str), error (str).
    """
    golden_path = str(Path(golden_path).resolve())
    if not Path(golden_path).exists():
        return {"error": f"Design spec / golden model not found: {golden_path}", "passed": False}

    try:
        result = subprocess.run(
            [sys.executable, golden_path],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(golden_path).parent),
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return {"error": "Golden model timed out after 30s", "passed": False}
    except Exception as e:
        return {"error": f"Golden model execution error: {e}", "passed": False}

    # Check for [PASS] and [FAIL] markers
    pass_lines = [l for l in output.splitlines() if "[PASS]" in l]
    fail_lines = [l for l in output.splitlines() if "[FAIL]" in l]

    if verbose:
        print(f"[golden_loader] Self-check output:\n{output[:3000]}", file=sys.stderr)

    if fail_lines:
        return {
            "passed": False,
            "test_count": len(pass_lines) + len(fail_lines),
            "pass_count": len(pass_lines),
            "fail_count": len(fail_lines),
            "failures": fail_lines[:_MAX_FAILURES_SHOWN],
            "output": output[:_MAX_GOLDEN_OUTPUT],
        }

    if result.returncode != 0:
        return {
            "passed": False,
            "error": f"Golden model exited with code {result.returncode}",
            "output": output[:_MAX_GOLDEN_OUTPUT],
        }

    return {
        "passed": True,
        "test_count": len(pass_lines),
        "pass_count": len(pass_lines),
        "fail_count": 0,
    }
