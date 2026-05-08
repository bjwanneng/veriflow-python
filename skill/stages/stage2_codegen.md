# Stage 2: codegen

Translate Python functions to Verilog modules + testbench.

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "codegen" --start
```

## Read inputs

Read `design_spec.py` only. The `coding_style_core.md` is passed by path to sub-agents — do NOT read it in the main session.

## Translation Rules

| # | Python Construct | Verilog Mapping | Bug Pattern |
|---|---|---|---|
| 1 | `def module_name(params):` | `module module_name(...);` | - |
| 2 | Function parameter (old value) | `reg` read in `always @(posedge clk)` | - |
| 3 | Function return value (`# reg_next`) | `<=` non-blocking assignment | - |
| 4 | Function return value (`# wire`) | `output wire` + `assign` | Pattern 16 |
| 5 | Local variable (wire) | `wire` or combinational `always @*` | - |
| 6 | `if not calc_en: return params` | Hold registers unchanged | - |
| 7 | `A, B, C = new_A, old_A, ROL(old_B, 9)` | `A <= new_A; B <= A; C <= ROL(B, 9);` (NBA) | - |
| 8 | Bit-width masking (e.g., `& MASK32`) | Not needed (fixed-width arithmetic) | - |
| 9 | `ROL(x, n)` where n is **constant** | `{x[W-1-N:0], x[W-1:W-N]}` concatenation | Pattern 13 |
| 10 | `ROL(x, n)` where n is **variable** | log2(W)-stage barrel shifter (Rule R5) | Pattern 17 |
| 11 | `for cycle in range(TOTAL_CYCLES)` | FSM state machine with counter | - |
| 12 | DONE/finalize: `accum = init_val XOR result` | Use selected init value (NOT stale register) (Rule R3) | Pattern 18 |
| 13 | DONE/finalize: `state_flag` update | Update when `done_en`; use latched input | Pattern 12 |
| 14 | Variable assigned in `always @*` | Declare as `reg` (NOT `wire`) | Pattern 16 |
| 15 | Multi-block: output `valid` gating | Must gate with last-block flag | Pattern 14 |
| 16 | Cross-module `delay: 0, type: "wire"` | Consumer reads combinational output directly | Pattern 1 |
| 17 | Cross-module `delay: 1, type: "reg_next"` | Consumer reads registered output next cycle | Pattern 4 |
| 18 | `print_*_verilog()` output | Execute function, paste output verbatim (Rule R6) | Pattern 13 |

### Critical Rules (MUST verify before writing Verilog)

**Rule R1 — Wire-Output**: `# wire` → `output wire` + `assign`. MUST NOT be `output reg`.

**Rule R2 — timing_contract**: `delay: 0` → combinational path; `delay: 1` → registered output.

**Rule R3 — Init-Value Consistency**: Finalize uses `init_val` (the init-value selector), NOT `accum_reg` (stale storage).

**Rule R4 — Multi-Operation Test**: TEST_VECTORS MUST include > 1 block/operation.

**Rule R5 — Variable Rotation**: Variable `ROL(x, n)` → barrel shifter. NOT variable part-select.

**Rule R6 — Deterministic Constant Extraction**: When design_spec.py contains `print_*_verilog()`
functions OR when `workspace/docs/_extracted_constants.v` exists from Stage 1, constants MUST be
copied from these sources. NEVER manually retype constant values — LLMs cannot reliably
perform bitwise arithmetic on 32-bit hex values.

How to apply R6:
1. Check if `workspace/docs/_extracted_constants.v` exists. If yes, copy its content directly.
2. If not, execute: `$PYTHON_EXE -c "import sys; sys.path.insert(0, 'workspace/docs'); from design_spec import *; import inspect; [getattr(mod, n)() for n in dir() if n.startswith('print_') and n.endswith('_verilog') and callable(eval(n))]"` — or call each `print_*_verilog()` function individually.
3. Paste the stdout output verbatim into the Verilog module.
4. DO NOT recompute, reformat, or retyp any hex value.
5. **VERIFICATION**: After pasting, re-run the extraction command and `diff` against the pasted
   content. Any difference = transcription error. Fix by re-copying from extraction output.

**Rule R6a — Constant Cross-Check (mandatory)**: After all codegen agents finish, run:
```bash
$PYTHON_EXE -c "
import importlib.util, re, sys
sys.path.insert(0, 'workspace/docs')
spec = importlib.util.spec_from_file_location('ds', 'workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# Check if design has LUT/constant verification functions
for name in dir(mod):
    if name.startswith('verify_') or name.startswith('check_'):
        fn = getattr(mod, name)
        if callable(fn):
            fn()
print('[R6a] Constant cross-check passed')
"
```
If the golden model provides `verify_*()` or `check_*()` functions, they MUST pass before
proceeding to Stage 3. If no such functions exist, the diff-based verification (step 5) suffices.

