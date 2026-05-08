#!/usr/bin/env python3
"""vcd2table.py — Convert a VCD file to a cycle-accurate text table for LLM analysis.

Usage:
    python3 vcd2table.py <vcd_file> [options]
    python3 vcd2table.py --vcd <vcd_file> [options]

Options:
    --vcd <path>            VCD file path (alias for positional vcd_file)
    --sim-log <path>        sim.log or sim_<module>.log — extract failing signal names
    --timing-yaml <path>    (optional) timing assertions YAML — annotate violations
    --signals <s1,s2,...>   comma-separated list of extra signals to always include
    --window <N>            cycles around each failure to show (default: 15)
    --module <name>         only show signals from this module scope
    --output <path>         write table to file instead of stdout
    --test-vector-index <N> golden model test vector index (default: 0)
    --apply-offset          apply detected golden/VCD cycle offset during diff
    --max-signals <N>       maximum number of signals to display (default: 20)

Output:
    A text table written to stdout (or --output), formatted for direct LLM reading.
    Columns: Cycle | <signal1> | <signal2> | ... | NOTES
    Rows: one per posedge clk
    NOTES column: flags assertion violations and [FAIL] markers.
"""

import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict
from typing import TextIO

# Shared golden model loader (also used by iverilog_runner.py)
from agent.golden_loader import load_golden_cycles as _load_golden


# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_MAX_SIGNALS = 20
DEFAULT_FSM_SIGNAL_CAP = 5
MAX_VCD_FILE_SIZE = 500 * 1024 * 1024  # 500 MB — warn if VCD exceeds this


def _is_zero_value(value: object) -> bool:
    """Check if a golden model value is effectively zero (any type)."""
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        return value.strip() in ("0", "0x0", "0x00000000", "0x0000000000000000", "")
    return False


def has_unknown_bits(value: str) -> bool:
    """Return True when a VCD value contains x/z unknown bits."""
    val = str(value)
    if val.lower().startswith("0x"):
        val = val[2:]
    return any(c in "xXzZ" for c in val)


def format_vcd_value(value: str) -> str:
    """Format a VCD value for display without hiding x/z states."""
    val = str(value)
    if has_unknown_bits(val):
        return val.lower()
    if len(val) > 4 and all(c in "01" for c in val):
        return hex(int(val, 2))
    return val


def normalize_for_compare(value: object) -> tuple[str, object]:
    """Normalize VCD/golden values while preserving unknowns.

    VCD vectors are binary strings. Golden values are usually ints or hex
    strings. Returning typed tuples avoids ambiguous string comparison.
    """
    if isinstance(value, int):
        return ("int", value)

    val = str(value).strip().lower().replace("_", "")
    if val in ("", "?"):
        return ("missing", val)
    if has_unknown_bits(val):
        return ("unknown", val)
    if val.startswith("0x"):
        try:
            return ("int", int(val, 16))
        except ValueError:
            return ("text", val)
    if re.fullmatch(r"[01]+", val):
        return ("int", int(val, 2))
    if re.fullmatch(r"\d+", val):
        return ("int", int(val, 10))
    return ("text", val)


# ─── VCD Parser ───────────────────────────────────────────────────────────────

