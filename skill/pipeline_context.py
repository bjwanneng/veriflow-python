#!/usr/bin/env python3
"""Pipeline context manager — lightweight JSON for cross-stage info passing.

Usage:
  python pipeline_context.py <project_dir> --init
  python pipeline_context.py <project_dir> --set key value
  python pipeline_context.py <project_dir> --get key
  python pipeline_context.py <project_dir> --dump
"""

import json
import sys
from pathlib import Path


def _ctx_path(project_dir: str) -> Path:
    return Path(project_dir) / ".veriflow" / "pipeline_context.json"


def load(project_dir: str) -> dict:
    p = _ctx_path(project_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save(project_dir: str, data: dict) -> None:
    p = _ctx_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    project_dir = sys.argv[1]
    action = sys.argv[2]

    if action == "--init":
        save(project_dir, {})
        print("[OK] pipeline_context.json initialized")

    elif action == "--set" and len(sys.argv) >= 5:
        key, value = sys.argv[3], sys.argv[4]
        data = load(project_dir)
        # Try to parse value as JSON (for lists/dicts); fall back to string
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
        data[key] = value
        save(project_dir, data)
        print(f"[OK] {key} = {json.dumps(value)}")

    elif action == "--set-multi" and len(sys.argv) >= 4:
        """--set-multi '{"key1":"val1","key2":"val2"}'"""
        data = load(project_dir)
        updates = json.loads(sys.argv[3])
        data.update(updates)
        save(project_dir, data)
        print(f"[OK] Updated: {', '.join(updates.keys())}")

    elif action == "--get" and len(sys.argv) >= 4:
        data = load(project_dir)
        val = data.get(sys.argv[3], "")
        if isinstance(val, (dict, list)):
            print(json.dumps(val))
        else:
            print(val)

    elif action == "--dump":
        data = load(project_dir)
        print(json.dumps(data, indent=2))

    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
