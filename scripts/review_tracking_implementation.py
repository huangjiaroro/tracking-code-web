#!/usr/bin/env python3
"""
Review manually implemented tracking changes against the generated schema.

The script is designed to be a completion gate after an agent hand-writes
tracking code. It validates:

- required tracking coverage exists in the implementation
- selectors/anchors from tracking_schema.json still resolve in the target HTML
- risky edits that may break original business logic are not present

Exit codes:
- 0: passed
- 1: failed (blocking errors)
- 2: needs_review (no hard failure, but warnings require manual review)
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, parse_html_dom, read_text, safe_json_load


DISALLOWED_API_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "wrong_sdk_api",
        re.compile(r"\bWL\.(trackEvent|trackPageShow)\b"),
        "Unsupported SDK API detected. Use window.weblog.setConfig/report instead.",
    ),
    (
        "legacy_config",
        re.compile(r"\b__weblog_config\b"),
        "Legacy __weblog_config usage is not allowed.",
    ),
]

RISKY_ADDED_PATTERNS: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "error",
        "overwrite_event_handler",
        re.compile(r"(?<!['\"])\b(?:window|document|[A-Za-z_$][\w$]*)\.(?:onload|onclick|onchange|onsubmit|oninput|onkeydown|onkeyup)\s*="),
        "Directly overwriting native event handlers is risky. Extend existing logic instead of replacing it.",
    ),
    (
        "error",
        "stop_immediate_propagation",
        re.compile(r"\bstopImmediatePropagation\s*\("),
        "stopImmediatePropagation can break original page behavior.",
    ),
    (
        "warn",
        "prevent_default",
        re.compile(r"\bpreventDefault\s*\("),
        "preventDefault may change original interaction behavior; confirm it is required by existing business logic.",
    ),
    (
        "warn",
        "stop_propagation",
        re.compile(r"\bstopPropagation\s*\("),
        "stopPropagation may block existing handlers upstream.",
    ),
    (
        "warn",
        "dom_replace",
        re.compile(r"\b(?:innerHTML\s*=|outerHTML\s*=|replaceChildren\s*\(|replaceWith\s*\()"),
        "DOM replacement is risky in tracking code because it may break existing bindings or state.",
    ),
    (
        "warn",
        "dom_remove",
        re.compile(r"\b(?:removeChild\s*\(|\.remove\s*\()"),
        "Node removal in tracking code is risky and should be reviewed carefully.",
    ),
]

RISKY_REMOVED_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "removed_business_logic",
        re.compile(r"\b(?:addEventListener|onclick|onchange|onsubmit|switchView|render|submit|fetch|axios|request|location\.|history\.)"),
        "Existing business/event logic appears to have been removed; review whether tracking changes altered the original flow.",
    ),
]

ATTR_SELECTOR_RE = re.compile(
    r"^(?:(?P<tag>[A-Za-z][A-Za-z0-9_-]*)\s*)?\[(?P<attr>[A-Za-z_:][-A-Za-z0-9_:.]*)=\"(?P<value>(?:\\.|[^\"])*)\"\]$"
)
CLASS_SELECTOR_RE = re.compile(
    r"^(?P<tag>[A-Za-z][A-Za-z0-9_-]*)?(?P<classes>(?:\.[A-Za-z0-9_-]+)+)$"
)
ID_SELECTOR_RE = re.compile(r"^#(?P<id>[A-Za-z][A-Za-z0-9_:\-\.]*)$")

HTML_SUFFIXES = {".html", ".htm"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review manual tracking implementation against tracking_schema.json.")
    parser.add_argument("--workspace-dir", required=True, help="Path to the tracking workspace session directory.")
    parser.add_argument("--schema-path", default="", help="Optional path to tracking_schema.json.")
    parser.add_argument(
        "--target-file",
        default="",
        help="Implementation file to review. Defaults to tracking_schema implementation_target_html/workspace_html.",
    )
    parser.add_argument(
        "--html-file",
        default="",
        help="HTML file used for selector/anchor verification. Defaults to target-file when it is HTML.",
    )
    parser.add_argument(
        "--baseline-file",
        default="",
        help="Baseline file for diff review. Defaults to implementation_baseline.html, then target-file.bak, then source_html.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output path for implementation_review.json. Default: <workspace-dir>/implementation_review.json",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def existing_file(path_text: str) -> Path | None:
    text = normalize_text(path_text)
    if not text:
        return None
    path = Path(text).expanduser().resolve()
    return path if path.exists() else None


def resolve_schema_path(args: argparse.Namespace, workspace_dir: Path) -> Path:
    explicit = existing_file(args.schema_path)
    if explicit:
        return explicit
    return (workspace_dir / "tracking_schema.json").resolve()


def resolve_target_file(args: argparse.Namespace, schema: dict[str, Any], workspace_dir: Path) -> Path | None:
    explicit = existing_file(args.target_file)
    if explicit:
        return explicit

    for key in ("implementation_target_html", "workspace_html"):
        candidate = existing_file(str(schema.get(key) or ""))
        if candidate:
            return candidate

    html_files = sorted(path.resolve() for path in workspace_dir.glob("*.html"))
    return html_files[0] if html_files else None


def resolve_html_file(args: argparse.Namespace, schema: dict[str, Any], target_file: Path | None) -> Path | None:
    explicit = existing_file(args.html_file)
    if explicit:
        return explicit

    if target_file and target_file.suffix.lower() in HTML_SUFFIXES and target_file.exists():
        return target_file

    for key in ("implementation_target_html", "workspace_html"):
        candidate = existing_file(str(schema.get(key) or ""))
        if candidate and candidate.suffix.lower() in HTML_SUFFIXES:
            return candidate
    return None


def resolve_baseline_file(args: argparse.Namespace, schema: dict[str, Any], workspace_dir: Path, target_file: Path | None) -> Path | None:
    explicit = existing_file(args.baseline_file)
    if explicit:
        return explicit

    workspace_baseline = existing_file(str(workspace_dir / "implementation_baseline.html"))
    workspace_html = existing_file(str(schema.get("workspace_html") or ""))

    if target_file:
        target_backup = existing_file(str(target_file.with_suffix(target_file.suffix + ".bak")))
        if target_backup:
            return target_backup
        if (
            target_file.suffix.lower() in HTML_SUFFIXES
            and workspace_baseline
            and workspace_html
            and target_file.resolve() == workspace_html.resolve()
        ):
            return workspace_baseline
        return None

    if workspace_baseline:
        return workspace_baseline
    return None


def resolve_output_path(args: argparse.Namespace, workspace_dir: Path) -> Path:
    explicit = normalize_text(args.output)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (workspace_dir / "implementation_review.json").resolve()


def add_finding(
    findings: list[dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    *,
    sample: str | None = None,
    event_id: str | None = None,
    selector: str | None = None,
) -> None:
    finding: dict[str, Any] = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if sample:
        finding["sample"] = sample
    if event_id:
        finding["event_id"] = event_id
    if selector:
        finding["selector"] = selector
    findings.append(finding)


def simple_selector_match(nodes: list[Any], selector: str) -> tuple[bool | None, int]:
    normalized = normalize_text(selector)
    if not normalized:
        return None, 0

    id_match = ID_SELECTOR_RE.fullmatch(normalized)
    if id_match:
        dom_id = id_match.group("id")
        count = sum(1 for node in nodes if normalize_text(node.attrs.get("id")) == dom_id)
        return True, count

    attr_match = ATTR_SELECTOR_RE.fullmatch(normalized)
    if attr_match:
        tag = normalize_text(attr_match.group("tag")).lower()
        attr_name = normalize_text(attr_match.group("attr")).lower()
        value = attr_match.group("value").replace('\\"', '"').replace("\\\\", "\\")
        count = 0
        for node in nodes:
            if tag and node.tag != tag:
                continue
            if normalize_text(node.attrs.get(attr_name)) == value:
                count += 1
        return True, count

    class_match = CLASS_SELECTOR_RE.fullmatch(normalized)
    if class_match:
        tag = normalize_text(class_match.group("tag")).lower()
        classes = [token for token in class_match.group("classes").split(".") if token]
        count = 0
        for node in nodes:
            if tag and node.tag != tag:
                continue
            if all(token in node.class_tokens for token in classes):
                count += 1
        return True, count

    return None, 0


def collect_text_diff(baseline_text: str, target_text: str) -> tuple[list[str], list[str]]:
    added: list[str] = []
    removed: list[str] = []
    for line in difflib.ndiff(baseline_text.splitlines(), target_text.splitlines()):
        if line.startswith("+ "):
            added.append(line[2:])
        elif line.startswith("- "):
            removed.append(line[2:])
    return added, removed


def strip_code_comments(code_text: str) -> str:
    result: list[str] = []
    index = 0
    length = len(code_text)
    in_single_quote = False
    in_double_quote = False
    in_template_string = False

    while index < length:
        char = code_text[index]
        next_char = code_text[index + 1] if index + 1 < length else ""

        if in_single_quote:
            result.append(char)
            if char == "\\" and index + 1 < length:
                result.append(code_text[index + 1])
                index += 2
                continue
            if char == "'":
                in_single_quote = False
            index += 1
            continue

        if in_double_quote:
            result.append(char)
            if char == "\\" and index + 1 < length:
                result.append(code_text[index + 1])
                index += 2
                continue
            if char == '"':
                in_double_quote = False
            index += 1
            continue

        if in_template_string:
            result.append(char)
            if char == "\\" and index + 1 < length:
                result.append(code_text[index + 1])
                index += 2
                continue
            if char == "`":
                in_template_string = False
            index += 1
            continue

        if code_text.startswith("<!--", index):
            index += 4
            while index < length and not code_text.startswith("-->", index):
                if code_text[index] == "\n":
                    result.append("\n")
                index += 1
            if index < length:
                index += 3
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < length and code_text[index] != "\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index < length and not (code_text[index] == "*" and index + 1 < length and code_text[index + 1] == "/"):
                if code_text[index] == "\n":
                    result.append("\n")
                index += 1
            if index < length:
                index += 2
            continue

        if char == "'":
            in_single_quote = True
            result.append(char)
            index += 1
            continue

        if char == '"':
            in_double_quote = True
            result.append(char)
            index += 1
            continue

        if char == "`":
            in_template_string = True
            result.append(char)
            index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def collect_string_aliases(code_text: str) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for match in re.finditer(
        r"""\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<quote>["'])(?P<value>(?:\\.|(?!\2).)*)\2""",
        code_text,
        flags=re.DOTALL,
    ):
        value = match.group("value")
        if not value:
            continue
        aliases.setdefault(value, set()).add(match.group("name"))
    return aliases