**Rule R7 — FSM Control Signal Timing**: FSM control outputs (e.g., `load_en`,
`calc_en`, `hash_valid`, `round_en`, `tx_done`, etc. — use names from design_spec.py)
MUST match the golden model's timing model. Two valid patterns:

- **Pattern A — Combinational control** (default for `# wire` annotated signals):
  ```verilog
  // Example: combinational assigns derived from FSM state
  assign calc_en   = (state_reg == S_CALC) && !load_en_reg;
  assign data_valid = (state_reg == S_DONE);
  ```
  Use when the golden model's compute() sets these signals in the same cycle as the
  state transition.

- **Pattern B — Registered control** (for `# reg_next` annotated signals):
  ```verilog
  assign calc_en = calc_en_reg;  // 1-cycle delay from state transition
  ```
  Use ONLY when the golden model explicitly delays control signals.

**MIXING IS FORBIDDEN**: If the golden model uses Pattern A timing, the Verilog MUST NOT
use Pattern B. This is the #1 cause of round-counter offset bugs (Pattern 19).

### Verification Checklist (check after writing)
- [ ] Every output port matches its timing annotation (wire vs reg_next)
- [ ] No variable part-select (use barrel shifter)
- [ ] DONE state uses _reg values (not _new wires)
- [ ] Cross-module timing matches timing_contract
- [ ] All always-block signals declared as reg (NOT wire)
- [ ] Constants copied from _extracted_constants.v or print_*_verilog() output (Rule R6)
- [ ] FSM control signals match golden model timing (Rule R7)

## DSL Path (if build_*() functions exist)

```bash
HAS_DSL=$($PYTHON_EXE -c "
import importlib.util
spec = importlib.util.spec_from_file_location('ds', '$PROJECT_DIR/workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
has_build = any(k.startswith('build_') and callable(v) for k, v in vars(mod).items())
print('yes' if has_build else 'no')
" 2>/dev/null || echo "no")
```

If `yes`: import DSL, run `VerilogEmitter().emit(build_fn())` for each `build_*()`. No AI translation needed.
If `no`: use standard AI path below.

## Deterministic Constant Extraction (BEFORE AI translation)

Execute BEFORE dispatching codegen agents. This step produces correct Verilog
constant declarations that agents paste directly — no AI transcription.

```bash
cd "$PROJECT_DIR"

# Check if Stage 1 already extracted constants
if [ -f workspace/docs/_extracted_constants.v ]; then
    echo "[OK] Using pre-extracted constants from Stage 1"
    cat workspace/docs/_extracted_constants.v
else
    # Extract constants by executing print_*_verilog() functions
    $PYTHON_EXE -c "
import importlib.util, sys, inspect, io, builtins

sys.path.insert(0, 'workspace/docs')
spec = importlib.util.spec_from_file_location('ds', 'workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

output_parts = []
for name, obj in inspect.getmembers(mod, inspect.isfunction):
    if name.startswith('print_') and name.endswith('_verilog'):
        buf = io.StringIO()
        _orig = builtins.print
        builtins.print = lambda *a, **kw: print(*a, file=buf, **kw)
        try:
            obj()
        finally:
            builtins.print = _orig
        output_parts.append(f'// === {name}() ===')
        output_parts.append(buf.getvalue())

if output_parts:
    print('\n'.join(output_parts))
else:
    print('// No print_*_verilog() functions found')
" 2>&1 | tee workspace/docs/_extracted_constants.v
fi
```

## Port Skeleton Extraction (Interface Lock)

Before dispatching codegen agents, extract the port/interface contract from
design_spec.py Section 1 and Section 5. This is the **single source of truth**
for all Verilog module ports.

```bash
$PYTHON_EXE -c "
import importlib.util, json, re, sys, inspect
spec = importlib.util.spec_from_file_location('ds', '$PROJECT_DIR/workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Extract DESIGN_NAME
print(f'DESIGN_NAME: {mod.DESIGN_NAME}')

# Dynamically detect Section 5 pseudocode functions:
# They contain timing_contract / # wire / # reg_next annotations.
# All other non-standard functions are S3/S4 helpers — not modules.
for name, fn in inspect.getmembers(mod, inspect.isfunction):
    if name.startswith('_') or name in ('compute', 'run', 'get_test_vectors'):
        continue
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        continue
    if 'timing_contract' in src or '# wire' in src or '# reg_next' in src:
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        print(f'MODULE: {name}({', '.join(params)})')
" 2>&1 | tee workspace/docs/module_contract.txt
```

## Standard AI Translation Path

### Phase 1: Generate Leaf Modules (parallel)

