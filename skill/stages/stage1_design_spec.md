# Stage 1: design_spec (Multi-Agent Parallel)

Generate a single `design_spec.py` file combining design specification,
algorithm reference model, and test vector verification.

Uses a **4-wave parallel agent** architecture to reduce latency:

```
Wave 1 (3 agents in parallel):
  Agent A: S1 (Interface) + S2 (Module Hierarchy)
  Agent B: S3 (Constants) + S4 (Helper Functions)
  Agent C: S7 (Test Vectors)

Wave 2 (1 agent, depends on Wave 1):
  Agent D: S5 (Module Pseudocode)

Wave 3 (1 agent, depends on Wave 1 + Wave 2):
  Agent E: S6 (compute body ONLY — scaffold-injected interface)

Wave 4:
  Scaffold S8 + Merge + Interface Validation + Self-test + Fix
```

**Key Optimization**: Section 8 (run/get_test_vectors/__main__) is
deterministic scaffold code — it is AUTO-INJECTED by the merge step,
NOT written by any agent. This eliminates the agent-template interface
mismatch problem that previously caused 40+ minute iteration loops.

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "design_spec" --start
mkdir -p workspace/docs/_parts
```

### Design Type Detection

Before dispatching agents, detect the design category from `requirement.md` to
provide targeted guidance. Read the requirement and classify:

| Category | Keywords | Section focus |
|----------|----------|---------------|
| **hash** | SM3, SHA, SHA-256, SHA-3, hash, digest | Compression function, message expansion, accumulator chain |
| **cipher** | AES, DES, SM4, encrypt, decrypt, cipher | Key expansion, round function, S-box |
| **asymmetric** | RSA, ECC, Montgomery, modular multiply | Large multiplier, modular reduction, pipeline stages |
| **protocol** | UART, SPI, I2C, PCIe, AXI, FIFO | FSM design, handshake protocol, buffer management |
| **processor** | CPU, RISC-V, pipeline, ALU, register file | Pipeline stages, hazard detection, forwarding |
| **dsp** | FFT, FIR, IIR, filter, NTT | Datapath pipeline, coefficient storage, symmetric structure |

The detected category is passed to agents as `DESIGN_CATEGORY` context.
This does NOT change the template — it adds **one paragraph of targeted guidance**
to each agent's prompt about what to focus on for that category.

## Shared Context

All agents receive:
- PROJECT_DIR path
- DESIGN_CATEGORY (from detection above)
- Contents of `${CLAUDE_SKILL_DIR}/templates/design_spec_template.py` (the template)
- ALL input file contents (requirement.md, context/*.md, clarifications)
- The section(s) they are responsible for, with clear boundaries

Read the template once before dispatching agents:
```bash
cat "${CLAUDE_SKILL_DIR}/templates/design_spec_template.py"
```
(For template section details, read `${CLAUDE_SKILL_DIR}/docs/template_guide.md` if needed.)

---

## Wave 1 — Launch 3 agents in parallel

### Agent A: Interface + Hierarchy (S1 + S2)

Write to: `workspace/docs/_parts/s1_interface.py` and `workspace/docs/_parts/s2_hierarchy.py`

Dispatch **Agent A** (subagent_type: general-purpose):
- Prompt includes: shared context + template Sections 1-2
- The agent MUST:
  1. Analyze the requirement and design the top-level module interface
  2. Write `workspace/docs/_parts/s1_interface.py` containing ONLY Section 1:
     - Module port list as a comment block (matching Verilog port declaration)
     - Each port: name, direction, width, protocol, timing annotation
     - DESIGN_NAME variable matching the top-level Verilog module name
  3. Write `workspace/docs/_parts/s2_hierarchy.py` containing ONLY Section 2:
     - Module hierarchy tree (ASCII art comment)
     - timing_contract dict defining cross-module signal delays
     - For each cross-module signal: `{source, delay: 0|1, type: wire|reg_next}`

### Agent B: Constants + Helpers (S3 + S4)

Write to: `workspace/docs/_parts/s3_constants.py` and `workspace/docs/_parts/s4_helpers.py`

Dispatch **Agent B** (subagent_type: general-purpose):
- Prompt includes: shared context + template Sections 3-4
- The agent MUST:
  1. Extract all algorithm constants from the specification (IV, round constants, etc.)
  2. Write `workspace/docs/_parts/s3_constants.py` containing ONLY Section 3:
     - All algorithm constants as Python variables
     - **CRITICAL**: LUT values MUST be computed by Python code, NOT hand-written.
       Include computation functions (e.g., `_compute_t_rot_lut()`) and their
       Verilog output helpers (e.g., `print_t_rot_lut_verilog()`).
     - MASK32 and other utility masks
  3. Write `workspace/docs/_parts/s4_helpers.py` containing ONLY Section 4:
     - `ROL()` function (left rotate, maps to Verilog bit-slice concatenation)
     - All other combinational helper functions from the algorithm spec
     - Each function: pure Python, no state, maps to Verilog wire logic
  4. Verify helpers are correct by running a quick self-test:
     ```bash
     $PYTHON_EXE -c "import sys; sys.path.insert(0,'workspace/docs/_parts'); from s4_helpers import *; print('S4 OK')"
     ```

### Agent C: Test Vectors (S7)

Write to: `workspace/docs/_parts/s7_test_vectors.py`

Dispatch **Agent C** (subagent_type: general-purpose):
- Prompt includes: shared context + template Section 7
- The agent MUST:
  1. Implement `_pad_message()` function per the algorithm specification
  2. Construct `TEST_VECTORS` list from the standard specification:
     - At least 2 vectors: one single-block, one multi-block
     - Each vector: `{name, inputs: {blocks, is_last_flags}, expected: {output signals}}`
     - Expected values MUST come from the official specification (e.g., RFC, NIST)
  3. Write `workspace/docs/_parts/s7_test_vectors.py` containing ONLY Section 7
  4. Validate test vector structure:
     ```bash
     $PYTHON_EXE -c "
     import sys; sys.path.insert(0, 'workspace/docs/_parts')
     from s7_test_vectors import TEST_VECTORS
     assert len(TEST_VECTORS) >= 2, 'Need at least 2 test vectors'
     assert any(len(tv['inputs']['blocks']) > 1 for tv in TEST_VECTORS), 'Need multi-block test'
     print('S7 OK: %d test vectors' % len(TEST_VECTORS))
     "
     ```

---

## Wave 2 — Module Pseudocode (S5)

Wait for Wave 1 (Agents A, B, C) to complete before launching.

### Agent D: Module Pseudocode (S5)

Write to: `workspace/docs/_parts/s5_pseudocode.py`

Dispatch **Agent D** (subagent_type: general-purpose):
- Prompt includes:
  - shared context
  - **outputs from Agent A** (S1 interface + S2 hierarchy — read the files)
  - **outputs from Agent B** (S3 constants + S4 helpers — read the files)
  - template Section 5
- The agent MUST:
  1. Read `workspace/docs/_parts/s1_interface.py` and `s2_hierarchy.py` to understand:
     - Module names and port lists
     - Cross-module timing contracts
  2. Read `workspace/docs/_parts/s3_constants.py` and `s4_helpers.py` to understand:
     - Available constants and helper functions
  3. Write `workspace/docs/_parts/s5_pseudocode.py` containing ONLY Section 5:
     - One function per Verilog module
     - Each function: parameters = current-cycle registers, returns = next-cycle registers
     - timing_contract in docstring for every cross-module signal
     - Return value annotations: `# wire` or `# reg_next` for every output
  4. Verify syntax:
     ```bash
     $PYTHON_EXE -c "import sys; sys.path.insert(0,'workspace/docs/_parts'); from s5_pseudocode import *; print('S5 OK')"
     ```
  5. **Incremental verification** — for each submodule function, write a quick smoke test
     that calls it with known inputs and verifies the output is in the expected range.
     This catches logic errors BEFORE the full integration in Wave 3.
     Example smoke test pattern:
     ```bash
     $PYTHON_EXE -c "
     import sys; sys.path.insert(0,'workspace/docs/_parts')
     from s3_constants import *; from s4_helpers import *; from s5_pseudocode import *
     # Test each submodule with zero/identity inputs
     result = <submodule_name>(0, 0, 0, ..., calc_en=1, init_vals=0)
     assert all(isinstance(v, int) for v in result), 'Submodule must return ints'
     print('S5 smoke: all submodules return valid ints')
     "
     ```

