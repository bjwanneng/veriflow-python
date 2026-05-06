# Stage 3: verify_fix (inline — main session)

**IMPORTANT**: This stage runs inline because error recovery needs main session context for Edit tool.

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "verify_fix" --start
```

## Design Spec Self-Check (BEFORE simulation)

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh"
cd "$PROJECT_DIR"
if [ -f workspace/docs/design_spec.py ]; then
    $PYTHON_EXE workspace/docs/design_spec.py 2>&1 | tee logs/design_spec_selfcheck.log
    if $PYTHON_EXE -c "import sys; sys.exit(1 if '[FAIL]' in open('logs/design_spec_selfcheck.log').read() else 0)"; then
        echo "[DESIGN_SPEC] Self-check FAILED — fix reference model FIRST."
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
" 2>&1 | tee logs/dsl_trace.log
```

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
        --verbose 2>&1 | tee logs/cocotb.log
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
    --verbose 2>&1 | tee logs/sim.log
```

## If PASS

```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "verify_fix" --hook="$PYTHON_EXE -c \"import sys; sys.exit(0 if 'ALL TESTS PASSED' in open('logs/sim.log').read() else 1)\"" --journal-outputs="logs/sim.log" --journal-notes="Simulation passed"
```

TaskUpdate complete. Go to Stage 4.

## If FAIL

1. **Read** `${CLAUDE_SKILL_DIR}/docs/bug_patterns_index.md` — match symptoms
2. **Read** the matching pattern detail from `${CLAUDE_SKILL_DIR}/docs/bug_patterns.md`
3. **Read** `${CLAUDE_SKILL_DIR}/docs/error_recovery.md` — follow full procedure
4. **Collect data**: read `logs/sim.log`, run vcd2table diff, classify bug type
5. **5-point root cause analysis** → write to `stage_journal.md`
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
