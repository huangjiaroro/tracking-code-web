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
- After hand-writing tracking code, run `python3 scripts/run_tracking_validation_gate.py --workspace-dir ".workspace/<session>" --json` and only treat the task as complete when `validation_gate.json.status` is `passed`.

## Main Workflow

1. Use `scripts/run_tracking_harness.sh --stop-after-prepare` to create the workspace HTML and recommendations.
2. Show the recommendations and catalog matches, then wait for user confirmation.
3. Generate `.workspace/<session>/llm_output.json` from the workspace HTML and `templates/llm_tracking_output_template.json`.
4. Rerun `scripts/run_tracking_harness.sh` with explicit `--app-id --app-code --business-code --llm-output`.
5. Check `.workspace/<session>/harness_result.json`, `apply_result.json`, `tracking_schema.json`, and `openclaw_tracking_implementation.md`.
6. Hand-write the tracking changes in the workspace HTML or target source based on the schema and implementation guide; do not use an auto-injection script.
7. Run `run_tracking_validation_gate.py`. If review passes but runtime gate fails, use `runtime_browser_session.py start/act/assert` to trigger real interactions until `runtime_browser_verification.json` passes, then rerun the gate.

## Key Files

- `SKILL.md` - source of truth for agent instructions.
- `templates/llm_tracking_output_template.json` - required LLM output shape.
- `references/weblog_sdk_reference.md` - Weblog SDK usage and constraints.
- `scripts/tracking_llm_utils.py` - shared helpers.

## Outputs

Session artifacts go under `.workspace/<session>/`, including:

- `prepare_context.json`
- `app_business_confirm.json`
- `llm_output.json`
- `draft_document.json`
- `change_set.json`
- `page_document_save_payload.json`
- `tracking_schema.json`
- `openclaw_tracking_implementation.md`
- `implementation_baseline.html`
- `implementation_review.json`
- `runtime_browser_sessions/`
- `runtime_browser_verification.json`
- `validation_gate.json`
- `harness_result.json`
