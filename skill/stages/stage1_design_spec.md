# Stage 1: design_spec

Generate a single `design_spec.py` file combining design specification, algorithm reference model, and test vector verification.

## Pre-stage

```bash
source "$PROJECT_DIR/.veriflow/eda_env.sh" && $PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "design_spec" --start
```

## Execute

Read the template:
```bash
cat "${CLAUDE_SKILL_DIR}/templates/design_spec_template.py"
```
(For template section details, read `${CLAUDE_SKILL_DIR}/docs/template_guide.md` if needed.)

Dispatch **one agent** (subagent_type: general-purpose):
- Prompt includes: PROJECT_DIR, CLARIFICATIONS path, template content inline, ALL input file contents inline
- The agent MUST:
  1. Write `workspace/docs/design_spec.py` following the template's 8-section structure
  2. Section 1: Interface Definition (comments matching Verilog port list)
  3. Section 2: Module Hierarchy
  4. Section 3: Algorithm Constants
  5. Section 4: Helper Functions (wire semantics)
  6. Section 5: Module Pseudocode (one function per module)
  7. Section 6: Top-Level Integration (cycle-accurate simulation)
  8. Section 7: Test Vectors (from standard specification)
  9. Section 8: Standard Interface (run(), get_test_vectors())
  10. Run `python workspace/docs/design_spec.py` — ALL test vectors MUST pass
  11. **Optional**: For complex cross-module timing, generate `build_<module_name>()` functions using VeriFlow DSL:
      - `m.d.comb += sig.eq(expr)` — combinational (wire, same-cycle)
      - `m.d.sync += sig.eq(expr)` — registered (reg_next, next-cycle)
      - DSL usage is OPTIONAL. Plain Python with `# wire`/`# reg_next` works.

## Verify

```bash
cd "$PROJECT_DIR"
$PYTHON_EXE workspace/docs/design_spec.py 2>&1 | tee logs/design_spec_selfcheck.log
$PYTHON_EXE -c "import sys; sys.exit(1 if '[FAIL]' in open('logs/design_spec_selfcheck.log').read() else 0)" && echo "[FATAL] design_spec.py has failing test vectors" && exit 1
```

## Post-stage

```bash
$PYTHON_EXE "${CLAUDE_SKILL_DIR}/skill/state.py" "$PROJECT_DIR" "design_spec" --hook="$PYTHON_EXE -c \"import os,sys; sys.exit(0 if os.path.isfile('workspace/docs/design_spec.py') else 1)\"" --journal-outputs="workspace/docs/design_spec.py, logs/design_spec_selfcheck.log" --journal-notes="Python design specification generated and verified against standard test vectors"
```

TaskUpdate complete.
