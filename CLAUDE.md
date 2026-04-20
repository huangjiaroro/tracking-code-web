# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a tracking-code-web repository containing the **LLM自动埋点设计** (LLM Auto Tracking Design) skill. It provides a workflow for designing tracking points (埋点) on HTML pages using LLM assistance.

## Core Workflow (5 Steps)

1. **Prepare** - `python3 scripts/prepare_tracking_context.py`
2. **Confirm App/Business** - `python3 scripts/confirm_app_business.py`
3. **LLM Design** - Let LLM read workspace_html and output tracking draft
4. **Normalize & Save** - `python3 scripts/apply_llm_output.py`
5. **Verify & Implement** - Check outputs and implement tracking code

## Scripts

- `scripts/prepare_tracking_context.py` - Prepares context and injects `data-ai-id` into HTML
- `scripts/confirm_app_business.py` - Confirms app_id, app_code, business_code with user
- `scripts/apply_llm_output.py` - Normalizes LLM output, generates save payload and schema
- `scripts/tracking_llm_utils.py` - Shared utilities for tracking operations

## Key Files

- `references/llm_tracking_output_template.json` - LLM output template for tracking design
- `SKILL.md` - Detailed skill documentation (source of truth for this workflow)

## Session Workspace

All intermediate outputs go to `.workspace/<session>/`:
- `prepare_context.json`, `app_business_confirm.json`, `llm_output.json`
- `page_document_save_payload.json`, `tracking_schema.json`
- `openclaw_tracking_implementation.md` - Code modification guide

## Naming Conventions

- Codes use `camelCase` (letters/numbers only): `pageCode`, `sectionCode`, `elementCode`
- Names can use Chinese: `pageName`, `sectionName`, `elementName`
- Actions limited to: `click/slide/show/hover/stay/dis/pull/dclick/start/press/end`

## Usage

When user asks to design tracking for an HTML file, invoke the `tracking-design-llm` skill and follow the 5-step process in SKILL.md. Always confirm app/business line with user before proceeding.