def has_event_tracking_call(code_text: str, event_id: str, aliases: dict[str, set[str]]) -> bool:
    escaped_event_id = re.escape(event_id)
    call_prefix = r"(?<![\w$])(?:track[A-Za-z0-9_$]*|report(?:Event|Tracking|Track)[A-Za-z0-9_$]*)\s*\("

    literal_patterns = [
        rf"\b(?:window\.)?weblog(?:\?\.|\.)report\s*\(\s*\{{[\s\S]{{0,800}}?\bid\s*:\s*([\"']){escaped_event_id}\1",
        rf"{call_prefix}\s*([\"']){escaped_event_id}\1",
    ]
    for pattern in literal_patterns:
        if re.search(pattern, code_text):
            return True

    for alias in aliases.get(event_id, set()):
        escaped_alias = re.escape(alias)
        alias_patterns = [
            rf"\b(?:window\.)?weblog(?:\?\.|\.)report\s*\(\s*\{{[\s\S]{{0,800}}?\bid\s*:\s*{escaped_alias}\b",
            rf"{call_prefix}\s*{escaped_alias}\b",
        ]
        for pattern in alias_patterns:
            if re.search(pattern, code_text):
                return True

    return False


def check_disallowed_usage(code_text: str, findings: list[dict[str, Any]]) -> list[str]:
    hits: list[str] = []
    for code, pattern, message in DISALLOWED_API_PATTERNS:
        match = pattern.search(code_text)
        if not match:
            continue
        hits.append(code)
        add_finding(findings, "error", code, message, sample=match.group(0))
    return hits


