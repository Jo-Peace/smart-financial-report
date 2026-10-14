#!/usr/bin/env python3
"""
Unified entry point for maintenance / YouTube / special / legacy tools.

Examples:
  python3 cli.py list
  python3 cli.py special weekend
  python3 cli.py special us-weekly
  python3 cli.py maintenance check-daily-workflow --date 2026-06-30
  python3 cli.py youtube regen-assets --date 2026-06-30 --visual-theme "FIFA 32強"
  python3 cli.py youtube check-youtube-package --date 2026-06-30
  python3 cli.py maintenance migrate-reports --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import runpy
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.tool_registry import TOOL_SPECS, find_tool, tools_by_category  # noqa: E402


def _script_path(spec) -> str:
    return os.path.join(BASE_DIR, spec.script)


def run_tool(spec, argv: list[str]) -> int:
    path = _script_path(spec)
    if not os.path.exists(path):
        print(f"Tool script not found: {path}", file=sys.stderr)
        return 1

    old_argv = sys.argv[:]
    sys.argv = [path, *argv]
    try:
        if spec.has_main:
            module_name = spec.script.replace("/", ".").removesuffix(".py")
            module = importlib.import_module(module_name)
            main = getattr(module, "main", None)
            if callable(main):
                result = main()
                return int(result or 0)
        namespace = runpy.run_path(path, run_name="__main__")
        if isinstance(namespace, dict) and callable(namespace.get("main")):
            result = namespace["main"]()
            return int(result or 0)
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    finally:
        sys.argv = old_argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="smart_financial_report 工具入口（canonical scripts 在 scripts/）",
    )
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="列出所有工具")
    list_parser.add_argument(
        "--category",
        choices=list(tools_by_category()),
        help="只列出指定分類",
    )

    for category, specs in tools_by_category().items():
        category_parser = subparsers.add_parser(
            category,
            help=f"{category} 工具",
            description=f"執行 {category}/ 底下的 canonical 腳本",
        )
        category_parser.add_argument(
            "tool_id",
            choices=[spec.id for spec in specs],
            help="工具名稱",
        )
        category_parser.add_argument(
            "tool_args",
            nargs=argparse.REMAINDER,
            help="傳給底層腳本的參數",
        )

    return parser


def cmd_list(category: str | None) -> int:
    grouped = tools_by_category()
    categories = [category] if category else list(grouped)
    for name in categories:
        print(f"[{name}]")
        for spec in grouped.get(name, []):
            legacy = f"  (舊: {', '.join(spec.legacy_names)})" if spec.legacy_names else ""
            print(f"  {spec.id:<24} {spec.description}{legacy}")
            print(f"    -> python3 cli.py {spec.category} {spec.id}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        argv = ["list"]

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        return cmd_list(getattr(args, "category", None))

    spec = find_tool(args.command, args.tool_id)
    if not spec:
        print(f"Unknown tool: {args.command} {args.tool_id}", file=sys.stderr)
        return 1

    tool_args = list(getattr(args, "tool_args", []) or [])
    if tool_args and tool_args[0] == "--":
        tool_args = tool_args[1:]
    return run_tool(spec, tool_args)


if __name__ == "__main__":
    raise SystemExit(main())