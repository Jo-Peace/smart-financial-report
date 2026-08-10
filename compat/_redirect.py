"""Shared redirect helper for deprecated root-level wrapper names."""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.tool_registry import find_tool_by_legacy_name  # noqa: E402


def run_legacy_wrapper(legacy_filename: str) -> None:
    spec = find_tool_by_legacy_name(legacy_filename)
    if not spec:
        print(f"Unknown legacy wrapper: {legacy_filename}", file=sys.stderr)
        raise SystemExit(1)

    print(
        f"注意：{legacy_filename} 已搬到 compat/。"
        f"建議改用：python3 cli.py {spec.category} {spec.id}",
        file=sys.stderr,
    )

    from cli import run_tool  # noqa: E402

    code = run_tool(spec, sys.argv[1:])
    raise SystemExit(code)