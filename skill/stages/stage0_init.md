# Stage 0: Initialization & Requirements

## Step 0: Run init script

```bash
PY_INIT="${PYTHON_EXE:-python}"
cd "$ARGUMENTS" && "$PY_INIT" "${CLAUDE_SKILL_DIR}/skill/init.py" "$ARGUMENTS"
source "$ARGUMENTS/.veriflow/eda_env.sh"
```

Read output to determine: new project or resuming. If resuming, skip stages in `stages_completed`.

## Step 0b: Requirements Clarification

Read ALL input files in parallel:
- `$ARGUMENTS/requirement.md` (required)
- `$ARGUMENTS/constraints.md` (optional)
- `$ARGUMENTS/design_intent.md` (optional)
- `$ARGUMENTS/context/*.md` files

Check categories A-G below. If input files already answer it → skip. Only ask about what's missing. Use AskUserQuestion with up to 4 questions per call.

**A.** Functional clarity: module functionality, interface protocol, data format, FSM behavior
**B.** Constraint clarity: clock frequency, target platform, area/power, reset strategy
**C.** Design intent: architecture style, module partitioning, interface preferences
**D.** Algorithm & protocol: algorithm reference, pseudocode, key formulas, test vectors
**E.** Timing completeness: cycle-level behavior, latency, throughput, backpressure
**F.** Domain knowledge: design domain, standard reference, prerequisite concepts
**G.** Information completeness: implicit assumptions, missing scenarios

After resolved, write `$ARGUMENTS/.veriflow/clarifications.md`.

## Step 0c: Create task list

Create one task per pipeline stage (skip if resuming and already completed):
- `Stage 1: design_spec`
- `Stage 2: codegen`
- `Stage 3: verify_fix`
- `Stage 4: lint_synth`
