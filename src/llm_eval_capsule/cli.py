"""Command-line interface for LLM Eval Capsule."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core import CapsuleError, Finding, audit, capture, compare, render_report, verify


def load_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapsuleError(f"could not read JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CapsuleError(f"{path} must contain a JSON object")
    return value


def write_json(value: Any, path: str | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path:
        Path(path).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def print_findings(findings: list[Finding] | tuple[Finding, ...]) -> None:
    labels = {"error": "ERROR", "warning": "WARN", "info": "OK"}
    for item in findings:
        print(f"[{labels[item.level]}] {item.code}: {item.message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-eval-capsule", description="Capture and verify reproducible LLM evaluations")
    sub = parser.add_subparsers(dest="command", required=True)
    audit_cmd = sub.add_parser("audit", help="audit a study specification and its files")
    audit_cmd.add_argument("study")
    audit_cmd.add_argument("--root", default=".")
    audit_cmd.add_argument("--policy")
    audit_cmd.add_argument("--format", choices=("text", "json"), default="text")
    capture_cmd = sub.add_parser("capture", help="create a hash-bound evaluation capsule")
    capture_cmd.add_argument("study")
    capture_cmd.add_argument("--root", default=".")
    capture_cmd.add_argument("--policy")
    capture_cmd.add_argument("-o", "--output", required=True)
    verify_cmd = sub.add_parser("verify", help="verify capsule metadata and artifact hashes")
    verify_cmd.add_argument("capsule")
    verify_cmd.add_argument("--root", default=".")
    verify_cmd.add_argument("--format", choices=("text", "json"), default="text")
    diff_cmd = sub.add_parser("diff", help="compare two evaluation capsules")
    diff_cmd.add_argument("baseline")
    diff_cmd.add_argument("current")
    diff_cmd.add_argument("--mode", choices=("exact", "model-comparison"), default="exact")
    diff_cmd.add_argument("--format", choices=("text", "json"), default="text")
    report_cmd = sub.add_parser("report", help="render a capsule as Markdown")
    report_cmd.add_argument("capsule")
    report_cmd.add_argument("--language", choices=("en", "zh"), default="en")
    report_cmd.add_argument("-o", "--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "audit":
            result = audit(load_json(args.study), args.root, load_json(args.policy) if args.policy else None)
            if args.format == "json":
                write_json(result.to_dict(), None)
            else:
                print("PASS" if result.passed else "FAIL")
                print_findings(result.findings)
            return 0 if result.passed else 2
        if args.command == "capture":
            value = capture(load_json(args.study), args.root, load_json(args.policy) if args.policy else None)
            write_json(value, args.output)
            return 0 if value["passed"] else 2
        if args.command == "verify":
            findings = verify(load_json(args.capsule), args.root)
        elif args.command == "diff":
            findings = compare(load_json(args.baseline), load_json(args.current), args.mode)
        else:
            text = render_report(load_json(args.capsule), args.language)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
            return 0
        if args.format == "json":
            write_json({"findings": [asdict_finding(item) for item in findings]}, None)
        else:
            print_findings(findings)
        return 2 if any(item.level == "error" for item in findings) else 0
    except CapsuleError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 3


def asdict_finding(item: Finding) -> dict[str, Any]:
    return {"code": item.code, "level": item.level, "message": item.message, "evidence": item.evidence}


if __name__ == "__main__":
    raise SystemExit(main())
