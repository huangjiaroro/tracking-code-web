from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load
from runtime_browser_support import resolve_target_file


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


VAR_NAME_PATTERN = r"[A-Za-z_$][A-Za-z0-9_$]*"
DIRECT_LISTENER_RE = re.compile(
    rf"(?P<var>{VAR_NAME_PATTERN})\.addEventListener\(\s*['\"](?P<dom_event>[^'\"]+)['\"]"
)
FOREACH_SELECTOR_RE = re.compile(
    rf"querySelectorAll\(\s*['\"](?P<selector>[^'\"]+)['\"]\s*\)\.forEach\(\(\s*(?P<item>{VAR_NAME_PATTERN})\b"
)
VIEW_HINT_RE = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*View)")
SWITCH_VIEW_RE = re.compile(r"switchView\(\s*(?P<view>[A-Za-z_$][A-Za-z0-9_$]*)\s*\)")
FUNCTION_PATTERNS = (
    re.compile(rf"function\s+(?P<name>{VAR_NAME_PATTERN})\s*\("),
    re.compile(rf"(?:const|let|var)\s+(?P<name>{VAR_NAME_PATTERN})\s*=\s*function\s*\("),
    re.compile(rf"(?:const|let|var)\s+(?P<name>{VAR_NAME_PATTERN})\s*=\s*\([^)]*\)\s*=>"),
)


def resolve_schema_path(workspace_dir: Path, schema_path_text: str) -> Path:
    text = normalize_text(schema_path_text)
    if text:
        candidate = Path(text).expanduser().resolve()
        if candidate.exists():
            return candidate
    return (workspace_dir / "tracking_schema.json").resolve()


def resolve_target_source(workspace_dir: Path, schema: dict[str, Any], target_file_text: str) -> Path:
    args = SimpleNamespace(target_file=normalize_text(target_file_text))
    return resolve_target_file(args, schema, workspace_dir).resolve()


