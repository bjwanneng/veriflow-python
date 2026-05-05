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
7. **Re-run simulation** (go back to "Run simulation")
8. **Retry budget**: 3 attempts total
   - 1st fail: fix RTL, retry
   - 2nd fail: `$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" --reset codegen`, restart Stage 2
   - 3rd fail: STOP, notify user
