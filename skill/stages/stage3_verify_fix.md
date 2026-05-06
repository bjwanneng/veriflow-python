# Stage 3: verify_fix (inline — main session)

**IMPORTANT**: This stage runs inline because error recovery needs main session context for Edit tool.

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "verify_fix" --start
```

## Golden Model Validation (BEFORE simulation)

Verify the reference model is correct before comparing against RTL.
If the golden model itself has bugs, any RTL comparison is meaningless.

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh"
cd "$PROJECT_DIR"
GOLDEN_MODEL=$($PYTHON_EXE -c "import os; print('workspace/docs/design_spec.py' if os.path.isfile('workspace/docs/design_spec.py') else '')")
if [ -n "$GOLDEN_MODEL" ]; then
    $PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/iverilog_runner.py" \
        --golden-check "$GOLDEN_MODEL" --verbose > logs/golden_selfcheck.log 2>&1
    echo "Golden check: $(tail -1 logs/golden_selfcheck.log)"
    if [ $? -ne 0 ]; then
        echo "[FATAL] Golden model self-check FAILED — fix design_spec.py FIRST."
    fi
fi
```

## DSL Cycle Trace (if build_*() functions available)

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE -c "
import importlib.util, sys
sys.path.insert(0, '${CLAUDE_SKILL_DIR}')
spec = importlib.util.spec_from_file_location('ds', 'workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
builders = [v for k, v in vars(mod).items() if k.startswith('build_') and callable(v)]
if builders:
    from dsl import CycleSimulator
    for b in builders:
        m = b()
        sim = CycleSimulator(m)
        trace = sim.run(10)
        for i, entry in enumerate(trace[:5]):
            parts = [f'{k}=0x{v:08x}' if v > 0xFFFF else f'{k}={v}' for k, v in sorted(entry.items())]
            print(f'DSL cycle {i}: {\" \".join(parts)}')
    print('[DSL] Cycle trace generated')
else:
    print('[DSL] No build_*() functions found, skipping')
" 2>&1 > logs/dsl_trace.log
echo "DSL: $(tail -1 logs/dsl_trace.log)"
```

## Golden Model Trace Extraction (for AI-translated designs)

Extract per-cycle golden model traces for automated A/B/D comparison during
failure diagnosis. Uses the golden_loader infrastructure.

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE -c "
import importlib.util, json, sys
from pathlib import Path

spec_path = 'workspace/docs/design_spec.py'
if not Path(spec_path).exists():
    print('[SKIP] No design_spec.py found')
    sys.exit(0)

sys.path.insert(0, '${CLAUDE_SKILL_DIR}')
from agent.golden_loader import load_golden_cycles

traces_found = 0
for tv_idx in range(4):  # up to 4 test vectors
    cycles = load_golden_cycles(spec_path, test_vector_index=tv_idx, verbose=False)
    if cycles is None:
        if tv_idx == 0:
            print('[GOLDEN] No per-cycle traces available from golden model')
        break
    trace_file = f'workspace/docs/golden_trace_tv{tv_idx}.json'
    serializable = {str(k): v for k, v in cycles.items()}
    with open(trace_file, 'w') as f:
        json.dump(serializable, f, indent=2)
    print(f'[GOLDEN] TV{tv_idx}: {len(cycles)} cycles -> {trace_file}')
    traces_found += 1

if traces_found == 0:
    print('[GOLDEN] Golden model does not produce per-cycle traces.')
    print('[GOLDEN] Failure classification will use value-based heuristics only.')
else:
    print(f'[GOLDEN] {traces_found} trace(s) extracted for A/B/D classification')
" 2>&1 > logs/golden_trace.log
echo "Golden: $(tail -1 logs/golden_trace.log)"
```

## RTL Pre-Flight Checks (BEFORE simulation)

Automatic checks for common bugs. Checks are **conditional on design category**
(read from `pipeline_context.json`) — hash-specific checks only run for hash designs.

```bash
cd "$PROJECT_DIR"
DESIGN_CATEGORY=$($PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/pipeline_context.py" "$PROJECT_DIR" --get design_category 2>/dev/null || echo "unknown")
echo "[PREFLIGHT] Design category: $DESIGN_CATEGORY"

$PYTHON_EXE -c "
import re, sys, glob

category = '$DESIGN_CATEGORY'
errors = []
warnings = []

for vfile in sorted(glob.glob('workspace/rtl/*.v')):
    with open(vfile, 'r') as f:
        content = f.read()

    mod_match = re.search(r'module\s+(\w+)', content)
    if not mod_match:
        continue
    mod_name = mod_match.group(1)

    posedge_blocks = re.findall(r'always\s*@\(posedge.*?\bend\b', content, re.DOTALL)

    # === Check P1 (hash): Chaining registers MUST reset to IV (Pattern 3/11) ===
    if category == 'hash':
        iv_defs = dict(re.findall(r'(IV_\d+)\s*=\s*32\'h([0-9a-fA-F]+)', content))
        v_resets = dict(re.findall(r'(V_\d+_reg)\s*<=\s*(\d+|32\'h[0-9a-fA-F]+|IV_\d+)', content))
        for reg_name, reset_val in v_resets.items():
            if reset_val in ('0', '32\'h0', \"32'd0\"):
                if iv_defs:
                    errors.append(f'{vfile}: {reg_name} resets to 0 but IV constants exist '
                                f'→ should reset to IV (Pattern 3/11)')

    # === Check P2 (hash): is_first register reset (Merkle-Damgard) ===
    if category == 'hash':
        is_first_reset = re.findall(r'is_first\S*\s*<=\s*(\S+)', content)
        if is_first_reset:
            in_rst_block = re.findall(r'if\s*\(\s*rst\s*\)(.*?)always', content, re.DOTALL)
            if in_rst_block:
                rst_block = in_rst_block[0]
                if 'is_first' in rst_block and \"1'b0\" in rst_block.split('is_first')[1][:50]:
                    warnings.append(f'{vfile}: is_first_reg resets to 0 in rst block '
                                  f'→ first block after reset should be is_first=1')

    # === Check P3 (all categories): FSM control outputs should be combinational (Pattern 19) ===
    has_fsm = 'state_reg' in content or 'cs' in content
    if has_fsm:
        # Detect control signals dynamically: any signal named *_en, *_valid, *_done, *_ready
        # that appears in the module
        all_sigs = set(re.findall(r'\b(\w*_en|\w*_valid|\w*_done|\w*_ready)\b', content))
        for sig in all_sigs:
            if sig in ('reset_en',):
                continue
            for block in posedge_blocks:
                if re.search(rf'{sig}\s*<=', block):
                    warnings.append(f'{vfile}: {sig} is registered (assigned in '
                                  f'posedge block) — should be combinational '
                                  f'assign (Pattern 19)')

    # === Check P4 (all categories): Finalize / data output must be combinational ===
    # Detect output signals dynamically from port declarations
    output_sigs = re.findall(r'output\s+wire\s+(?:\[\d+:\d+\]\s+)?(\w+)', content)
    finalize_sigs = [s for s in output_sigs if any(kw in s for kw in
                     ('_out', '_result', '_data', '_hash', '_digest', '_cipher', '_product'))]
    for sig in finalize_sigs:
        for block in posedge_blocks:
            if re.search(rf'{sig}\s*<=', block):
                errors.append(f'{vfile}: {sig} assigned in posedge block — '
                            f'finalize/data outputs must be combinational (mux with '
                            f'done/valid signal)')

    # === Check P5 (hash): Duplicate LUT constants ===
    if category == 'hash':
        lut_names = set(re.findall(r'(T_ROT_LUT_\d+)\s*=', content))
        lut_values = {}
        for name in lut_names:
            vals = re.findall(rf'{name}\s*=\s*32\'h([0-9a-fA-F]+)', content)
            if vals:
                lut_values[name] = vals[0]
        # Check for duplicate values among different LUT names
        seen = {}
        for name, val in lut_values.items():
            if val in seen:
                errors.append(f'{vfile}: {name} == {seen[val]} (duplicate constants)')

# Report
for w in warnings:
    print(f'[WARN] {w}')
for e in errors:
    print(f'[ERROR] {e}')

if errors:
    print(f'\\n[PREFLIGHT] {len(errors)} error(s) found — fix before simulation')
    sys.exit(1)
elif warnings:
    print(f'\\n[PREFLIGHT] {len(warnings)} warning(s) — review recommended but not blocking')
else:
    print('[PREFLIGHT] All checks passed')
" 2>&1 > logs/preflight.log
cat logs/preflight.log | grep -E '\[(ERROR|WARN|PREFLIGHT)\]' || echo "[PREFLIGHT] All checks passed"
```

If preflight finds errors, fix them BEFORE running simulation. Warnings should be
reviewed but are not blocking.

## Cocotb per-cycle verification (if available)

```bash
if $PYTHON_EXE -c "import cocotb" 2>/dev/null; then
    cd "$PROJECT_DIR"
    TOP_MODULE=$($PYTHON_EXE -c "
import importlib.util, os
spec = importlib.util.spec_from_file_location('ds', 'workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.__dict__.get('DESIGN_NAME', os.path.basename(os.getcwd())))
" 2>/dev/null || $PYTHON_EXE -c "import os; print(os.path.basename(os.getcwd()))")
    $PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/cocotb_runner.py" \
        --rtl-dir workspace/rtl --tb-dir workspace/tb \
        --module $TOP_MODULE --build-dir workspace/sim \
        --verbose > logs/cocotb.log 2>&1
    echo "Cocotb: $(tail -1 logs/cocotb.log)"
fi
```

## Run simulation

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh"
cd "$PROJECT_DIR"
TOP_MODULE=$($PYTHON_EXE -c "
import importlib.util, os
spec = importlib.util.spec_from_file_location('ds', 'workspace/docs/design_spec.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.__dict__.get('DESIGN_NAME', os.path.basename(os.getcwd())))
" 2>/dev/null || $PYTHON_EXE -c "import os; print(os.path.basename(os.getcwd()))")
VERILOG_TB=$($PYTHON_EXE -c "import glob; t=sorted(glob.glob('workspace/tb/tb_*.v')); print(t[0] if t else '')")
GOLDEN_MODEL=$($PYTHON_EXE -c "import os; print('workspace/docs/design_spec.py' if os.path.isfile('workspace/docs/design_spec.py') else '')")
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/agent/iverilog_runner.py" \
    --module $TOP_MODULE --rtl-dir workspace/rtl --tb-file "$VERILOG_TB" \
    --build-dir workspace/sim --golden-model "$GOLDEN_MODEL" \
    --save-raw-log logs/sim_raw.log \
    --verbose > logs/sim.log 2>&1
echo "Simulation: $(tail -1 logs/sim.log)"
```

## If PASS

```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "verify_fix" --hook="$PYTHON_EXE -c \"import sys; sys.exit(0 if 'ALL TESTS PASSED' in open('logs/sim.log').read() else 1)\"" --journal-outputs="logs/sim.log" --journal-notes="Simulation passed"
```

Update pipeline context:
```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/pipeline_context.py" "$PROJECT_DIR" --set sim_passed true
```

Write stage summary:
```bash
cat >> "$PROJECT_DIR/.veriflow/stage_summaries.md" << 'EOF'

## Stage 3 — verify_fix
- Simulation: ALL TESTS PASSED
- Logs: logs/sim.log
EOF
```

TaskUpdate complete. Go to Stage 4.

## If FAIL — Automated Bug Classification

The iverilog_runner produces structured failure classification using the golden
model. Read the JSON output from `logs/sim.log` to get:

- **first_fail_cycle**: The cycle number where the first divergence occurs
- **failures[].classification**: A/B/D type for each failure
- **failures[].reasoning**: Human-readable explanation

### Classification → Bug Pattern Mapping

Use the classification to narrow down bug patterns:

| Classification | Meaning | Most Likely Patterns | Check First |
|---|---|---|---|
| **D** (Init) | RTL value is 0/x/z, expected non-zero | 3, 5, 16, 18 | Register initialization, wire vs reg_next |
| **B** (Timing) | Correct value at wrong cycle | 4, 12, 15, 19 | FSM control timing, pipeline alignment |
| **A** (Logic) | Wrong value, no timing match | 2, 8, 10, 13, 17 | Datapath formula, ROL, concatenation width |

### Step-by-Step Failure Diagnosis

1. **Read classification results**: Extract first failure's classification and reasoning
   ```bash
   $PYTHON_EXE -c "
   import json, sys
   for line in open('logs/sim.log'):
       line = line.strip()
       if line.startswith('{'):
           try:
               data = json.loads(line)
               if 'failures' in data:
                   print(f'First fail cycle: {data.get(\"first_fail_cycle\", \"N/A\")}')
                   for f in data['failures'][:3]:
                       cls = f.get('classification', '?')
                       reason = f.get('reasoning', '')
                       sig = f.get('signal', '?')
                       exp = f.get('expected', '?')
                       act = f.get('actual', '?')
                       cyc = f.get('cycle', '?')
                       print(f'  [{cls}] cycle={cyc} signal={sig} expected={exp} actual={act}')
                       print(f'       {reason}')
                   break
           except json.JSONDecodeError:
               pass
   "
   ```

2. **Read bug patterns (lazy-load)**: Based on classification, extract ONLY
   the relevant patterns — do NOT read the entire bug_patterns.md:
   ```bash
   # Determine pattern numbers from classification
   CLASSIFICATION=$($PYTHON_EXE -c "
   import json, sys
   for line in open('logs/sim.log'):
       line = line.strip()
       if line.startswith('{'):
           try:
               data = json.loads(line)
               if 'failures' in data and data['failures']:
                   print(data['failures'][0].get('classification', 'A'))
                   break
           except: pass
   " 2>/dev/null || echo "A")

   PATTERN_NUMS=$($PYTHON_EXE -c "
   m = {'D': '3 5 16 18', 'B': '4 12 15 19', 'A': '2 8 10 13 17'}
   print(m.get('$CLASSIFICATION', '2 8 10'))
   ")

   # Extract only matching patterns from bug_patterns_index.md
   for p in $PATTERN_NUMS; do
       echo "=== Pattern $p ==="
       sed -n "/### Pattern $p/,/^### Pattern/p" "${CLAUDE_SKILL_DIR}/docs/bug_patterns_index.md" | head -30
   done

   # Then read full detail for the TOP pattern only
   TOP_PATTERN=$(echo $PATTERN_NUMS | cut -d' ' -f1)
   sed -n "/## Pattern $TOP_PATTERN:/,/^## Pattern/p" "${CLAUDE_SKILL_DIR}/docs/bug_patterns.md" | head -80
   ```

3. **Read error recovery (summary only)**: Extract the key steps without
   reading the full document:
   ```bash
   head -20 "${CLAUDE_SKILL_DIR}/docs/error_recovery.md"
   ```

4. **Collect data**: Extract relevant portions of `logs/sim.log` (do NOT read
   the entire file):
   ```bash
   # Show only failure-related lines
   grep -E '(FAIL|Error|mismatch|expected|actual|cycle)' logs/sim.log | head -30
   ```
   Check VCD waveform if available, examine the RTL module at the failure point.

5. **5-point root cause analysis** → write to `stage_journal.md`:
   - What signal diverged first?
   - What classification (A/B/D)?
   - What bug pattern matches?
   - What is the root cause (specific code line)?
   - What is the fix?

6. **Fix RTL** using Edit tool

7. **Port consistency check** (ensure fix didn't break interface):
   ```bash
   cd "$PROJECT_DIR"
   $PYTHON_EXE -c "
   import re, sys, glob

   with open('workspace/docs/design_spec.py', 'r') as f:
       spec_content = f.read()
   spec_ports = set()
   for m in re.finditer(r'(input|output)\s+wire\s+(?:\[\d+:\d+\]\s+)?(\w+)', spec_content):
       spec_ports.add(m.group(2))

   design_name_match = re.search(r\"DESIGN_NAME\s*=\s*['\\\"](\\w+)['\\\"]\" , spec_content)
   if not design_name_match:
       print('[SKIP] Cannot determine DESIGN_NAME for port check')
       sys.exit(0)

   top_module = design_name_match.group(1)
   top_file = f'workspace/rtl/{top_module}.v'
   if not __import__('os').path.isfile(top_file):
       # Try finding it with a different naming convention
       candidates = glob.glob(f'workspace/rtl/*.v')
       top_file = None
       for c in candidates:
           with open(c, 'r') as f:
               if f'module {top_module}' in f.read():
                   top_file = c
                   break
       if not top_file:
           print('[SKIP] Top module file not found')
           sys.exit(0)

   with open(top_file, 'r') as f:
       vcontent = f.read()
   verilog_ports = set()
   for m in re.finditer(r'(input|output)\s+wire\s+(?:\[\d+:\d+\]\s+)?(\w+)', vcontent):
       verilog_ports.add(m.group(2))
   verilog_ports -= {'clk', 'rst'}
   spec_ports -= {'clk', 'rst'}

   missing = spec_ports - verilog_ports
   extra = verilog_ports - spec_ports
   if missing or extra:
       print(f'[WARN] Port drift detected after fix:')
       if missing: print(f'  Missing from Verilog: {missing}')
       if extra: print(f'  Extra in Verilog: {extra}')
       print('  If intentional, proceed. If accidental, revert the port change.')
   else:
       print('[OK] Port consistency maintained after fix')
   "
   ```

8. **Re-run simulation** (go back to "Run simulation")

9. **Retry budget**: 3 attempts total
   - 1st fail: fix RTL, retry
   - 2nd fail: `$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" --reset codegen`, restart Stage 2
   - 3rd fail: STOP, notify user