class VCDParser:
    """Streaming VCD parser. Handles scalar and vector signals.

    Reads the file line-by-line to avoid loading the entire file into memory.
    """

    def __init__(self):
        self.signals = {}       # id → {name, width, scope}
        self.id_to_name = {}    # id → full_name
        self.changes = defaultdict(dict)  # time → {full_name: value}
        self.timescale = "1ns"
        self._scope_stack = []

    def parse(self, path: str):
        vcd_path = Path(path)

        # Check file size and warn for large files
        try:
            file_size = vcd_path.stat().st_size
            if file_size > MAX_VCD_FILE_SIZE:
                print(f"[vcd2table] WARNING: VCD file is {file_size / 1024 / 1024:.0f} MB — "
                      f"parsing may be slow and memory-intensive", file=sys.stderr)
        except OSError:
            pass

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            self._parse_stream(f)

        return self

    def _parse_stream(self, f: TextIO):
        """Parse VCD from a text stream, line by line."""
        current_time = 0

        for raw_line in f:
            # Split line into tokens for directive parsing
            line = raw_line.strip()
            if not line:
                continue

            # Handle time directives
            if line.startswith("#"):
                try:
                    current_time = int(line[1:])
                except ValueError:
                    pass
                continue

            # Handle $end on its own line
            if line == "$end":
                continue

            # Handle $timescale
            if line.startswith("$timescale"):
                parts = line.split()
                ts_parts = []
                for p in parts[1:]:
                    if p == "$end":
                        break
                    ts_parts.append(p)
                self.timescale = " ".join(ts_parts)
                continue

            # Skip metadata blocks ($date, $version, $comment) until $end
            if line.startswith(("$date", "$version", "$comment")):
                if "$end" in line:
                    continue  # single-line: "$date ... $end"
                # Multi-line: skip until $end
                for skip_line in f:
                    if skip_line.strip() == "$end":
                        break
                continue

            # Handle $scope
            if line.startswith("$scope"):
                parts = line.split()
                if len(parts) >= 3:
                    self._scope_stack.append(parts[2])
                continue

            # Handle $upscope
            if line.startswith("$upscope"):
                if self._scope_stack:
                    self._scope_stack.pop()
                continue

            # Handle $var
            if line.startswith("$var"):
                parts = line.split()
                if len(parts) >= 5:
                    var_type = parts[1]
                    try:
                        width = int(parts[2])
                    except ValueError:
                        width = 1
                    var_id = parts[3]
                    var_name = parts[4]
                    scope = ".".join(self._scope_stack)
                    full_name = f"{scope}.{var_name}" if scope else var_name
                    self.signals[var_id] = {
                        "name": var_name,
                        "full_name": full_name,
                        "width": width,
                        "scope": scope,
                    }
                    self.id_to_name[var_id] = full_name
                continue

            # Handle $dumpvars / $dumpall
            if line.startswith("$dumpvars") or line.startswith("$dumpall"):
                # Read subsequent lines until $end
                for sub_line in f:
                    sub_stripped = sub_line.strip()
                    if sub_stripped == "$end":
                        break
                    self._parse_value_change_line(sub_stripped, current_time)
                continue

            # Handle value change lines (scalar and vector)
            self._parse_value_change_line(line, current_time)

    def _parse_value_change_line(self, line: str, current_time: int):
        """Parse a single value change line."""
        line = line.strip()
        if not line:
            return

        # Scalar: 0/1/x/z followed by id (e.g., "1!A" or "0!B")
        if line[0] in "01xXzZ" and len(line) > 1:
            val = line[0].lower()
            var_id = line[1:]
            if var_id in self.id_to_name:
                self.changes[current_time][self.id_to_name[var_id]] = val
            return

        # Vector: b<value> <id> (e.g., "b0101 A")
        if line[0] in "bBrR":
            parts = line.split()
            if len(parts) >= 2:
                val = parts[0][1:]  # strip the 'b'/'r' prefix
                var_id = parts[1]
                if var_id in self.id_to_name:
                    self.changes[current_time][self.id_to_name[var_id]] = val
            return


# ─── Signal Selection ─────────────────────────────────────────────────────────

def extract_failing_signals_from_log(log_path: str) -> list[str]:
    """Parse sim.log / sim_<module>.log for signal names in [FAIL] lines."""
    signals = []
    if not Path(log_path).exists():
        return signals

    text = Path(log_path).read_text(encoding="utf-8", errors="replace")

    fail_lines = [l for l in text.splitlines() if re.search(r'\[FAIL\]|FAILED:', l)]

    for line in fail_lines:
        # Extract identifiers that look like signal names (Verilog-style identifiers)
        words = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*(?:_reg|_next|_out|_in|_valid|_ready|_en|_flag)?)\b', line)
        signals.extend(words)

    # Remove common non-signal words
    noise = {"expected", "got", "test", "cycle", "failed", "assert", "module", "at", "in"}
    return [s for s in dict.fromkeys(signals) if s not in noise]


