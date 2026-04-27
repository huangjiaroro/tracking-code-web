#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from tracking_runtime_config import resolve_runtime_config, runtime_config_issues, runtime_config_required_reads
from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load

STATUS_WAITING_AGENT = "WAITING_AGENT"
STATUS_WAITING_USER = "WAITING_USER"
STATUS_RUNNING = "RUNNING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"

STAGE_PREPARE_INIT = "prepare_init"
STAGE_CONFIRM_RUNTIME_CONFIG = "confirm_runtime_config"
STAGE_APP_BUSINESS_GUESS = "app_business_guess"
STAGE_CONFIRM_APP_BUSINESS = "confirm_app_business"
STAGE_LLM_OUTPUT_DESIGN = "llm_output_design"
STAGE_MANUAL_IMPLEMENTATION = "manual_implementation"
STAGE_REVIEW_FIX = "review_fix"
STAGE_RUNTIME_FIX = "runtime_fix"
STAGE_COMPLETED = "completed"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="State-machine harness for tracking design and validation.")
    parser.add_argument("--html", default="", help="Source HTML path. Required for first initialization.")
    parser.add_argument("--session-id", default="", help="Session id. If omitted on first run, auto-generate.")
    parser.add_argument("--workspace-root", default="", help="Workspace root. Default: <repo>/.workspace")

    parser.add_argument("--agent-app-business-json", default="", help="Agent recommendation JSON path.")
    parser.add_argument("--confirm-app-id", default="", help="Confirmed app id from user.")
    parser.add_argument("--confirm-app-code", default="", help="Confirmed app code from user.")
    parser.add_argument("--confirm-business-code", default="", help="Confirmed business code from user.")
    parser.add_argument("--accept-recommendation", action="store_true", help="Accept recommended app/business directly.")
    parser.add_argument("--agent-llm-output-json", default="", help="Agent llm_output JSON path.")
    parser.add_argument("--implementation-done", action="store_true", help="Mark manual implementation as done and run closed loop.")

    parser.add_argument("--runtime-start", action="store_true", help="Start runtime browser session (runs env + preflight).")
    parser.add_argument("--runtime-session-id", default="", help="Runtime browser session id. Default: agent-loop.")
    parser.add_argument("--runtime-act-json", default="", help="JSON string for runtime act step.")
    parser.add_argument(
        "--runtime-assert-json",
        default="",
        help='JSON object for runtime assert, e.g. {"event_id":"...","action":"click"}',
    )
    parser.add_argument("--runtime-check", action="store_true", help="Run validation gate check.")

    parser.add_argument("--save", action="store_true", help="Enable real save API in apply step.")
    parser.add_argument("--tracking-env", default="", help="Forwarded to prepare_tracking_context.py")
    parser.add_argument("--tracking-base-url", default="", help="Forwarded to prepare/apply scripts.")
    parser.add_argument("--cert-path", default="", help="Deprecated. Certificate settings are loaded from config files only.")
    parser.add_argument("--cert-password", default="", help="Deprecated. Certificate settings are loaded from config files only.")
    parser.add_argument("--user-name", default="", help="Forwarded to prepare_tracking_context.py")
    parser.add_argument("--app-page-size", default="200", help="Forwarded to prepare_tracking_context.py")
    parser.add_argument("--weblog-app-key", default="", help="Forwarded to apply_llm_output.py")
    parser.add_argument("--weblog-debug", action="store_true", help="Forwarded to apply_llm_output.py")
    parser.add_argument("--page-binding-id", default="", help="Forwarded to apply_llm_output.py")
    parser.add_argument("--project-id", default="", help="Forwarded to apply_llm_output.py")
    parser.add_argument("--base-revision", default="1", help="Forwarded to apply_llm_output.py")
    parser.add_argument("--save-endpoint", default="tracking/page_document/save", help="Forwarded to apply_llm_output.py")
    parser.add_argument("--save-timeout", default="30", help="Forwarded to apply_llm_output.py")

    parser.add_argument("--reset-all", action="store_true", help="Reset state file for this session.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def scripts_dir() -> Path:
    return repo_root() / "scripts"


def resolve_harness_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    return resolve_runtime_config(
        repo_root(),
        overrides={
            "tracking_env": normalize_text(args.tracking_env),
            "tracking_base_url": normalize_text(args.tracking_base_url),
            "user_name": normalize_text(args.user_name),
        },
    )


def prepare_config_submit_command(args: argparse.Namespace, state: dict[str, Any], html_path: Path) -> str:
    return (
        f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" '
        f'--html "{html_path}" '
        '--tracking-env "<env>" [--tracking-base-url "<custom_url>"] '
        '[--user-name "<email>"] --json'
    )


def redact_runtime_config(runtime_config: dict[str, Any], config_issues: list[str]) -> dict[str, Any]:
    sources = runtime_config.get("sources") if isinstance(runtime_config.get("sources"), dict) else {}
    cert_path = normalize_text(runtime_config.get("cert_path"))
    cert_password = normalize_text(runtime_config.get("cert_password"))
    cert_path_exists = bool(cert_path and Path(cert_path).expanduser().exists())
    return {
        "tracking_env": runtime_config.get("tracking_env"),
        "tracking_base_url": runtime_config.get("tracking_base_url"),
        "user_name": runtime_config.get("user_name"),
        "sources": sources,
        "certificate_config": {
            "cert_path_configured": cert_path_exists,
            "cert_password_configured": bool(cert_password),
            "cert_path_source": sources.get("cert_path") or "missing",
            "cert_password_source": sources.get("cert_password") or "missing",
        },
        "missing_or_unconfirmed": config_issues,
        "note": (
            "Certificate path/password are loaded from config files only. "
            "If certificate settings need changes, update one of the config files in required_reads and rerun."
        ),
    }


def workspace_root_from_args(args: argparse.Namespace) -> Path:
    explicit = normalize_text(args.workspace_root)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (repo_root() / ".workspace").resolve()


def make_session_id() -> str:
    return "tracking-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def normalize_session_id(args: argparse.Namespace) -> str:
    return normalize_text(args.session_id) or ""


def state_file(workspace_dir: Path) -> Path:
    return (workspace_dir / "harness_state.json").resolve()


def result_file(workspace_dir: Path) -> Path:
    return (workspace_dir / "harness_result.json").resolve()


def read_json_from_stdout(stdout: str) -> dict[str, Any]:
    text = normalize_text(stdout)
    if not text:
        return {}
    try:
        parsed = json.loads(stdout)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def safe_json_from_text(raw: str) -> dict[str, Any]:
    text = normalize_text(raw)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "stdout_json": read_json_from_stdout(completed.stdout),
    }


