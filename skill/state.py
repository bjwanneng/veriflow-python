"""Pipeline state management - zero external dependencies."""

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


# Strict execution order — no stage may be skipped
STAGE_ORDER = ["design_spec", "codegen", "verify_fix", "lint_synth"]

# Prerequisite stages that must all complete before a given stage can run
STAGE_PREREQUISITES = {
    "design_spec":  [],                     # no prerequisites
    "codegen":      ["design_spec"],        # needs design_spec.py
    "verify_fix":   ["codegen"],            # needs RTL + testbench
    "lint_synth":   ["verify_fix"],         # needs verified RTL
}


def next_pending_stage(stages_completed: list) -> str | None:
    """Return the first stage not yet completed. Strict STAGE_ORDER, no skipping."""
    for stage in STAGE_ORDER:
        if stage not in stages_completed:
            return stage
    return None  # all complete


def can_execute(stage: str, stages_completed: list) -> tuple[bool, str]:
    """Check whether a stage can execute (all prerequisites met).

    Returns:
        (can_run, reason) — can_run=True means OK to execute
    """
    prereqs = STAGE_PREREQUISITES.get(stage, [])
    missing = [p for p in prereqs if p not in stages_completed]
    if missing:
        return False, f"Prerequisite stages not completed: {missing}"
    return True, ""


