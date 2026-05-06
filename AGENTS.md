# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with this repository.

## Project Overview

This repository contains the `tracking-design-llm` skill for LLM-assisted Weblog tracking on local HTML pages.
`SKILL.md` is the workflow source of truth; this file only keeps repository-level guardrails.

## Core Rules

- Keep the source HTML read-only. Work only on `.workspace/<session>/` copies and artifacts.
- Treat app/business confirmation as a hard gate. Do not generate LLM output, save payloads, or tracking code until the user confirms `app_id/app_code/business_code`.
- Treat tracking design confirmation as the next hard gate. After LLM output design, confirm the event list, trigger timing, extra fields, and reporting environment before generating apply artifacts or writing tracking code.
- Resolve app/business values from `all_apps_catalog.json` and `all_business_lines_catalog.json`; do not treat user-facing names as IDs or codes.
- Default to dry-run behavior. Call the real `tracking/page_document/save` API only after explicit user approval.
- Use `[data-ai-id="..."]` selectors first, and read `logmap` values at trigger time.
- Manual tracking changes must be fail-open and must not change original business behavior.
- Before the first `runtime_browser_session.py` run, initialize `.workspace/runtime-verify-venv` with `python3 scripts/setup_runtime_verify_env.py --json`.
- After hand-writing tracking code, run `python3 scripts/run_tracking_validation_gate.py --workspace-dir ".workspace/<session>" --json`. Completion now requires both review to pass and `runtime_browser_session` artifacts to satisfy the default runtime gate.

## Workflow Source

- Follow `SKILL.md` routing table and default `harness-first` workflow.
- Use `references/*.md` for phase-specific details.
- Use `EXAMPLES.md` for common execution patterns.

## Key Files

- `SKILL.md`: primary skill definition and output contracts.
- `EXAMPLES.md`: common scenario playbook.
- `references/`: step-by-step constraints and troubleshooting.
- `templates/llm_tracking_output_template.json`: required LLM output shape.
- `references/weblog_sdk_reference.md`: Weblog SDK usage and constraints.
