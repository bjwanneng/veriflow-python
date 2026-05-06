# Stage 4: lint_synth

## Pre-check

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh"
cd "$PROJECT_DIR"
$PYTHON_EXE -c "
import os, glob, sys
missing = 0
rtl_files = glob.glob('workspace/rtl/*.v')
if not rtl_files:
    print('[FATAL] No RTL files found'); missing += 1
if not os.path.isfile('workspace/docs/design_spec.py'):
    print('[FATAL] Missing design_spec.py'); missing += 1
if missing:
    sys.exit(1)
"
if [ $? -ne 0 ]; then
    $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" --reset codegen
fi
$PYTHON_EXE -c "import os, sys; yosys=os.environ.get('YOSYS_EXE',''); sys.exit(0 if yosys and os.path.isfile(yosys) else 1)" || echo "[WARN] Yosys not found — synthesis skipped, lint only."
```

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "lint_synth" --start
```

## Execute

Dispatch 2 parallel agents:

- **vf-linter** (subagent_type: general-purpose)
  - **OUTPUT DISCIPLINE**: On completion, report ONLY: Status (SUCCESS/FAIL), Files written (paths), Summary (1-2 sentences), Errors (only if FAIL). DO NOT output file contents in your response.
  - Include: PROJECT_DIR, EDA_ENV path, PYTHON_EXE, SKILL_DIR

- **vf-synthesizer** (subagent_type: general-purpose)
  - **OUTPUT DISCIPLINE**: On completion, report ONLY: Status (SUCCESS/FAIL), Files written (paths), Summary (1-2 sentences), Errors (only if FAIL). DO NOT output file contents in your response.
  - Include: PROJECT_DIR, DESIGN_SPEC path, EDA_ENV path, PYTHON_EXE, SKILL_DIR

After BOTH return:
- If lint failed → fix syntax errors in main session, re-run lint only
- If synth failed → check report, fix if needed

## Post-stage

```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "lint_synth" --hook="$PYTHON_EXE -c \"import os,sys; sys.exit(0 if os.path.isfile('logs/lint.log') and os.path.isfile('workspace/synth/synth_report.txt') else 1)\"" --journal-outputs="logs/lint.log, workspace/synth/synth_report.txt" --journal-notes="Lint and synthesis complete"
```

Update pipeline context:
```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/pipeline_context.py" "$PROJECT_DIR" --set-multi '{"lint_done": true, "synth_done": true}'
```

Write stage summary:
```bash
cat >> "$PROJECT_DIR/.veriflow/stage_summaries.md" << 'EOF'

## Stage 4 — lint_synth
- Lint: logs/lint.log
- Synthesis: workspace/synth/synth_report.txt
- Pipeline complete
EOF
```

TaskUpdate complete. Pipeline done.
