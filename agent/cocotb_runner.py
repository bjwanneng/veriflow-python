#!/usr/bin/env python3
"""Parameterized cocotb simulation runner for VeriFlow-CC pipeline.

Runs cocotb tests for a single Verilog module using the Icarus Verilog (iverilog)
simulator backend. Designed to be called from Bash by Stage 3 (verify_fix).

Usage:
    python3 cocotb_runner.py \
        --rtl-dir     <path/to/workspace/rtl> \
        --tb-dir      <path/to/workspace/tb> \
        --module      <module_name> \
        --build-dir   <path/to/build_dir> \
        [--results-file <path/to/results.xml>] \
        [--verbose]

Exit codes:
    0 — all tests passed
    1 — one or more tests failed
    2 — environment error (cocotb not installed, RTL not found, etc.)

Output (stdout):
    JSON object: {"tests": N, "passed": M, "failed": F, "xml_path": "...",
                  "failures": [{"test": "...", "message": "..."}, ...]}
"""

import argparse
import json
import os
import sys
from pathlib import Path

from agent.eda_paths import (
    get_eda_bin,
    get_eda_lib,
    get_ivl_home,
    get_python_exe,
    get_yosys_exe,
    build_subprocess_env,
)

# Default configuration
DEFAULT_TIMEOUT_SECONDS = 300  # 5-minute simulation timeout


class CocotbRunnerError(Exception):
    """Raised for environment/config errors (maps to exit code 2)."""
    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload or {}


def check_environment():
    """Verify cocotb is importable. Raises CocotbRunnerError if not."""
    try:
        import cocotb                           # noqa: F401
        import cocotb_tools.runner              # noqa: F401
    except ImportError as e:
        raise CocotbRunnerError(
            f"cocotb not available: {e}",
            {"tests": 0, "passed": 0, "failed": 0,
             "error": f"cocotb not available: {e}",
             "hint": "Install with: pip install cocotb"}
        )


def collect_rtl_sources(rtl_dir: Path) -> list[str]:
    """Find all Verilog source files in rtl_dir."""
    sources = sorted(rtl_dir.glob("*.v"))
    if not sources:
        raise CocotbRunnerError(
            f"No .v files found in {rtl_dir}",
            {"tests": 0, "passed": 0, "failed": 0,
             "error": f"No .v files found in {rtl_dir}"}
        )
    return [str(s) for s in sources]


def find_test_module(tb_dir: Path, module_name: str) -> str:
    """Find the cocotb test file: test_<module>.py."""
    test_file = tb_dir / f"test_{module_name}.py"
    if not test_file.exists():
        raise CocotbRunnerError(
            f"Test file not found: {test_file}",
            {"tests": 0, "passed": 0, "failed": 0,
             "error": f"Test file not found: {test_file}"}
        )
    return f"test_{module_name}"


