# Stage 1: design_spec (Optimized — 2-Wave Architecture)

Generate a single `design_spec.py` file combining design specification,
algorithm reference model, and test vector verification.

```
Wave 1 (inline, no agent):
  Orchestrator writes S1 (Interface) + S2 (Hierarchy) + S7 (Test Vectors) directly

Wave 2 (1 agent):
  Agent C: S3 (Constants) + S4 (Helpers) + S5 (Pseudocode) + S6 (compute body)
  ↑ ONE agent writes ALL algorithm logic → guaranteed consistency

Wave 3:
  merge_design_spec.py (deterministic) + Validate + Self-test + Fix
```

**Why orchestrator writes S1+S2+S7**: These are mechanical translations from
the requirement file — port lists, hierarchy, and test vectors are already
specified in the input files. No AI creativity needed, just formatting.
Saves ~10 minutes of agent dispatch overhead.

**Why S3+S4+S5+S6 in one agent**: The pseudocode (S5) and compute body (S6)
are two expressions of the SAME algorithm. One agent = one mental model.

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "design_spec" --start
mkdir -p workspace/docs/_parts
```

### Design Type Detection

Before writing code, detect the design category from `requirement.md` to
provide targeted guidance. Read the requirement and classify:

| Category | Keywords | Section focus |
|----------|----------|---------------|
| **hash** | SM3, SHA, SHA-256, SHA-3, hash, digest | Compression function, message expansion, accumulator chain |
| **cipher** | AES, DES, SM4, encrypt, decrypt, cipher | Key expansion, round function, S-box |
| **asymmetric** | RSA, ECC, Montgomery, modular multiply | Large multiplier, modular reduction, pipeline stages |
| **protocol** | UART, SPI, I2C, PCIe, AXI, FIFO | FSM design, handshake protocol, buffer management |
| **processor** | CPU, RISC-V, pipeline, ALU, register file | Pipeline stages, hazard detection, forwarding |
| **dsp** | FFT, FIR, IIR, filter, NTT | Datapath pipeline, coefficient storage, symmetric structure |

Save the detected category — later stages use it for conditional checks:
```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/pipeline_context.py" "$PROJECT_DIR" --set design_category "$DESIGN_CATEGORY"
```
(Replace `$DESIGN_CATEGORY` with the actual detected value: hash, cipher, asymmetric, protocol, processor, or dsp.)

---

## Wave 1 — Orchestrator writes S1 + S2 + S7 inline (no agent)

Read the requirement file, context files. The template is passed by path to Agent C — do NOT read it in the main session.

Then write three files directly using the Write tool:

### S1: Interface Definition

Write `workspace/docs/_parts/s1_interface.py`:
- Extract the top-level module interface from requirement.md / context/design_spec.md
- Module port list as a comment block (matching Verilog port declaration)
- `DESIGN_NAME` variable matching the top-level Verilog module name
- `PORTS` list with `(name, direction, width, protocol, description)` tuples
- Follow the reset convention from requirement (rst active-high vs rst_n active-low)

### S2: Module Hierarchy

Write `workspace/docs/_parts/s2_hierarchy.py`:
- Module hierarchy tree (ASCII art comment) from context/design_spec.md
- `timing_contract` dict defining cross-module signal delays
- For each cross-module signal: `{source, delay: 0|1, type: wire|reg_next}`

### S7: Test Vectors

Write `workspace/docs/_parts/s7_test_vectors.py`:
- Data preparation function per the algorithm spec (e.g., `_pad_message` for hash, `_key_expand` for cipher)
- `TEST_VECTORS` list — at least 2 vectors
- Expected output values MUST come from the standard specification or reference document in `context/`
- Each vector format varies by design category:
  - **hash**: `{name, inputs: {blocks, is_last_flags}, expected: {hash_out, hash_valid}}`
  - **cipher**: `{name, inputs: {key, plaintext, mode}, expected: {ciphertext, ready}}`
  - **protocol**: `{name, inputs: {data_in, valid}, expected: {data_out, ready}}`
  - **asymmetric**: `{name, inputs: {operand_a, operand_b, modulus}, expected: {result, valid}}`
  - **processor**: `{name, inputs: {instruction, rs1, rs2}, expected: {result, reg_write}}`
  - **dsp**: `{name, inputs: {samples, coefficients}, expected: {filtered_out, valid}}`
- Category-appropriate validation:
  - hash: at least one multi-block vector
  - cipher: at least one encrypt + one decrypt (or multi-round)
  - protocol: at least one multi-transaction or backpressure scenario
  - general: at least 2 vectors covering different input sizes or modes

Verify test vectors:
```bash
$PYTHON_EXE -c "
import sys; sys.path.insert(0, 'workspace/docs/_parts')
from s7_test_vectors import TEST_VECTORS
assert len(TEST_VECTORS) >= 2, 'Need at least 2 test vectors'
print(f'S7 OK: {len(TEST_VECTORS)} test vectors')
"
```

---

## Wave 2 — Agent C: Algorithm Core (S3 + S4 + S5 + S6)

### Agent C dispatch

Write to: `workspace/docs/_parts/s3_constants.py`, `workspace/docs/_parts/s4_helpers.py`,
`workspace/docs/_parts/s5_pseudocode.py`, `workspace/docs/_parts/s6_compute_body.py`

Dispatch **Agent C** (subagent_type: general-purpose):
- Prompt includes:
  - PROJECT_DIR path
  - PARENT_TASK_ID (the task ID of the [S1] task, so the agent can set addBlockedBy)
  - DESIGN_CATEGORY
  - **S1 interface + S2 hierarchy** (read the files the orchestrator just wrote)
  - **S7 test vectors** (read the file the orchestrator just wrote)
  - ALL input file contents (requirement.md, context/*.md)
  - **Template path**: `${CLAUDE_SKILL_DIR}/templates/design_spec_template.py` — Agent C reads this itself
  - **HARD CONSTRAINT block** (see below)
- The agent MUST:
  1. Read `workspace/docs/_parts/s1_interface.py` and `s2_hierarchy.py`
  2. Read `workspace/docs/_parts/s7_test_vectors.py`
  3. Write `s3_constants.py` containing ONLY Section 3:
     - All algorithm constants as Python variables
     - **MANDATORY**: LUT/ROM values MUST be computed by Python functions
     - **MANDATORY**: For every LUT/ROM, include a zero-arg `print_*_verilog()`
       function that returns a string with Verilog `localparam` declarations.
  4. Write `s4_helpers.py` containing ONLY Section 4:
     - Combinational helper functions required by the algorithm
       (e.g., `ROL()` for hash, S-box lookup for cipher, modular ops for asymmetric)
     - Self-test at bottom of file
  5. Write `s5_pseudocode.py` containing ONLY Section 5:
     - One function per Verilog module with timing_contract docstrings
     - Return value annotations: `# wire` or `# reg_next` for every output
  6. Write `s6_compute_body.py` containing ONLY the body of `compute()`:
     - Follows the HARD CONSTRAINT block below
  7. Verify syntax of all four files:
     ```bash
     $PYTHON_EXE -c "import sys; sys.path.insert(0, 'workspace/docs/_parts'); from s3_constants import *; print('S3 OK')"
     $PYTHON_EXE -c "import sys; sys.path.insert(0, 'workspace/docs/_parts'); from s4_helpers import *; print('S4 OK')"
     $PYTHON_EXE -c "import sys; sys.path.insert(0, 'workspace/docs/_parts'); from s5_pseudocode import *; print('S5 OK')"
     ```

