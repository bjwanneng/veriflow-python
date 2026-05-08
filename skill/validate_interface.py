"""design_spec.py Interface Contract Validator.

Validates that design_spec.py exports match the template's required interface:
  - compute(inputs: dict, trace: bool = False) -> dict | list[dict]
  - run(test_vector_index: int = 0) -> list[dict]
  - get_test_vectors() -> list[dict]

Usage:
  python validate_interface.py <path_to_design_spec.py>
  python validate_interface.py workspace/docs/design_spec.py --smoke

Exit code 0 = pass, 1 = fail.
"""

import importlib.util
import inspect
import os
import sys
from pathlib import Path


# Required interface contract: {function_name: [(param_name, has_default), ...]}
REQUIRED_INTERFACE = {
    "compute": [
        ("inputs", False),
        ("trace", True),
    ],
    "run": [
        ("test_vector_index", True),
    ],
    "get_test_vectors": [],
}

# Required top-level variables
REQUIRED_VARIABLES = [
    "DESIGN_NAME",
    "TEST_VECTORS",
]

# Optional top-level variables (design-specific)
OPTIONAL_VARIABLES = [
    "MASK32",
]


def load_module(filepath: str):
    """Dynamically load a Python file as a module."""
    filepath = str(Path(filepath).resolve())
    module_dir = str(Path(filepath).parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    spec = importlib.util.spec_from_file_location("_design_spec", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate_variables(mod) -> list[str]:
    """Check that required top-level variables exist and optional ones are typed correctly."""
    errors = []
    for var_name in REQUIRED_VARIABLES:
        if not hasattr(mod, var_name):
            errors.append(f"MISSING variable: {var_name}")
        else:
            val = getattr(mod, var_name)
            if var_name == "DESIGN_NAME" and not isinstance(val, str):
                errors.append(f"DESIGN_NAME must be str, got {type(val).__name__}")
            if var_name == "TEST_VECTORS" and not isinstance(val, list):
                errors.append(f"TEST_VECTORS must be list, got {type(val).__name__}")
    for var_name in OPTIONAL_VARIABLES:
        if hasattr(mod, var_name):
            val = getattr(mod, var_name)
            if var_name == "MASK32" and not isinstance(val, int):
                errors.append(f"MASK32 must be int, got {type(val).__name__}")
    return errors


def validate_function_signatures(mod) -> list[str]:
    """Check that required functions exist with correct signatures."""
    errors = []
    for func_name, expected_params in REQUIRED_INTERFACE.items():
        # Check existence
        if not hasattr(mod, func_name):
            errors.append(f"MISSING function: {func_name}()")
            continue

        func = getattr(mod, func_name)
        if not callable(func):
            errors.append(f"{func_name} exists but is not callable (got {type(func).__name__})")
            continue

        # Check signature
        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError) as e:
            errors.append(f"{func_name}(): cannot inspect signature: {e}")
            continue

        actual_params = list(sig.parameters.keys())

        # Check parameter names match exactly
        expected_names = [p[0] for p in expected_params]
        if actual_params != expected_names:
            errors.append(
                f"{func_name}(): parameter mismatch — "
                f"expected {expected_names}, got {actual_params}"
            )
            continue

        # Check default values where required
        for i, (param_name, has_default) in enumerate(expected_params):
            param = sig.parameters[param_name]
            if has_default and param.default is inspect.Parameter.empty:
                errors.append(
                    f"{func_name}(): parameter '{param_name}' must have a default value"
                )
            if not has_default and param.default is not inspect.Parameter.empty:
                errors.append(
                    f"{func_name}(): parameter '{param_name}' must NOT have a default value"
                )

    return errors


def validate_functional_smoke(mod) -> list[str]:
    """Run functional smoke tests on compute() and run()."""
    errors = []

    # Need TEST_VECTORS to run smoke
    if not hasattr(mod, "TEST_VECTORS") or not mod.TEST_VECTORS:
        errors.append("Cannot run smoke test: TEST_VECTORS is empty or missing")
        return errors

    tv = mod.TEST_VECTORS[0]
    if "inputs" not in tv:
        errors.append("Cannot run smoke test: TEST_VECTORS[0] missing 'inputs' key")
        return errors

    # Test compute(trace=False) returns dict
    try:
        result = mod.compute(tv["inputs"], trace=False)
        if not isinstance(result, dict):
            errors.append(
                f"compute(trace=False) must return dict, got {type(result).__name__}"
            )
        elif len(result) == 0:
            errors.append("compute(trace=False) returned empty dict")
    except Exception as e:
        errors.append(f"compute() smoke test failed: {e}")

    # Test compute(trace=True) returns list
    try:
        result = mod.compute(tv["inputs"], trace=True)
        if not isinstance(result, list):
            errors.append(
                f"compute(trace=True) must return list, got {type(result).__name__}"
            )
        elif len(result) == 0:
            errors.append("compute(trace=True) returned empty list")
    except Exception as e:
        errors.append(f"compute(trace=True) smoke test failed: {e}")

    # Test run() returns list
    try:
        result = mod.run(0)
        if not isinstance(result, list):
            errors.append(f"run() must return list, got {type(result).__name__}")
    except Exception as e:
        errors.append(f"run() smoke test failed: {e}")

    # Test get_test_vectors() returns list
    try:
        result = mod.get_test_vectors()
        if not isinstance(result, list):
            errors.append(
                f"get_test_vectors() must return list, got {type(result).__name__}"
            )
        elif len(result) < 2:
            errors.append(
                f"get_test_vectors() returned {len(result)} vectors, need >= 2"
            )
    except Exception as e:
        errors.append(f"get_test_vectors() smoke test failed: {e}")

    return errors


def validate_test_vector_structure(mod) -> list[str]:
    """Validate TEST_VECTORS structure and multi-block requirement."""
    errors = []

    if not hasattr(mod, "TEST_VECTORS"):
        return errors  # Already caught by validate_variables

    tvs = mod.TEST_VECTORS
    if not isinstance(tvs, list):
        return errors  # Already caught

    if len(tvs) < 2:
        errors.append(f"TEST_VECTORS has {len(tvs)} entries, need >= 2")

    for i, tv in enumerate(tvs):
        if not isinstance(tv, dict):
            errors.append(f"TEST_VECTORS[{i}] must be dict, got {type(tv).__name__}")
            continue

        # Check required keys
        if "inputs" not in tv:
            errors.append(f"TEST_VECTORS[{i}] missing 'inputs' key")
        else:
            inputs = tv["inputs"]
            if not isinstance(inputs, dict):
                errors.append(f"TEST_VECTORS[{i}]['inputs'] must be dict")
            else:
                if "blocks" not in inputs:
                    errors.append(f"TEST_VECTORS[{i}]['inputs'] missing 'blocks'")
                if "is_last_flags" not in inputs:
                    errors.append(f"TEST_VECTORS[{i}]['inputs'] missing 'is_last_flags'")

        if "expected" not in tv:
            errors.append(f"TEST_VECTORS[{i}] missing 'expected' key")

    # Multi-block requirement
    has_multi_block = any(
        isinstance(tv, dict)
        and isinstance(tv.get("inputs"), dict)
        and len(tv["inputs"].get("blocks", [])) > 1
        for tv in tvs
    )
    if not has_multi_block:
        errors.append(
            "TEST_VECTORS must include at least one multi-block test "
            "(to verify is_first_block transitions and accumulator propagation)"
        )

    return errors


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate design_spec.py interface contract"
    )
    parser.add_argument(
        "filepath",
        help="Path to design_spec.py to validate",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run functional smoke tests (requires TEST_VECTORS)",
    )
    args = parser.parse_args()

    filepath = args.filepath
    if not os.path.isfile(filepath):
        print(f"[FATAL] File not found: {filepath}")
        sys.exit(1)

    print(f"=== Interface Contract Validation: {filepath} ===\n")

    # Load module
    try:
        mod = load_module(filepath)
    except SyntaxError as e:
        print(f"[FATAL] Syntax error in {filepath}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] Failed to load {filepath}: {e}")
        sys.exit(1)

    all_errors = []

    # 1. Variable checks
    print("[1/4] Checking required variables...")
    var_errors = validate_variables(mod)
    all_errors.extend(var_errors)
    if var_errors:
        for e in var_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK: All required variables present")

    # 2. Signature checks
    print("\n[2/4] Checking function signatures...")
    sig_errors = validate_function_signatures(mod)
    all_errors.extend(sig_errors)
    if sig_errors:
        for e in sig_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK: All function signatures match template contract")

    # 3. Test vector structure
    print("\n[3/4] Checking TEST_VECTORS structure...")
    tv_errors = validate_test_vector_structure(mod)
    all_errors.extend(tv_errors)
    if tv_errors:
        for e in tv_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK: TEST_VECTORS structure valid")

    # 4. Functional smoke tests
    if args.smoke:
        print("\n[4/4] Running functional smoke tests...")
        smoke_errors = validate_functional_smoke(mod)
        all_errors.extend(smoke_errors)
        if smoke_errors:
            for e in smoke_errors:
                print(f"  FAIL: {e}")
        else:
            print("  OK: All smoke tests passed")
    else:
        print("\n[4/4] Skipping smoke tests (use --smoke to enable)")

    # Summary
    print(f"\n{'=' * 60}")
    if all_errors:
        print(f"[FAIL] {len(all_errors)} error(s) found:")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("[PASS] Interface contract validation OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
