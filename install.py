#!/usr/bin/env python3
"""install.py — Install VeriFlow-python as a Claude Code skill.

Usage:
    python install.py [--uninstall] [--force]

On Windows, creates a directory junction (similar to symlink).
On Linux/macOS, creates a symbolic link.

The script:
  1. Locates the Claude Code skills directory (~/.claude/skills/)
  2. Creates a link: skills/vf-pyverilog -> VeriFlow-python/
  3. Verifies the installation by reading SKILL.md

If a previous installation exists:
  - Without --force: prints warning and exits
  - With --force: removes old installation first
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_NAME = "vf-pyverilog"

# The directory containing this script = the VeriFlow-python project root
PROJECT_DIR = Path(__file__).resolve().parent


def find_skills_dir() -> Path:
    """Find the Claude Code skills directory."""
    home = Path.home()
    skills_dir = home / ".claude" / "skills"

    if not skills_dir.exists():
        print(f"[INFO] Creating skills directory: {skills_dir}")
        skills_dir.mkdir(parents=True, exist_ok=True)

    return skills_dir


def is_junction(path: Path) -> bool:
    """Check if path is a Windows directory junction."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(path)],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def create_link(target: Path, link: Path) -> None:
    """Create a link from link_path -> target.

    On Windows: use directory junction (no admin required).
    On Linux/macOS: use symbolic link.
    """
    if sys.platform == "win32":
        # Use mklink /J (junction) — no admin rights needed
        cmd = ["cmd", "/c", "mklink", "/J", str(link), str(target)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"mklink /J failed (exit {result.returncode}):\n"
                f"  stdout: {result.stdout}\n"
                f"  stderr: {result.stderr}\n"
                f"Try running as administrator, or use --copy instead."
            )
    else:
        # Linux/macOS: symlink
        os.symlink(str(target), str(link))


def remove_link(link: Path) -> None:
    """Remove an existing link/junction/directory at link path."""
    if not link.exists() and not link.is_symlink() and not is_junction(link):
        return

    if sys.platform == "win32" and link.is_dir():
        if is_junction(link):
            # Junction: use rmdir (removes junction, not target)
            link.rmdir()
        else:
            # Real directory: remove it
            shutil.rmtree(link)
    elif link.is_symlink():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    else:
        link.unlink()


def copy_install(source: Path, dest: Path) -> None:
    """Copy the project directory to the skills directory."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)


def verify_installation(skills_dir: Path) -> bool:
    """Verify the skill is correctly installed and readable."""
    skill_path = skills_dir / SKILL_NAME
    skill_md = skill_path / "SKILL.md"

    if not skill_path.exists():
        print(f"[ERROR] Skill directory not found: {skill_path}")
        return False

    if not skill_md.exists():
        print(f"[ERROR] SKILL.md not found: {skill_md}")
        return False

    # Read and validate SKILL.md header
    content = skill_md.read_text(encoding="utf-8")
    if "name: vf-pyverilog" not in content:
        print(f"[ERROR] SKILL.md does not contain 'name: vf-pyverilog'")
        return False

    # Verify subdirectory structure
    expected_dirs = ["skill", "agent", "docs", "templates"]
    for d in expected_dirs:
        if not (skill_path / d).exists():
            print(f"[ERROR] Missing subdirectory: {skill_path / d}")
            return False

    # Verify key files
    key_files = [
        "skill/init.py",
        "skill/state.py",
        "agent/iverilog_runner.py",
        "agent/cocotb_runner.py",
        "agent/vcd2table.py",
        "docs/coding_style.md",
        "docs/design_rules.md",
        "docs/error_recovery.md",
        "docs/bug_patterns.md",
        "templates/design_spec_template.py",
    ]
    for f in key_files:
        if not (skill_path / f).exists():
            print(f"[ERROR] Missing file: {skill_path / f}")
            return False

    return True


def install(force: bool = False, use_copy: bool = False) -> int:
    """Install the skill. Returns 0 on success."""
    skills_dir = find_skills_dir()
    link_path = skills_dir / SKILL_NAME

    print(f"[INFO] Project directory: {PROJECT_DIR}")
    print(f"[INFO] Skills directory:  {skills_dir}")
    print(f"[INFO] Skill name:        {SKILL_NAME}")
    print()

    # Check if already installed
    if link_path.exists() or link_path.is_symlink() or is_junction(link_path):
        if not force:
            # Check if it points to the same target
            if link_path.resolve() == PROJECT_DIR:
                print(f"[OK] Skill already installed and pointing to this directory.")
                print(f"     Path: {link_path} -> {PROJECT_DIR}")
                return 0
            else:
                print(f"[WARN] Skill already installed at: {link_path}")
                print(f"       Current target: {link_path.resolve()}")
                print(f"       New target:     {PROJECT_DIR}")
                print(f"       Use --force to overwrite.")
                return 1

        print(f"[INFO] Removing existing installation: {link_path}")
        remove_link(link_path)

    # Create installation
    if use_copy:
        print(f"[INFO] Copying project to: {link_path}")
        copy_install(PROJECT_DIR, link_path)
    else:
        print(f"[INFO] Creating link: {link_path} -> {PROJECT_DIR}")
        try:
            create_link(PROJECT_DIR, link_path)
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            print(f"[HINT] Try: python install.py --copy")
            return 1

    # Verify
    print()
    print("[INFO] Verifying installation...")
    if verify_installation(skills_dir):
        print(f"[OK] Skill '{SKILL_NAME}' installed successfully!")
        print(f"     Path: {link_path}")
        print()
        print("Usage in Claude Code:")
        print(f"  /{SKILL_NAME} /path/to/your/project")
    else:
        print("[ERROR] Verification failed!")
        return 1

    return 0


def uninstall() -> int:
    """Uninstall the skill. Returns 0 on success."""
    skills_dir = find_skills_dir()
    link_path = skills_dir / SKILL_NAME

    if not link_path.exists() and not link_path.is_symlink() and not is_junction(link_path):
        print(f"[INFO] Skill '{SKILL_NAME}' is not installed.")
        return 0

    print(f"[INFO] Removing skill: {link_path}")
    remove_link(link_path)
    print(f"[OK] Skill '{SKILL_NAME}' uninstalled.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Install/Uninstall VeriFlow-python as a Claude Code skill",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Uninstall the skill instead of installing",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing installation",
    )
    parser.add_argument(
        "--copy", action="store_true",
        help="Copy files instead of creating a link (useful if link fails)",
    )
    args = parser.parse_args()

    if args.uninstall:
        sys.exit(uninstall())
    else:
        sys.exit(install(force=args.force, use_copy=args.copy))


if __name__ == "__main__":
    main()