Determine module dependency order from MODULE_HIERARCHY:
- **Leaf modules**: NOT listed as keys in MODULE_HIERARCHY (or have empty submodules list). These have no user-defined sub-instances.
- **Parent modules**: Listed as keys in MODULE_HIERARCHY with non-empty submodules.

If MODULE_HIERARCHY is not defined in design_spec.py, fall back to generating ALL modules in parallel (legacy behavior).

Dispatch **leaf module agents** in parallel:


- **One vf-coder per module** (subagent_type: general-purpose)
  - **AGENT DISCIPLINE**: You are a sub-agent under [S2]. Create one subtask per module with prefix [S2.rtl] (e.g. `[S2.rtl] Codegen <module_name>`), set addBlockedBy to PARENT_TASK_ID. Mark in_progress when starting, completed when done. DO NOT create tasks for other stages.
  - **OUTPUT DISCIPLINE**: On completion, report ONLY: Status (SUCCESS/FAIL), Files written (paths), Summary (1-2 sentences), Errors (only if FAIL). DO NOT output file contents in your response.
  - Prompt includes: PARENT_TASK_ID, MODULE_NAME, OUTPUT_FILE, design_spec.py **path** (agent reads it itself), `coding_style_core.md` **path** (agent reads it itself)
  - **HARD CONSTRAINT**: The module port list MUST match design_spec.py Section 1
    (for top module) or Section 5 function parameters (for submodules).
    Port names and widths are FROZEN — do NOT rename or resize.
  - **CONSTANT CONSTRAINT (Rule R6)**: For any LUT/ROM/constant in the module,
    read `workspace/docs/_extracted_constants.v` and copy the relevant section
    verbatim. DO NOT recompute constant values.
  - **FSM CONSTRAINT (Rule R7)**: FSM control signals MUST match the timing
    model used in design_spec.py's compute() function. Read compute() carefully
    to determine whether control signals are combinational or registered.
  - 5-section structured prompt: TRANSLATION RULES → TIMING CONTRACT → CRITICAL RULES → VERIFICATION CHECKLIST → CODE

- **One vf-tb-gen** (subagent_type: general-purpose)
  - **AGENT DISCIPLINE**: You are a sub-agent under [S2]. Create one subtask `[S2.tb] Generate testbenches`, set addBlockedBy to PARENT_TASK_ID. Mark in_progress when starting, completed when done. DO NOT create tasks for other stages.
  - **OUTPUT DISCIPLINE**: On completion, report ONLY: Status (SUCCESS/FAIL), Files written (paths), Summary (1-2 sentences), Errors (only if FAIL). DO NOT output file contents in your response.
  - Prompt includes: PARENT_TASK_ID, PROJECT_DIR, DESIGN_NAME, design_spec.py **path** (agent reads it itself), COCOTB_AVAILABLE flag, templates path

After Phase 1 agents return, verify leaf module outputs:
```bash
ls "$PROJECT_DIR/workspace/rtl/"*.v 2>/dev/null
```

### Phase 1.5: Extract Ports + Generate Instance Code

After leaf modules are generated, extract their actual Verilog port
declarations and generate instance code from MODULE_HIERARCHY:

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh"
cd "$PROJECT_DIR"

# Step A: Check MODULE_HIERARCHY consistency
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/hierarchy_check.py" workspace/docs/design_spec.py --verbose

# Step B: Extract actual ports from generated Verilog
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/instance_gen.py" \
    --rtl-dir workspace/rtl \
    --extract > workspace/docs/module_ports.json

# Step C: Generate instance code from MODULE_HIERARCHY
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/instance_gen.py" \
    --rtl-dir workspace/rtl \
    --hierarchy workspace/docs/design_spec.py \
    --code > workspace/docs/instance_code.v
```

If MODULE_HIERARCHY does not exist, generate templates instead:
```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/instance_gen.py" \
    --rtl-dir workspace/rtl \
    --template > workspace/docs/instance_templates.v