def load_schema_events(
    schema: dict[str, Any],
    *,
    event_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    events = schema.get("events") if isinstance(schema.get("events"), list) else []
    results: list[dict[str, Any]] = []
    wanted = {normalize_text(value) for value in event_ids or set() if normalize_text(value)}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = normalize_text(event.get("id"))
        if not event_id:
            continue
        if wanted and event_id not in wanted:
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        runtime_hints = event.get("runtime_hints") if isinstance(event.get("runtime_hints"), dict) else {}
        results.append(
            {
                "id": event_id,
                "action": normalize_text(event.get("action")) or None,
                "element_name": normalize_text(event.get("element_name")) or None,
                "selector_candidates": event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else [],
                "section_code": normalize_text(metadata.get("section_code")) or None,
                "section_name": normalize_text(metadata.get("section_name")) or None,
                "runtime_hints": runtime_hints,
            }
        )
    return results


def line_snippet(lines: list[str], center_index: int, *, context_lines: int = 2) -> str:
    start = max(0, center_index - context_lines)
    end = min(len(lines), center_index + context_lines + 1)
    return "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))


def event_call_sites(lines: list[str], event_id: str, *, max_hits: int = 6) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    quoted_event_id = re.compile(rf"(['\"]){re.escape(event_id)}\1")
    for index, line in enumerate(lines):
        if not quoted_event_id.search(line):
            continue
        results.append(
            {
                "line": index + 1,
                "code": line.strip(),
                "snippet": line_snippet(lines, index),
            }
        )
        if len(results) >= max_hits:
            break
    return results


def nearest_function_name(lines: list[str], before_index: int) -> str | None:
    start = max(-1, before_index - 80)
    for index in range(before_index, start, -1):
        line = lines[index]
        for pattern in FUNCTION_PATTERNS:
            match = pattern.search(line)
            if match:
                return normalize_text(match.group("name")) or None
    return None


def nearest_view_hint(lines: list[str], center_index: int, section_code: str | None) -> str | None:
    if section_code:
        return section_code
    start = max(0, center_index - 30)
    end = min(len(lines), center_index + 31)
    for index in range(center_index, start - 1, -1):
        match = SWITCH_VIEW_RE.search(lines[index])
        if match:
            return normalize_text(match.group("view")) or None
    for index in range(start, end):
        match = VIEW_HINT_RE.search(lines[index])
        if match:
            return normalize_text(match.group(1)) or None
    return None


def variable_assignment(lines: list[str], variable_name: str, before_index: int) -> dict[str, Any] | None:
    escaped = re.escape(variable_name)
    patterns = (
        (
            re.compile(
                rf"(?:const|let|var)\s+{escaped}\s*=\s*document\.getElementById\(\s*['\"](?P<id>[^'\"]+)['\"]\s*\)"
            ),
            "getElementById",
        ),
        (
            re.compile(
                rf"(?:const|let|var)\s+{escaped}\s*=\s*document\.querySelector\(\s*['\"](?P<selector>[^'\"]+)['\"]\s*\)"
            ),
            "querySelector",
        ),
        (
            re.compile(
                rf"(?:const|let|var)\s+{escaped}\s*=\s*document\.querySelectorAll\(\s*['\"](?P<selector>[^'\"]+)['\"]\s*\)"
            ),
            "querySelectorAll",
        ),
    )
    for index in range(before_index, -1, -1):
        line = lines[index]
        for pattern, source_kind in patterns:
            match = pattern.search(line)
            if not match:
                continue
            selector = ""
            if source_kind == "getElementById":
                selector = f"#{normalize_text(match.group('id'))}"
            else:
                selector = normalize_text(match.group("selector"))
            return {
                "line": index + 1,
                "source_kind": source_kind,
                "selector": selector or None,
                "code": line.strip(),
            }
    return None


def dynamic_element_selector(lines: list[str], variable_name: str, before_index: int) -> dict[str, Any] | None:
    start = max(0, before_index - 40)
    escaped = re.escape(variable_name)
    create_pattern = re.compile(
        rf"(?:const|let|var)\s+{escaped}\s*=\s*document\.createElement\(\s*['\"](?P<tag>[^'\"]+)['\"]\s*\)"
    )
    class_pattern = re.compile(rf"{escaped}\.className\s*=\s*['\"](?P<class_name>[^'\"]+)['\"]")
    id_pattern = re.compile(rf"{escaped}\.id\s*=\s*['\"](?P<id>[^'\"]+)['\"]")

    tag_name = ""
    class_name = ""
    dom_id = ""
    create_line = None
    create_code = None
    for index in range(before_index, start - 1, -1):
        line = lines[index]
        if not tag_name:
            create_match = create_pattern.search(line)
            if create_match:
                tag_name = normalize_text(create_match.group("tag"))
                create_line = index + 1
                create_code = line.strip()
        if not class_name:
            class_match = class_pattern.search(line)
            if class_match:
                class_name = normalize_text(class_match.group("class_name"))
        if not dom_id:
            id_match = id_pattern.search(line)
            if id_match:
                dom_id = normalize_text(id_match.group("id"))
        if tag_name and (class_name or dom_id):
            break

    if not tag_name:
        return None
    if dom_id:
        selector = f"#{dom_id}"
    else:
        selector = tag_name
        if class_name:
            classes = ".".join(part for part in re.split(r"\s+", class_name) if part)
            if classes:
                selector = f"{selector}.{classes}"
    return {
        "line": create_line,
        "source_kind": "dynamicElement",
        "selector": selector or None,
        "code": create_code,
    }


def foreach_selector(lines: list[str], item_variable: str, before_index: int) -> dict[str, Any] | None:
    start = max(0, before_index - 20)
    escaped = re.escape(item_variable)
    pattern = re.compile(
        rf"querySelectorAll\(\s*['\"](?P<selector>[^'\"]+)['\"]\s*\)\.forEach\(\(\s*{escaped}\b"
    )
    for index in range(before_index, start - 1, -1):
        line = lines[index]
        match = pattern.search(line)
        if not match:
            continue
        return {
            "line": index + 1,
            "selector": normalize_text(match.group("selector")) or None,
            "code": line.strip(),
        }
    return None


def preferred_runtime_selector(
    *,
    event: dict[str, Any],
    binding_selector: str | None,
    view_hint: str | None,
) -> str | None:
    if event.get("action") == "click" and binding_selector:
        return binding_selector
    if event.get("action") == "show" and view_hint:
        return f"#{view_hint}.active"
    selectors = event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else []
    for selector in selectors:
        normalized = normalize_text(selector)
        if normalized:
            return normalized
    return None


def prerequisite_hints(
    *,
    event: dict[str, Any],
    binding_selector: str | None,
    view_hint: str | None,
) -> list[str]:
    hints: list[str] = []
    action = normalize_text(event.get("action")).lower()
    selectors = [
        normalize_text(selector)
        for selector in event.get("selector_candidates")
        if normalize_text(selector)
    ] if isinstance(event.get("selector_candidates"), list) else []
    if action == "click" and view_hint:
        hints.append(f"Reach `#{view_hint}.active` before triggering this event.")
    if action == "show" and view_hint:
        hints.append(f"Wait until `#{view_hint}.active` is visible before checking this report.")
    if binding_selector and binding_selector not in selectors:
        hints.append(f"Prefer source-bound selector `{binding_selector}` over container-style schema candidates.")
    runtime_hints = event.get("runtime_hints") if isinstance(event.get("runtime_hints"), dict) else {}
    pre_steps = runtime_hints.get("pre_steps") if isinstance(runtime_hints.get("pre_steps"), list) else []
    pre_selectors = [
        normalize_text(step.get("selector"))
        for step in pre_steps
        if isinstance(step, dict) and normalize_text(step.get("selector"))
    ]
    if pre_selectors:
        hints.append("Schema runtime_hints already suggest prerequisite selectors: " + ", ".join(pre_selectors[:3]) + (", ..." if len(pre_selectors) > 3 else ""))
    return hints


def binding_details(lines: list[str], event: dict[str, Any], call_line: int) -> dict[str, Any]:
    line_index = max(0, call_line - 1)
    function_name = nearest_function_name(lines, line_index)
    view_hint = nearest_view_hint(lines, line_index, normalize_text(event.get("section_code")) or None)

    for index in range(line_index, max(-1, line_index - 20), -1):
        line = lines[index]
        listener_match = DIRECT_LISTENER_RE.search(line)
        if not listener_match:
            continue
        variable_name = normalize_text(listener_match.group("var")) or None
        dom_event = normalize_text(listener_match.group("dom_event")) or None
        foreach = foreach_selector(lines, variable_name or "", index) if variable_name else None
        assignment = variable_assignment(lines, variable_name or "", index) if variable_name else None
        dynamic_selector = dynamic_element_selector(lines, variable_name or "", index) if variable_name else None
        binding_selector = (
            normalize_text(foreach.get("selector"))
            if isinstance(foreach, dict) and normalize_text(foreach.get("selector"))
            else normalize_text(assignment.get("selector"))
            if isinstance(assignment, dict) and normalize_text(assignment.get("selector"))
            else normalize_text(dynamic_selector.get("selector"))
            if isinstance(dynamic_selector, dict) and normalize_text(dynamic_selector.get("selector"))
            else None
        )
        return {
            "resolution_status": "resolved" if binding_selector or view_hint else "partial",
            "binding_kind": "querySelectorAll.forEach.addEventListener" if foreach else "element.addEventListener",
            "call_line": call_line,
            "listener_line": index + 1,
            "listener_event": dom_event,
            "listener_variable": variable_name,
            "listener_code": line.strip(),
            "enclosing_function": function_name,
            "view_hint": view_hint,
            "binding_selector": binding_selector,
            "binding_selector_source": (
                "querySelectorAll"
                if foreach
                else normalize_text(assignment.get("source_kind"))
                if isinstance(assignment, dict)
                else normalize_text(dynamic_selector.get("source_kind"))
                if isinstance(dynamic_selector, dict)
                else None
            ),
            "binding_selector_line": (
                foreach.get("line")
                if isinstance(foreach, dict)
                else assignment.get("line")
                if isinstance(assignment, dict)
                else dynamic_selector.get("line")
                if isinstance(dynamic_selector, dict)
                else None
            ),
            "binding_selector_code": (
                normalize_text(foreach.get("code"))
                if isinstance(foreach, dict)
                else normalize_text(assignment.get("code"))
                if isinstance(assignment, dict)
                else normalize_text(dynamic_selector.get("code"))
                if isinstance(dynamic_selector, dict)
                else None
            ),
            "preferred_runtime_selector": preferred_runtime_selector(
                event=event,
                binding_selector=binding_selector,
                view_hint=view_hint,
            ),
            "prerequisite_hints": prerequisite_hints(
                event=event,
                binding_selector=binding_selector,
                view_hint=view_hint,
            ),
        }

    return {
        "resolution_status": "partial" if call_line else "unresolved",
        "binding_kind": "call_site_only",
        "call_line": call_line,
        "listener_line": None,
        "listener_event": None,
        "listener_variable": None,
        "listener_code": None,
        "enclosing_function": function_name,
        "view_hint": view_hint,
        "binding_selector": preferred_runtime_selector(event=event, binding_selector=None, view_hint=view_hint),
        "binding_selector_source": "schema_selector_candidates" if event.get("selector_candidates") else None,
        "binding_selector_line": None,
        "binding_selector_code": None,
        "preferred_runtime_selector": preferred_runtime_selector(event=event, binding_selector=None, view_hint=view_hint),
        "prerequisite_hints": prerequisite_hints(event=event, binding_selector=None, view_hint=view_hint),
    }


def event_preflight_item(lines: list[str], event: dict[str, Any]) -> dict[str, Any]:
    call_sites = event_call_sites(lines, normalize_text(event.get("id")) or "")
    primary_call_line = call_sites[0]["line"] if call_sites else None
    inferred_binding = binding_details(lines, event, primary_call_line or 0) if primary_call_line else {
        "resolution_status": "unresolved",
        "binding_kind": "not_found_in_source",
        "call_line": None,
        "listener_line": None,
        "listener_event": None,
        "listener_variable": None,
        "listener_code": None,
        "enclosing_function": None,
        "view_hint": normalize_text(event.get("section_code")) or None,
        "binding_selector": preferred_runtime_selector(
            event=event,
            binding_selector=None,
            view_hint=normalize_text(event.get("section_code")) or None,
        ),
        "binding_selector_source": "schema_selector_candidates" if event.get("selector_candidates") else None,
        "binding_selector_line": None,
        "binding_selector_code": None,
        "preferred_runtime_selector": preferred_runtime_selector(
            event=event,
            binding_selector=None,
            view_hint=normalize_text(event.get("section_code")) or None,
        ),
        "prerequisite_hints": prerequisite_hints(
            event=event,
            binding_selector=None,
            view_hint=normalize_text(event.get("section_code")) or None,
        ),
    }
    return {
        "event_id": normalize_text(event.get("id")) or None,
        "action": normalize_text(event.get("action")) or None,
        "element_name": normalize_text(event.get("element_name")) or None,
        "section_code": normalize_text(event.get("section_code")) or None,
        "section_name": normalize_text(event.get("section_name")) or None,
        "selector_candidates": event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else [],
        "call_site_count": len(call_sites),
        "call_sites": call_sites,
        "inferred_binding": inferred_binding,
        "runtime_hints": event.get("runtime_hints") if isinstance(event.get("runtime_hints"), dict) else {},
    }


def build_runtime_browser_preflight(
    *,
    workspace_dir: Path,
    schema_path_text: str = "",
    target_file_text: str = "",
    event_ids: set[str] | None = None,
) -> dict[str, Any]:
    schema_path = resolve_schema_path(workspace_dir, schema_path_text)
    schema = safe_json_load(schema_path)
    if not schema:
        raise SystemExit(f"Schema not found or invalid: {schema_path}")
    target_file = resolve_target_source(workspace_dir, schema, target_file_text)
    lines = target_file.read_text(encoding="utf-8").splitlines()
    items = [
        event_preflight_item(lines, event)
        for event in load_schema_events(schema, event_ids=event_ids)
    ]
    resolved_event_ids = [
        normalize_text(item.get("event_id"))
        for item in items
        if normalize_text(item.get("event_id"))
        and normalize_text(item.get("inferred_binding", {}).get("resolution_status")).lower() == "resolved"
    ]
    partially_resolved_event_ids = [
        normalize_text(item.get("event_id"))
        for item in items
        if normalize_text(item.get("event_id"))
        and normalize_text(item.get("inferred_binding", {}).get("resolution_status")).lower() == "partial"
    ]
    unresolved_event_ids = [
        normalize_text(item.get("event_id"))
        for item in items
        if normalize_text(item.get("event_id"))
        and normalize_text(item.get("inferred_binding", {}).get("resolution_status")).lower() == "unresolved"
    ]
    return {
        "ok": True,
        "status": "prepared",
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "schema_path": str(schema_path),
        "target_file": str(target_file),
        "inputs": {
            "schema_path": str(schema_path),
            "schema_sha256": file_sha256(schema_path),
            "target_file": str(target_file),
            "target_file_sha256": file_sha256(target_file),
        },
        "summary": {
            "event_count": len(items),
            "resolved_event_count": len(resolved_event_ids),
            "partial_event_count": len(partially_resolved_event_ids),
            "unresolved_event_count": len(unresolved_event_ids),
            "resolved_event_ids": resolved_event_ids,
            "partial_event_ids": partially_resolved_event_ids,
            "unresolved_event_ids": unresolved_event_ids,
        },
        "items": items,
        "next_step": (
            "Read this source preflight before starting runtime_browser_session. "
            "Use each item's inferred_binding.preferred_runtime_selector, view_hint, and prerequisite_hints "
            "to plan the first browser pass. If runtime coverage still fails, focus on the uncovered event_id entries here before continuing act/assert."
        ),
    }


def write_runtime_browser_preflight(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