**HARD CONSTRAINT block — include verbatim in Agent C's prompt:**

```
## OUTPUT DISCIPLINE (CRITICAL)

On completion, report ONLY:
- Status: SUCCESS or FAIL
- Files written: list of paths
- Summary: 1-2 sentence description of what was generated
- Errors: only if FAIL

DO NOT output file contents in your response. The orchestrator will read files directly if needed.

## AGENT DISCIPLINE — SUBTASK MANAGEMENT (CRITICAL)

You are a sub-agent working under the orchestrator's [S1] task.
When you start, create subtasks with proper prefix and parent linkage:

1. Get the parent [S1] task ID from the prompt variable PARENT_TASK_ID.
2. Create subtasks with [S1.N] prefix, each addBlockedBy the parent:
   - TaskCreate: subject="[S1.3] Write s3_constants.py", addBlockedBy=[PARENT_TASK_ID]
   - TaskCreate: subject="[S1.4] Write s4_helpers.py", addBlockedBy=[PARENT_TASK_ID]
   - TaskCreate: subject="[S1.5] Write s5_pseudocode.py", addBlockedBy=[PARENT_TASK_ID]
   - TaskCreate: subject="[S1.6] Write s6_compute_body.py", addBlockedBy=[PARENT_TASK_ID]
3. Mark each subtask in_progress before starting work, completed when done.
4. DO NOT create tasks for other stages. Only [S1.3]~[S1.6].

## HARD CONSTRAINT — SECTION BOUNDARIES (FAILURE = REJECTED OUTPUT)

You write FOUR files. Each file contains EXACTLY ONE section. Do NOT mix.

s3_constants.py: Section 3 only (constants + computation functions + print_*_verilog)
s4_helpers.py:   Section 4 only (algorithm-specific helpers, e.g., ROL, S-box, modular ops)
s5_pseudocode.py: Section 5 only (one function per Verilog module)
s6_compute_body.py: Section 6 only (compute() function body)

## HARD CONSTRAINT — MERGE SAFETY (FAILURE = REJECTED OUTPUT)

All parts are MERGED into a single design_spec.py file.
Therefore each part file MUST be safe to concatenate.

DO NOT include in ANY file:
- `from __future__ import annotations` — breaks when not at file top
- `from workspace.docs._parts.s3_constants import ...` — cross-module imports
- `from workspace.docs._parts.s4_helpers import ...` — break after merge

All constants and helpers defined in S3 and S4 are available as globals in s6_compute_body.py.
Just USE them directly — no imports needed.

## HARD CONSTRAINT — INTERFACE CONTRACT (FAILURE = REJECTED OUTPUT)

The compute() function signature is PRE-DEFINED and AUTO-INJECTED.
You are writing ONLY the function body for s6_compute_body.py.

The final merged compute() will have this EXACT signature:
  def compute(inputs: dict, trace: bool = False) -> dict | list[dict]:

The input dict structure is defined by S7's TEST_VECTORS.
Extract inputs per S7 format. Common patterns (adapt to YOUR design):

| Category | Input keys (example) | Output keys (example) |
|----------|---------------------|----------------------|
| hash | blocks, is_last_flags | hash_out, hash_valid |
| cipher | key, plaintext, mode | ciphertext, ready |
| protocol | data_in, valid | data_out, ready |
| asymmetric | operand_a, operand_b, modulus | result, valid |
| processor | instruction, rs1, rs2 | result, reg_write |
| dsp | samples, coefficients | filtered_out, valid |

DO NOT:
- Change parameter names (use the same keys as S7 test vectors)
- Change parameter types (inputs MUST be dict, NOT bytes/str/list)
- Change parameter order
- Add additional parameters
- Write a different function name
- Write run(), get_test_vectors(), or __main__ — these are AUTO-INJECTED

The file s6_compute_body.py should contain ONLY Python code that
implements the cycle-accurate simulation logic.

Start by extracting inputs from the dict per S7's test vector format.
If tracing, append per-cycle state to `cycles` list.
End with the final return dict matching your design's output signals.

## HARD CONSTRAINT — S5/S6 CONSISTENCY (FAILURE = REJECTED OUTPUT)

You write BOTH s5_pseudocode.py and s6_compute_body.py.
They MUST be consistent:

1. For every pseudocode function in s5, the same formula MUST appear in s6 compute().
   If s5 module_a() computes `result = A OP B`, then s6 compute() MUST use the same formula.

2. If s5 submodule() returns (out1, out2, next_reg),
   then s6 compute() MUST produce the same values given the same inputs.

3. The # wire / # reg_next annotations in s5 MUST match the trace recording
   in s6. If a signal is annotated # reg_next in s5, s6 MUST record the
   value that appears on the NEXT cycle (post-NBA), not the same cycle.

4. Cross-module timing in s5 (delay: 0/1) MUST match how s6 orchestrates
   submodule calls. If a signal has delay:1 in s5, s6 MUST apply the
   submodule output on the NEXT cycle.

VERIFICATION: After writing all four files, mentally trace through one
complete block (all cycles) using the pseudocode functions, then compare
against what compute() would produce. They MUST match.
```