---

## Wave 3 — Integration (S6 compute body ONLY)

Wait for Wave 2 (Agent D) to complete before launching.

### Agent E: compute() body ONLY

Write to: `workspace/docs/_parts/s6_compute_body.py`

**CRITICAL CHANGE**: Agent E now writes ONLY the body of the `compute()` function.
The function signature and Section 8 (`run`, `get_test_vectors`, `__main__`)
are scaffold-injected by the merge step. This eliminates interface drift.

Dispatch **Agent E** (subagent_type: general-purpose):
- Prompt includes:
  - shared context
  - **outputs from ALL previous agents** (read all files in `_parts/`)
  - template Section 6
  - **HARD CONSTRAINT block** (see below)
- The agent MUST:
  1. Read ALL files in `workspace/docs/_parts/` to understand the full design
  2. Write `workspace/docs/_parts/s6_compute_body.py` containing ONLY the body
     of the compute function — the code that goes INSIDE the function after
     the `blocks = inputs["blocks"]` line, up to (but not including) the
     final return statement.

**HARD CONSTRAINT block — include verbatim in Agent E's prompt:**

```
## HARD CONSTRAINT — INTERFACE CONTRACT (FAILURE = REJECTED OUTPUT)

The compute() function signature is PRE-DEFINED and AUTO-INJECTED.
You are writing ONLY the function body.

The final merged compute() will have this EXACT signature:
  def compute(inputs: dict, trace: bool = False) -> dict | list[dict]:

Inside the function body, you MUST use these exact variable names:
  blocks = inputs["blocks"]              # list of block ints
  is_last_flags = inputs["is_last_flags"] # list of bools

DO NOT:
- Change parameter names (NO msg_bytes, NO message, NO data)
- Change parameter types (inputs MUST be dict, NOT bytes/str/list)
- Change parameter order
- Add additional parameters
- Write a different function name (NO compute_hash, NO sm3_compute)
- Write run(), get_test_vectors(), or __main__ — these are AUTO-INJECTED

The file s6_compute_body.py should contain ONLY Python code that
implements the cycle-accurate simulation logic. Start with:
  blocks = inputs["blocks"]
  is_last_flags = inputs["is_last_flags"]
  cycles = [] if trace else None

And end with the final return:
  if trace:
      return cycles
  hash_out = 0
  for v in V:
      hash_out = (hash_out << 32) | v
  return {"hash_out": hash_out, "hash_valid": 1}

(Adjust the output dict keys to match your design's output signals.)
```

  3. Verify syntax:
     ```bash
     $PYTHON_EXE -c "import sys; sys.path.insert(0,'workspace/docs/_parts'); from s6_compute_body import *; print('S6 body OK')"
     ```