def existing(path: Path) -> str | None:
    return str(path.resolve()) if path.exists() else None


def workspace_artifacts(workspace_dir: Path) -> dict[str, str | None]:
    return {
        "prepare_context_json": existing(workspace_dir / "prepare_context.json"),
        "all_apps_catalog_json": existing(workspace_dir / "all_apps_catalog.json"),
        "all_business_lines_catalog_json": existing(workspace_dir / "all_business_lines_catalog.json"),
        "all_sections_catalog_json": existing(workspace_dir / "all_sections_catalog.json"),
        "all_elements_catalog_json": existing(workspace_dir / "all_elements_catalog.json"),
        "all_fields_catalog_json": existing(workspace_dir / "all_fields_catalog.json"),
        "app_business_recommendation_json": existing(workspace_dir / "app_business_recommendation.json"),
        "app_business_confirm_json": existing(workspace_dir / "app_business_confirm.json"),
        "llm_output_json": existing(workspace_dir / "llm_output.json"),
        "apply_result_json": existing(workspace_dir / "apply_result.json"),
        "page_document_save_payload_json": existing(workspace_dir / "page_document_save_payload.json"),
        "tracking_schema_json": existing(workspace_dir / "tracking_schema.json"),
        "implementation_guide_md": existing(workspace_dir / "openclaw_tracking_implementation.md"),
        "implementation_review_json": existing(workspace_dir / "implementation_review.json"),
        "runtime_browser_preflight_json": existing(workspace_dir / "runtime_browser_preflight.json"),
        "runtime_browser_verification_json": existing(workspace_dir / "runtime_browser_verification.json"),
        "validation_gate_json": existing(workspace_dir / "validation_gate.json"),
        "closed_loop_result_json": existing(workspace_dir / "closed_loop_result.json"),
        "runtime_browser_sessions_dir": existing(workspace_dir / "runtime_browser_sessions"),
    }


def default_state(session_id: str, workspace_dir: Path) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "workspace_dir": str(workspace_dir.resolve()),
        "status": "",
        "current_stage": "",
        "handoff_file": None,
        "next_action": None,
        "runtime_session_id": "agent-loop",
        "source_html": None,
        "last_error": None,
        "updated_at": now_utc_iso(),
    }


