#!/usr/bin/env python3
"""Instance code generator for VeriFlow pipeline.

Extracts port declarations from generated Verilog files and produces
instantiation code. Used in Stage 2 two-phase codegen to eliminate
port-name fabrication errors.

Two modes:
  1. Template mode (--template): Generate commented templates for agents
  2. Code mode (--code): Generate complete instance code from MODULE_HIERARCHY

Usage:
    # Extract all module ports as JSON
    python instance_gen.py --rtl-dir workspace/rtl --extract

    # Generate instance templates (for agent prompt injection)
    python instance_gen.py --rtl-dir workspace/rtl --template

    # Generate instance code from MODULE_HIERARCHY declaration
    python instance_gen.py --rtl-dir workspace/rtl --hierarchy workspace/docs/design_spec.py --code
"""

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

__all__ = ["extract_ports", "generate_instance_code", "generate_template"]


# ---------------------------------------------------------------------------
# Port Extraction
# ---------------------------------------------------------------------------

_MODULE_DECL_RE = re.compile(
    r'module\s+(\w+)\s*\((.*?)\)\s*;',
    re.DOTALL,
)

_PORT_RE = re.compile(
    r'(input|output)\s+(wire|reg)?\s*(\[[\d:]+\])?\s*(\w+)'
)


def extract_ports(rtl_dir: Path) -> dict[str, dict]:
    """Parse all .v files and extract module port declarations.

    Returns:
        {module_name: {
            "ports": {port_name: {"direction": str, "type": str, "width": str}},
            "file": str (relative filename),
        }}
    """
    modules = {}

    for vfile in sorted(rtl_dir.glob("*.v")):
        code = vfile.read_text(encoding="utf-8", errors="replace")

        for m in _MODULE_DECL_RE.finditer(code):
            mod_name = m.group(1)
            port_block = m.group(2)

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

            modules[mod_name] = {
                "ports": ports,
                "file": vfile.name,
            }

    return modules


# ---------------------------------------------------------------------------
# Instance Template Generation (for agent prompt injection)
# ---------------------------------------------------------------------------

def generate_template(modules: dict[str, dict]) -> str:
    """Generate Verilog instance templates with commented placeholders.

    For each module, produces an instance template where the agent only
    needs to fill in the signal connections.
    """
    lines = []
    lines.append("// === Auto-generated instance templates ===")
    lines.append("// Fill in the /* signal */ placeholders with actual connections.")
    lines.append("")

    for mod_name, mod_info in sorted(modules.items()):
        ports = mod_info["ports"]
        lines.append(f"// --- {mod_name} (from {mod_info['file']}) ---")

        # Find max port name length for alignment
        max_name_len = max(len(n) for n in ports) if ports else 0

        lines.append(f"{mod_name} INST_NAME (")

        port_lines = []
        for port_name, port_info in ports.items():
            direction = port_info["direction"]
            width = port_info["width"]
            type_str = port_info["type"]

            # Build comment showing port info
            width_str = width if width else "1-bit"
            comment = f"// {direction} {type_str} {width_str}"

            padding = " " * (max_name_len - len(port_name))
            port_lines.append(
                f"    .{port_name}{padding} (/* connect */){comment}"
            )

        lines.append(",\n".join(port_lines))
        lines.append(");")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Instance Code Generation (from MODULE_HIERARCHY)
# ---------------------------------------------------------------------------