---

## Wave 4 — Scaffold S8 + Merge + Validate + Self-test + Fix

### Step 1: Auto-inject S8 scaffold

Generate `workspace/docs/_parts/s8_interface.py` from the deterministic scaffold.
This file is ALWAYS the same — no agent involvement:

```bash
$PYTHON_EXE -c "
scaffold = '''# ============================================================
# Section 8: Standard Interface (AUTO-INJECTED SCAFFOLD)
# ============================================================
# This section is auto-generated by the pipeline merge step.
# DO NOT edit manually — changes will be overwritten.

def run(test_vector_index: int = 0) -> list[dict]:
    \"\"\"Run a test vector and return cycle-accurate expected values.

    Consumed by:
      - vcd2table.py (import run(), get list[dict])
      - cocotb testbench (per-cycle comparison)
      - Verilog TB generation (extract expected final outputs)
    \"\"\"
    tv = TEST_VECTORS[test_vector_index]
    return compute(tv[\"inputs\"], trace=True)


def get_test_vectors() -> list[dict]:
    \"\"\"Return test vectors with final expected outputs (for testbench generation).\"\"\"
    results = []
    for tv in TEST_VECTORS:
        computed = compute(tv[\"inputs\"], trace=False)
        results.append({
            \"name\": tv[\"name\"],
            \"inputs\": tv[\"inputs\"],
            \"expected\": tv.get(\"expected\") or computed,
        })
    return results


if __name__ == \"__main__\":
    import sys

    # === Interface Contract Validation (auto-injected, DO NOT EDIT) ===
    print(\"=== Interface Contract Validation ===\")
    _contract_ok = True

    # Check compute() signature
    import inspect
    _sig = inspect.signature(compute)
    _params = list(_sig.parameters.keys())
    if _params != [\"inputs\", \"trace\"]:
        print(f\"[FATAL] compute() signature mismatch: {_params} != ['inputs', 'trace']\")
        _contract_ok = False

    # Check run() signature
    _sig_run = inspect.signature(run)
    _params_run = list(_sig_run.parameters.keys())
    if _params_run != [\"test_vector_index\"]:
        print(f\"[FATAL] run() signature mismatch: {_params_run} != ['test_vector_index']\")
        _contract_ok = False

    # Check get_test_vectors() signature
    _sig_gtv = inspect.signature(get_test_vectors)
    _params_gtv = list(_sig_gtv.parameters.keys())
    if _params_gtv != []:
        print(f\"[FATAL] get_test_vectors() signature mismatch: {_params_gtv} != []\")
        _contract_ok = False

    # Functional smoke: compute must accept dict input
    try:
        _tv0 = TEST_VECTORS[0]
        _r = compute(_tv0[\"inputs\"], trace=False)
        if not isinstance(_r, dict):
            print(f\"[FATAL] compute(trace=False) must return dict, got {type(_r).__name__}\")
            _contract_ok = False
    except Exception as _e:
        print(f\"[FATAL] compute() smoke test failed: {_e}\")
        _contract_ok = False

    if _contract_ok:
        print(\"[PASS] Interface contract OK\")
    else:
        sys.exit(1)

    # Run cycle trace for vcd2table
    print(\"\\n=== Design Spec: cycle trace ===\")
    cycles = run(0)
    for i, entry in enumerate(cycles):
        parts = []
        for k in sorted(entry.keys()):
            v = entry[k]
            if isinstance(v, int) and v > 0xFFFF:
                parts.append(f\"{k}=0x{v:08x}\")
            else:
                parts.append(f\"{k}={v}\")
        print(f\"cycle {i}: {' '.join(parts)}\")

    # Verify all test vectors
    print(\"\\n=== Verification ===\")
    all_pass = True
    for tv in get_test_vectors():
        computed = compute(tv[\"inputs\"], trace=False)
        ok = computed == tv[\"expected\"]
        all_pass = all_pass and ok
        h = tv[\"expected\"].get(\"hash_out\", 0)
        print(f\"[{'PASS' if ok else 'FAIL'}] {tv['name']}: hash_out=0x{h:064x}\")
        if not ok:
            print(f\"  expected: {tv['expected']}\")
            print(f\"  computed: {computed}\")

    sys.exit(0 if all_pass else 1)
'''
with open('workspace/docs/_parts/s8_interface.py', 'w', encoding='utf-8') as f:
    f.write(scaffold)
print('[OK] S8 scaffold injected')
"
```