def extract_timing_assertions(timing_yaml_path: str) -> list[dict]:
    """Parse timing_model.yaml assertions into structured form.

    Returns list of {name, description, assertions: [str], stimulus: [dict]}
    """
    if not Path(timing_yaml_path).exists():
        return []

    text = Path(timing_yaml_path).read_text(encoding="utf-8", errors="replace")
    scenarios = []

    # Simple YAML parser for our known structure
    current = None
    in_assertions = False
    in_stimulus = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- name:"):
            if current:
                scenarios.append(current)
            current = {"name": stripped[7:].strip(), "assertions": [], "stimulus": []}
            in_assertions = False
            in_stimulus = False
        elif current and stripped.startswith("assertions:"):
            in_assertions = True
            in_stimulus = False
        elif current and stripped.startswith("stimulus:"):
            in_stimulus = True
            in_assertions = False
        elif current and in_assertions and stripped.startswith("- "):
            current["assertions"].append(stripped[2:].strip().strip('"'))
        elif current and in_stimulus and stripped.startswith("- {"):
            current["stimulus"].append(stripped[2:])

    if current:
        scenarios.append(current)

    return scenarios


def parse_assertion(assertion_str: str) -> dict | None:
    """Parse SVA-style assertion string into {signal, delay_min, delay_max, expected}.

    Supports: "signal_A |-> ##N signal_B" and "condition |-> ##[min:max] expected"
    Returns None if unparseable.
    """
    m = re.match(r'(.+?)\s*\|->\s*##(\[[\d:]+\]|\d+)\s*(.+)', assertion_str)
    if not m:
        return None
    antecedent = m.group(1).strip()
    delay_str = m.group(2).strip()
    consequent = m.group(3).strip()

    if delay_str.startswith("["):
        parts = delay_str[1:-1].split(":")
        delay_min, delay_max = int(parts[0]), int(parts[1])
    else:
        delay_min = delay_max = int(delay_str)

    return {
        "antecedent": antecedent,
        "consequent": consequent,
        "delay_min": delay_min,
        "delay_max": delay_max,
    }


# ─── Waveform Table Builder ───────────────────────────────────────────────────

def find_clk_signal(all_signals: set[str]) -> str | None:
    for name in sorted(all_signals):
        basename = name.split(".")[-1]
        if basename in ("clk", "clock", "clk_i"):
            return name
    return None


def find_rst_signal(all_signals: set[str]) -> str | None:
    for name in sorted(all_signals):
        basename = name.split(".")[-1]
        if basename in ("rst", "reset", "rst_i", "rst_n"):
            return name
    return None


def find_fsm_signals(all_signals: set[str], max_signals: int = DEFAULT_FSM_SIGNAL_CAP) -> list[str]:
    fsm_signals = []
    for name in sorted(all_signals):
        basename = name.split(".")[-1]
        if re.search(r'state|fsm|phase|mode', basename, re.IGNORECASE):
            fsm_signals.append(name)
            if len(fsm_signals) >= max_signals:
                break
    return fsm_signals