```

### Phase 2: Generate Parent Modules (with instance code injected)

Dispatch **parent module agents** — each agent receives the auto-generated
instance code as part of its prompt:

- **One vf-coder per parent module** (subagent_type: general-purpose)
  - Same constraints as Phase 1 agents, PLUS:
  - **INSTANCE CONSTRAINT**: Copy the instance code from `workspace/docs/instance_code.v`
    (or `instance_templates.v` if MODULE_HIERARCHY is absent) into the module's
    Verilog file. DO NOT rewrite instance port connections from memory.
    The instance code is auto-generated from actual Verilog port declarations —
    it is guaranteed correct. Only fill in signal names if using templates.
  - Prompt MUST include the contents of `instance_code.v` (or `instance_templates.v`)
    in the INSTANTIATION section.

After Phase 2 agents return, verify all outputs:
```bash
ls "$PROJECT_DIR/workspace/rtl/"*.v "$PROJECT_DIR/workspace/tb/"*.v "$PROJECT_DIR/workspace/tb/"*.py 2>/dev/null
```

### Fallback: No MODULE_HIERARCHY (Legacy Parallel Mode)

If MODULE_HIERARCHY is not defined in design_spec.py, fall back to the
original parallel generation mode: dispatch ALL module agents simultaneously,
each receiving instance templates (not code) from `instance_gen.py --template`.
This is less reliable but maintains backward compatibility.

## Port Consistency Validation (Post-Agent Check)

After all codegen agents return, verify that the generated Verilog module ports
match the design_spec.py interface definition:

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE -c "
import re, sys

with open('workspace/docs/design_spec.py', 'r') as f:
    spec_content = f.read()

spec_ports = set()
for m in re.finditer(r'(input|output)\s+wire\s+(?:\[\d+:\d+\]\s+)?(\w+)', spec_content):
    spec_ports.add(m.group(2))

import glob
errors = []
for vfile in sorted(glob.glob('workspace/rtl/*.v')):
    with open(vfile, 'r') as f:
        vcontent = f.read()
    mod_match = re.search(r'module\s+(\w+)', vcontent)
    if not mod_match:
        errors.append(f'{vfile}: no module declaration found')
        continue
    mod_name = mod_match.group(1)

    design_name = re.search(r'DESIGN_NAME\s*=\s*['\"](\\w+)['\"]', spec_content)
    if design_name and mod_name == design_name.group(1):
        verilog_ports = set()
        for m in re.finditer(r'(input|output)\s+wire\s+(?:\[\d+:\d+\]\s+)?(\w+)', vcontent):
            verilog_ports.add(m.group(2))
        verilog_ports -= {'clk', 'rst'}
        spec_no_clk = spec_ports - {'clk', 'rst'}
        missing = spec_no_clk - verilog_ports
        extra = verilog_ports - spec_no_clk
        if missing:
            errors.append(f'{vfile} ({mod_name}): missing ports from spec: {missing}')
        if extra:
            errors.append(f'{vfile} ({mod_name}): extra ports not in spec: {extra}')

if errors:
    print('[FAIL] Port consistency check:')
    for e in errors:
        print(f'  - {e}')
    sys.exit(1)
else:
    print('[PASS] Port consistency OK')
"
```

If port consistency fails, the offending module must be fixed before proceeding.

## Automated RTL Verification (Post-Agent Check)

Run `rtl_checker.py` to catch port mismatches, reset strategy violations,
output reg violations, and testbench race conditions:

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh"
cd "$PROJECT_DIR"

# Detect reset strategy from design_spec.py
RESET_STRATEGY=$($PYTHON_EXE -c "
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location('ds', 'workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
content = open('workspace/docs/design_spec.py').read()
m = re.search(r'Reset strategy:\s*(\w+)', content)
print(m.group(1) if m else 'synchronous')
" 2>/dev/null || echo "synchronous")

$PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/rtl_checker.py" \
    --rtl-dir workspace/rtl \
    --tb-dir workspace/tb \
    --reset-strategy "$RESET_STRATEGY" \
    --verbose
```

If errors found, fix before proceeding to Stage 3.

## Syntax Verification (before Stage 3)

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh"
cd "$PROJECT_DIR"
$PYTHON_EXE -c "
import subprocess, sys, glob
errors = 0
for vfile in sorted(glob.glob('workspace/rtl/*.v')):
    r = subprocess.run(['iverilog', '-g2005', '-t', 'null', vfile],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        errors += 1
    else:
        print(f'[SYNTAX] {vfile}: OK')
if errors:
    print(f'[FATAL] {errors} RTL file(s) have syntax errors.')
    sys.exit(1)
"
```

## Post-stage

```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "codegen" --hook="$PYTHON_EXE -c \"import glob,sys; sys.exit(0 if glob.glob('workspace/rtl/*.v') and (glob.glob('workspace/tb/test_*.py') or glob.glob('workspace/tb/tb_*.v')) else 1)\"" --journal-outputs="workspace/rtl/*.v, workspace/tb/test_*.py, workspace/tb/tb_*.v" --journal-notes="RTL translated from design_spec.py"
```

Update pipeline context:
```bash
cd "$PROJECT_DIR"
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/pipeline_context.py" "$PROJECT_DIR" --set-multi "$(cat <<'JSON'
{"rtl_ready": true, "rtl_files": [$(ls workspace/rtl/*.v | sed 's/.*/"&"/' | paste -sd,)]}
JSON
)"
```

Write stage summary:
```bash
cat >> "$PROJECT_DIR/.veriflow/stage_summaries.md" << 'EOF'

## Stage 2 — codegen
- Output: workspace/rtl/*.v, workspace/tb/*.{v,py}
- Port consistency: verified
- Syntax check: passed
EOF
```

TaskUpdate complete.