def check_fail_open(code_text: str, findings: list[dict[str, Any]]) -> dict[str, bool]:
    report_call_present = bool(re.search(r"\b(?:window\.)?weblog(?:\?\.|\.)report\s*\(", code_text))
    set_config_present = bool(re.search(r"\b(?:window\.)?weblog(?:\?\.|\.)setConfig\s*\(", code_text))
    report_guard_present = bool(
        re.search(r"window\.weblog\s*&&\s*window\.weblog\.report", code_text)
        or re.search(r"window\.weblog\?\.report", code_text)
        or re.search(r"if\s*\(\s*!window\.weblog\s*\|\|\s*!window\.weblog\.report", code_text)
        or re.search(r"typeof\s+window\.weblog\.report\s*!==?\s*['\"]function['\"]", code_text)
    )
    config_guard_present = bool(
        re.search(r"window\.weblog\s*&&\s*window\.weblog\.setConfig", code_text)
        or re.search(r"window\.weblog\?\.setConfig", code_text)
        or re.search(r"if\s*\(\s*!window\.weblog\s*\|\|\s*!window\.weblog\.setConfig", code_text)
        or re.search(r"typeof\s+window\.weblog\.setConfig\s*!==?\s*['\"]function['\"]", code_text)
    )

    if not set_config_present:
        add_finding(
            findings,
            "error",
            "missing_sdk_config",
            "Tracking implementation is missing weblog.setConfig(...).",
        )
    elif not config_guard_present:
        add_finding(
            findings,
            "error",
            "unguarded_sdk_config",
            "weblog.setConfig(...) is not guarded. Manual tracking code must fail open when SDK is unavailable.",
        )

    if not report_call_present:
        add_finding(
            findings,
            "error",
            "missing_report_call",
            "Tracking implementation is missing weblog.report(...).",
        )
    elif not report_guard_present:
        add_finding(
            findings,
            "error",
            "unguarded_report_call",
            "weblog.report(...) is not guarded. Manual tracking code must not break original functionality when SDK is unavailable.",
        )

    return {
        "set_config_present": set_config_present,
        "set_config_guarded": config_guard_present,
        "report_call_present": report_call_present,
        "report_call_guarded": report_guard_present,
    }