def build_cycle_table(
    vcd: VCDParser,
    include_signals: list[str],
    window_cycles: list[tuple[int, int]],  # [(start_cycle, end_cycle), ...]
    annotations: dict[int, list[str]],     # cycle → [annotation strings]
    clk_name: str,
    max_snapshots: int = 0,
) -> tuple[str, list[tuple[int, dict[str, str]]]]:
    """Build a text cycle table from VCD data.

    Returns (formatted_string, cycle_snapshots) where cycle_snapshots is
    [(cycle_num, {signal_name: value})] for cycles within the window range.

    Args:
        max_snapshots: Maximum number of cycle snapshots to retain in memory.
            0 means no limit (retain all). For large VCD files, set this to
            a reasonable limit (e.g., 10000) to avoid excessive memory usage.
    """
    if not include_signals:
        return "(no signals to display)\n", []

    # Build timeline: sort all timestamps, find posedge clk
    all_times = sorted(vcd.changes.keys())
    if not all_times:
        return "(VCD has no signal changes)\n", []

    # Reconstruct signal state across all time steps
    current_state: dict[str, str] = {}
    cycle_snapshots: list[tuple[int, dict[str, str]]] = []
    cycle_num = -1
    total_cycles = 0

    for t in all_times:
        changes = vcd.changes[t]
        current_state.update(changes)

        if clk_name in changes and changes[clk_name] == "1":
            cycle_num += 1
            total_cycles += 1
            if max_snapshots <= 0 or total_cycles <= max_snapshots:
                cycle_snapshots.append((cycle_num, dict(current_state)))

    if total_cycles > max_snapshots and max_snapshots > 0:
        print(f"[vcd2table] WARNING: VCD has {total_cycles} cycles, "
              f"only first {max_snapshots} retained in memory", file=sys.stderr)

    if not cycle_snapshots:
        return "(No clock edges found in VCD)\n", []

    # Determine which cycles to show based on window
    if window_cycles:
        show_cycles = set()
        for start, end in window_cycles:
            for c in range(max(0, start), min(end + 1, len(cycle_snapshots))):
                show_cycles.add(c)
    else:
        show_cycles = set(range(len(cycle_snapshots)))

    if not show_cycles:
        return "(No cycles in display window)\n", cycle_snapshots

    # Shorten signal names — strip common scope prefix
    def short_name(name: str) -> str:
        parts = name.split(".")
        return parts[-1] if len(parts) > 1 else name

    col_names = ["Cycle"] + [short_name(s) for s in include_signals] + ["NOTES"]

    # Collect rows
    rows = []
    for cycle_num, state in cycle_snapshots:
        if cycle_num not in show_cycles:
            continue

        row = [str(cycle_num)]
        for sig in include_signals:
            val = state.get(sig, "?")
            # Format known binary vectors as hex, but preserve x/z exactly.
            val = format_vcd_value(val)
            row.append(val)

        notes = annotations.get(cycle_num, [])
        row.append(" | ".join(notes) if notes else "")
        rows.append(row)

    # Calculate column widths
    col_widths = [max(len(col_names[i]), max((len(r[i]) for r in rows), default=0))
                  for i in range(len(col_names))]

    # Format header
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header = "|" + "|".join(f" {col_names[i]:<{col_widths[i]}} " for i in range(len(col_names))) + "|"

    lines = [sep, header, sep]
    for row in rows:
        padded = [f" {row[i]:<{col_widths[i]}} " for i in range(len(col_names))]
        lines.append("|" + "|".join(padded) + "|")
    lines.append(sep)

    return "\n".join(lines) + "\n", cycle_snapshots


# ─── Golden Model Comparison ──────────────────────────────────────────────────

# Minimum number of matching signals to confirm a cycle offset
_MIN_OFFSET_CONFIRMATIONS = 3


def detect_cycle_offset(
    cycle_snapshots: list[tuple[int, dict[str, str]]],
    golden_cycles: dict[int, dict[str, str]],
    include_signals: list[str],
) -> int:
    """Auto-detect the VCD cycle offset where golden cycle 0 should align.

    Strategy: Find the first golden cycle with a non-trivial value, then find
    the first VCD cycle where matching signals have the same value. Requires
    at least _MIN_OFFSET_CONFIRMATIONS matching signals to confirm, and the
    match must span signals wider than 1 bit to avoid false positives from
    narrow signals that frequently match by coincidence.
    Falls back to 0 offset if no clear alignment point is found.
    """
    short_names = {s: s.split(".")[-1] for s in include_signals}

    # Find the first golden cycle that has any non-zero, non-trivial value
    golden_start = None
    for gc in sorted(golden_cycles.keys()):
        vals = golden_cycles[gc]
        has_active = any(
            not _is_zero_value(v)
            for v in vals.values()
        )
        if has_active:
            golden_start = gc
            break

    if golden_start is None:
        return 0

    # Find the first VCD cycle where MULTIPLE signals match the golden start cycle.
    # Require wider signals (>1 bit) to confirm to avoid false positives from 1-bit toggles.
    for vcd_cycle, state in cycle_snapshots:
        match_count = 0
        for sig in include_signals:
            short = short_names[sig]
            if short not in golden_cycles.get(golden_start, {}):
                continue
            vcd_val = state.get(sig, "0")
            golden_val = golden_cycles[golden_start].get(short, "0")
            vcd_norm = normalize_for_compare(vcd_val)
            golden_norm = normalize_for_compare(golden_val)
            if vcd_norm == golden_norm and vcd_norm != ("int", 0):
                match_count += 1

        if match_count >= _MIN_OFFSET_CONFIRMATIONS:
            offset = vcd_cycle - golden_start
            return max(0, offset)

    return 0