def main() -> int:
    """Run cocotb simulation. Returns exit code (0 pass, 1 fail, 2 env error).

    Outputs JSON summary to stdout on success/failure; error details on env error.
    Can be called programmatically (returns int) or via CLI (sys.exit(main())).
    """
    parser = argparse.ArgumentParser(
        description="cocotb simulation runner for VeriFlow-CC pipeline"
    )
    parser.add_argument("--rtl-dir", required=True,
                        help="Directory containing *.v RTL source files")
    parser.add_argument("--tb-dir", required=True,
                        help="Directory containing test_<module>.py testbench")
    parser.add_argument("--module", required=True,
                        help="Verilog top-level module name")
    parser.add_argument("--build-dir", required=True,
                        help="Build directory for cocotb artifacts")
    parser.add_argument("--results-file", default=None,
                        help="Path to write xUnit XML results (default: <build_dir>/results.xml)")
    parser.add_argument("--no-vcd", action="store_true",
                        help="Disable VCD waveform dump (default: enabled)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed per-test results")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                        help=f"Simulation timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})")
    args = parser.parse_args()

    try:
        # Phase 0: Check cocotb is importable
        check_environment()

        # Lazy imports after env check
        from cocotb_tools.runner import Icarus, get_results

        rtl_dir = Path(args.rtl_dir).resolve()
        tb_dir = Path(args.tb_dir).resolve()
        build_dir = Path(args.build_dir).resolve()
        module_name = args.module
        test_module = find_test_module(tb_dir, module_name)
        enable_vcd = not args.no_vcd

        build_dir.mkdir(parents=True, exist_ok=True)

        # Copy test file to build_dir so cocotb can import it
        # (cocotb's test_module discovery searches the test_dir)
        rtl_sources = collect_rtl_sources(rtl_dir)

        if args.verbose:
            print(f"[cocotb_runner] RTL dir   : {rtl_dir}", file=sys.stderr)
            print(f"[cocotb_runner] RTL files : {len(rtl_sources)}", file=sys.stderr)
            print(f"[cocotb_runner] TB dir    : {tb_dir}", file=sys.stderr)
            print(f"[cocotb_runner] Module    : {module_name}", file=sys.stderr)
            print(f"[cocotb_runner] Build dir : {build_dir}", file=sys.stderr)
            print(f"[cocotb_runner] VCD       : {'enabled' if enable_vcd else 'disabled'}", file=sys.stderr)
            print(f"[cocotb_runner] Timeout   : {args.timeout}s", file=sys.stderr)

        # ── Build clean PATH (Windows DLL pollution fix) ──────────────────
        clean_path = None
        original_path = os.environ.get("PATH", "")
        eda_bin = get_eda_bin()
        if sys.platform == "win32" and eda_bin:
            clean_path_parts = [eda_bin]
            eda_lib = get_eda_lib()
            if eda_lib:
                for lib_dir in eda_lib.split(os.pathsep):
                    if lib_dir:
                        clean_path_parts.append(lib_dir)
            clean_path_parts.append(os.path.dirname(sys.executable))
            windir = os.environ.get("WINDIR", r"C:\Windows")
            clean_path_parts.append(os.path.join(windir, "System32"))
            clean_path = os.pathsep.join(clean_path_parts)
            os.environ["PATH"] = clean_path

        # ── Icarus Verilog runner ──────────────────────────────────────────
        runner = Icarus()

        if clean_path:
            os.environ["PATH"] = original_path

        runner.env = {}
        if clean_path:
            runner.env["PATH"] = clean_path
        else:
            runner.env["PATH"] = build_subprocess_env().get("PATH", original_path)

        for key in ("SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE",
                    "PYTHONPATH", "LD_LIBRARY_PATH", "COVERAGE_FILE",
                    "COVERAGE_PROCESS_START"):
            if key in os.environ:
                runner.env[key] = os.environ[key]

        eda_lib = get_eda_lib()
        ivl_home = get_ivl_home()
        yosys_exe = get_yosys_exe()
        python_exe = get_python_exe()
        if eda_bin:
            runner.env["EDA_BIN"] = eda_bin
        if eda_lib:
            runner.env["EDA_LIB"] = eda_lib
        if ivl_home:
            runner.env["IVL_HOME"] = ivl_home
            runner.env["IVL"] = ivl_home
        if yosys_exe:
            runner.env["YOSYS_EXE"] = yosys_exe
        if python_exe:
            runner.env["PYTHON_EXE"] = python_exe

        runner.env["PYTHONUTF8"] = "1"
        runner.env["COCOTB_TIMEOUT"] = str(args.timeout)

        # ── Build ──────────────────────────────────────────────────────────
        if args.verbose:
            print(f"[cocotb_runner] Building {module_name}...", file=sys.stderr)

        try:
            runner.build(
                sources=rtl_sources,
                hdl_toplevel=module_name,
                build_dir=str(build_dir),
                build_args=[],
                waves=enable_vcd,
                timescale=("1ns", "1ps"),
            )
        except Exception as e:
            raise CocotbRunnerError(
                f"Build failed: {e}",
                {"tests": 0, "passed": 0, "failed": 0,
                 "error": f"Build failed: {e}"}
            )

        # ── Test ───────────────────────────────────────────────────────────
        if args.verbose:
            print(f"[cocotb_runner] Running tests for {test_module}...", file=sys.stderr)

        results_xml_path = args.results_file or str(build_dir / "results.xml")

        try:
            runner.test(
                test_module=test_module,
                hdl_toplevel=module_name,
                test_dir=str(tb_dir),
                build_dir=str(build_dir),
                results_xml=results_xml_path,
                waves=enable_vcd,
            )
        except Exception as e:
            print(json.dumps({
                "tests": 0, "passed": 0, "failed": 1,
                "error": f"Simulation crashed: {e}",
                "xml_path": results_xml_path,
                "failures": [{"test": test_module, "message": str(e)}]
            }))
            return 1

        # ── Parse results ──────────────────────────────────────────────────
        try:
            num_tests, num_failed = get_results(Path(results_xml_path))
        except RuntimeError as e:
            print(json.dumps({
                "tests": 0, "passed": 0, "failed": 1,
                "error": str(e),
                "xml_path": results_xml_path,
                "failures": [{"test": test_module, "message": str(e)}]
            }))
            return 1

        num_passed = num_tests - num_failed

        # ── Extract failure details from XML ───────────────────────────────
        failures = []
        if num_failed > 0:
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(results_xml_path)
                for suite in tree.iter("testsuite"):
                    for tc in suite.iter("testcase"):
                        fail_elem = tc.find("failure")
                        if fail_elem is not None:
                            msg = fail_elem.get("message", "")
                            text = fail_elem.text or ""
                            failures.append({
                                "test": tc.get("name", "unknown"),
                                "message": msg,
                                "traceback": text[:500],
                            })
            except Exception:
                pass

        # ── Output JSON summary to stdout ──────────────────────────────────
        vcd_files = sorted(build_dir.glob("*.vcd"))
        vcd_path = str(vcd_files[0]) if vcd_files else None
        if args.verbose and vcd_path:
            print(f"[cocotb_runner] VCD file  : {vcd_path}", file=sys.stderr)

        result = {
            "tests": num_tests,
            "passed": num_passed,
            "failed": num_failed,
            "xml_path": results_xml_path,
            "vcd_path": vcd_path,
            "failures": failures,
        }
        print(json.dumps(result))

        return 1 if num_failed > 0 else 0

    except CocotbRunnerError as e:
        print(json.dumps(e.payload))
        return 2


if __name__ == "__main__":
    sys.exit(main())