@dataclass
class PipelineState:
    """Pipeline state - serializable to JSON, driven by Claude Code main session."""

    project_dir: str

    current_stage: str = ""
    stages_completed: list = field(default_factory=list)
    stages_failed: list = field(default_factory=list)

    # Per-stage output summaries
    design_spec_output: Optional[dict] = None
    codegen_output: Optional[dict] = None
    verify_fix_output: Optional[dict] = None
    lint_synth_output: Optional[dict] = None

    # Error recovery
    retry_count: dict = field(default_factory=dict)
    error_history: dict = field(default_factory=dict)
    feedback_source: str = ""
    max_retries_per_stage: int = 3

    # Persistent context summary — new sessions read this field to recover state
    stage_summaries: dict = field(default_factory=dict)

    # Per-stage timing
    stage_timings: dict = field(default_factory=dict)  # {"design_spec": {"start": ts, "end": ts, "duration_s": float}, ...}

    # Metadata
    start_time: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.project_dir, Path):
            self.project_dir = str(self.project_dir)

    def mark_complete(self, stage: str, result: dict) -> bool:
        """Mark a stage as complete. Returns False if prerequisites not met."""
        if stage in STAGE_PREREQUISITES:
            ok, reason = can_execute(stage, self.stages_completed)
            if not ok:
                print(f"[ERROR] Stage '{stage}' blocked — {reason}", file=sys.stderr)
                return False
        if stage not in self.stages_completed:
            self.stages_completed.append(stage)
        setattr(self, f"{stage}_output", result)
        self.current_stage = stage
        self.last_updated = time.time()
        # Save summary for context recovery
        summary = result.get("summary", "")
        if summary:
            self.stage_summaries[stage] = summary
        # Record end time
        self._record_end(stage)
        return True

    def mark_failed(self, stage: str, result: dict):
        """Mark a stage as failed."""
        if stage not in self.stages_failed:
            self.stages_failed.append(stage)
        if stage not in self.error_history:
            self.error_history[stage] = []
        self.error_history[stage].append({
            "time": time.time(),
            "errors": result.get("errors", []),
        })
        setattr(self, f"{stage}_output", result)
        self.current_stage = stage
        self.feedback_source = stage
        self.last_updated = time.time()
        # Increment retry counter (enforces 3-retry budget from SKILL.md)
        self.inc_retry(stage)
        # Record end time
        self._record_end(stage)

    def mark_started(self, stage: str):
        """Record the start time of a stage."""
        now = time.time()
        if stage not in self.stage_timings:
            self.stage_timings[stage] = {}
        self.stage_timings[stage]["start"] = now
        self.current_stage = stage
        self.last_updated = now
        self.save()

    def _record_end(self, stage: str):
        """Record the end time and compute duration for a stage."""
        now = time.time()
        if stage not in self.stage_timings:
            self.stage_timings[stage] = {}
        self.stage_timings[stage]["end"] = now
        start = self.stage_timings[stage].get("start")
        if start and isinstance(start, (int, float)):
            self.stage_timings[stage]["duration_s"] = round(now - start, 1)

    def inc_retry(self, stage: str):
        self.retry_count[stage] = self.retry_count.get(stage, 0) + 1
        if self.retry_count[stage] >= self.max_retries_per_stage:
            print(f"[BUDGET] Stage '{stage}' exhausted {self.max_retries_per_stage} retries. Escalating to user.", file=sys.stderr)

    def is_retry_exhausted(self, stage: str) -> bool:
        """Check if the retry budget has been exhausted for a stage."""
        return self.retry_count.get(stage, 0) >= self.max_retries_per_stage

    def get_output(self, stage: str) -> Optional[dict]:
        return getattr(self, f"{stage}_output", None)

    def is_done(self, stage: str) -> bool:
        return stage in self.stages_completed

    def is_pipeline_complete(self) -> bool:
        return "lint_synth" in self.stages_completed

    # -- Persistence ---------------------------------------------------------

    def save(self) -> Path:
        """Save state to .veriflow/pipeline_state.json"""
        d = Path(self.project_dir) / ".veriflow"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "pipeline_state.json"
        p.write_text(json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")
        return p

    @classmethod
    def load(cls, project_dir: str) -> "PipelineState":
        """Load from file, create new state if file does not exist."""
        p = Path(project_dir) / ".veriflow" / "pipeline_state.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return cls(**data)
            except (TypeError, json.JSONDecodeError) as e:
                print(f"[WARNING] Corrupted pipeline_state.json, starting fresh: {e}", file=sys.stderr)
        return cls(project_dir=project_dir)

    def reset_stage(self, stage: str):
        """Clear a stage and all subsequent completion records, for rollback."""
        if stage not in STAGE_ORDER:
            return
        idx = STAGE_ORDER.index(stage)
        to_remove = STAGE_ORDER[idx:]
        self.stages_completed = [s for s in self.stages_completed if s not in to_remove]
        self.stages_failed = [s for s in self.stages_failed if s not in to_remove]
        for s in to_remove:
            setattr(self, f"{s}_output", None)
            self.stage_summaries.pop(s, None)
            self.stage_timings.pop(s, None)
        self.save()

    def next_stage(self) -> str | None:
        """Return the next stage to execute (strict order, no skipping)."""
        return next_pending_stage(self.stages_completed)

    def validate_before_run(self, stage: str) -> tuple[bool, str]:
        """Pre-execution validation. Must be called before every stage execution."""
        # 1. Check strict ordering
        expected = next_pending_stage(self.stages_completed)
        if stage != expected:
            return False, f"Order violation: expected '{expected}', but attempted '{stage}'. Stages cannot be skipped."

        # 2. Check prerequisites
        return can_execute(stage, self.stages_completed)

    def validate_design_spec(self, project_dir: str) -> tuple[bool, list[str]]:
        """Validate design_spec.py completeness after Stage 1.

        Checks: file exists, valid Python syntax, defines required functions
        (run, compute, get_test_vectors) with correct signatures, and self-test
        passes all test vectors.

        Delegates to skill.validate_interface for signature and structural
        checks to avoid duplicating validation logic.

        Returns:
            (is_valid, issues) — is_valid=True means design spec is usable.
        """
        import py_compile
        try:
            from skill import validate_interface as vi
        except ImportError:
            import validate_interface as vi

        issues = []
        ds_path = Path(project_dir) / "workspace" / "docs" / "design_spec.py"

        if not ds_path.exists():
            return (False, ["design_spec.py file missing"])

        # Check syntax
        try:
            py_compile.compile(str(ds_path), doraise=True)
        except py_compile.PyCompileError as e:
            issues.append(f"design_spec.py syntax error: {e}")
            return (False, issues)

        # Load module for validation
        try:
            mod = vi.load_module(str(ds_path))
        except SyntaxError as e:
            issues.append(f"design_spec.py syntax error: {e}")
            return (False, issues)
        except Exception as e:
            issues.append(f"design_spec.py import error: {e}")
            return (False, issues)

        # Delegate structural validation to validate_interface
        issues.extend(vi.validate_variables(mod))
        issues.extend(vi.validate_function_signatures(mod))
        issues.extend(vi.validate_test_vector_structure(mod))

        # Check for module pseudocode functions (Section 5)
        content = ds_path.read_text(encoding="utf-8")
        if "Section 5" not in content and "# Module Pseudocode" not in content:
            issues.append("design_spec.py missing Section 5: Module Pseudocode")

        if issues:
            return (False, issues)

        # Functional smoke tests
        smoke_errors = vi.validate_functional_smoke(mod)
        if smoke_errors:
            return (False, smoke_errors)

        # Run the self-test (subprocess execution validates standalone run)
        try:
            result = subprocess.run(
                [sys.executable, str(ds_path)],
                capture_output=True, text=True, timeout=30,
                cwd=str(ds_path.parent),
            )
            output = result.stdout + result.stderr

            # Check for [FAIL] markers
            fail_lines = [l for l in output.splitlines() if "[FAIL]" in l]
            if fail_lines:
                for fl in fail_lines[:5]:
                    issues.append(f"design_spec.py self-test failed: {fl.strip()}")
                return (False, issues)

            # If no PASS markers and non-zero exit, something is wrong
            pass_lines = [l for l in output.splitlines() if "[PASS]" in l]
            if not pass_lines and result.returncode != 0:
                issues.append(
                    f"design_spec.py exited with code {result.returncode} "
                    f"and produced no [PASS] output"
                )
                return (False, issues)

        except subprocess.TimeoutExpired:
            issues.append("design_spec.py self-test timed out (30s limit)")
            return (False, issues)
        except Exception as e:
            issues.append(f"design_spec.py execution error: {e}")
            return (False, issues)

        return (True, [])

    def detect_fix_loop(self, stage: str, error_signature: str) -> bool:
        """Detect if error recovery is cycling on the same error.

        Args:
            stage: Current stage name
            error_signature: A short string identifying the error (e.g., "lint:line42:syntax")

        Returns:
            True if this exact error has been seen 2+ times in recent history
        """
        if stage not in self.error_history:
            return False

        recent_errors = self.error_history[stage][-3:]  # Last 3 attempts
        signature_count = sum(
            1 for e in recent_errors
            if error_signature in str(e.get("errors", []))
        )
        return signature_count >= 2



# -- CLI entry point (called by SKILL.md state update command) -----------------

def _run_hook_safely(hook_cmd: str, project_dir: str) -> tuple[bool, str]:
    """Run a hook command safely without shell=True.

    Resolves $PROJECT_DIR placeholder. Uses shlex.split for safe parsing.
    Falls back to shell=True only for commands requiring shell features
    (pipes, redirects, &&, ||).

    Returns:
        (passed, output_message)
    """
    # Quote project_dir to prevent command injection via shell metacharacters
    hook_cmd_resolved = hook_cmd.replace("$PROJECT_DIR", shlex.quote(project_dir))

    # Detect shell features that require shell=True
    shell_features = ("|", "&&", "||", ">", ">>", "<", ";", "$(", "`")
    needs_shell = any(f in hook_cmd_resolved for f in shell_features)

    try:
        if needs_shell:
            # Shell commands: use shell=True but with explicit cwd control
            result = subprocess.run(
                hook_cmd_resolved, shell=True,
                capture_output=True, text=True,
                cwd=project_dir,
            )
        else:
            # Simple commands: safe shlex.split, no shell
            args = shlex.split(hook_cmd_resolved)
            result = subprocess.run(
                args, capture_output=True, text=True,
                cwd=project_dir,
            )

        output_parts = []
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.returncode != 0:
            if result.stderr.strip():
                output_parts.append(f"stderr: {result.stderr.strip()}")
            return False, f"Hook failed (exit code {result.returncode}). " + " ".join(output_parts)
        return True, "\n".join(output_parts)

    except FileNotFoundError as e:
        return False, f"Hook command not found: {e}"
    except Exception as e:
        return False, f"Hook execution error: {e}"


def _append_journal(project_dir: str, stage: str, outputs: str = "", notes: str = "",
                    status: str = "completed"):
    """Append a stage journal entry to workspace/docs/stage_journal.md."""
    journal_path = Path(project_dir) / "workspace" / "docs" / "stage_journal.md"
    from datetime import datetime
    ts = datetime.now().isoformat()
    entry = f"\n## Stage: {stage}\n**Status**: {status}\n**Timestamp**: {ts}\n"
    if outputs:
        entry += f"**Outputs**: {outputs}\n"
    if notes:
        entry += f"**Notes**: {notes}\n"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(entry)


def build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="VeriFlow pipeline state manager",
    )
    parser.add_argument("project_dir", help="Project directory path")
    parser.add_argument("stage", nargs="?", help="Stage name (required unless --reset)")

    # Actions
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--start", action="store_true", help="Record stage start time")
    action.add_argument("--fail", action="store_true", help="Mark stage as failed")
    action.add_argument("--reset", metavar="STAGE", help="Rollback from STAGE onward")

    # Options
    parser.add_argument("--hook", help="Hook command to run before marking complete")
    parser.add_argument("--journal-outputs", help="Output files to record in journal")
    parser.add_argument("--journal-notes", help="Notes to record in journal")

    return parser


