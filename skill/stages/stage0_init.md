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

## Step 0c: Initialize pipeline context

```bash
cd "$ARGUMENTS"
"$PYTHON_EXE" "${CLAUDE_SKILL_DIR}/skill/pipeline_context.py" "$ARGUMENTS" --init
```

## Step 0d: Create task list

Create one task per pipeline stage (skip if resuming and already completed).
Use `TaskCreate` with formatted subject, description, and activeForm for each:

```
TaskCreate:
  subject:    "[S1] Generate design_spec.py"
  description: "Stage 1: Parse requirements → golden model. Produce workspace/docs/design_spec.py with interface, algorithm, compute(), run(), get_test_vectors()."
  activeForm:  "[S1] Generating design specification"

TaskCreate:
  subject:    "[S2] Codegen — translate to Verilog"
  description: "Stage 2: Translate design_spec.py → RTL + testbench. Produce workspace/rtl/*.v and workspace/tb/*.{v,py}. Follow Rules R1-R7 and Verification Checklist."
  activeForm:  "[S2] Translating Python to Verilog"

TaskCreate:
  subject:    "[S3] Verify & fix RTL"
  description: "Stage 3: Run iverilog/cocotb simulation. Golden model A/B/D classification on failure. Fix RTL bugs with retry budget (3 attempts)."
  activeForm:  "[S3] Running simulation and verifying RTL"

TaskCreate:
  subject:    "[S4] Lint & synthesis"
  description: "Stage 4: Run iverilog syntax lint and yosys synthesis. Report area/timing/resource utilization."
  activeForm:  "[S4] Running lint and synthesis"
```

## Step 0e: Write stage summary

Append to `.veriflow/stage_summaries.md`:
```bash
cat >> "$ARGUMENTS/.veriflow/stage_summaries.md" << 'EOF'

## Stage 0 — Init & Requirements
- EDA tools discovered and saved to `.veriflow/eda_env.sh`
- Requirements clarified and saved to `.veriflow/clarifications.md`
- Pipeline context initialized at `.veriflow/pipeline_context.json`
EOF
```