def _load_hierarchy(design_spec_path: str) -> dict:
    """Load MODULE_HIERARCHY from design_spec.py."""
    spec = importlib.util.spec_from_file_location(
        "_ds", str(Path(design_spec_path).resolve())
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not hasattr(mod, "MODULE_HIERARCHY"):
        return {}

    hierarchy = getattr(mod, "MODULE_HIERARCHY")
    if not isinstance(hierarchy, dict):
        return {}

    return hierarchy


def generate_instance_code(
    hierarchy: dict,
    modules: dict[str, dict],
) -> tuple[str, list[str]]:
    """Generate complete Verilog instance code from MODULE_HIERARCHY.

    Args:
        hierarchy: MODULE_HIERARCHY dict from design_spec.py
        modules: Extracted port database from Verilog files

    Returns:
        (verilog_code, errors)
    """
    lines = []
    errors = []

    for parent_name, parent_info in sorted(hierarchy.items()):
        submodules = parent_info.get("submodules", [])
        if not submodules:
            continue

        lines.append(f"// === Instance code for {parent_name} ===")
        lines.append("")

        for sub in submodules:
            inst_name = sub.get("instance_name", "u_inst")
            mod_name = sub.get("module", "")
            connections = sub.get("connections", {})

            if mod_name not in modules:
                errors.append(
                    f"[ERROR] Module '{mod_name}' (instance '{inst_name}' in "
                    f"'{parent_name}') not found in generated Verilog files. "
                    f"Available: {sorted(modules.keys())}"
                )
                lines.append(f"// ERROR: module '{mod_name}' not found")
                lines.append("")
                continue

            declared_ports = modules[mod_name]["ports"]

            # Validate connections against declared ports
            for conn_port in connections:
                if conn_port not in declared_ports:
                    errors.append(
                        f"[ERROR] Instance '{inst_name}' of '{mod_name}' in "
                        f"'{parent_name}': connection port '.{conn_port}' not "
                        f"in module declaration. "
                        f"Available: {sorted(declared_ports.keys())}"
                    )

            # Check for unconnected declared ports (skip clk/rst)
            connected = set(connections.keys())
            declared = set(declared_ports.keys())
            skip = {"clk", "rst", "rst_n"}
            missing = (declared - skip) - connected
            if missing:
                errors.append(
                    f"[WARN] Instance '{inst_name}' of '{mod_name}': "
                    f"declared ports not connected: {sorted(missing)}"
                )

            # Generate instance code
            max_name_len = max(len(n) for n in declared_ports) if declared_ports else 0

            lines.append(f"{mod_name} {inst_name} (")

            port_lines = []
            for port_name in sorted(connections.keys()):
                signal = connections[port_name]
                padding = " " * (max_name_len - len(port_name))
                port_lines.append(
                    f"    .{port_name}{padding} ({signal})"
                )

            lines.append(",\n".join(port_lines))
            lines.append(");")
            lines.append("")

    return "\n".join(lines), errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Instance code generator for VeriFlow pipeline"
    )
    parser.add_argument("--rtl-dir", required=True,
                        help="Directory containing generated .v files")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--extract", action="store_true",
                       help="Extract all module ports as JSON")
    group.add_argument("--template", action="store_true",
                       help="Generate instance templates")
    group.add_argument("--code", action="store_true",
                       help="Generate instance code from MODULE_HIERARCHY")

    parser.add_argument("--hierarchy",
                        help="Path to design_spec.py (required for --code)")

    args = parser.parse_args()

    rtl_dir = Path(args.rtl_dir).resolve()
    if not rtl_dir.is_dir():
        print(json.dumps({"error": f"RTL dir not found: {rtl_dir}"}))
        sys.exit(2)

    modules = extract_ports(rtl_dir)

    if args.extract:
        # JSON output of all module ports
        print(json.dumps(modules, indent=2))

    elif args.template:
        # Verilog instance templates
        print(generate_template(modules))

    elif args.code:
        if not args.hierarchy:
            parser.error("--hierarchy required for --code mode")

        hierarchy = _load_hierarchy(args.hierarchy)
        if not hierarchy:
            print("// No MODULE_HIERARCHY found in design_spec.py")
            print("// Falling back to template mode:")
            print("")
            print(generate_template(modules))
            sys.exit(0)

        code, errors = generate_instance_code(hierarchy, modules)

        for e in errors:
            print(e, file=sys.stderr)

        print(code)

        if any("[ERROR]" in e for e in errors):
            sys.exit(1)


if __name__ == "__main__":
    main()