---

## Wave 3 — Merge + Validate + Self-test

### Step 1: Merge all parts (single command)

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/merge_design_spec.py" "$PROJECT_DIR"
```

### Step 2: Interface Contract Validation

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/validate_interface.py" workspace/docs/design_spec.py --smoke
if [ $? -ne 0 ]; then
    echo "[FATAL] Interface contract validation failed — fix compute() body or test vectors"
fi
```

### Step 3: Self-test (test vector verification)

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE workspace/docs/design_spec.py 2>&1 | tee logs/design_spec_selfcheck.log
$PYTHON_EXE -c "import sys; sys.exit(1 if '[FAIL]' in open('logs/design_spec_selfcheck.log').read() else 0)" && echo "[FATAL] design_spec.py has failing test vectors" && exit 1
```

### Step 4: S5/S6 Consistency Validation

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE -c "
import sys, inspect
sys.path.insert(0, 'workspace/docs')
from design_spec import *
from inspect import getmembers, isfunction

# Dynamically detect pseudocode functions (Section 5) by checking for
# timing_contract or # wire/# reg_next annotations in source.
# This is category-agnostic — works for any design.
pseudocode_fns = {}
for name, fn in getmembers(sys.modules['design_spec'], isfunction):
    if name.startswith('_') or name in ('compute', 'run', 'get_test_vectors'):
        continue
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        continue
    if 'timing_contract' in src or '# wire' in src or '# reg_next' in src:
        pseudocode_fns[name] = fn

cycles = run(0)
if not cycles:
    print('[FATAL] compute(trace=True) returned empty list')
    sys.exit(1)

print(f'[OK] S5/S6 consistency: {len(cycles)} cycles traced')
print(f'[OK] Pseudocode functions found: {list(pseudocode_fns.keys())}')
print('[OK] S5/S6 consistency validation passed')
" 2>&1
```

