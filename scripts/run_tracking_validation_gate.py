#!/usr/bin/env python3
"""
Run the formal validation gate for a tracking workspace session.

The gate combines:
- static review (required)
- runtime verification from runtime_browser_session artifacts (required by default)

It does not repair code by itself, but it produces a single validation_gate.json
artifact that an agent can consume in a fix/rerun loop.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run tracking review and runtime verification as a unified validation gate.")
    parser.add_argument("--workspace-dir", required=True, help="Path to the tracking workspace session directory.")
    parser.add_argument("--schema-path", default="", help="Optional path to tracking_schema.json.")
    parser.add_argument("--target-file", default="", help="Optional target file for review/runtime verification.")
    parser.add_argument("--html-file", default="", help="Optional HTML file for review selector coverage.")
    parser.add_argument("--baseline-file", default="", help="Optional baseline file for review diff checks.")
    parser.add_argument(
        "--require-runtime",
        choices=("auto", "always", "never"),
        default="always",
        help="Runtime verification requirement. Default: always",
    )
    parser.add_argument("--output", default="", help="Output path for validation_gate.json.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def resolve_output_path(args: argparse.Namespace, workspace_dir: Path) -> Path:
    explicit = normalize_text(args.output)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (workspace_dir / "validation_gate.json").resolve()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_runtime_source_exists(workspace_dir: Path) -> bool:
    session_root = (workspace_dir / "runtime_browser_sessions").resolve()
    return any(session_root.glob("*/session.json"))


def safe_json_load_from_text(raw: str) -> Any:
    text = normalize_text(raw)
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def command_result(
    *,
    name: str,
    command: list[str],
    artifact_path: Path,
    required: bool = True,
    skipped_reason: str | None = None,
) -> dict[str, Any]:
    if skipped_reason:
        return {
            "name": name,
            "required": required,
            "status": "skipped",
            "exit_code": None,
            "command": command,
            "artifact_path": str(artifact_path),
            "skipped_reason": skipped_reason,
            "stdout": "",
            "stderr": "",
            "artifact": {},
        }

    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except Exception as exc:
        return {
            "name": name,
            "required": required,
            "status": "failed",
            "exit_code": None,
            "command": command,
            "artifact_path": str(artifact_path),
            "stdout": "",
            "stderr": str(exc),
            "artifact": safe_json_load(artifact_path),
        }

    artifact = safe_json_load(artifact_path)
    if (not isinstance(artifact, dict) or not artifact) and completed.stdout:
        artifact = safe_json_load_from_text(completed.stdout)
    artifact_status = normalize_text(artifact.get("status")).lower() if isinstance(artifact, dict) else ""
    if completed.returncode != 0:
        status = "failed"
    elif artifact_status:
        status = artifact_status
    elif completed.returncode == 0:
        status = "passed"
    else:
        status = "failed"

    return {
        "name": name,
        "required": required,
        "status": status,
        "exit_code": completed.returncode,
        "command": command,
        "artifact_path": str(artifact_path),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "artifact": artifact if isinstance(artifact, dict) else {},
    }


def compact_review_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": artifact.get("status"),
        "ok": artifact.get("ok"),
        "error_count": artifact.get("error_count"),
        "warning_count": artifact.get("warning_count"),
        "finding_count": len(artifact.get("findings") or []) if isinstance(artifact, dict) else None,
    }


def compact_preflight_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    return {
        "status": artifact.get("status"),
        "ok": artifact.get("ok"),
        "event_count": summary.get("event_count"),
        "resolved_event_count": summary.get("resolved_event_count"),
        "partial_event_count": summary.get("partial_event_count"),
        "unresolved_event_count": summary.get("unresolved_event_count"),
        "resolved_event_ids": summary.get("resolved_event_ids") if isinstance(summary.get("resolved_event_ids"), list) else None,
        "partial_event_ids": summary.get("partial_event_ids") if isinstance(summary.get("partial_event_ids"), list) else None,
        "unresolved_event_ids": summary.get("unresolved_event_ids") if isinstance(summary.get("unresolved_event_ids"), list) else None,
    }


def compact_runtime_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    return {
        "status": artifact.get("status"),
        "ok": artifact.get("ok"),
        "session_count": summary.get("session_count"),
        "state_count": summary.get("state_count"),
        "schema_event_count": summary.get("schema_event_count"),
        "captured_event_count": summary.get("captured_event_count"),
        "covered_event_count": summary.get("covered_event_count"),
        "uncovered_event_ids": summary.get("uncovered_event_ids") if isinstance(summary.get("uncovered_event_ids"), list) else None,
        "source_review_required_event_ids": (
            summary.get("source_review_required_event_ids")
            if isinstance(summary.get("source_review_required_event_ids"), list)
            else None
        ),
        "suspected_unreachable_event_ids": (
            summary.get("suspected_unreachable_event_ids")
            if isinstance(summary.get("suspected_unreachable_event_ids"), list)
            else None
        ),
        "suspected_unreachable_count": summary.get("suspected_unreachable_count"),
        "matched_assertion_count": summary.get("matched_assertion_count"),
    }


def overall_status(review_status: str, runtime_status: str, *, runtime_required: bool) -> str:
    if review_status != "passed":
        return "failed"
    if runtime_required and runtime_status != "passed":
        return "failed"
    if not runtime_required and runtime_status in {"failed", "needs_review"}:
        return "failed"
    return "passed"


def next_action(
    status: str,
    review: dict[str, Any],
    runtime_preflight: dict[str, Any],
    runtime: dict[str, Any],
    *,
    runtime_required: bool,
    workspace_dir: Path,
) -> str:
    del runtime_preflight
    if status == "passed":
        return "Validation gate passed. It is safe to treat the implementation as complete."

    if review.get("status") != "passed":
        return (
            "Read implementation_review.json, fix the workspace implementation, and rerun "
            "run_tracking_validation_gate.py until review passes."
        )

    if runtime_required and runtime.get("status") != "passed":
        runtime_artifact = runtime.get("artifact") if isinstance(runtime.get("artifact"), dict) else {}
        failure_reason = normalize_text(runtime_artifact.get("failure_reason")).lower()
        uncovered_event_ids = (
            runtime_artifact.get("summary", {}).get("uncovered_event_ids")
            if isinstance(runtime_artifact.get("summary"), dict)
            else []
        )
        suspected_unreachable_event_ids = (
            runtime_artifact.get("summary", {}).get("suspected_unreachable_event_ids")
            if isinstance(runtime_artifact.get("summary"), dict)
            else []
        )
        if failure_reason == "no_runtime_browser_sessions":
            return (
                "Read runtime_browser_preflight.json first, then initialize the runtime env with "
                f"`python3 scripts/setup_runtime_verify_env.py --json`, start a browser session under \"{workspace_dir}\", "
                "and use runtime_browser_session.py start/act/assert to explore real interactions before rerunning the gate."
            )
        if failure_reason in {"no_reports_captured", "schema_events_not_covered"}:
            preview = ", ".join(uncovered_event_ids[:5]) if isinstance(uncovered_event_ids, list) else ""
            preview_suffix = "" if not isinstance(uncovered_event_ids, list) or len(uncovered_event_ids) <= 5 else ", ..."
            suspected_preview = ", ".join(suspected_unreachable_event_ids[:5]) if isinstance(suspected_unreachable_event_ids, list) else ""
            suspected_suffix = "" if not isinstance(suspected_unreachable_event_ids, list) or len(suspected_unreachable_event_ids) <= 5 else ", ..."
            suspected_clause = ""
            if suspected_preview:
                suspected_clause = (
                    " For suspected-unreachable events, read the source callsite and interaction flow first, "
                    "and only remove them if the source confirms they are not manually reachable"
                    + f": {suspected_preview}{suspected_suffix}."
                )
            return (
                "Read runtime_browser_verification.json and runtime_browser_preflight.json. After one exploration pass, focus on the uncovered event_id entries, "
                "locate the real track binding and derive the trigger node/view/prerequisite path, then continue using "
                "runtime_browser_session.py act/assert to capture the remaining schema events and rerun the gate"
                + (f": {preview}{preview_suffix}." if preview else ".")
                + suspected_clause
            )
        return (
            "Read runtime_browser_verification.json and runtime_browser_preflight.json, continue runtime_browser_session exploration, "
            "and rerun run_tracking_validation_gate.py after collecting more runtime evidence."
        )

    return "Validation gate failed. Read validation_gate.json and the underlying artifacts, fix the workspace copy, and rerun."


def main() -> int:
    args = parse_args()
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    output_path = resolve_output_path(args, workspace_dir)
    root = repo_root()
    review_artifact = (workspace_dir / "implementation_review.json").resolve()
    runtime_preflight_artifact = (workspace_dir / "runtime_browser_preflight.json").resolve()
    runtime_artifact = (workspace_dir / "runtime_browser_verification.json").resolve()
    runtime_source_exists = resolve_runtime_source_exists(workspace_dir)

    if args.require_runtime == "always":
        runtime_required = True
    elif args.require_runtime == "never":
        runtime_required = False
    else:
        runtime_required = runtime_source_exists

    review_command = [
        sys.executable,
        str((root / "scripts" / "review_tracking_implementation.py").resolve()),
        "--workspace-dir",
        str(workspace_dir),
        "--json",
    ]
    if normalize_text(args.schema_path):
        review_command.extend(["--schema-path", normalize_text(args.schema_path)])
    if normalize_text(args.target_file):
        review_command.extend(["--target-file", normalize_text(args.target_file)])
    if normalize_text(args.html_file):
        review_command.extend(["--html-file", normalize_text(args.html_file)])
    if normalize_text(args.baseline_file):
        review_command.extend(["--baseline-file", normalize_text(args.baseline_file)])

    review_result = command_result(name="review", command=review_command, artifact_path=review_artifact, required=True)

    preflight_command = [
        sys.executable,
        str((root / "scripts" / "prepare_runtime_browser_preflight.py").resolve()),
        "--workspace-dir",
        str(workspace_dir),
        "--json",
    ]
    if normalize_text(args.schema_path):
        preflight_command.extend(["--schema-path", normalize_text(args.schema_path)])
    if normalize_text(args.target_file):
        preflight_command.extend(["--target-file", normalize_text(args.target_file)])

    if normalize_text(review_result.get("status")).lower() == "passed":
        runtime_preflight_result = command_result(
            name="runtime_browser_preflight",
            command=preflight_command,
            artifact_path=runtime_preflight_artifact,
            required=False,
        )
    else:
        runtime_preflight_result = command_result(
            name="runtime_browser_preflight",
            command=preflight_command,
            artifact_path=runtime_preflight_artifact,
            required=False,
            skipped_reason="review_not_passed",
        )

    runtime_command = [
        sys.executable,
        str((root / "scripts" / "verify_tracking_runtime_browser_session.py").resolve()),
        "--workspace-dir",
        str(workspace_dir),
        "--json",
    ]
    if normalize_text(args.schema_path):
        runtime_command.extend(["--schema-path", normalize_text(args.schema_path)])

    if not runtime_required and not runtime_source_exists:
        runtime_result = command_result(
            name="runtime_verification",
            command=runtime_command,
            artifact_path=runtime_artifact,
            required=False,
            skipped_reason="runtime_source_missing_and_runtime_not_required",
        )
    elif not runtime_required:
        runtime_result = command_result(
            name="runtime_verification",
            command=runtime_command,
            artifact_path=runtime_artifact,
            required=False,
            skipped_reason="runtime_requirement_disabled",
        )
    else:
        runtime_result = command_result(
            name="runtime_verification",
            command=runtime_command,
            artifact_path=runtime_artifact,
            required=True,
        )

    status = overall_status(
        normalize_text(review_result.get("status")).lower(),
        normalize_text(runtime_result.get("status")).lower(),
        runtime_required=runtime_required,
    )

    result = {
        "ok": status == "passed",
        "status": status,
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "artifacts": {
            "implementation_review_json": str(review_artifact),
            "runtime_browser_preflight_json": str(runtime_preflight_artifact),
            "runtime_browser_verification_json": str(runtime_artifact),
            "validation_gate_json": str(output_path),
            "runtime_browser_sessions_dir": str((workspace_dir / "runtime_browser_sessions").resolve()),
        },
        "runtime_requirement": {
            "mode": args.require_runtime,
            "required": runtime_required,
            "runtime_source_exists": runtime_source_exists,
        },
        "review": {
            **review_result,
            "summary": compact_review_summary(review_result.get("artifact") or {}),
        },
        "runtime_browser_preflight": {
            **runtime_preflight_result,
            "summary": compact_preflight_summary(runtime_preflight_result.get("artifact") or {}),
        },
        "runtime_verification": {
            **runtime_result,
            "summary": compact_runtime_summary(runtime_result.get("artifact") or {}),
        },
        "fix_loop_contract": "If status != 'passed', read the failing artifact(s), fix only the workspace copy, and rerun this gate until it passes or a real blocker is identified.",
        "next_action": next_action(
            status,
            review_result,
            runtime_preflight_result,
            runtime_result,
            runtime_required=runtime_required,
            workspace_dir=workspace_dir,
        ),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