def load_state(path: Path, *, session_id: str, workspace_dir: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state(session_id, workspace_dir)
    payload = safe_json_load(path)
    state = default_state(session_id, workspace_dir)
    state.update(payload)
    if not normalize_text(state.get("session_id")):
        state["session_id"] = session_id
    if not normalize_text(state.get("workspace_dir")):
        state["workspace_dir"] = str(workspace_dir.resolve())
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_utc_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def write_handoff(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def write_result(workspace_dir: Path, state: dict[str, Any], *, message: str | None = None) -> dict[str, Any]:
    result = {
        "ok": state.get("status") in {STATUS_RUNNING, STATUS_WAITING_AGENT, STATUS_WAITING_USER, STATUS_DONE},
        "status": state.get("status"),
        "current_stage": state.get("current_stage"),
        "message": message,
        "session_id": state.get("session_id"),
        "workspace_dir": str(workspace_dir.resolve()),
        "handoff_file": state.get("handoff_file"),
        "next_action": state.get("next_action"),
        "error": state.get("last_error"),
        "artifacts": workspace_artifacts(workspace_dir),
        "generated_at": now_utc_iso(),
    }
    result_path = result_file(workspace_dir)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def set_stage(
    state: dict[str, Any],
    *,
    status: str,
    stage: str,
    handoff_file: str | None,
    next_action: dict[str, Any] | None,
    error: str | None = None,
) -> None:
    state["status"] = status
    state["current_stage"] = stage
    state["handoff_file"] = handoff_file
    state["next_action"] = next_action
    state["last_error"] = error


def set_running(state: dict[str, Any], *, stage: str, message: str) -> None:
    set_stage(
        state,
        status=STATUS_RUNNING,
        stage=stage,
        handoff_file=state.get("handoff_file"),
        next_action={
            "actor": "system",
            "summary": message,
            "required_reads": [],
            "submit_via": "",
        },
        error=None,
    )


def failure(
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
    stage: str,
    error: str,
) -> tuple[int, dict[str, Any]]:
    set_stage(
        state,
        status=STATUS_FAILED,
        stage=stage,
        handoff_file=state.get("handoff_file"),
        next_action=state.get("next_action"),
        error=error,
    )
    save_state(state_path, state)
    return 1, write_result(workspace_dir, state, message=error)


def reject_action(
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
    error: str,
) -> tuple[int, dict[str, Any]]:
    state["last_error"] = error
    save_state(state_path, state)
    return 1, write_result(workspace_dir, state, message=error)


def prepare_handoff_payload(prepare_context: dict[str, Any], workspace_dir: Path) -> dict[str, Any]:
    return {
        "stage": STAGE_APP_BUSINESS_GUESS,
        "goal": "Read workspace HTML and metadata catalogs, then recommend app_id/app_code/business_code with backups.",
        "input": {
            "workspace_html": prepare_context.get("workspace_html"),
            "prepare_context_json": str((workspace_dir / "prepare_context.json").resolve()),
            "all_apps_catalog_json": str((workspace_dir / "all_apps_catalog.json").resolve()),
            "all_business_lines_catalog_json": str((workspace_dir / "all_business_lines_catalog.json").resolve()),
        },
        "output_schema": {
            "recommendation": {
                "app_id": "string",
                "app_code": "string",
                "business_code": "string",
                "reason": "string",
            },
            "alternatives": [
                {
                    "app_id": "string",
                    "app_code": "string",
                    "business_code": "string",
                    "reason": "string",
                }
            ],
        },
        "constraints": [
            "Only output a JSON object.",
            "Do not fabricate ids/codes not present in local catalogs.",
            "Provide exactly one primary recommendation and up to three alternatives.",
        ],
    }


def llm_output_handoff_payload(workspace_dir: Path) -> dict[str, Any]:
    prepare_context = safe_json_load(resolve_prepare_path(workspace_dir))
    return {
        "stage": STAGE_LLM_OUTPUT_DESIGN,
        "goal": "Generate llm_output JSON for real user-reachable interactions, referencing real data-ai-id selectors and reusing existing section/element/field metadata whenever possible.",
        "input": {
            "workspace_html": prepare_context.get("workspace_html"),
            "app_business_confirm_json": str((workspace_dir / "app_business_confirm.json").resolve()),
            "template_json": str((repo_root() / "templates" / "llm_tracking_output_template.json").resolve()),
            "all_sections_catalog_json": str((workspace_dir / "all_sections_catalog.json").resolve()),
            "all_elements_catalog_json": str((workspace_dir / "all_elements_catalog.json").resolve()),
            "all_fields_catalog_json": str((workspace_dir / "all_fields_catalog.json").resolve()),
        },
        "output_schema": {
            "page_name": "string",
            "page_code": "camelCase",
            "regions": [
                {
                    "data_ai_id": "string",
                    "section_name": "string",
                    "section_code": "camelCase",
                    "section_id": "existing section id when reusing",
                    "element_name": "string",
                    "element_code": "camelCase",
                    "element_id": "existing element id when reusing",
                    "action": "allowed action",
                    "action_id": "camelCase",
                    "action_fields": [
                        {
                            "fieldName": "string",
                            "fieldCode": "camelCase",
                            "field_id": "existing field id when reusing",
                        }
                    ],
                }
            ],
        },
        "constraints": [
            "Only output a JSON object.",
            "Do not include runtime_hints or page_runtime_hints.",
            "Every data_ai_id must exist in workspace HTML.",
            "Only design events that are reachable through a real user path in the current workspace HTML/JS flow.",
            "Do not add click events for hidden or disabled controls, auto-advanced steps, auto-submitted branches, or controls that never become explicitly clickable for the user.",
            "When a prior selection already auto-advances or auto-finishes the flow, keep the real selection click and do not also design a synthetic continue/start button click unless that button is actually visible and manually clickable on that path.",
            "Read local all_sections_catalog.json / all_elements_catalog.json / all_fields_catalog.json before inventing new section, element, or field metadata.",
            "Prefer reusing existing section_id/section_code, element_id/element_code, and field_id/fieldCode when a close match already exists.",
            "Only invent a new section_code, element_code, or fieldCode when no suitable existing catalog entry fits the region semantics.",
        ],
    }


def manual_implementation_handoff_payload(workspace_dir: Path) -> dict[str, Any]:
    return {
        "stage": STAGE_MANUAL_IMPLEMENTATION,
        "goal": "Implement tracking code manually in workspace copy and keep business behavior unchanged.",
        "input": {
            "tracking_schema_json": str((workspace_dir / "tracking_schema.json").resolve()),
            "implementation_guide_md": str((workspace_dir / "openclaw_tracking_implementation.md").resolve()),
        },
        "constraints": [
            "Edit only files under current workspace session.",
            "Changes must be fail-open and non-breaking for existing behavior.",
            "After edits, rerun harness with --implementation-done.",
        ],
    }


def review_fix_handoff_payload(workspace_dir: Path) -> dict[str, Any]:
    return {
        "stage": STAGE_REVIEW_FIX,
        "goal": "Fix issues reported by implementation review and rerun closed loop.",
        "input": {
            "implementation_review_json": str((workspace_dir / "implementation_review.json").resolve()),
            "tracking_schema_json": str((workspace_dir / "tracking_schema.json").resolve()),
        },
    }


def runtime_fix_handoff_payload(workspace_dir: Path) -> dict[str, Any]:
    return {
        "stage": STAGE_RUNTIME_FIX,
        "goal": "Use runtime browser session to cover remaining schema events until validation gate passes.",
        "input": {
            "runtime_browser_preflight_json": str((workspace_dir / "runtime_browser_preflight.json").resolve()),
            "runtime_browser_verification_json": str((workspace_dir / "runtime_browser_verification.json").resolve()),
            "validation_gate_json": str((workspace_dir / "validation_gate.json").resolve()),
        },
    }


def set_runtime_fix_waiting(state: dict[str, Any], *, workspace_dir: Path, summary: str) -> None:
    set_waiting_agent(
        state,
        stage=STAGE_RUNTIME_FIX,
        handoff_file=write_handoff((workspace_dir / "handoff_runtime_fix.json").resolve(), runtime_fix_handoff_payload(workspace_dir)),
        summary=summary,
        required_reads=[
            str((workspace_dir / "runtime_browser_preflight.json").resolve()),
            str((workspace_dir / "runtime_browser_verification.json").resolve()),
            str((repo_root() / "references" / "runtime_verification.md").resolve()),
        ],
        submit_via=(
            f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" '
            '--runtime-start | --runtime-act-json ... | --runtime-assert-json ... | --runtime-check'
        ),
    )


def set_waiting_agent(
    state: dict[str, Any],
    *,
    stage: str,
    handoff_file: str | None,
    summary: str,
    required_reads: list[str],
    submit_via: str,
) -> None:
    set_stage(
        state,
        status=STATUS_WAITING_AGENT,
        stage=stage,
        handoff_file=handoff_file,
        next_action={
            "actor": "agent",
            "summary": summary,
            "required_reads": required_reads,
            "submit_via": submit_via,
        },
    )


def set_waiting_user(
    state: dict[str, Any],
    *,
    stage: str,
    summary: str,
    required_reads: list[str],
    submit_via: str,
    recommended: dict[str, Any] | None = None,
) -> None:
    next_action: dict[str, Any] = {
        "actor": "user",
        "summary": summary,
        "required_reads": required_reads,
        "submit_via": submit_via,
    }
    if isinstance(recommended, dict):
        next_action["recommended"] = recommended
    set_stage(
        state,
        status=STATUS_WAITING_USER,
        stage=stage,
        handoff_file=None,
        next_action=next_action,
    )


def resolve_prepare_path(workspace_dir: Path) -> Path:
    return (workspace_dir / "prepare_context.json").resolve()


def resolve_confirm_path(workspace_dir: Path) -> Path:
    return (workspace_dir / "app_business_confirm.json").resolve()


def resolve_recommendation_path(workspace_dir: Path) -> Path:
    return (workspace_dir / "app_business_recommendation.json").resolve()


def resolve_llm_output_path(workspace_dir: Path) -> Path:
    return (workspace_dir / "llm_output.json").resolve()


def resolve_apply_result_path(workspace_dir: Path) -> Path:
    return (workspace_dir / "apply_result.json").resolve()


def resolve_closed_loop_result_path(workspace_dir: Path) -> Path:
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


def closed_loop_next_action(gate_step: dict[str, Any], *, status: str) -> str:
    gate_artifact = gate_step.get("artifact") if isinstance(gate_step.get("artifact"), dict) else {}
    if status == "passed":
        return "Closed loop passed. Treat validation_gate.json as the source of truth for completion."
    gate_next_action = normalize_text(gate_artifact.get("next_action"))
    if gate_next_action:
        return gate_next_action
    return "Closed loop did not pass. Inspect closed_loop_result.json and validation_gate.json, then rerun."


def write_closed_loop_result(
    workspace_dir: Path,
    *,
    gate_command: list[str],
    gate_step: dict[str, Any],
    require_runtime: str = "always",
) -> dict[str, Any]:
    output_path = resolve_closed_loop_result_path(workspace_dir)
    gate_artifact_path = (workspace_dir / "validation_gate.json").resolve()
    gate_artifact = safe_json_load(gate_artifact_path)
    if not isinstance(gate_artifact, dict):
        gate_artifact = {}
    state_snapshot = workspace_state_snapshot(workspace_dir)
    state_fingerprint = normalize_text(state_snapshot.get("fingerprint"))

    artifact_status = normalize_text(gate_artifact.get("status")).lower()
    step_status = artifact_status or ("passed" if int(gate_step.get("exit_code") or 1) == 0 else "failed")
    step_payload = {
        "name": "run_tracking_validation_gate",
        "status": step_status,
        "exit_code": gate_step.get("exit_code"),
        "command": gate_command,
        "stdout": gate_step.get("stdout"),
        "stderr": gate_step.get("stderr"),
        "artifact": gate_artifact,
    }
    final_status = normalize_text(gate_artifact.get("status")).lower() or normalize_text(step_payload.get("status")).lower() or "failed"

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
        "runtime_requirement": (
            gate_artifact.get("runtime_requirement")
            if isinstance(gate_artifact.get("runtime_requirement"), dict)
            else {"mode": require_runtime}
        ),
        "summary": {
            "gate_status": normalize_text(step_payload.get("status")).lower() or None,
            "state_fingerprint": state_fingerprint or None,
            "tracked_state_files": state_snapshot.get("files"),
        },
        "steps": {
            "validation_gate": step_payload,
        },
        "next_action": closed_loop_next_action(step_payload, status=final_status),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def initialize_prepare(
    args: argparse.Namespace,
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    html_path = Path(normalize_text(args.html)).expanduser().resolve()
    if not html_path.exists():
        return failure(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            stage=STAGE_PREPARE_INIT,
            error=f"HTML file not found: {html_path}",
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    state["source_html"] = str(html_path)
    runtime_config = resolve_harness_runtime_config(args)
    config_issues = runtime_config_issues(runtime_config)
    if config_issues:
        recommended = redact_runtime_config(runtime_config, config_issues)
        set_waiting_user(
            state,
            stage=STAGE_CONFIRM_RUNTIME_CONFIG,
            summary=(
                "Confirm runtime config before prepare. "
                f"Missing or unconfirmed: {', '.join(config_issues)}. "
                "Certificate settings are read from config files only."
            ),
            required_reads=runtime_config_required_reads(repo_root()),
            submit_via=prepare_config_submit_command(args, state, html_path),
            recommended=recommended,
        )
        save_state(state_path, state)
        return 0, write_result(workspace_dir, state, message="Runtime config confirmation required before prepare.")

    set_running(state, stage=STAGE_PREPARE_INIT, message="Preparing workspace and downloading catalogs.")
    save_state(state_path, state)
    prepare_json = resolve_prepare_path(workspace_dir)
    cmd = [
        sys.executable,
        str((scripts_dir() / "prepare_tracking_context.py").resolve()),
        str(html_path),
        "--workspace-dir",
        str(workspace_dir),
        "--session-id",
        str(state["session_id"]),
        "--app-page-size",
        str(args.app_page_size),
        "--output",
        str(prepare_json),
        "--json",
    ]
    if normalize_text(args.tracking_env):
        cmd.extend(["--tracking-env", normalize_text(args.tracking_env)])
    if normalize_text(args.tracking_base_url):
        cmd.extend(["--tracking-base-url", normalize_text(args.tracking_base_url)])
    if normalize_text(args.user_name):
        cmd.extend(["--user-name", normalize_text(args.user_name)])

    step = run_command(cmd)
    if step["exit_code"] != 0:
        return failure(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            stage=STAGE_PREPARE_INIT,
            error=f"prepare_tracking_context failed: {normalize_text(step['stderr']) or normalize_text(step['stdout'])}",
        )

    prepare_context = safe_json_load(prepare_json)
    workspace_html = Path(str(prepare_context.get("workspace_html") or "")).expanduser().resolve()
    if prepare_context.get("ok") is not True or not workspace_html.exists():
        return failure(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            stage=STAGE_PREPARE_INIT,
            error="Invalid prepare_context output.",
        )

    handoff_file = write_handoff(
        workspace_dir / "handoff_app_business_guess.json",
        prepare_handoff_payload(prepare_context, workspace_dir),
    )
    state["source_html"] = str(html_path)
    set_waiting_agent(
        state,
        stage=STAGE_APP_BUSINESS_GUESS,
        handoff_file=handoff_file,
        summary="Read workspace HTML and catalogs, then submit app/business recommendation JSON.",
        required_reads=[
            str(workspace_html),
            str(prepare_json),
            str((workspace_dir / "all_apps_catalog.json").resolve()),
            str((workspace_dir / "all_business_lines_catalog.json").resolve()),
            str((repo_root() / "references" / "prepare_and_confirm.md").resolve()),
        ],
        submit_via=f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" --agent-app-business-json "<file>"',
    )
    save_state(state_path, state)
    return 0, write_result(workspace_dir, state, message="Prepare completed. Waiting for agent recommendation.")


def handle_agent_app_business(
    args: argparse.Namespace,
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if state.get("status") != STATUS_WAITING_AGENT or state.get("current_stage") != STAGE_APP_BUSINESS_GUESS:
        return reject_action(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            error="Current stage does not accept --agent-app-business-json.",
        )
    agent_json_path = Path(normalize_text(args.agent_app_business_json)).expanduser().resolve()
    if not agent_json_path.exists():
        return failure(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            stage=STAGE_APP_BUSINESS_GUESS,
            error=f"Agent recommendation JSON not found: {agent_json_path}",
        )

    recommendation_json = resolve_recommendation_path(workspace_dir)
    set_running(state, stage=STAGE_APP_BUSINESS_GUESS, message="Validating agent app/business recommendation.")
    save_state(state_path, state)
    cmd = [
        sys.executable,
        str((scripts_dir() / "validate_app_business_recommendation.py").resolve()),
        "--prepare-context",
        str(resolve_prepare_path(workspace_dir)),
        "--agent-json",
        str(agent_json_path),
        "--output",
        str(recommendation_json),
        "--json",
    ]
    step = run_command(cmd)
    if step["exit_code"] != 0:
        set_waiting_agent(
            state,
            stage=STAGE_APP_BUSINESS_GUESS,
            handoff_file=state.get("handoff_file"),
            summary="Fix app/business recommendation JSON and resubmit.",
            required_reads=[
                str(agent_json_path),
                str(resolve_prepare_path(workspace_dir)),
                str((workspace_dir / "all_apps_catalog.json").resolve()),
                str((workspace_dir / "all_business_lines_catalog.json").resolve()),
            ],
            submit_via=f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" --agent-app-business-json "<file>"',
        )
        state["last_error"] = normalize_text(step["stderr"]) or normalize_text(step["stdout"]) or "Validation failed."
        save_state(state_path, state)
        return 1, write_result(workspace_dir, state, message="App/business recommendation validation failed.")

    recommendation = safe_json_load(recommendation_json)
    recommended = recommendation.get("recommended") if isinstance(recommendation.get("recommended"), dict) else {}
    set_waiting_user(
        state,
        stage=STAGE_CONFIRM_APP_BUSINESS,
        summary="Confirm final app_id/app_code/business_code.",
        required_reads=[
            str(recommendation_json),
            str(resolve_prepare_path(workspace_dir)),
            str((repo_root() / "references" / "prepare_and_confirm.md").resolve()),
        ],
        submit_via=(
            f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" '
            '--confirm-app-id "<id>" --confirm-app-code "<code>" --confirm-business-code "<code>"'
        ),
        recommended=recommended,
    )
    save_state(state_path, state)
    return 0, write_result(workspace_dir, state, message="Recommendation accepted. Waiting for user confirmation.")


def resolve_confirm_inputs(args: argparse.Namespace, workspace_dir: Path) -> tuple[str, str, str]:
    app_id = normalize_text(args.confirm_app_id)
    app_code = normalize_text(args.confirm_app_code)
    business_code = normalize_text(args.confirm_business_code)
    if args.accept_recommendation and (not app_id or not app_code or not business_code):
        recommendation = safe_json_load(resolve_recommendation_path(workspace_dir))
        recommended = recommendation.get("recommended") if isinstance(recommendation.get("recommended"), dict) else {}
        app_id = app_id or normalize_text(recommended.get("app_id"))
        app_code = app_code or normalize_text(recommended.get("app_code"))
        business_code = business_code or normalize_text(recommended.get("business_code"))
    return app_id, app_code, business_code


def handle_user_confirm(
    args: argparse.Namespace,
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if state.get("status") != STATUS_WAITING_USER or state.get("current_stage") != STAGE_CONFIRM_APP_BUSINESS:
        return reject_action(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            error="Current stage does not accept confirm app/business inputs.",
        )

    app_id, app_code, business_code = resolve_confirm_inputs(args, workspace_dir)
    if not (app_id and app_code and business_code):
        set_waiting_user(
            state,
            stage=STAGE_CONFIRM_APP_BUSINESS,
            summary="Confirm app_id/app_code/business_code. Values cannot be empty.",
            required_reads=[
                str(resolve_recommendation_path(workspace_dir)),
                str((workspace_dir / "all_apps_catalog.json").resolve()),
                str((workspace_dir / "all_business_lines_catalog.json").resolve()),
            ],
            submit_via=(
                f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" '
                '--confirm-app-id "<id>" --confirm-app-code "<code>" --confirm-business-code "<code>"'
            ),
        )
        state["last_error"] = "Missing confirmed app/business values."
        save_state(state_path, state)
        return 1, write_result(workspace_dir, state, message="Missing confirmation values.")

    confirm_json = resolve_confirm_path(workspace_dir)
    set_running(state, stage=STAGE_CONFIRM_APP_BUSINESS, message="Validating confirmed app/business values.")
    save_state(state_path, state)
    cmd = [
        sys.executable,
        str((scripts_dir() / "confirm_app_business.py").resolve()),
        "--prepare-context",
        str(resolve_prepare_path(workspace_dir)),
        "--app-id",
        app_id,
        "--app-code",
        app_code,
        "--business-code",
        business_code,
        "--strict",
        "--output",
        str(confirm_json),
        "--json",
    ]
    step = run_command(cmd)
    if step["exit_code"] != 0:
        set_waiting_user(
            state,
            stage=STAGE_CONFIRM_APP_BUSINESS,
            summary="Submitted values failed strict catalog validation. Please confirm again.",
            required_reads=[
                str(resolve_recommendation_path(workspace_dir)),
                str((workspace_dir / "all_apps_catalog.json").resolve()),
                str((workspace_dir / "all_business_lines_catalog.json").resolve()),
            ],
            submit_via=(
                f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" '
                '--confirm-app-id "<id>" --confirm-app-code "<code>" --confirm-business-code "<code>"'
            ),
        )
        state["last_error"] = normalize_text(step["stderr"]) or normalize_text(step["stdout"]) or "Strict confirm failed."
        save_state(state_path, state)
        return 1, write_result(workspace_dir, state, message="Strict app/business confirmation failed.")

    handoff_file = write_handoff(
        workspace_dir / "handoff_llm_output_design.json",
        llm_output_handoff_payload(workspace_dir),
    )
    workspace_html_text = normalize_text(safe_json_load(resolve_prepare_path(workspace_dir)).get("workspace_html"))
    required_reads = [
        str(resolve_confirm_path(workspace_dir)),
        str((workspace_dir / "all_sections_catalog.json").resolve()),
        str((workspace_dir / "all_elements_catalog.json").resolve()),
        str((workspace_dir / "all_fields_catalog.json").resolve()),
        str((repo_root() / "references" / "llm_output_spec.md").resolve()),
        str((repo_root() / "templates" / "llm_tracking_output_template.json").resolve()),
    ]
    if workspace_html_text:
        required_reads.insert(0, str(Path(workspace_html_text).expanduser().resolve()))
    set_waiting_agent(
        state,
        stage=STAGE_LLM_OUTPUT_DESIGN,
        handoff_file=handoff_file,
        summary="Generate llm_output JSON based on workspace HTML and confirmed app/business, keeping only real user-reachable interactions and reusing existing section/element/field metadata whenever possible.",
        required_reads=required_reads,
        submit_via=f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" --agent-llm-output-json "<file>"',
    )
    save_state(state_path, state)
    return 0, write_result(workspace_dir, state, message="App/business confirmed. Waiting for llm_output.")


def handle_agent_llm_output(
    args: argparse.Namespace,
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if state.get("status") != STATUS_WAITING_AGENT or state.get("current_stage") != STAGE_LLM_OUTPUT_DESIGN:
        return reject_action(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            error="Current stage does not accept llm_output submission.",
        )

    agent_llm_path_text = normalize_text(args.agent_llm_output_json)
    agent_llm_path = Path(agent_llm_path_text).expanduser().resolve()
    if not agent_llm_path.exists():
        return failure(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            stage=STAGE_LLM_OUTPUT_DESIGN,
            error=f"llm_output JSON not found: {agent_llm_path}",
        )

    llm_output_json = resolve_llm_output_path(workspace_dir)
    set_running(state, stage=STAGE_LLM_OUTPUT_DESIGN, message="Validating llm_output and generating apply artifacts.")
    save_state(state_path, state)
    validate_cmd = [
        sys.executable,
        str((scripts_dir() / "validate_llm_output.py").resolve()),
        "--prepare-context",
        str(resolve_prepare_path(workspace_dir)),
        "--agent-json",
        str(agent_llm_path),
        "--output",
        str(llm_output_json),
        "--json",
    ]
    validate_step = run_command(validate_cmd)
    if validate_step["exit_code"] != 0:
        set_waiting_agent(
            state,
            stage=STAGE_LLM_OUTPUT_DESIGN,
            handoff_file=state.get("handoff_file"),
            summary="Fix llm_output JSON and resubmit.",
            required_reads=[
                str(agent_llm_path),
                str(resolve_prepare_path(workspace_dir)),
                str((repo_root() / "references" / "llm_output_spec.md").resolve()),
            ],
            submit_via=f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" --agent-llm-output-json "<file>"',
        )
        state["last_error"] = normalize_text(validate_step["stderr"]) or normalize_text(validate_step["stdout"]) or "llm_output validation failed."
        save_state(state_path, state)
        return 1, write_result(workspace_dir, state, message="llm_output validation failed.")

    payload_json = (workspace_dir / "page_document_save_payload.json").resolve()
    apply_cmd = [
        sys.executable,
        str((scripts_dir() / "apply_llm_output.py").resolve()),
        "--prepare-context",
        str(resolve_prepare_path(workspace_dir)),
        "--app-business",
        str(resolve_confirm_path(workspace_dir)),
        "--llm-output",
        str(llm_output_json),
        "--output",
        str(payload_json),
        "--base-revision",
        str(args.base_revision),
        "--save-endpoint",
        normalize_text(args.save_endpoint) or "tracking/page_document/save",
        "--save-timeout",
        str(args.save_timeout),
        "--json",
    ]
    if normalize_text(args.page_binding_id):
        apply_cmd.extend(["--page-binding-id", normalize_text(args.page_binding_id)])
    if normalize_text(args.project_id):
        apply_cmd.extend(["--project-id", normalize_text(args.project_id)])
    if normalize_text(args.tracking_env):
        apply_cmd.extend(["--tracking-env", normalize_text(args.tracking_env)])
    if normalize_text(args.tracking_base_url):
        apply_cmd.extend(["--tracking-base-url", normalize_text(args.tracking_base_url)])
    if normalize_text(args.weblog_app_key):
        apply_cmd.extend(["--weblog-app-key", normalize_text(args.weblog_app_key)])
    if args.weblog_debug:
        apply_cmd.append("--weblog-debug")
    if not args.save:
        apply_cmd.append("--skip-save")

    apply_step = run_command(apply_cmd)
    apply_result = apply_step.get("stdout_json") if isinstance(apply_step.get("stdout_json"), dict) else {}
    resolve_apply_result_path(workspace_dir).write_text(
        json.dumps(apply_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if apply_step["exit_code"] != 0 or apply_result.get("ok") is not True:
        set_waiting_agent(
            state,
            stage=STAGE_LLM_OUTPUT_DESIGN,
            handoff_file=state.get("handoff_file"),
            summary="llm_output apply failed; fix JSON and resubmit.",
            required_reads=[
                str(resolve_apply_result_path(workspace_dir)),
                str(llm_output_json),
                str((repo_root() / "references" / "apply_and_save.md").resolve()),
            ],
            submit_via=f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" --agent-llm-output-json "<file>"',
        )
        state["last_error"] = normalize_text(apply_step["stderr"]) or normalize_text(apply_step["stdout"]) or "apply_llm_output failed."
        save_state(state_path, state)
        return 1, write_result(workspace_dir, state, message="apply_llm_output failed.")

    handoff_file = write_handoff(
        workspace_dir / "handoff_manual_implementation.json",
        manual_implementation_handoff_payload(workspace_dir),
    )
    set_waiting_agent(
        state,
        stage=STAGE_MANUAL_IMPLEMENTATION,
        handoff_file=handoff_file,
        summary="Implement tracking code manually in workspace copy, then run closed loop.",
        required_reads=[
            str((workspace_dir / "tracking_schema.json").resolve()),
            str((workspace_dir / "openclaw_tracking_implementation.md").resolve()),
            str((repo_root() / "references" / "manual_implementation_rules.md").resolve()),
            str((repo_root() / "references" / "weblog_sdk_reference.md").resolve()),
        ],
        submit_via=f'scripts/run_tracking_harness.sh --session-id "{state["session_id"]}" --implementation-done',
    )
    save_state(state_path, state)
    return 0, write_result(workspace_dir, state, message="llm_output applied. Waiting for manual implementation.")


def stage_after_gate_failure(workspace_dir: Path) -> tuple[str, str, dict[str, Any]]:
    review_status = normalize_text(safe_json_load((workspace_dir / "implementation_review.json").resolve()).get("status")).lower()
    if review_status != "passed":
        return (
            STAGE_REVIEW_FIX,
            write_handoff((workspace_dir / "handoff_review_fix.json").resolve(), review_fix_handoff_payload(workspace_dir)),
            {
                "actor": "agent",
                "summary": "Fix implementation review findings and rerun closed loop.",
                "required_reads": [
                    str((workspace_dir / "implementation_review.json").resolve()),
                    str((repo_root() / "references" / "review_protocol.md").resolve()),
                ],
                "submit_via": f'scripts/run_tracking_harness.sh --session-id "{normalize_text(workspace_dir.name)}" --implementation-done',
            },
        )
    return (
        STAGE_RUNTIME_FIX,
        write_handoff((workspace_dir / "handoff_runtime_fix.json").resolve(), runtime_fix_handoff_payload(workspace_dir)),
        {
            "actor": "agent",
            "summary": "Complete runtime browser coverage and rerun validation.",
            "required_reads": [
                str((workspace_dir / "runtime_browser_preflight.json").resolve()),
                str((workspace_dir / "runtime_browser_verification.json").resolve()),
                str((repo_root() / "references" / "runtime_verification.md").resolve()),
                str((repo_root() / "references" / "validation_loop.md").resolve()),
            ],
            "submit_via": (
                f'scripts/run_tracking_harness.sh --session-id "{normalize_text(workspace_dir.name)}" '
                '--runtime-start | --runtime-act-json ... | --runtime-assert-json ... | --runtime-check'
            ),
        },
    )


def handle_implementation_done(
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if state.get("status") != STATUS_WAITING_AGENT or normalize_text(state.get("current_stage")) not in {
        STAGE_MANUAL_IMPLEMENTATION,
        STAGE_REVIEW_FIX,
        STAGE_RUNTIME_FIX,
    }:
        return reject_action(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            error="Current stage does not accept --implementation-done.",
        )

    cmd = [
        sys.executable,
        str((scripts_dir() / "run_tracking_validation_gate.py").resolve()),
        "--workspace-dir",
        str(workspace_dir),
        "--require-runtime",
        "always",
        "--json",
    ]
    set_running(state, stage=normalize_text(state.get("current_stage")) or STAGE_MANUAL_IMPLEMENTATION, message="Running closed loop validation.")
    save_state(state_path, state)
    step = run_command(cmd)
    closed_loop_result = write_closed_loop_result(
        workspace_dir,
        gate_command=cmd,
        gate_step=step,
        require_runtime="always",
    )
    gate_status = normalize_text(closed_loop_result.get("status")).lower()
    if gate_status == "passed":
        set_stage(
            state,
            status=STATUS_DONE,
            stage=STAGE_COMPLETED,
            handoff_file=None,
            next_action=None,
        )
        save_state(state_path, state)
        return 0, write_result(workspace_dir, state, message="Closed loop passed. Validation gate is passed.")

    next_stage, handoff_file, next_action = stage_after_gate_failure(workspace_dir)
    set_stage(
        state,
        status=STATUS_WAITING_AGENT,
        stage=next_stage,
        handoff_file=handoff_file,
        next_action=next_action,
        error=normalize_text(step["stderr"]) or normalize_text(step["stdout"]) or "Closed loop failed.",
    )
    save_state(state_path, state)
    return 1, write_result(workspace_dir, state, message="Closed loop not passed. Follow next_action and retry.")


def runtime_python() -> Path:
    return (repo_root() / ".workspace" / "runtime-verify-venv" / "bin" / "python").resolve()


def handle_runtime_actions(
    args: argparse.Namespace,
    *,
    workspace_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    # Backward compatibility: recover sessions stuck in RUNNING/runtime_fix from earlier harness versions.
    if state.get("status") == STATUS_RUNNING and normalize_text(state.get("current_stage")) == STAGE_RUNTIME_FIX:
        set_runtime_fix_waiting(
            state,
            workspace_dir=workspace_dir,
            summary="Recovered from stale RUNNING state. Continue runtime_browser_session and run --runtime-check until gate passes.",
        )
        save_state(state_path, state)

    if state.get("status") != STATUS_WAITING_AGENT or normalize_text(state.get("current_stage")) not in {
        STAGE_RUNTIME_FIX,
        STAGE_MANUAL_IMPLEMENTATION,
    }:
        return reject_action(
            workspace_dir=workspace_dir,
            state_path=state_path,
            state=state,
            error="Current stage does not accept runtime commands.",
        )

    runtime_id = normalize_text(args.runtime_session_id) or normalize_text(state.get("runtime_session_id")) or "agent-loop"
    state["runtime_session_id"] = runtime_id

    assert_payload_text = normalize_text(args.runtime_assert_json)
    assert_json = safe_json_from_text(assert_payload_text) if assert_payload_text else {}
    assert_event_id = normalize_text(assert_json.get("event_id"))
    assert_action = normalize_text(assert_json.get("action")) or "click"
    if assert_payload_text:
        if not assert_json:
            return failure(
                workspace_dir=workspace_dir,
                state_path=state_path,
                state=state,
                stage=STAGE_RUNTIME_FIX,
                error="--runtime-assert-json must be a valid JSON object.",
            )
        if not assert_event_id:
            return failure(
                workspace_dir=workspace_dir,
                state_path=state_path,
                state=state,
                stage=STAGE_RUNTIME_FIX,
                error='--runtime-assert-json requires non-empty field "event_id".',
            )

    if args.runtime_start:
        set_running(state, stage=STAGE_RUNTIME_FIX, message="Setting up runtime env and starting browser session.")
        save_state(state_path, state)
        setup_cmd = [sys.executable, str((scripts_dir() / "setup_runtime_verify_env.py").resolve()), "--json"]
        setup_step = run_command(setup_cmd)
        if setup_step["exit_code"] != 0:
            set_runtime_fix_waiting(
                state,
                workspace_dir=workspace_dir,
                summary="Runtime env setup failed. Fix and retry runtime commands.",
            )
            state["last_error"] = normalize_text(setup_step["stderr"]) or normalize_text(setup_step["stdout"]) or "setup_runtime_verify_env failed."
            save_state(state_path, state)
            return 1, write_result(workspace_dir, state, message="Runtime env setup failed.")

        preflight_cmd = [
            sys.executable,
            str((scripts_dir() / "prepare_runtime_browser_preflight.py").resolve()),
            "--workspace-dir",
            str(workspace_dir),
            "--json",
        ]
        preflight_step = run_command(preflight_cmd)
        if preflight_step["exit_code"] != 0:
            set_runtime_fix_waiting(
                state,
                workspace_dir=workspace_dir,
                summary="Runtime preflight failed. Fix and retry runtime commands.",
            )
            state["last_error"] = normalize_text(preflight_step["stderr"]) or normalize_text(preflight_step["stdout"]) or "preflight failed."
            save_state(state_path, state)
            return 1, write_result(workspace_dir, state, message="Runtime preflight failed.")

        start_cmd = [
            str(runtime_python()),
            str((scripts_dir() / "runtime_browser_session.py").resolve()),
            "start",
            "--workspace-dir",
            str(workspace_dir),
            "--session-id",
            runtime_id,
            "--reset",
            "--json",
        ]
        start_step = run_command(start_cmd)
        if start_step["exit_code"] != 0:
            set_runtime_fix_waiting(
                state,
                workspace_dir=workspace_dir,
                summary="Runtime browser start failed. Fix and retry runtime commands.",
            )
            state["last_error"] = normalize_text(start_step["stderr"]) or normalize_text(start_step["stdout"]) or "runtime start failed."
            save_state(state_path, state)
            return 1, write_result(workspace_dir, state, message="Runtime browser start failed.")

    if normalize_text(args.runtime_act_json):
        set_running(state, stage=STAGE_RUNTIME_FIX, message="Running runtime act step.")
        save_state(state_path, state)
        act_cmd = [
            str(runtime_python()),
            str((scripts_dir() / "runtime_browser_session.py").resolve()),
            "act",
            "--workspace-dir",
            str(workspace_dir),
            "--session-id",
            runtime_id,
            "--step-json",
            args.runtime_act_json,
            "--json",
        ]
        act_step = run_command(act_cmd)
        if act_step["exit_code"] != 0:
            set_runtime_fix_waiting(
                state,
                workspace_dir=workspace_dir,
                summary="Runtime act failed. Fix the action payload and retry.",
            )
            state["last_error"] = normalize_text(act_step["stderr"]) or normalize_text(act_step["stdout"]) or "runtime act failed."
            save_state(state_path, state)
            return 1, write_result(workspace_dir, state, message="Runtime act failed.")

    if assert_event_id:
        set_running(state, stage=STAGE_RUNTIME_FIX, message="Running runtime assert step.")
        save_state(state_path, state)
        assert_cmd = [
            str(runtime_python()),
            str((scripts_dir() / "runtime_browser_session.py").resolve()),
            "assert",
            "--workspace-dir",
            str(workspace_dir),
            "--session-id",
            runtime_id,
            "--event-id",
            assert_event_id,
            "--action",
            assert_action,
            "--json",
        ]
        assert_step = run_command(assert_cmd)
        if assert_step["exit_code"] != 0:
            set_runtime_fix_waiting(
                state,
                workspace_dir=workspace_dir,
                summary="Runtime assert failed. Fix the assertion payload and retry.",
            )
            state["last_error"] = normalize_text(assert_step["stderr"]) or normalize_text(assert_step["stdout"]) or "runtime assert failed."
            save_state(state_path, state)
            return 1, write_result(workspace_dir, state, message="Runtime assert failed.")

    if args.runtime_check:
        set_running(state, stage=STAGE_RUNTIME_FIX, message="Running runtime validation gate check.")
        save_state(state_path, state)
        check_cmd = [
            sys.executable,
            str((scripts_dir() / "run_tracking_validation_gate.py").resolve()),
            "--workspace-dir",
            str(workspace_dir),
            "--json",
        ]
        check_step = run_command(check_cmd)
        gate_status = normalize_text(safe_json_load((workspace_dir / "validation_gate.json").resolve()).get("status")).lower()
        if check_step["exit_code"] == 0 and gate_status == "passed":
            set_stage(
                state,
                status=STATUS_DONE,
                stage=STAGE_COMPLETED,
                handoff_file=None,
                next_action=None,
            )
            save_state(state_path, state)
            return 0, write_result(workspace_dir, state, message="Runtime validation passed. Flow completed.")

        next_stage, handoff_file, next_action = stage_after_gate_failure(workspace_dir)
        set_stage(
            state,
            status=STATUS_WAITING_AGENT,
            stage=next_stage,
            handoff_file=handoff_file,
            next_action=next_action,
            error=normalize_text(check_step["stderr"]) or normalize_text(check_step["stdout"]) or "Validation gate failed.",
        )
        save_state(state_path, state)
        return 1, write_result(workspace_dir, state, message="Runtime check not passed.")

    set_runtime_fix_waiting(
        state,
        workspace_dir=workspace_dir,
        summary="Continue runtime_browser_session and run --runtime-check until gate passes.",
    )
    save_state(state_path, state)

    return 0, write_result(workspace_dir, state, message="Runtime command finished.")


def main() -> int:
    args = parse_args()
    session_id = normalize_session_id(args)
    if not session_id and normalize_text(args.html):
        session_id = make_session_id()
    if not session_id:
        raise SystemExit("session_id is required unless initializing with --html.")

    workspace_root = workspace_root_from_args(args)
    workspace_dir = (workspace_root / session_id).resolve()
    state_path = state_file(workspace_dir)
    result_path = result_file(workspace_dir)

    if args.reset_all:
        if state_path.exists():
            state_path.unlink()
        if result_path.exists():
            result_path.unlink()

    state = load_state(state_path, session_id=session_id, workspace_dir=workspace_dir)
    state["session_id"] = session_id
    state["workspace_dir"] = str(workspace_dir)

    has_agent_reco = bool(normalize_text(args.agent_app_business_json))
    has_confirm = bool(
        normalize_text(args.confirm_app_id)
        or normalize_text(args.confirm_app_code)
        or normalize_text(args.confirm_business_code)
        or args.accept_recommendation
    )
    has_llm_output = bool(normalize_text(args.agent_llm_output_json))
    has_impl_done = bool(args.implementation_done)
    has_runtime = bool(
        args.runtime_start
        or normalize_text(args.runtime_act_json)
        or normalize_text(args.runtime_assert_json)
        or args.runtime_check
    )
    current_stage = normalize_text(state.get("current_stage"))
    has_init = bool(normalize_text(args.html)) and (
        not current_stage
        or (
            state.get("status") == STATUS_WAITING_USER
            and current_stage == STAGE_CONFIRM_RUNTIME_CONFIG
        )
    )

    action_count = sum([has_init, has_agent_reco, has_confirm, has_llm_output, has_impl_done, has_runtime])
    if action_count > 1:
        raise SystemExit("Only one action can be submitted per invocation.")

    if has_init:
        code, result = initialize_prepare(args, workspace_dir=workspace_dir, state_path=state_path, state=state)
    elif has_agent_reco:
        code, result = handle_agent_app_business(args, workspace_dir=workspace_dir, state_path=state_path, state=state)
    elif has_confirm:
        code, result = handle_user_confirm(args, workspace_dir=workspace_dir, state_path=state_path, state=state)
    elif has_llm_output:
        code, result = handle_agent_llm_output(args, workspace_dir=workspace_dir, state_path=state_path, state=state)
    elif has_impl_done:
        code, result = handle_implementation_done(workspace_dir=workspace_dir, state_path=state_path, state=state)
    elif has_runtime:
        code, result = handle_runtime_actions(args, workspace_dir=workspace_dir, state_path=state_path, state=state)
    else:
        if not normalize_text(state.get("current_stage")):
            raise SystemExit("No active session state. Initialize first with --html.")
        save_state(state_path, state)
        result = write_result(workspace_dir, state, message="Session status unchanged.")
        code = 0 if state.get("status") != STATUS_FAILED else 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