def check_event_coverage(schema: dict[str, Any], code_text: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_code_text = strip_code_comments(code_text)
    aliases = collect_string_aliases(normalized_code_text)
    raw_events = schema.get("events") if isinstance(schema.get("events"), list) else []
    matched: list[str] = []
    missing: list[str] = []
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        event_id = normalize_text(event.get("id") or event.get("event_name"))
        if not event_id:
            continue
        if has_event_tracking_call(normalized_code_text, event_id, aliases):
            matched.append(event_id)
        else:
            missing.append(event_id)
            add_finding(
                findings,
                "error",
                "missing_event_tracking_call",
                "Tracking event id is not passed to any recognized tracking/report call in the implementation file.",
                event_id=event_id,
            )
    return {
        "matched_count": len(matched),
        "missing_count": len(missing),
        "matched_event_ids": matched,
        "missing_event_ids": missing,
    }


def check_selector_coverage(schema: dict[str, Any], html_file: Path | None, findings: list[dict[str, Any]]) -> dict[str, Any]:
    if html_file is None or html_file.suffix.lower() not in HTML_SUFFIXES or not html_file.exists():
        add_finding(
            findings,
            "warn",
            "missing_html_for_selector_review",
            "No HTML file was available for selector review. Coverage was checked only at code-text level.",
        )
        return {
            "html_file": str(html_file) if html_file else None,
            "matched_count": 0,
            "unmatched_count": 0,
            "unsupported_count": 0,
            "matched_events": [],
            "unmatched_events": [],
            "unsupported_events": [],
        }

    nodes, _, _ = parse_html_dom(html_file)
    raw_events = schema.get("events") if isinstance(schema.get("events"), list) else []
    matched_events: list[str] = []
    unmatched_events: list[dict[str, Any]] = []
    unsupported_events: list[dict[str, Any]] = []
    seen_anchor_ids: set[str] = set()

    for event in raw_events:
        if not isinstance(event, dict):
            continue
        event_id = normalize_text(event.get("id") or event.get("event_name"))
        action = normalize_text(event.get("action")).lower()
        scope = normalize_text(event.get("scope") or event.get("target") or event.get("metadata", {}).get("event_scope")).lower()
        selectors = event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else []

        if action in {"start", "end", "stay"} or (action == "show" and scope == "page" and not selectors):
            matched_events.append(event_id)
            continue

        supported_selector_seen = False
        matched = False
        first_selector = None
        for selector in selectors:
            text = normalize_text(selector)
            if not text:
                continue
            first_selector = first_selector or text
            attr_match = ATTR_SELECTOR_RE.fullmatch(text)
            if attr_match and normalize_text(attr_match.group("attr")).lower() == "data-ai-id":
                seen_anchor_ids.add(attr_match.group("value").replace('\\"', '"').replace("\\\\", "\\"))
            supported, count = simple_selector_match(nodes, text)
            if supported is None:
                continue
            supported_selector_seen = True
            if count > 0:
                matched = True
                break

        if matched:
            matched_events.append(event_id)
        elif supported_selector_seen:
            unmatched_events.append({"event_id": event_id, "selector": first_selector})
            add_finding(
                findings,
                "error",
                "selector_not_found",
                "At least one supported selector from tracking_schema.json no longer matches the target HTML.",
                event_id=event_id,
                selector=first_selector,
            )
        else:
            unsupported_events.append({"event_id": event_id, "selector_candidates": selectors})
            add_finding(
                findings,
                "warn",
                "unsupported_selector_pattern",
                "Selector coverage could not be validated for this event because all selectors were unsupported by the lightweight reviewer.",
                event_id=event_id,
                selector=first_selector,
            )

    return {
        "html_file": str(html_file),
        "matched_count": len(matched_events),
        "unmatched_count": len(unmatched_events),
        "unsupported_count": len(unsupported_events),
        "matched_events": matched_events,
        "unmatched_events": unmatched_events,
        "unsupported_events": unsupported_events,
        "schema_anchor_ids": sorted(seen_anchor_ids),
    }


def check_anchor_preservation(schema: dict[str, Any], html_file: Path | None, findings: list[dict[str, Any]]) -> dict[str, Any]:
    if html_file is None or html_file.suffix.lower() not in HTML_SUFFIXES or not html_file.exists():
        return {
            "html_file": str(html_file) if html_file else None,
            "required_anchor_count": 0,
            "missing_anchor_count": 0,
            "missing_anchor_ids": [],
        }

    _, by_data_ai_id, _ = parse_html_dom(html_file)
    raw_events = schema.get("events") if isinstance(schema.get("events"), list) else []
    required_anchor_ids: set[str] = set()
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        selectors = event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else []
        for selector in selectors:
            match = ATTR_SELECTOR_RE.fullmatch(normalize_text(selector))
            if not match:
                continue
            if normalize_text(match.group("attr")).lower() != "data-ai-id":
                continue
            required_anchor_ids.add(match.group("value").replace('\\"', '"').replace("\\\\", "\\"))

    missing = sorted(anchor_id for anchor_id in required_anchor_ids if anchor_id not in by_data_ai_id)
    for anchor_id in missing:
        add_finding(
            findings,
            "error",
            "missing_anchor",
            "A required data-ai-id anchor from tracking_schema.json is missing in the target HTML.",
            selector=f'[data-ai-id="{anchor_id}"]',
        )
    return {
        "html_file": str(html_file),
        "required_anchor_count": len(required_anchor_ids),
        "missing_anchor_count": len(missing),
        "missing_anchor_ids": missing,
    }


def check_diff_risks(baseline_file: Path | None, target_text: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    if baseline_file is None or not baseline_file.exists():
        return {
            "baseline_file": str(baseline_file) if baseline_file else None,
            "skipped": True,
            "skip_reason": "baseline_unavailable",
            "added_line_count": 0,
            "removed_line_count": 0,
            "risky_added": [],
            "risky_removed": [],
        }

    added_lines, removed_lines = collect_text_diff(read_text(baseline_file), target_text)

    risky_added: list[dict[str, Any]] = []
    for line in added_lines:
        normalized = normalize_text(line)
        if not normalized:
            continue
        for severity, code, pattern, message in RISKY_ADDED_PATTERNS:
            if not pattern.search(line):
                continue
            risky_added.append({"severity": severity, "code": code, "line": normalized})
            add_finding(findings, severity, code, message, sample=normalized)

    risky_removed: list[str] = []
    for line in removed_lines:
        normalized = normalize_text(line)
        if not normalized:
            continue
        for code, pattern, message in RISKY_REMOVED_PATTERNS:
            if not pattern.search(line):
                continue
            risky_removed.append(normalized)
            add_finding(findings, "warn", code, message, sample=normalized)
            break

    removed_nonempty = [normalize_text(line) for line in removed_lines if normalize_text(line)]
    if len(removed_nonempty) > 8:
        add_finding(
            findings,
            "warn",
            "large_removal",
            "Manual tracking implementation removed many existing lines. Confirm the original functionality still behaves the same.",
            sample=removed_nonempty[0],
        )

    return {
        "baseline_file": str(baseline_file) if baseline_file else None,
        "added_line_count": len([line for line in added_lines if normalize_text(line)]),
        "removed_line_count": len(removed_nonempty),
        "risky_added": risky_added,
        "risky_removed": risky_removed[:20],
    }


def build_result(
    workspace_dir: Path,
    schema_path: Path,
    target_file: Path | None,
    html_file: Path | None,
    baseline_file: Path | None,
    findings: list[dict[str, Any]],
    checks: dict[str, Any],
) -> dict[str, Any]:
    error_count = sum(1 for item in findings if item.get("severity") == "error")
    warning_count = sum(1 for item in findings if item.get("severity") == "warn")
    if error_count:
        status = "failed"
    elif warning_count:
        status = "needs_review"
    else:
        status = "passed"

    return {
        "ok": status == "passed",
        "status": status,
        "workspace_dir": str(workspace_dir),
        "schema_path": str(schema_path),
        "target_file": str(target_file) if target_file else None,
        "html_file": str(html_file) if html_file else None,
        "baseline_file": str(baseline_file) if baseline_file else None,
        "error_count": error_count,
        "warning_count": warning_count,
        "completion_gate": "Only treat manual tracking implementation as done when status == 'passed'.",
        "checks": checks,
        "findings": findings,
    }


def main() -> int:
    args = parse_args()
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    schema_path = resolve_schema_path(args, workspace_dir)
    schema = safe_json_load(schema_path)
    if not schema:
        raise SystemExit(f"Schema not found or invalid: {schema_path}")

    target_file = resolve_target_file(args, schema, workspace_dir)
    if target_file is None or not target_file.exists():
        raise SystemExit("Implementation target file not found. Pass --target-file explicitly.")

    html_file = resolve_html_file(args, schema, target_file)
    baseline_file = resolve_baseline_file(args, schema, workspace_dir, target_file)
    output_path = resolve_output_path(args, workspace_dir)

    target_text = read_text(target_file)
    findings: list[dict[str, Any]] = []
    checks: dict[str, Any] = {
        "disallowed_usage": check_disallowed_usage(target_text, findings),
        "sdk_fail_open": check_fail_open(target_text, findings),
        "event_coverage": check_event_coverage(schema, target_text, findings),
        "selector_coverage": check_selector_coverage(schema, html_file, findings),
        "anchor_preservation": check_anchor_preservation(schema, html_file, findings),
        "diff_review": check_diff_risks(baseline_file, target_text, findings),
    }

    result = build_result(workspace_dir, schema_path, target_file, html_file, baseline_file, findings, checks)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["status"] == "passed":
        return 0
    if result["status"] == "needs_review":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