def run_golden_model_comparison(
    golden_path: str,
    cycle_snapshots: list[tuple[int, dict[str, str]]],
    include_signals: list[str],
    test_vector_index: int = 0,
    apply_offset: bool = False,
) -> str | None:
    """Run a golden model and compare outputs with RTL cycle-by-cycle.

    The golden model script can use either of two interfaces:
      Strategy 1: Run as standalone script producing stdout lines like:
          "cycle N: signal_name=0xVALUE signal2=0xVALUE"
      Strategy 2: Define a run() function returning list[dict] where each dict
          maps signal_name (short, no scope) -> integer value.

    Returns a diff report string, or None if the golden model cannot be loaded.
    """

    golden_path = str(Path(golden_path).resolve())
    if not Path(golden_path).exists():
        return f"ERROR: Golden model not found: {golden_path}\n"

    # Use shared golden model loader (avoids duplicate implementation)
    golden_cycles = _load_golden(golden_path, test_vector_index=test_vector_index)
    if golden_cycles is None:
        golden_cycles = {}

    if not golden_cycles:
        return "[GOLDEN] Golden model produced no parseable cycle data.\n" \
               "[GOLDEN] Expected format: 'cycle N: signal_name=0xVALUE' on each line, " \
               "or a run() function returning list[dict].\n"

    # Build diff report
    lines = []
    lines.append("=" * 72)
    lines.append("GOLDEN MODEL DIFF REPORT")
    lines.append("=" * 72)
    lines.append("")

    short_names = {s: s.split(".")[-1] for s in include_signals}
    first_divergence = None
    first_divergence_info = None
    divergence_count = 0

    # Detect, but do not silently apply, cycle offset. Applying it by default
    # can hide the exact early/late timing bugs this tool is meant to expose.
    offset_candidate = detect_cycle_offset(cycle_snapshots, golden_cycles, include_signals)
    offset = offset_candidate if apply_offset else 0
    if offset_candidate != 0:
        lines.append(
            f"CYCLE OFFSET CANDIDATE: +{offset_candidate} "
            f"(golden cycle N appears to align to VCD cycle N+{offset_candidate})"
        )
        if apply_offset:
            lines.append("OFFSET APPLIED because --apply-offset was requested.")
        else:
            lines.append("OFFSET NOT APPLIED by default; timing bugs remain visible.")
        lines.append("")

    for cycle_num, state in cycle_snapshots:
        golden_cycle = cycle_num - offset
        if golden_cycle not in golden_cycles:
            continue
        golden = golden_cycles[golden_cycle]

        for sig in include_signals:
            short = short_names[sig]
            if short not in golden:
                continue

            raw_rtl_val = state.get(sig, "?")
            rtl_val = format_vcd_value(raw_rtl_val)

            golden_val = golden[short]
            rtl_norm = normalize_for_compare(raw_rtl_val)
            golden_norm = normalize_for_compare(golden_val)

            if rtl_norm != golden_norm:
                if first_divergence is None:
                    first_divergence = cycle_num
                    first_divergence_info = {
                        "cycle": cycle_num,
                        "signal": short,
                        "golden": golden_val,
                        "rtl": rtl_val,
                        "rtl_kind": rtl_norm[0],
                        "golden_kind": golden_norm[0],
                    }
                divergence_count += 1
                lines.append(
                    f"  CYCLE {cycle_num}: {short} — golden={golden_val} rtl={rtl_val}"
                )

    lines.append("")
    if first_divergence is not None:
        lines.append(f"FIRST DIVERGENCE: cycle {first_divergence}")
        lines.append(f"TOTAL MISMATCHES: {divergence_count}")
        if first_divergence_info:
            div = first_divergence_info
            if div["rtl_kind"] == "unknown":
                bug_type = "unknown-or-undriven"
            elif offset_candidate:
                bug_type = "possible-timing-offset"
            else:
                bug_type = "data-or-initialization"
            lines.append(
                "FIRST DIVERGENCE SUMMARY: "
                f"cycle={div['cycle']} signal={div['signal']} "
                f"golden={div['golden']} rtl={div['rtl']} type={bug_type}"
            )
        lines.append("")
        lines.append("DIAGNOSIS:")
        lines.append(f"  1. The first divergence at cycle {first_divergence} is the root cause.")
        lines.append("  2. Check if the divergence is a timing offset (value correct but")
        lines.append("     arrives 1 cycle early/late) or a computation error (value is wrong).")
        lines.append("  3. For timing offset: check shift register alignment and control")
        lines.append("     signal co-assertion between FSM and consumer modules.")
        lines.append("  4. For computation error: trace the exact formula producing the wrong value.")
    else:
        lines.append("NO DIVERGENCES FOUND — golden model matches RTL for the compared cycles.")
    lines.append("")

    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Convert VCD to LLM-readable cycle table")
    parser.add_argument("vcd_file", nargs="?", help="Path to .vcd file")
    parser.add_argument("--vcd", dest="vcd_file_alias", help="Path to .vcd file")
    parser.add_argument("--sim-log", help="sim.log path — extract failing signal names")
    parser.add_argument("--timing-yaml", help="timing_model.yaml — annotate assertion violations")
    parser.add_argument("--signals", help="Extra signals to always include (comma-separated)")
    parser.add_argument("--window", type=int, default=15, help="Cycles around each failure (default 15)")
    parser.add_argument("--module", help="Filter signals to this module scope")
    parser.add_argument("--output", help="Write output to file instead of stdout")
    parser.add_argument("--golden-model", help="Python design_spec.py or golden_model.py — compare RTL outputs cycle-by-cycle")
    parser.add_argument("--test-vector-index", type=int, default=0,
                        help="Golden model test vector index (default: 0)")
    parser.add_argument("--apply-offset", action="store_true",
                        help="Apply detected golden/VCD cycle offset during diff")
    parser.add_argument("--max-signals", type=int, default=DEFAULT_MAX_SIGNALS,
                        help=f"Maximum number of signals to display (default: {DEFAULT_MAX_SIGNALS})")
    args = parser.parse_args()

    args.vcd_file = args.vcd_file_alias or args.vcd_file
    if not args.vcd_file:
        parser.error("vcd_file is required (positional or --vcd)")

    if not Path(args.vcd_file).exists():
        print(f"ERROR: VCD file not found: {args.vcd_file}", file=sys.stderr)
        sys.exit(1)

    # 1. Parse VCD
    print(f"[vcd2table] Parsing {args.vcd_file}...", file=sys.stderr)
    vcd = VCDParser().parse(args.vcd_file)

    all_signal_names = set(vcd.id_to_name.values())
    if not all_signal_names:
        print("ERROR: No signals found in VCD file", file=sys.stderr)
        sys.exit(1)

    print(f"[vcd2table] Found {len(all_signal_names)} signals, "
          f"{len(vcd.changes)} time steps", file=sys.stderr)

    # 2. Find clk and rst
    clk_name = find_clk_signal(all_signal_names)
    if not clk_name:
        print("ERROR: Could not find clock signal (clk/clock/clk_i)", file=sys.stderr)
        sys.exit(1)

    rst_name = find_rst_signal(all_signal_names)
    fsm_signals = find_fsm_signals(all_signal_names, max_signals=DEFAULT_FSM_SIGNAL_CAP)

    # 3. Determine signals to show
    include_signals = []
    if rst_name:
        include_signals.append(rst_name)

    # FSM signals always included
    include_signals.extend(fsm_signals)

    # Signals from failing assertions in sim.log
    if args.sim_log:
        failing_sigs = extract_failing_signals_from_log(args.sim_log)
        print(f"[vcd2table] Failing signals from log: {failing_sigs}", file=sys.stderr)
        for sig_basename in failing_sigs:
            # Find full name matching this basename
            matches = [n for n in all_signal_names if n.split(".")[-1] == sig_basename]
            if args.module:
                matches = [n for n in matches if args.module in n]
            include_signals.extend(matches[:2])  # cap at 2 matches per name

    # Extra signals from --signals flag
    if args.signals:
        for sig_basename in args.signals.split(","):
            sig_basename = sig_basename.strip()
            matches = [n for n in all_signal_names if n.split(".")[-1] == sig_basename]
            include_signals.extend(matches[:2])

    # Deduplicate, preserve order
    seen = set()
    include_signals = [s for s in include_signals if not (s in seen or seen.add(s))]

    if not include_signals:
        # Fallback: show all top-level port-like signals
        include_signals = sorted(
            [n for n in all_signal_names if n.split(".")[-1] not in ("clk", "clock")],
        )[:args.max_signals]
        print(f"[vcd2table] No specific signals identified — showing first {len(include_signals)}", file=sys.stderr)

    # Enforce max signals limit
    if len(include_signals) > args.max_signals:
        print(f"[vcd2table] Truncating from {len(include_signals)} to {args.max_signals} signals", file=sys.stderr)
        include_signals = include_signals[:args.max_signals]

    print(f"[vcd2table] Displaying {len(include_signals)} signals: "
          f"{[s.split('.')[-1] for s in include_signals]}", file=sys.stderr)

    # 4. Find failure cycles from sim.log
    fail_cycles: list[int] = []
    if args.sim_log and Path(args.sim_log).exists():
        log_text = Path(args.sim_log).read_text(encoding="utf-8", errors="replace")
        for line in log_text.splitlines():
            m = re.search(r'cycle[=:\s]+(\d+)', line, re.IGNORECASE)
            if m and re.search(r'\[FAIL\]|FAILED:', line):
                fail_cycles.append(int(m.group(1)))

    # 5. Build annotations from timing_model.yaml assertions
    annotations: dict[int, list[str]] = defaultdict(list)

    timing_scenarios = []
    if args.timing_yaml:
        timing_scenarios = extract_timing_assertions(args.timing_yaml)

    # Mark fail cycles in annotations
    for fc in fail_cycles:
        annotations[fc].append("[FAIL]")

    # 6. Determine display windows
    if fail_cycles:
        window_cycles = [(max(0, fc - args.window // 2), fc + args.window // 2)
                         for fc in fail_cycles]
    else:
        # No specific failures found — show first 50 cycles
        window_cycles = [(0, 49)]

    # 7. Build table
    table, cycle_snapshots = build_cycle_table(vcd, include_signals, window_cycles, annotations, clk_name)

    # 8. Build header summary
    output_parts = []
    output_parts.append("=" * 72)
    output_parts.append("VCD WAVEFORM TABLE — for LLM timing analysis")
    output_parts.append(f"VCD file  : {args.vcd_file}")
    output_parts.append(f"Timescale : {vcd.timescale}")
    output_parts.append(f"Clock     : {clk_name}")
    output_parts.append(f"Signals   : {', '.join(s.split('.')[-1] for s in include_signals)}")
    if fail_cycles:
        output_parts.append(f"Fail cycles: {fail_cycles}  (window ±{args.window//2} cycles shown)")
    else:
        output_parts.append("No [FAIL] cycles detected — showing first 50 cycles")
    output_parts.append("=" * 72)
    output_parts.append("")

    # Timing assertion summary
    if timing_scenarios:
        output_parts.append("TIMING ASSERTIONS (from timing_model.yaml):")
        for sc in timing_scenarios:
            output_parts.append(f"  Scenario: {sc['name']}")
            for a in sc["assertions"]:
                output_parts.append(f"    {a}")
        output_parts.append("")

    output_parts.append(table)

    # 9. Golden model comparison (if available)
    if args.golden_model and Path(args.golden_model).exists():
        golden_diff = run_golden_model_comparison(
            args.golden_model,
            cycle_snapshots,
            include_signals,
            test_vector_index=args.test_vector_index,
            apply_offset=args.apply_offset,
        )
        if golden_diff:
            output_parts.append("")
            output_parts.append(golden_diff)
    elif args.golden_model:
        output_parts.append(f"\n[GOLDEN] Golden model not found: {args.golden_model}\n")

    # 10. Guidance for LLM
    if fail_cycles:
        output_parts.append("")
        output_parts.append("DIAGNOSIS HINTS:")
        output_parts.append("  1. Look at the [FAIL] row — note the cycle number.")
        output_parts.append("  2. Trace the failing signal backwards: when did it last change?")
        output_parts.append("  3. Check FSM state at [FAIL] cycle — is it the expected state?")
        output_parts.append("  4. Compare with timing_model.yaml assertions above.")
        output_parts.append("  5. Common causes: off-by-one pipeline stage, reset not clearing"
                             " output register, handshake held too long/short.")

    final_output = "\n".join(output_parts)

    # 11. Write output
    if args.output:
        Path(args.output).write_text(final_output, encoding="utf-8")
        print(f"[vcd2table] Written to {args.output}", file=sys.stderr)
    else:
        print(final_output)


if __name__ == "__main__":
    main()
