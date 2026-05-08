"""design_spec.py -- Python Design Specification for __DESIGN_NAME__.

This file serves THREE roles simultaneously:
  1. Design specification: interface, module hierarchy, protocol, timing
  2. Reference model: runnable algorithm verified against standard test vectors
  3. Translation blueprint: each function -> one Verilog module

NBA Timing Convention (CRITICAL):
  Python assignment semantics naturally match Verilog NBA:
  - Function parameters  = current cycle register values (right-hand side of <=)
  - Function return values = next cycle register values (left-hand side of <=)
  - Local variables       = combinational wires (same-cycle visible)
  - Assignment point       = clock edge (cycle boundary)

  Example:  A, B, C = TT1, A, ROL(B, 9)
  Reads:    A (old), B (old)      -- all right-hand sides read old values
  Writes:   A=TT1, B=old_A, C=ROL(old_B,9) -- all left-hand sides update simultaneously
  This is EXACTLY Verilog NBA semantics.

  Registered outputs (ready, valid, etc.):
  Signals driven by _reg flip-flops have a 1-cycle delay from the combinational
  logic that computes them. The trace must record _reg values, not _next values.

  Trace cycle N = register state cocotb reads after RisingEdge at cycle N.
  At cycle N, registers hold POST-NBA of posedge N-1.
  Computation at cycle N produces new register values visible at cycle N+1.

Return Timing Annotations (CRITICAL for codegen):
  Every function return value MUST be annotated with its timing semantics:

    # wire:      same-cycle combinational output → output wire + assign
    # reg_next:  next-cycle register update      → output reg + always @(posedge clk) NBA

  Example:
    def w_gen_shift(w_reg, round_cnt):
        W_j = w_reg[0]                          # wire
        W_prime_j = (w_reg[0] ^ w_reg[4]) & MASK32  # wire
        next_w_reg = ...                         # reg_next
        return W_j, W_prime_j, next_w_reg

  Verilog mapping:
    W_j       → output wire [31:0] w_j_o;  assign w_j_o = w_reg[0];
    W_prime_j → output wire [31:0] w_prime_j_o;  assign w_prime_j_o = w_reg[0] ^ w_reg[4];
    next_w_reg → always @(posedge clk) w_reg[i] <= w_reg_n[i];

  RULE R1: wire-annotated outputs MUST be output wire + assign.
           MUST NOT be output reg + always @(posedge clk).
"""

# ============================================================
# Section 1: Interface Definition
# ============================================================
# Describe the top-level module interface as comments.
# Format: input/output, name, width, protocol, description
#
# Example:
# module <top_module_name> (
#     input  wire         clk,          // clock
#     input  wire         rst,          // synchronous active-high reset
#     input  wire         msg_valid,    // input valid (Valid-Ready handshake)
#     input  wire [511:0] msg_block,    // 512-bit message block
#     input  wire         is_last,      // last block flag
#     output wire         ready,        // module ready (IDLE=1)
#     output wire         hash_valid,   // output valid pulse (1 cycle)
#     output wire [255:0] hash_out      // 256-bit hash result
# );

# ============================================================
# Section 2: Module Hierarchy + Interface Connections
# ============================================================
# Declare module hierarchy AND the exact port-to-port wiring between
# modules. This serves as the single source of truth for Stage 2
# codegen — the instance code is generated FROM this declaration,
# not guessed by the AI agent.
#
# Format:
#   MODULE_HIERARCHY = {
#       "parent_module": {
#           "submodules": [
#               {
#                   "instance_name": "u_sub0",
#                   "module": "sub_module_name",
#                   "connections": {
#                       "sub_port_name": "parent_signal",
#                       # One connection per port in the submodule declaration.
#                       # Port names MUST match the submodule's Python function
#                       # parameters exactly.
#                   },
#               },
#           ],
#       },
#   }
#
# Example (hash design):
#   MODULE_HIERARCHY = {
#       "sm3_core": {
#           "submodules": [
#               {
#                   "instance_name": "u_expand",
#                   "module": "w_gen_shift",
#                   "connections": {
#                       "w_reg":    "w_reg",
#                       "round_cnt": "round_cnt",
#                   },
#               },
#               {
#                   "instance_name": "u_compress",
#                   "module": "ff1_compress",
#                   "connections": {
#                       "A_reg":     "A_reg",
#                       "W_j":       "W_j",
#                       "W_prime_j": "W_prime_j",
#                   },
#               },
#           ],
#       },
#   }
#
# Validation rules (enforced by hierarchy_check.py):
#   1. Every "module" name must have a matching Section 5 Python function
#   2. Every connection port name must match the function's parameter name
#   3. Instance names must be unique within a parent module
#
# For leaf modules (no submodules), omit from MODULE_HIERARCHY or use empty list.

