# Template Guide

Supplementary explanations for the design_spec and cocotb templates.
Read this ONLY if you need clarification on a specific template section.

---

## design_spec_template.py

### Section 1: Interface Definition
- Each port: `name, direction, width, protocol, timing, reset`
- `timing` field: `"wire"` for combinational outputs, `"reg_next"` for registered outputs
- `protocol` field: `"data"`, `"valid"`, `"reset"`, etc.
- This section maps 1:1 to the Verilog module port list

### Section 2: Module Hierarchy
- Defines which modules instantiate which submodules
- `timing_contract` dict: specifies cross-module signal delays
  - `delay: 0, type: "wire"` → consumer reads directly (combinational)
  - `delay: 1, type: "reg_next"` → consumer reads next cycle (registered)

### Section 3: Algorithm Constants
- All magic numbers, IVs, round constants in Python
- For LUT-based designs: generate constants via Python code, not hardcoded

### Section 4: Helper Functions
- Pure Python functions implementing wire-level combinational logic
- Each function's parameters = inputs, return values = outputs
- Use `# wire` or `# reg_next` comments on return values for timing annotation
- Optional: `build_<module_name>()` functions using VeriFlow DSL for structural timing

### Section 5: Module Pseudocode
- One function per Verilog module
- Function signature = module interface (params = inputs, returns = outputs)
- Body = behavioral algorithm description
- NBA convention: all right-hand-side reads old values, all left-hand-side update simultaneously

### Section 6: Top-Level Integration
- `compute()` function: cycle-accurate simulation
- When `trace=True`: returns `list[dict]` where each dict = per-cycle signal snapshot
- Drives submodules, tracks cycle count, manages FSM

### Section 7: Test Vectors
- `TEST_VECTORS` list: each entry has `name`, `inputs`, `expected`
- MUST include multi-block test (Pattern 14 prevention)
- Self-test loop runs all vectors and prints `[PASS]`/`[FAIL]`

### Section 8: Standard Interface
- `run(test_vector_index)`: execute a test vector, return trace
- `get_test_vectors()`: return the TEST_VECTORS list
- These are called by cocotb_runner and iverilog_runner

---

## cocotb_template.py

### Key Architecture
- `GOLDEN_TO_PORT`: maps golden model signal names → DUT port names
- `test_layered()`: per-cycle golden model comparison
- `DRIVE_PHASE_CYCLES`: offset between golden trace and DUT cycle count
- cocotb reads POST-NBA values (previous posedge's result)

### Timing Convention
- `await RisingEdge(dut.clk)` → reads values from previous posedge's NBA
- Golden trace cycle K corresponds to cocotb cycle K+DRIVE_PHASE_CYCLES
- Registered outputs: visible 1 cycle after `_next` is computed

---

## tb_integration_template.v

### Structure
- Clock generation: `always #5 clk = ~clk`
- Reset sequence: assert rst for a few cycles, then release
- Test loop: drive inputs, wait posedge, check outputs
- `[PASS]`/`[FAIL]` markers for iverilog_runner parsing
- `$dumpvars(0, uut)` — DUT scope only, not testbench scope