if __name__ == "__main__":
    parser = build_argparser()
    args = parser.parse_args()

    _project_dir = args.project_dir

    # --reset mode: rollback from target stage
    if args.reset:
        _target = args.reset
        if _target not in STAGE_ORDER:
            print(f"ERROR: Unknown stage '{_target}'. Valid stages: {STAGE_ORDER}", file=sys.stderr)
            sys.exit(1)
        _state = PipelineState.load(_project_dir)
        _state.reset_stage(_target)
        print(f"[STATE] Rolled back to '{_target}' — cleared it and all subsequent stages")
        print(f"[STATE] stages_completed: {_state.stages_completed}")
        _next = _state.next_stage()
        print(f"[STATE] Next: {_next}" if _next else "[STATE] Pipeline complete")
        sys.exit(0)

    # Validate stage name
    _stage = args.stage
    if not _stage:
        parser.error("stage is required (unless using --reset)")

    if _stage not in STAGE_ORDER:
        print(f"ERROR: Unknown stage '{_stage}'. Valid stages: {STAGE_ORDER}", file=sys.stderr)
        sys.exit(1)

    _state = PipelineState.load(_project_dir)

    if args.start:
        _state.mark_started(_stage)
        _append_journal(_project_dir, _stage, status="started")
        print(f"[STATE] {_stage} → STARTED")
    elif args.fail:
        _state.mark_failed(_stage, {"success": False, "errors": ["Hook failed"]})
        _state.save()
        _append_journal(_project_dir, _stage, status="failed")
        print(f"[STATE] {_stage} → FAILED")
    else:
        # Run hook if provided
        _hook_passed = True
        if args.hook:
            _hook_passed, _hook_msg = _run_hook_safely(args.hook, _project_dir)
            if _hook_passed:
                if _hook_msg:
                    print(_hook_msg)
            else:
                print(f"[HOOK] FAIL: {_hook_msg}", file=sys.stderr)

        if _hook_passed:
            if _state.mark_complete(_stage, {"success": True, "summary": "Hook passed"}):
                _state.save()
                print(f"[STATE] {_stage} → COMPLETE")
                # Print timing summary
                if _stage in _state.stage_timings:
                    t = _state.stage_timings[_stage]
                    dur = t.get("duration_s", "?")
                    print(f"[STATE] {_stage} duration: {dur}s")
                # Append journal entry if requested
                _journal_outputs = args.journal_outputs or ""
                _journal_notes = args.journal_notes or ""
                if _journal_outputs or _journal_notes:
                    _append_journal(_project_dir, _stage, _journal_outputs, _journal_notes)
                    print(f"[JOURNAL] {_stage} entry appended")
            else:
                print(f"[STATE] {_stage} → BLOCKED (prerequisites not met)", file=sys.stderr)
                sys.exit(1)
        else:
            _state.mark_failed(_stage, {"success": False, "errors": ["Hook failed"]})
            _state.save()
            print(f"[STATE] {_stage} → FAILED (hook)")

    _next = _state.next_stage()
    print(f"[STATE] Next: {_next}" if _next else "[STATE] Pipeline complete")