# === Uncomment and fill in after designing Section 5 ===
# MODULE_HIERARCHY = {
#     "TOP_MODULE_NAME": {
#         "submodules": [
#             # Add submodule instances here
#         ],
#     },
# }

# ============================================================
# Section 3: Algorithm Constants
# ============================================================

# CRITICAL: DESIGN_NAME must match the top-level Verilog module name.
# Used by SKILL.md pipeline to determine TOP_MODULE for simulation.
DESIGN_NAME = "<top_module_name>"

MASK32 = 0xFFFFFFFF  # 32-bit mask for modular arithmetic


# CRITICAL: Lookup tables (LUT) and wide constants (>64 bit) MUST be computed
# by Python code below, NEVER hand-written by the AI agent. The agent must
# call these generator functions and copy the output verbatim into Verilog.


def print_lut_verilog(name: str, values: list[int], width: int = 32):
    """Print a LUT as Verilog localparam array. Call from design_spec.py main block."""
    print(f"localparam [{width-1}:0] {name} [0:{len(values)-1}] = {{")
    for i, v in enumerate(values):
        comma = "," if i < len(values) - 1 else ""
        print(f"    {width}'h{v:0{width//4}x}{comma}  // [{i}]")
    print("};")


def print_wide_const_verilog(value: int, width: int, name: str):
    """Print a wide constant as Verilog concatenation of 32-bit segments."""
    segments = []
    for i in range(width // 32 - 1, -1, -1):
        seg = (value >> (i * 32)) & 0xFFFFFFFF
        segments.append(f"32'h{seg:08x}")
    print(f"localparam [{width-1}:0] {name} = {{")
    for i in range(0, len(segments), 4):
        line_segs = ", ".join(segments[i:i+4])
        trailing = "," if i + 4 < len(segments) else ""
        print(f"    {line_segs}{trailing}")
    print("};")


# Pattern for LUT / ROM constants:
#   1. Define the computation function (e.g., _compute_t_rot_lut())
#   2. Call print_lut_verilog("T_ROT_LUT", values) to generate Verilog output
#   3. Copy the stdout output into the .v file
#
# For wide constants (>64 bit), use print_wide_const_verilog(value, width, name).


# ============================================================
# Section 4: Helper Functions (wire semantics)
# ============================================================
# These map to Verilog combinational functions or inline wire expressions.
# They do NOT cross cycle boundaries.
#
# IMPORTANT: ROL(x, n) translation rules:
#   - n is CONSTANT: {x[W-1-N:0], x[W-1:W-N]} (bit-slice concatenation)
#   - n is VARIABLE: log2(W)-stage barrel shifter (MUST NOT use variable part-select)
#     See SKILL.md Rule R5 for barrel shifter template.

def ROL(x: int, n: int, width: int = 32) -> int:
    """Left rotate for width-bit values. Maps to Verilog: {x[W-1-N:0], x[W-1:W-N]}"""
    n = n % width
    return ((x << n) | (x >> (width - n))) & ((1 << width) - 1)


# ============================================================
# Section 4.5: DSL Module Construction (NOT RECOMMENDED)
# ============================================================
# The VeriFlow DSL provides formal timing semantics but is only suitable
# for simple leaf modules (counters, muxes, small datapaths).
# For complex designs (hash, cipher, processor), use plain Python with
# # wire / # reg_next annotations in Section 5 — this is the DEFAULT path.
#
# DSL is available if needed:
# from dsl import Module, Signal, Const, Cat, Mux
# def build_<module_name>():
#     m = Module("<module_name>")
#     ...
#     m.d.comb += sig.eq(expr)   # combinational
#     m.d.sync += sig.eq(expr)   # registered
#     return m
#
# Benefits:
#   - Structural timing: m.d.comb / m.d.sync replaces # wire / # reg_next
#   - Cycle-accurate simulation: CycleSimulator replaces manual compute()
#   - Verilog emission: exact Verilog output, no AI translation uncertainty
#   - Timing contract extraction: auto-populates cocotb GOLDEN_TO_PORT
#
# If build_*() functions exist, Section 6 compute() auto-detects and uses
# CycleSimulator. Plain Python functions still work as fallback.


# ============================================================
# Section 5: Module Pseudocode
# ============================================================
# Each function = one Verilog module.
# Convention:
#   - Function name = module name
#   - Parameters = current-cycle register values (old values, = NBA right-hand side)
#   - Return values = next-cycle register values (new values, = NBA left-hand side)
#   - Local variables = combinational wires (same-cycle visible)
#   - Assignment at call site = clock edge (cycle boundary)
#
# Wire vs Register annotation:
#   - wire: function-local variable, consumed within same cycle
#   - reg: function parameter (input) or return value (output), persists across cycles
#
# Return value timing annotations (REQUIRED):
#   Every return value MUST be annotated as either:
#     # wire:      same-cycle combinational output
#     # reg_next:  next-cycle register update (NBA)
#
# timing_contract (REQUIRED for cross-module signals):
#   Structured YAML-like annotation in docstring specifying:
#     inputs:  {source: <module>, delay: <0|1>, type: <wire|reg|reg_next>}
#     outputs: {delay: <0|1>, type: <wire|reg_next>}
#   delay=0 means same-cycle visible (combinational path)
#   delay=1 means next-cycle visible (registered output)

def <submodule_name>(<param1>, <param2>, ..., calc_en, init_vals):
    """<Module description>

    Interface (Verilog ports):
        input  wire         clk,
        input  wire         rst,
        input  wire         calc_en,       // CALC state enable
        ... (other ports)

    timing_contract:
        inputs:
            <param1>:  {source: "<module>", delay: 0, type: "reg"}
            <param2>:  {source: "<module>", delay: 0, type: "wire"}
            calc_en:   {source: "fsm", delay: 1, type: "reg"}
        outputs:
            <return1>: {delay: 0, type: "wire"}      # combinational output
            <return2>: {delay: 1, type: "reg_next"}  # registered output

    Timing:
        - calc_en registered from FSM, visible 1 cycle after assertion
        - <output> registered, visible 1 cycle after computation

    Args:
        <param1>: current value of <register1> (reg, = NBA right-hand side)
        <param2>: current value of <register2> (reg)
        calc_en: processing enable (reg, from FSM)
        init_vals: initialization values for first block (reg, from FSM)

    Returns:
        Tuple of next-cycle register values (with timing annotations):
            <return1>: <type>  # wire: same-cycle combinational output
            <return2>: <type>  # reg_next: next-cycle register update
    """
    if not calc_en:
        # Hold registers unchanged
        return <param1>, <param2>, ...

    # ---- Combinational logic (wire) ----
    # Local variables are wires: computed this cycle, consumed this cycle
    temp = <combinational_expression>   # wire
    result = <combinational_expression> # wire

    # ---- Register update (NBA) ----
    # Return values are new register values, visible next cycle
    return (
        <new_param1>,   # wire: same-cycle combinational output
        <new_param2>,   # reg_next: <param2> <= <new_param2>
        ...
    )


# ============================================================
# Section 6: Top-Level Integration
# ============================================================
# This function runs the complete design cycle-accurately.
# It connects all submodules and tracks FSM state.

def compute(inputs: dict, trace: bool = False) -> dict | list[dict]:
    """Execute the design cycle-accurately.

    Default path: manual state tracking with plain Python functions.
    DSL auto-detection: if build_<top_module>() exists in globals,
    uses CycleSimulator instead. DSL is only recommended for simple designs.

    Args:
        inputs: {"blocks": [int, ...], "is_last_flags": [bool, ...]}
        trace:  False -> {"<output>": value, ...}
                True  -> list[dict] per cycle for vcd2table/cocotb comparison

    Trace convention:
        Cycle N = register state after posedge N (POST-NBA of N-1).
        Computation at cycle N produces values visible at cycle N+1.
    """
    # --- DSL auto-detection ---
    import importlib, os, sys
    _dsl_available = False
    # Add skill directory to path for DSL module access
    _skill_dir = os.environ.get('CLAUDE_SKILL_DIR', '')
    if _skill_dir and _skill_dir not in sys.path:
        sys.path.insert(0, _skill_dir)
    try:
        from dsl import CycleSimulator
        # Look for build_<DESIGN_NAME> or any build_* function
        _builder = None
        for _name in list(globals()):
            if _name.startswith("build_"):
                _builder = globals()[_name]
                break
        if _builder is not None:
            _dsl_available = True
    except ImportError:
        pass

    if _dsl_available and trace:
        # DSL path: use CycleSimulator for cycle-accurate trace
        _m = _builder()
        _sim = CycleSimulator(_m)
        # Build input sequence from design inputs
        # (codegen customizes this per design)
        _input_seq = []  # populated per-design
        return _sim.run(1, _input_seq)  # placeholder, overridden by codegen
    blocks = inputs["blocks"]
    is_last_flags = inputs["is_last_flags"]
    cycles = [] if trace else None

    # Initialize accumulator
    V = list(<INIT_VALUES>)

    for blk_idx, (msg_block, is_last) in enumerate(zip(blocks, is_last_flags)):
        # Select init values for this block
        # CRITICAL (Rule R3 — Init-Value Consistency):
        #   `init` here is the value used to initialize working registers.
        #   In Verilog: this maps to `init_val` (the init-value selector signal).
        #   The finalize/DONE computation MUST use `init_val`, NOT the raw
        #   storage register (e.g., accum_reg). The storage register may
        #   contain stale values from a previous operation when is_first=1.
        #   Pattern: init_val = is_first ? CONST : accum_reg;
        #            result = init_val ^ work_out;  (NOT accum_reg ^ work_out)
        init = list(<INIT_VALUES>) if blk_idx == 0 else list(V)

        # Initialize working registers
        A, B, C, D, E, F, G, H = 0, 0, 0, 0, 0, 0, 0, 0

        for cycle in range(<TOTAL_CYCLES>):
            # --- Record pre-computation state (what cocotb reads) ---
            if trace:
                entry = {
                    "ready":      <1 if IDLE else 0>,
                    "hash_valid": <1 if DONE and is_last else 0>,
                    "hash_out":   0,
                    "calc_en":    <1 if CALC else 0>,
                    "round_cnt":  <current round>,
                    "A_reg": A, "B_reg": B, "C_reg": C, "D_reg": D,
                    "E_reg": E, "F_reg": F, "G_reg": G, "H_reg": H,
                    # Include ALL registers for full visibility
                }

            # --- Computation at this cycle (produces values for next cycle) ---
            if cycle == 0:
                # IDLE: wait for msg_valid, transition to CALC
                pass
            elif 1 <= cycle <= <NUM_ROUNDS>:
                # CALC: one round per cycle
                j = cycle - 1  # round index

                if cycle == 1:
                    # Load init values, then compute round 0
                    A, B, C, D, E, F, G, H = init

                A, B, C, D, E, F, G, H = <submodule_name>(
                    A, B, C, D, E, F, G, H, ...)

            elif cycle == <TOTAL_CYCLES - 1>:
                # DONE: update accumulator, output if last block
                # CRITICAL (Rule R3 — Init-Value Consistency):
                #   V here is the INIT value for this operation.
                #   In Verilog, this is `init_val` (the init-value selector).
                #   Do NOT use the raw storage register (accum_reg) which may
                #   hold stale values from a previous operation.
                #   When is_first=1: init_val=CONST, accum_reg may be stale.
                ah = [A, B, C, D, E, F, G, H]
                for i in range(8):
                    V[i] = (V[i] ^ ah[i]) & MASK32  # V = init_val (NOT accum_reg)

                if is_last:
                    hash_out = 0
                    for v in V:
                        hash_out = (hash_out << 32) | v
                    if trace:
                        entry["hash_valid"] = 1
                        entry["hash_out"] = hash_out

            # --- Record post-computation state ---
            if trace:
                if 1 <= cycle <= <NUM_ROUNDS>:
                    entry["A_reg"] = A
                    # ... update all register entries
                cycles.append(entry)

    if trace:
        return cycles

    hash_out = 0
    for v in V:
        hash_out = (hash_out << 32) | v
    return {"hash_out": hash_out, "hash_valid": 1}


# ============================================================
# Section 7: Test Vectors (from standard specification)
# ============================================================

def _pad_message(msg_bytes: bytes) -> list[int]:
    """Message padding per algorithm specification. Returns list of block ints."""
    # Implement per-algorithm padding
    ...


TEST_VECTORS = [
    {
        "name": "<test_name>",
        "inputs": {
            "blocks": [...],          # list of 512-bit block integers
            "is_last_flags": [...],   # list of bool
        },
        "expected": {
            "hash_out": <expected_int>,
            "hash_valid": 1,
        },
    },
    # ... more test vectors ...
]

# VALIDATION: At least one test vector MUST have > 1 block
# (to verify is_first_block transitions, V accumulator propagation,
#  and multi-block state continuity — Rule R4)
assert any(len(tv["inputs"]["blocks"]) > 1 for tv in TEST_VECTORS), \
    "TEST_VECTORS must include at least one multi-block test"


# ============================================================
# Section 8: Standard Interface
# ============================================================

def run(test_vector_index: int = 0) -> list[dict]:
    """Run a test vector and return cycle-accurate expected values.

    Consumed by:
      - vcd2table.py (import run(), get list[dict])
      - cocotb testbench (per-cycle comparison)
      - Verilog TB generation (extract expected final outputs)
    """
    tv = TEST_VECTORS[test_vector_index]
    return compute(tv["inputs"], trace=True)


def get_test_vectors() -> list[dict]:
    """Return test vectors with final expected outputs (for testbench generation)."""
    results = []
    for tv in TEST_VECTORS:
        computed = compute(tv["inputs"], trace=False)
        results.append({
            "name": tv["name"],
            "inputs": tv["inputs"],
            "expected": tv.get("expected") or computed,
        })
    return results


if __name__ == "__main__":
    import sys

    # Run cycle trace for vcd2table
    print("=== Design Spec: cycle trace ===")
    cycles = run(0)
    for i, entry in enumerate(cycles):
        parts = []
        for k in sorted(entry.keys()):
            v = entry[k]
            if isinstance(v, int) and v > 0xFFFF:
                parts.append(f"{k}=0x{v:08x}")
            else:
                parts.append(f"{k}={v}")
        print(f"cycle {i}: {' '.join(parts)}")

    # Verify all test vectors
    print("\n=== Verification ===")
    all_pass = True
    for tv in get_test_vectors():
        computed = compute(tv["inputs"], trace=False)
        ok = computed == tv["expected"]
        all_pass = all_pass and ok
        h = tv["expected"].get("hash_out", 0)
        print(f"[{'PASS' if ok else 'FAIL'}] {tv['name']}: hash_out=0x{h:064x}")
        if not ok:
            print(f"  expected: {tv['expected']}")
            print(f"  computed: {computed}")

    sys.exit(0 if all_pass else 1)
