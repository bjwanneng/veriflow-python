#!/usr/bin/env python3
"""Static consistency checker for MODULE_HIERARCHY vs design_spec.py.

Validates that the declarative MODULE_HIERARCHY in design_spec.py is
consistent with the Python function signatures (Section 5) and that
every declared connection has a matching function parameter.

Usage:
    python hierarchy_check.py <design_spec.py>
    python hierarchy_check.py workspace/docs/design_spec.py --verbose

Exit codes:
    0 — all checks passed
    1 — inconsistencies found
"""

import argparse
import inspect
import json
import re
import sys
from pathlib import Path

__all__ = ["check_hierarchy"]


def _load_design_spec(filepath: str):
    """Load design_spec.py as a module."""
    import importlib.util

    filepath = str(Path(filepath).resolve())
    module_dir = str(Path(filepath).parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)

    spec = importlib.util.spec_from_file_location("_ds", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_section5_functions(mod) -> dict[str, list[str]]:
    """Extract Section 5 pseudocode functions and their parameter names.

    Returns:
        {function_name: [param_name, ...]}
    """
    functions = {}

    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if name.startswith("_") or name in (
            "compute", "run", "get_test_vectors",
            "ROL", "print_lut_verilog", "print_wide_const_verilog",
        ):
            continue

        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue

        # Only functions with timing annotations are Section 5 modules
        if not (
            "timing_contract" in src
            or "# wire" in src
            or "# reg_next" in src
            or "build_" in name
        ):
            continue

        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        functions[name] = params

    return functions


def check_hierarchy(mod, *, verbose: bool = False) -> list[str]:
    """Check MODULE_HIERARCHY consistency against Python function signatures.

    Checks:
    1. Every module referenced in MODULE_HIERARCHY has a corresponding Python function
    2. Every connection port name matches a function parameter name
    3. Every function parameter is covered by a connection (no missing)
    4. Instance names are unique within a parent module

    Returns:
        List of error strings (empty = all OK).
    """
    errors = []

    if not hasattr(mod, "MODULE_HIERARCHY"):
        errors.append(
            "MODULE_HIERARCHY not found in design_spec.py. "
            "Add a MODULE_HIERARCHY dict to Section 2 declaring "
            "submodule instances and connections."
        )
        return errors

    hierarchy = getattr(mod, "MODULE_HIERARCHY")
    if not isinstance(hierarchy, dict):
        errors.append("MODULE_HIERARCHY must be a dict")
        return errors

    functions = _get_section5_functions(mod)

    if verbose:
        print(f"[INFO] Section 5 functions: {sorted(functions.keys())}")
        print(f"[INFO] Hierarchy parents: {sorted(hierarchy.keys())}")

    for parent_name, parent_info in hierarchy.items():
        if not isinstance(parent_info, dict):
            errors.append(
                f"MODULE_HIERARCHY['{parent_name}'] must be a dict "
                f"with 'submodules' key"
            )
            continue

        submodules = parent_info.get("submodules", [])
        if not isinstance(submodules, list):
            errors.append(
                f"MODULE_HIERARCHY['{parent_name}']['submodules'] must be a list"
            )
            continue

        # Check instance name uniqueness
        inst_names = [s.get("instance_name", "") for s in submodules]
        seen = set()
        for name in inst_names:
            if name in seen:
                errors.append(
                    f"Duplicate instance name '{name}' in '{parent_name}'"
                )
            seen.add(name)

        for sub in submodules:
            inst_name = sub.get("instance_name", "<unnamed>")
            mod_name = sub.get("module", "")
            connections = sub.get("connections", {})

            # Check 1: Referenced module must have a Python function
            if mod_name and mod_name not in functions:
                # Also check if it's the top module itself (self-reference is OK for leaf modules)
                design_name = getattr(mod, "DESIGN_NAME", "")
                if mod_name != design_name:
                    errors.append(
                        f"Instance '{inst_name}' in '{parent_name}' references "
                        f"module '{mod_name}', but no Section 5 function with "
                        f"that name exists. Available: {sorted(functions.keys())}"
                    )
                continue

            if not mod_name:
                continue

            func_params = functions.get(mod_name, [])

            # Check 2: Connection port names should match function parameter names
            for conn_port in connections:
                # Skip clk/rst (auto-injected by emitter)
                if conn_port in ("clk", "rst", "rst_n"):
                    continue
                if conn_port not in func_params:
                    errors.append(
                        f"Instance '{inst_name}' of '{mod_name}' in "
                        f"'{parent_name}': connection port '{conn_port}' "
                        f"not in function parameters {func_params}. "
                        f"Either add the parameter or fix the connection name."
                    )

            # Check 3: Function parameters should be covered by connections
            # (skip clk/rst, and output params that are return values)
            connected = set(connections.keys())
            for param in func_params:
                if param in ("clk", "rst", "rst_n"):
                    continue
                if param not in connected:
                    # Could be an input parameter driven by testbench
                    # Report as warning rather than error
                    if verbose:
                        print(
                            f"[WARN] Instance '{inst_name}' of '{mod_name}': "
                            f"function parameter '{param}' not in connections"
                        )

    return errors


def main():
    parser = argparse.ArgumentParser(
        description="Check MODULE_HIERARCHY consistency in design_spec.py"
    )
    parser.add_argument("filepath", help="Path to design_spec.py")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed info")
    args = parser.parse_args()

    if not Path(args.filepath).is_file():
        print(f"[FATAL] File not found: {args.filepath}", file=sys.stderr)
        sys.exit(2)

    print(f"=== MODULE_HIERARCHY Consistency Check: {args.filepath} ===\n")

    try:
        mod = _load_design_spec(args.filepath)
    except Exception as e:
        print(f"[FATAL] Cannot load {args.filepath}: {e}", file=sys.stderr)
        sys.exit(2)

    errors = check_hierarchy(mod, verbose=args.verbose)

    if errors:
        print(f"[FAIL] {len(errors)} issue(s) found:\n")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("[PASS] MODULE_HIERARCHY is consistent with Section 5 functions")
        sys.exit(0)


if __name__ == "__main__":
    main()
