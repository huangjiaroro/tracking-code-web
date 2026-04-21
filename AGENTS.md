# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with this repository.

## Project Overview

This repository contains the `tracking-design-llm` skill for LLM-assisted Weblog tracking on local HTML pages.
`SKILL.md` is the workflow source of truth; this file only keeps repository-level guardrails.

## Core Rules

- Keep the source HTML read-only. Work only on `.workspace/<session>/` copies and artifacts.
- Treat app/business confirmation as a hard gate. Do not generate LLM output, save payloads, or tracking code until the user confirms `app_id/app_code/business_code`.
- Resolve app/business values from `all_apps_catalog.json` and `all_business_lines_catalog.json`; do not treat user-facing names as IDs or codes.
- Default to dry-run behavior. Call the real `tracking/page_document/save` API only after explicit user approval.
- Use `[data-ai-id="..."]` selectors first, and read `logmap` values at trigger time.
- Manual tracking changes must be fail-open and must not change original business behavior.
- After hand-writing tracking code, run `python3 scripts/review_tracking_implementation.py --workspace-dir ".workspace/<session>" --json` and only treat the task as complete when `status` is `passed`.

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