### Step 2: Merge parts into final design_spec.py

The merge wraps s6_compute_body.py inside the `compute()` function scaffold:

```bash
$PYTHON_EXE -c "
import os
parts_dir = 'workspace/docs/_parts'
output = 'workspace/docs/design_spec.py'
header = '\"\"\"design_spec.py -- Python Design Specification.\n\nGenerated by VeriFlow Stage 1 (multi-agent parallel).\nSee template_guide.md for section descriptions.\n\"\"\"\n\n'
parts_order = ['s1_interface', 's2_hierarchy', 's3_constants', 's4_helpers', 's5_pseudocode', 's7_test_vectors']

with open(output, 'w', encoding='utf-8') as f:
    f.write(header)

    # Write sections 1-5 and 7 as-is
    for part in parts_order:
        path = os.path.join(parts_dir, part + '.py')
        if not os.path.isfile(path):
            print(f'[FATAL] Missing part: {path}')
            exit(1)
        with open(path, 'r', encoding='utf-8') as pf:
            f.write(pf.read())
            f.write('\n\n')

    # Section 6: compute() with scaffold wrapper + agent body
    compute_scaffold_path = os.path.join(parts_dir, 's6_compute_body.py')
    if not os.path.isfile(compute_scaffold_path):
        print(f'[FATAL] Missing part: {compute_scaffold_path}')
        exit(1)

    with open(compute_scaffold_path, 'r', encoding='utf-8') as pf:
        compute_body = pf.read()

    f.write('# ============================================================\n')
    f.write('# Section 6: Top-Level Integration\n')
    f.write('# ============================================================\n')
    f.write('# compute() function: cycle-accurate simulation.\n')
    f.write('# Function signature is scaffold-injected; body from Agent E.\n\n')
    f.write('def compute(inputs: dict, trace: bool = False) -> dict | list[dict]:\n')
    f.write('    \"\"\"Execute the design cycle-accurately.\n\n')
    f.write('    Args:\n')
    f.write('        inputs: {\"blocks\": [int, ...], \"is_last_flags\": [bool, ...]}\n')
    f.write('        trace:  False -> dict of final outputs\n')
    f.write('                True  -> list[dict] per cycle for cocotb comparison\n')
    f.write('    \"\"\"\n')
    # Indent the body to be inside the function
    for line in compute_body.splitlines():
        if line.strip():
            f.write('    ' + line + '\n')
        else:
            f.write('\n')
    f.write('\n\n')

    # Section 8: auto-injected scaffold
    s8_path = os.path.join(parts_dir, 's8_interface.py')
    if not os.path.isfile(s8_path):
        print(f'[FATAL] Missing part: {s8_path}')
        exit(1)
    with open(s8_path, 'r', encoding='utf-8') as pf:
        f.write(pf.read())

print(f'[OK] Merged into {output}')
"
```

