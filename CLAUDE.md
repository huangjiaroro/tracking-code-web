# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

This repository contains the `tracking-design-llm` skill for LLM-assisted Weblog tracking design on local HTML pages. `SKILL.md` is the source of truth for the workflow.

## Core Rules

- Keep the source HTML read-only. Work only on `.workspace/<session>/` copies and artifacts.
- Treat app/business confirmation as a hard gate. Do not generate LLM output, save payloads, or tracking code until the user confirms `app_id/app_code/business_code`.
- Resolve app/business values from `all_apps_catalog.json` and `all_business_lines_catalog.json`; do not treat user-facing names as IDs or codes.
- Default to dry-run behavior. Call the real `tracking/page_document/save` API only after explicit user approval.
- Use `[data-ai-id="..."]` selectors first, and read `logmap` values at trigger time.
- Manual tracking changes must be fail-open and must not change original business behavior.
- Before the first `runtime_browser_session.py` run, initialize `.workspace/runtime-verify-venv` with `python3 scripts/setup_runtime_verify_env.py --json`.
- After hand-writing tracking code, submit `scripts/run_tracking_harness.sh --session-id "<session>" --implementation-done --json`. Completion still requires `validation_gate.json.status=passed`.

## Main Workflow

1. Initialize once with `scripts/run_tracking_harness.sh --html "<html_path>" --session-id "<session>" --json`.
2. At `WAITING_AGENT/app_business_guess`, submit agent recommendation JSON with `--agent-app-business-json`.
3. At `WAITING_USER/confirm_app_business`, submit confirmed values with `--confirm-app-id --confirm-app-code --confirm-business-code` (or `--accept-recommendation`).
4. At `WAITING_AGENT/llm_output_design`, submit agent output with `--agent-llm-output-json` (optional `--save` only after explicit approval).
5. At `WAITING_AGENT/manual_implementation`, hand-write tracking code in workspace copy and submit `--implementation-done`.
6. If routed to `review_fix` or `runtime_fix`, follow `harness_result.json.next_action` and continue through `--implementation-done` or runtime commands (`--runtime-start`, `--runtime-act-json`, `--runtime-assert-json`, `--runtime-check`).
7. Finish only when harness reaches `DONE/completed` and `.workspace/<session>/validation_gate.json.status=passed`.

## Key Files

- `SKILL.md` - source of truth for agent instructions.
- `templates/llm_tracking_output_template.json` - required LLM output shape.
- `references/weblog_sdk_reference.md` - Weblog SDK usage and constraints.
- `scripts/tracking_llm_utils.py` - shared helpers.

## Outputs

Session artifacts go under `.workspace/<session>/`, including:

- `harness_state.json`
- `harness_result.json`
- `prepare_context.json`
- `app_business_recommendation.json`
- `app_business_confirm.json`
- `llm_output.json`
- `apply_result.json`
- `page_document_save_payload.json`
- `tracking_schema.json`
- `openclaw_tracking_implementation.md`
- `implementation_review.json`
- `runtime_browser_sessions/`
- `runtime_browser_preflight.json`
- `runtime_browser_verification.json`
- `validation_gate.json`
