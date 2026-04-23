#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the post-implementation closed loop for a tracking workspace: "
            "review plus runtime_browser_session-based runtime verification until validation_gate passes."
        )
    )
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
    parser.add_argument("--output", default="", help="Output path. Default: <workspace-dir>/closed_loop_result.json")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_output_path(args: argparse.Namespace, workspace_dir: Path) -> Path:
    explicit = normalize_text(args.output)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (workspace_dir / "closed_loop_result.json").resolve()


def tracked_state_paths(workspace_dir: Path) -> list[Path]:
    candidates = sorted(path.resolve() for path in workspace_dir.glob("*.html"))
    for name in (
        "tracking_schema.json",
        "openclaw_tracking_implementation.md",
        "implementation_review.json",
        "runtime_browser_preflight.json",
        "runtime_browser_verification.json",
        "validation_gate.json",
    ):
        path = (workspace_dir / name).resolve()
        if path.exists():
            candidates.append(path)
    session_files = sorted(
        path.resolve()
        for path in (workspace_dir / "runtime_browser_sessions").glob("**/*.json")
        if path.is_file()
    )
    candidates.extend(session_files)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def workspace_state_snapshot(workspace_dir: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for path in tracked_state_paths(workspace_dir):
        try:
            relative = str(path.relative_to(workspace_dir))
        except ValueError:
            relative = str(path)
        files[relative] = file_sha256(path)
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "files": files,
        "fingerprint": hashlib.sha256(payload).hexdigest(),
    }


def safe_json_load_from_text(raw: str) -> Any:
    text = normalize_text(raw)
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def command_step(name: str, command: list[str], *, artifact_path: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    artifact = safe_json_load(artifact_path) if artifact_path is not None else {}
    if (not isinstance(artifact, dict) or not artifact) and completed.stdout:
        artifact = safe_json_load_from_text(completed.stdout)

    artifact_status = normalize_text(artifact.get("status")).lower() if isinstance(artifact, dict) else ""
    if artifact_status:
        status = artifact_status
    else:
        status = "passed" if completed.returncode == 0 else "failed"

    return {
        "name": name,
        "status": status,
        "exit_code": completed.returncode,
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "artifact": artifact if isinstance(artifact, dict) else {},
    }


def next_action(gate_step: dict[str, Any], *, status: str) -> str:
    gate_artifact = gate_step.get("artifact") if isinstance(gate_step.get("artifact"), dict) else {}
    if status == "passed":
        return "Closed loop passed. Treat validation_gate.json as the source of truth for completion."
    gate_next_action = normalize_text(gate_artifact.get("next_action"))
    if gate_next_action:
        return gate_next_action
    return "Closed loop did not pass. Inspect closed_loop_result.json and validation_gate.json, then rerun."


def main() -> int:
    args = parse_args()
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    output_path = resolve_output_path(args, workspace_dir)
    gate_artifact_path = (workspace_dir / "validation_gate.json").resolve()
    state_snapshot = workspace_state_snapshot(workspace_dir)
    state_fingerprint = normalize_text(state_snapshot.get("fingerprint"))

    gate_command = [
        sys.executable,
        str((repo_root() / "scripts" / "run_tracking_validation_gate.py").resolve()),
        "--workspace-dir",
        str(workspace_dir),
        "--require-runtime",
        args.require_runtime,
        "--json",
    ]
    if normalize_text(args.schema_path):
        gate_command.extend(["--schema-path", normalize_text(args.schema_path)])
    if normalize_text(args.target_file):
        gate_command.extend(["--target-file", normalize_text(args.target_file)])
    if normalize_text(args.html_file):
        gate_command.extend(["--html-file", normalize_text(args.html_file)])
    if normalize_text(args.baseline_file):
        gate_command.extend(["--baseline-file", normalize_text(args.baseline_file)])

    gate_step = command_step(
        "run_tracking_validation_gate",
        gate_command,
        artifact_path=gate_artifact_path,
    )
    gate_artifact = gate_step.get("artifact") if isinstance(gate_step.get("artifact"), dict) else {}
    final_status = normalize_text(gate_artifact.get("status")).lower() or normalize_text(gate_step.get("status")).lower() or "failed"

    result = {
        "ok": final_status == "passed",
        "status": final_status,
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "artifacts": {
            "closed_loop_result_json": str(output_path),
            "validation_gate_json": str(gate_artifact_path),
            "runtime_browser_verification_json": str((workspace_dir / "runtime_browser_verification.json").resolve()),
        },
        "runtime_requirement": gate_artifact.get("runtime_requirement") if isinstance(gate_artifact.get("runtime_requirement"), dict) else {
            "mode": args.require_runtime,
        },
        "summary": {
            "gate_status": normalize_text(gate_step.get("status")).lower() or None,
            "state_fingerprint": state_fingerprint or None,
            "tracked_state_files": state_snapshot.get("files"),
        },
        "steps": {
            "validation_gate": gate_step,
        },
        "next_action": next_action(gate_step, status=final_status),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if final_status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