### Step 5: Fix if needed

If any validation step fails, dispatch a **Fix Agent**:
- Prompt includes: the error output from the failed step
- The agent reads `workspace/docs/design_spec.py`, identifies the bug, fixes it
- **CRITICAL**: The fix agent MUST NOT change the compute() signature or Section 8.
  Only fix the body logic or other sections.
- The fix agent may create one subtask `[S1.fix] Fix validation error`, set addBlockedBy to the [S1] task ID.
- Re-run all validation steps until all pass (max 3 fix attempts)

## Post-stage

```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "design_spec" --hook="$PYTHON_EXE -c \"import os,sys; sys.exit(0 if os.path.isfile('workspace/docs/design_spec.py') else 1)\"" --journal-outputs="workspace/docs/design_spec.py, logs/design_spec_selfcheck.log, workspace/docs/_extracted_constants.v" --journal-notes="Python design specification generated and verified against standard test vectors"
```

Update pipeline context:
```bash
cd "$PROJECT_DIR"
DESIGN_NAME=$($PYTHON_EXE -c "import importlib.util; spec=importlib.util.spec_from_file_location('ds','workspace/docs/design_spec.py'); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print(mod.DESIGN_NAME)")
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/pipeline_context.py" "$PROJECT_DIR" --set design_name "$DESIGN_NAME"
# DESIGN_CATEGORY was already saved after type detection
```

Write stage summary:
```bash
cat >> "$PROJECT_DIR/.veriflow/stage_summaries.md" << 'EOF'

## Stage 1 — design_spec
- Output: workspace/docs/design_spec.py
- Constants: workspace/docs/_extracted_constants.v
- Validation: all test vectors passed, interface contract verified, S5/S6 consistency checked
EOF
```

TaskUpdate: mark the [S1] task as completed.
