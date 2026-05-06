# Stage 2: codegen

Translate Python functions to Verilog modules + testbench.

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "codegen" --start
```

## Read inputs

Read design_spec.py and `${CLAUDE_SKILL_DIR}/docs/coding_style_core.md` (parallel Read calls).

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
| 8 | `MASK32` and arithmetic masking | Not needed (fixed-width arithmetic) | - |
| 9 | `ROL(x, n)` where n is **constant** | `{x[W-1-N:0], x[W-1:W-N]}` concatenation | Pattern 13 |
| 10 | `ROL(x, n)` where n is **variable** | log2(W)-stage barrel shifter (Rule R5) | Pattern 17 |
| 11 | `for cycle in range(TOTAL_CYCLES)` | FSM state machine with counter | - |
| 12 | DONE/finalize: `accum = init_val XOR result` | Use selected init value (NOT stale register) (Rule R3) | Pattern 18 |
| 13 | DONE/finalize: `state_flag` update | Update when `done_en`; use latched input | Pattern 12 |
| 14 | Variable assigned in `always @*` | Declare as `reg` (NOT `wire`) | Pattern 16 |
| 15 | Multi-block: output `valid` gating | Must gate with last-block flag | Pattern 14 |
| 16 | Cross-module `delay: 0, type: "wire"` | Consumer reads combinational output directly | Pattern 1 |
| 17 | Cross-module `delay: 1, type: "reg_next"` | Consumer reads registered output next cycle | Pattern 4 |

### Critical Rules (MUST verify before writing Verilog)

**Rule R1 — Wire-Output**: `# wire` → `output wire` + `assign`. MUST NOT be `output reg`.

**Rule R2 — timing_contract**: `delay: 0` → combinational path; `delay: 1` → registered output.

**Rule R3 — Init-Value Consistency**: Finalize uses `init_val` (the init-value selector), NOT `accum_reg` (stale storage).

**Rule R4 — Multi-Operation Test**: TEST_VECTORS MUST include > 1 block/operation.

**Rule R5 — Variable Rotation**: Variable `ROL(x, n)` → barrel shifter. NOT variable part-select.

### Verification Checklist (check after writing)
- [ ] Every output port matches its timing annotation (wire vs reg_next)
- [ ] No variable part-select (use barrel shifter)
- [ ] DONE state uses _reg values (not _new wires)
- [ ] Cross-module timing matches timing_contract
- [ ] All always-block signals declared as reg (NOT wire)

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

## Port Skeleton Extraction (Interface Lock)

Before dispatching codegen agents, extract the port/interface contract from
design_spec.py Section 1 and Section 5. This is the **single source of truth**
for all Verilog module ports.

```bash
$PYTHON_EXE -c "
import importlib.util, json, re, sys
spec = importlib.util.spec_from_file_location('ds', '$PROJECT_DIR/workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Extract DESIGN_NAME
print(f'DESIGN_NAME: {mod.DESIGN_NAME}')

# Extract module names from Section 5 functions
import inspect
funcs = [(name, obj) for name, obj in inspect.getmembers(mod, inspect.isfunction)
         if not name.startswith('_') and name not in ('compute', 'run', 'get_test_vectors',
            'ROL', 'print_lut_verilog', 'print_wide_const_verilog')]
for name, func in funcs:
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    print(f'MODULE: {name}({', '.join(params)})')
" 2>&1 | tee workspace/docs/module_contract.txt
```

## Standard AI Translation Path

Dispatch ALL agents in parallel:

- **One vf-coder per module** (subagent_type: general-purpose)
  - Prompt includes: MODULE_NAME, OUTPUT_FILE, design_spec.py content, coding_style_core.md content
  - **HARD CONSTRAINT**: The module port list MUST match design_spec.py Section 1
    (for top module) or Section 5 function parameters (for submodules).
    Port names and widths are FROZEN — do NOT rename or resize.
  - 5-section structured prompt: TRANSLATION RULES → TIMING CONTRACT → CRITICAL RULES → VERIFICATION CHECKLIST → CODE

- **One vf-tb-gen** (subagent_type: general-purpose)
  - Prompt includes: PROJECT_DIR, DESIGN_NAME, design_spec.py content, COCOTB_AVAILABLE flag, templates path

After ALL return, verify outputs:
```bash
ls "$PROJECT_DIR/workspace/rtl/"*.v "$PROJECT_DIR/workspace/tb/"*.v "$PROJECT_DIR/workspace/tb/"*.py 2>/dev/null
```

## Port Consistency Validation (Post-Agent Check)

After all codegen agents return, verify that the generated Verilog module ports
match the design_spec.py interface definition:

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE -c "
import re, sys

# Parse design_spec.py for module interface comments
# Look for Section 1 port declarations
with open('workspace/docs/design_spec.py', 'r') as f:
    spec_content = f.read()

# Extract port names from spec (comment lines like 'input  wire [511:0] msg_block')
spec_ports = set()
for m in re.finditer(r'(input|output)\s+wire\s+(?:\[\d+:\d+\]\s+)?(\w+)', spec_content):
    spec_ports.add(m.group(2))

# Parse each generated Verilog file for its port list
import glob
errors = []
for vfile in sorted(glob.glob('workspace/rtl/*.v')):
    with open(vfile, 'r') as f:
        vcontent = f.read()
    # Extract module name
    mod_match = re.search(r'module\s+(\w+)', vcontent)
    if not mod_match:
        errors.append(f'{vfile}: no module declaration found')
        continue
    mod_name = mod_match.group(1)

    # For the top-level module, check port names match spec
    # (Skip submodules — their ports come from Section 5 pseudocode)
    design_name = re.search(r'DESIGN_NAME\s*=\s*['\'](\\w+)['\']', spec_content)
    if design_name and mod_name == design_name.group(1):
        verilog_ports = set()
        for m in re.finditer(r'(input|output)\s+wire\s+(?:\[\d+:\d+\]\s+)?(\w+)', vcontent):
            verilog_ports.add(m.group(2))
        # clk and rst are always present but may not be in spec comments
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

TaskUpdate complete.
