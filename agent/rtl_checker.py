#!/usr/bin/env python3
"""Post-codegen RTL verification for VeriFlow pipeline.

Catches common errors that weaker AI models make when generating Verilog,
before simulation runs. Checks:

  1. Port matching: instantiation port names must match module declarations
  2. Reset strategy: sensitive list must match declared strategy
  3. Output reg violation: combinational outputs should be output wire
  4. Testbench NBA: sync-driven signals must use <= not =
  5. Missing default in case statements
  6. Full-design compilation via iverilog (catches cross-module errors)

Usage:
    python rtl_checker.py --rtl-dir <rtl_dir> [--tb-dir <tb_dir>]
    python rtl_checker.py --rtl-dir workspace/rtl --tb-dir workspace/tb

Exit codes:
    0 — all checks passed
    1 — errors found
    2 — environment error

Output (stdout):
    JSON object: {"errors": [...], "warnings": [...], "passed": bool}
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from agent.eda_paths import find_executable, build_subprocess_env

__all__ = ["check_rtl"]


# ---------------------------------------------------------------------------
# Verilog Parser Helpers
# ---------------------------------------------------------------------------

_MODULE_RE = re.compile(
    r'module\s+(\w+)\s*\((.*?)\)\s*;', re.DOTALL
)

_PORT_RE = re.compile(
    r'(input|output)\s+(wire|reg)?\s*(\[\d+:\d+\])?\s*(\w+)'
)

_INSTANTIATION_RE = re.compile(
    r'(\w+)\s+(?:#\s*\(.*?\)\s*)?(\w+)\s*\((.*?)\)\s*;',
    re.DOTALL
)

_NAMED_PORT_RE = re.compile(
    r'\.(\w+)\s*\('
)

_ALWAYS_RE = re.compile(
    r'always\s*@\s*\((.*?)\)\s*begin(.*?)end',
    re.DOTALL
)

_CASE_RE = re.compile(
    r'\b(case|casex|casez)\b(.*?)(?=case|endcase|endmodule)',
    re.DOTALL
)

_BLOCKING_ASSIGN_RE = re.compile(
    r'^\s*(\w+)\s*=\s*',
    re.MULTILINE
)


def parse_modules(verilog_code: str) -> dict[str, dict]:
    """Parse module declarations and extract port information.

    Returns:
        {module_name: {
            "ports": {port_name: {"direction": str, "type": str, "width": str}},
            "line": int (1-based line number of module keyword)
        }}
    """
    modules = {}
    for m in _MODULE_RE.finditer(verilog_code):
        mod_name = m.group(1)
        port_block = m.group(2)
        line = verilog_code[:m.start()].count('\n') + 1

        ports = {}
        for p in _PORT_RE.finditer(port_block):
            direction = p.group(1)
            port_type = p.group(2) or "wire"
            width = p.group(3) or ""
            port_name = p.group(4)
            ports[port_name] = {
                "direction": direction,
                "type": port_type,
                "width": width,
            }

        modules[mod_name] = {"ports": ports, "line": line}

    return modules


def parse_instantiations(verilog_code: str) -> list[dict]:
    """Parse module instantiation statements.

    Returns:
        [{"module_name": str, "instance_name": str, "ports": [str],
          "line": int}]
    """
    instances = []
    for m in _INSTANTIATION_RE.finditer(verilog_code):
        mod_name = m.group(1)
        inst_name = m.group(2)
        port_block = m.group(3)
        line = verilog_code[:m.start()].count('\n') + 1

        # Skip Verilog keywords that look like instantiations
        if mod_name in ("assign", "wire", "reg", "integer", "always",
                        "initial", "parameter", "localparam", "genvar",
                        "generate", "endgenerate"):
            continue

        ports = _NAMED_PORT_RE.findall(port_block)
        if ports:  # Only named port connections
            instances.append({
                "module_name": mod_name,
                "instance_name": inst_name,
                "ports": ports,
                "line": line,
            })

    return instances


# ---------------------------------------------------------------------------
# Check Functions
# ---------------------------------------------------------------------------

def check_port_matching(
    all_modules: dict[str, dict],
    all_instances: list[dict],
    filename: str,
) -> list[dict]:
    """Check that instantiation port names match module declarations."""
    errors = []

    for inst in all_instances:
        mod_name = inst["module_name"]
        if mod_name not in all_modules:
            # Module not in our parsed set — could be a primitive
            continue

        declared_ports = all_modules[mod_name]["ports"]
        for port_name in inst["ports"]:
            if port_name not in declared_ports:
                errors.append({
                    "check": "port_matching",
                    "severity": "error",
                    "file": filename,
                    "line": inst["line"],
                    "message": (
                        f"Instance '{inst['instance_name']}' of '{mod_name}' "
                        f"uses port '.{port_name}()' which does not exist in "
                        f"module declaration. Available ports: "
                        f"{sorted(declared_ports.keys())}"
                    ),
                })

        # Check for missing ports (declared but not connected)
        connected = set(inst["ports"])
        declared = set(declared_ports.keys())
        # clk and rst are often auto-connected, skip those
        declared_minus_clk = declared - {"clk", "rst", "rst_n"}
        missing = declared_minus_clk - connected
        if missing:
            errors.append({
                "check": "port_matching",
                "severity": "warning",
                "file": filename,
                "line": inst["line"],
                "message": (
                    f"Instance '{inst['instance_name']}' of '{mod_name}' "
                    f"missing connections for ports: {sorted(missing)}"
                ),
            })

    return errors


def check_output_reg_violation(
    modules: dict[str, dict],
    filename: str,
) -> list[dict]:
    """Flag 'output reg' that should be 'output wire' per two-block pattern."""
    errors = []
    for mod_name, mod_info in modules.items():
        for port_name, port_info in mod_info["ports"].items():
            if port_info["direction"] == "output" and port_info["type"] == "reg":
                errors.append({
                    "check": "output_reg_violation",
                    "severity": "warning",
                    "file": filename,
                    "line": mod_info["line"],
                    "message": (
                        f"Module '{mod_name}': port '{port_name}' declared as "
                        f"'output reg'. Per coding_style_core.md C2, outputs "
                        f"should be 'output wire' driven via 'assign' from a "
                        f"'_reg' internal signal."
                    ),
                })
    return errors


def check_reset_strategy(
    verilog_code: str,
    filename: str,
    strategy: str = "synchronous",
) -> list[dict]:
    """Check that reset implementation matches declared strategy.

    Args:
        strategy: "synchronous" (default per design_rules.md) or "asynchronous"
    """
    errors = []

    for m in _ALWAYS_RE.finditer(verilog_code):
        sensitive = m.group(1).strip()
        line = verilog_code[:m.start()].count('\n') + 1

        has_posedge_clk = "posedge clk" in sensitive
        has_negedge_rst = "negedge rst" in sensitive or "negedge rst_n" in sensitive
        has_posedge_rst = "posedge rst" in sensitive

        if not has_posedge_clk:
            continue

        if strategy == "synchronous":
            if has_negedge_rst or has_posedge_rst:
                errors.append({
                    "check": "reset_strategy",
                    "severity": "error",
                    "file": filename,
                    "line": line,
                    "message": (
                        f"Async reset in sensitive list '{sensitive}' but "
                        f"design_rules.md specifies synchronous reset. "
                        f"Use 'always @(posedge clk)' with 'if (rst)' inside."
                    ),
                })
        elif strategy == "asynchronous":
            if not (has_negedge_rst or has_posedge_rst):
                # Check if this always block uses rst inside
                body = m.group(2)
                if re.search(r'\bif\s*\(\s*!?rst', body):
                    errors.append({
                        "check": "reset_strategy",
                        "severity": "error",
                        "file": filename,
                        "line": line,
                        "message": (
                            f"Synchronous reset implementation but "
                            f"design_rules.md specifies asynchronous. "
                            f"Add 'negedge rst_n' or 'posedge rst' to "
                            f"sensitive list."
                        ),
                    })

    return errors


def check_missing_default(
    verilog_code: str,
    filename: str,
) -> list[dict]:
    """Flag case/casex/casez statements without default."""
    errors = []

    for m in _CASE_RE.finditer(verilog_code):
        case_body = m.group(2)
        line = verilog_code[:m.start()].count('\n') + 1
        case_type = m.group(1)

        if "default" not in case_body:
            errors.append({
                "check": "missing_default",
                "severity": "warning",
                "file": filename,
                "line": line,
                "message": (
                    f"{case_type} statement at line {line} missing "
                    f"'default' branch (coding_style_core.md C12)."
                ),
            })

    return errors


def check_testbench_nba(
    verilog_code: str,
    filename: str,
) -> list[dict]:
    """Check testbench for blocking assignments on sync-driven signals.

    In testbenches, signals like rst_n, start, data_in that are consumed
    by the DUT's always @(posedge clk) must use <= (non-blocking).
    """
    errors = []
    # Skip clk itself — blocking assignment for clk is standard
    clk_names = {"clk", "clock", "clk_i"}

    # Find all always @(posedge clk) blocks in the testbench
    for m in _ALWAYS_RE.finditer(verilog_code):
        sensitive = m.group(1).strip()
        if "posedge" not in sensitive:
            continue

        body = m.group(2)
        line_start = verilog_code[:m.start()].count('\n') + 1

        for assign_m in _BLOCKING_ASSIGN_RE.finditer(body):
            signal_name = assign_m.group(1)
            if signal_name in clk_names:
                continue
            # Non-blocking uses <=, blocking uses = but not ==
            # The regex already matches `signal = ...`, check it's not `<=`
            pos = assign_m.start()
            if pos > 0 and body[pos - 1] == '<':
                continue  # This is <=, not =

            assign_line = line_start + body[:pos].count('\n')
            errors.append({
                "check": "testbench_nba",
                "severity": "error",
                "file": filename,
                "line": assign_line,
                "message": (
                    f"Blocking assignment '{signal_name} = ...' inside "
                    f"@(posedge ...) block. Use non-blocking '{signal_name} <= ...' "
                    f"to prevent race condition with DUT."
                ),
            })

    return errors


def check_full_compilation(
    rtl_dir: Path,
    verbose: bool = False,
) -> list[dict]:
    """Run iverilog with all RTL files together to catch cross-module errors."""
    errors = []
    sources = sorted(rtl_dir.glob("*.v"))
    if not sources:
        return errors

    iverilog_exe = find_executable(["iverilog", "iverilog.exe"])
    if not iverilog_exe:
        return [{"check": "compilation", "severity": "warning",
                 "file": "", "line": 0,
                 "message": "iverilog not found, skipping full compilation check"}]

    cmd = [iverilog_exe, "-g2005", "-tnull"] + [str(s) for s in sources]
    env = build_subprocess_env()

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, env=env,
        )
        if result.returncode != 0 and result.stderr.strip():
            for line in result.stderr.strip().splitlines()[:20]:
                errors.append({
                    "check": "compilation",
                    "severity": "error",
                    "file": "",
                    "line": 0,
                    "message": f"iverilog: {line.strip()}",
                })
    except subprocess.TimeoutExpired:
        errors.append({
            "check": "compilation", "severity": "warning",
            "file": "", "line": 0,
            "message": "iverilog compilation timed out after 60s",
        })
    except Exception as e:
        errors.append({
            "check": "compilation", "severity": "warning",
            "file": "", "line": 0,
            "message": f"iverilog execution error: {e}",
        })

    return errors


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def check_rtl(
    rtl_dir: Path,
    tb_dir: Path | None = None,
    *,
    reset_strategy: str = "synchronous",
    verbose: bool = False,
) -> dict:
    """Run all RTL checks and return structured result.

    Returns:
        {"errors": [...], "warnings": [...], "passed": bool}
    """
    all_errors: list[dict] = []
    all_warnings: list[dict] = []

    # Phase 1: Parse all RTL files
    all_modules: dict[str, dict] = {}
    all_instances_by_file: dict[str, list[dict]] = {}

    rtl_sources = sorted(rtl_dir.glob("*.v"))
    if not rtl_sources:
        return {
            "errors": [{"check": "rtl_dir", "severity": "error",
                        "file": "", "line": 0,
                        "message": f"No .v files found in {rtl_dir}"}],
            "warnings": [],
            "passed": False,
        }

    for vfile in rtl_sources:
        code = vfile.read_text(encoding="utf-8", errors="replace")
        fname = vfile.name

        modules = parse_modules(code)
        all_modules.update(modules)

        instances = parse_instantiations(code)
        all_instances_by_file[fname] = instances

        # Per-file checks
        all_errors.extend(check_port_matching(modules, instances, fname))
        all_errors.extend(check_output_reg_violation(modules, fname))
        all_errors.extend(check_reset_strategy(code, fname, reset_strategy))
        all_warnings.extend(check_missing_default(code, fname))

    # Phase 2: Cross-file port matching
    # Re-check port matching with full module database
    cross_file_errors = []
    for fname, instances in all_instances_by_file.items():
        code = (rtl_dir / fname).read_text(encoding="utf-8", errors="replace")
        for inst in instances:
            mod_name = inst["module_name"]
            if mod_name not in all_modules:
                continue
            declared_ports = all_modules[mod_name]["ports"]
            for port_name in inst["ports"]:
                if port_name not in declared_ports:
                    cross_file_errors.append({
                        "check": "port_matching",
                        "severity": "error",
                        "file": fname,
                        "line": inst["line"],
                        "message": (
                            f"Instance '{inst['instance_name']}' of '{mod_name}' "
                            f"uses port '.{port_name}()' not in declaration. "
                            f"Available: {sorted(declared_ports.keys())}"
                        ),
                    })

    # Deduplicate port matching errors (per-file check may overlap)
    seen_messages = set()
    for e in all_errors + cross_file_errors:
        seen_messages.add(e.get("message", ""))

    # Phase 3: Testbench checks
    if tb_dir and tb_dir.is_dir():
        for tb_file in sorted(tb_dir.glob("tb_*.v")):
            code = tb_file.read_text(encoding="utf-8", errors="replace")
            all_errors.extend(check_testbench_nba(code, tb_file.name))

    # Phase 4: Full compilation
    comp_results = check_full_compilation(rtl_dir, verbose=verbose)

    # Classify results
    errors = [r for r in all_errors + cross_file_errors + comp_results
              if r.get("severity") == "error"]
    warnings = [r for r in all_errors + all_warnings + comp_results
                if r.get("severity") == "warning"]

    # Deduplicate
    def _dedup(items):
        seen = set()
        result = []
        for item in items:
            key = (item.get("check", ""), item.get("message", ""))
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    errors = _dedup(errors)
    warnings = _dedup(warnings)

    return {
        "errors": errors,
        "warnings": warnings,
        "passed": len(errors) == 0,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Post-codegen RTL verification for VeriFlow pipeline"
    )
    parser.add_argument("--rtl-dir", required=True,
                        help="Directory containing *.v RTL files")
    parser.add_argument("--tb-dir", default=None,
                        help="Directory containing tb_*.v testbench files")
    parser.add_argument("--reset-strategy", default="synchronous",
                        choices=["synchronous", "asynchronous"],
                        help="Expected reset strategy (default: synchronous)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed output")
    args = parser.parse_args()

    rtl_dir = Path(args.rtl_dir).resolve()
    tb_dir = Path(args.tb_dir).resolve() if args.tb_dir else None

    if not rtl_dir.is_dir():
        print(json.dumps({
            "errors": [{"message": f"RTL dir not found: {rtl_dir}"}],
            "warnings": [], "passed": False,
        }))
        sys.exit(2)

    result = check_rtl(rtl_dir, tb_dir,
                       reset_strategy=args.reset_strategy,
                       verbose=args.verbose)

    print(json.dumps(result, indent=2))

    if args.verbose:
        print(f"\n[rtl_checker] Errors: {len(result['errors'])}, "
              f"Warnings: {len(result['warnings'])}", file=sys.stderr)

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