### Step 3: Interface Contract Validation

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/validate_interface.py" workspace/docs/design_spec.py --smoke
if [ $? -ne 0 ]; then
    echo "[FATAL] Interface contract validation failed — fix compute() body or test vectors"
fi
```

### Step 4: Self-test (test vector verification)

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE workspace/docs/design_spec.py 2>&1 | tee logs/design_spec_selfcheck.log
$PYTHON_EXE -c "import sys; sys.exit(1 if '[FAIL]' in open('logs/design_spec_selfcheck.log').read() else 0)" && echo "[FATAL] design_spec.py has failing test vectors" && exit 1
```

### Step 5: Fix if needed

If verification fails, dispatch a **Fix Agent**:
- Prompt includes: the error output from validation/self-test
- The agent reads `workspace/docs/design_spec.py`, identifies the bug, fixes it
- **CRITICAL**: The fix agent MUST NOT change the compute() signature or Section 8.
  Only fix the body logic (s6_compute_body.py) or other sections.
- Re-run validation + self-test until all tests pass (max 3 fix attempts)

## Post-stage

```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "design_spec" --hook="$PYTHON_EXE -c \"import os,sys; sys.exit(0 if os.path.isfile('workspace/docs/design_spec.py') else 1)\"" --journal-outputs="workspace/docs/design_spec.py, logs/design_spec_selfcheck.log" --journal-notes="Python design specification generated and verified against standard test vectors"
```

TaskUpdate complete.
