---
name: vf-pyverilog
description: Use this skill to start or resume the VeriFlow RTL hardware design pipeline (architect to synth). Trigger this when the user asks to "run the RTL flow", "design hardware", or "start the pipeline". Pass the project directory path as the argument.
---

# RTL Pipeline Orchestrator

This skill IS the plan — execute each stage immediately using Read/Write/Bash/Agent tools. Do NOT plan before executing.

Project directory path: `$ARGUMENTS`

If `$ARGUMENTS` is empty, ask the user for it.

**Variable**: `${CLAUDE_SKILL_DIR}` is set by Claude Code to the skill's installed directory.

---

## Pipeline Overview

```
Stage 0: Init & Requirements Clarification
Stage 1: Natural Language → AI → design_spec.py
Stage 2: design_spec.py → Verilog modules + testbench
Stage 3: iverilog/cocotb simulation → fix RTL bugs
Stage 4: lint + synthesis
```

**Key principle**: `design_spec.py` serves THREE roles:
1. **Design specification** — interface, module hierarchy, protocol, timing
2. **Reference model** — runnable algorithm verified against standard test vectors
3. **Translation blueprint** — each Python function maps to one Verilog module

**NBA timing convention** (Python = Verilog NBA):
- Function parameters = current-cycle register values (right-hand side of `<=`)
- Function return values = next-cycle register values (left-hand side of `<=`)
- Local variables = combinational wires (same-cycle visible)
- Assignment at call site = clock edge (cycle boundary)

---

## Stage Pattern (ALL stages follow this)

Every stage MUST execute these 3 steps in order:

**Pre-stage:**
```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "<STAGE>" --start
```

**Execute:** dispatch agents (Stages 1/2/4) or run inline (Stage 3)

**Post-stage:**
```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "<STAGE>" --hook="<HOOK_CMD>" --journal-outputs="<FILES>" --journal-notes="<NOTES>"
```
Then: `TaskUpdate` mark the stage task (matched by subject prefix `[S<n>]`) as completed.

---

## Design Rules Summary

See `${CLAUDE_SKILL_DIR}/docs/design_rules.md` for full rules.

- Synchronous active-high reset named `rst`
- Port naming: `_n` suffix for active-low, `_i`/`_o` for direction
- **Verilog-2005 only** — NO SystemVerilog
- Interface Lock: port names, handshake protocols, and module hierarchy are frozen after Stage 1

---

## Canonical Inputs (after Stage 0)

Once Stage 0 completes, the ONLY canonical inputs for Stages 1–4 are:
- `.veriflow/clarifications.md` — extracted requirements
- `workspace/docs/design_spec.py` — spec + golden model + blueprint (from Stage 1 onward)
- `.veriflow/pipeline_context.json` — cross-stage metadata (design name, modules, ports, file paths)

Do NOT re-read `requirement.md`, `constraints.md`, or `context/*.md` after Stage 0.
Sub-agents receive file paths and read files themselves — the main session does NOT read docs files on their behalf.

## Stage Gate (context optimization)

Between stages, minimize context carry-over:

1. **Before reading the next stage file**, write a one-paragraph summary of the completed stage to `.veriflow/stage_summaries.md` (append mode).
2. **Stage summaries replace old context** — if you need to recall a prior stage's result, read `.veriflow/stage_summaries.md` or `.veriflow/pipeline_context.json`, NOT the stage file or conversation history.
3. **Stage instruction files are single-use** — once a stage completes, its `.md` file should not be referenced again.

## Execution

Read the stage file for the current stage and follow its instructions exactly:

- **Stage 0**: Read `${CLAUDE_SKILL_DIR}/skill/stages/stage0_init.md`
- **Stage 1**: Read `${CLAUDE_SKILL_DIR}/skill/stages/stage1_design_spec.md`
- **Stage 2**: Read `${CLAUDE_SKILL_DIR}/skill/stages/stage2_codegen.md`
- **Stage 3**: Read `${CLAUDE_SKILL_DIR}/skill/stages/stage3_verify_fix.md`
- **Stage 4**: Read `${CLAUDE_SKILL_DIR}/skill/stages/stage4_lint_synth.md`

Start with Stage 0. After each stage completes, proceed to the next.
If resuming, check `.veriflow/pipeline_state.json` and skip completed stages.
