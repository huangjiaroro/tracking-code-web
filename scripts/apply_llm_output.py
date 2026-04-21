#!/usr/bin/env python3
"""
Apply minimal LLM output into normalized tracking save payloads.

Input:
- prepare_context.json
- app_business_confirm.json
- llm_output.json (page_name/page_code/regions)

Output:
- draft_document.json
- change_set.json
- page_document_save_payload.json
- tracking_schema.json
- openclaw_tracking_implementation.md
- save_api_response.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import ssl
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tracking_llm_utils import (
    DEFAULT_WEBLOG_CDN,
    build_selector_candidates,
    ensure_camel_case,
    load_json_or_markdown_json,
    node_role,
    normalize_action,
    normalize_text,
    normalize_tracking_id_part,
    now_utc_iso,
    parse_html_dom,
    safe_json_load,
    summarize_parent_chain,
    to_camel_case,
    unique_strings,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply LLM output to tracking save payload.")
    parser.add_argument("--prepare-context", required=True, help="Path to prepare_context.json")
    parser.add_argument("--app-business", required=True, help="Path to app_business_confirm.json")
    parser.add_argument("--llm-output", required=True, help="Path to llm_output.json")
    parser.add_argument("--page-binding-id", default="", help="Optional page_binding_id")
    parser.add_argument("--project-id", default="", help="Optional project_id")
    parser.add_argument("--base-revision", type=int, default=1, help="base_revision for save payload")
    parser.add_argument("--weblog-app-key", default="", help="Optional weblog appKey")
    parser.add_argument("--weblog-debug", action="store_true", help="Enable weblog debug")
    parser.add_argument("--tracking-base-url", default="", help="Optional override for tracking API base URL.")
    parser.add_argument(
        "--save-endpoint",
        default="tracking/page_document/save",
        help="Save endpoint path. Default: tracking/page_document/save",
    )
    parser.add_argument("--save-timeout", type=int, default=30, help="Timeout seconds for save API call.")
    parser.add_argument("--cert-path", default="", help="Optional p12 cert path for save API call.")
    parser.add_argument("--cert-password", default="", help="Optional p12 cert password for save API call.")
    parser.add_argument("--skip-save", action="store_true", help="Skip real save API call (debug only).")
    parser.add_argument(
        "--output",
        default="",
        help="Path of page_document_save_payload.json. Default in workspace session dir.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser.parse_args()


def empty_change_set() -> dict[str, list[Any]]:
    return {
        "added_regions": [],
        "updated_regions": [],
        "deleted_region_ids": [],
        "rebound_regions": [],
    }


def make_unverified_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    except Exception:
        try:
            ctx.set_ciphers("DEFAULT")
        except Exception:
            pass
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def make_p12_ssl_context(cert_path: str, cert_password: str) -> ssl.SSLContext:
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        pkcs12,
    )

    cert_file = Path(cert_path).expanduser().resolve()
    if not cert_file.exists():
        raise FileNotFoundError(f"Certificate file not found: {cert_file}")

    p12_data = cert_file.read_bytes()
    private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
        p12_data,
        cert_password.encode("utf-8"),
    )
    if private_key is None or certificate is None:
        raise RuntimeError("P12 certificate did not contain both certificate and private key.")

    fd, temp_path = tempfile.mkstemp(suffix=".pem", prefix="tracking_apply_cert_")
    try:
        os.write(
            fd,
            private_key.private_bytes(
                encoding=Encoding.PEM,
                format=PrivateFormat.PKCS8,
                encryption_algorithm=NoEncryption(),
            ),
        )
        os.write(fd, certificate.public_bytes(Encoding.PEM))
        for extra_cert in additional_certs or []:
            os.write(fd, extra_cert.public_bytes(Encoding.PEM))
    finally:
        os.close(fd)

    ctx = make_unverified_ssl_context()
    try:
        ctx.load_cert_chain(certfile=temp_path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
    return ctx


def make_https_opener(base_url: str, cert_path: str | None, cert_password: str | None) -> urllib.request.OpenerDirector:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "https":
        return urllib.request.build_opener()
    if cert_path and cert_password:
        try:
            ctx = make_p12_ssl_context(cert_path, cert_password)
        except Exception:
            ctx = make_unverified_ssl_context()
    else:
        ctx = make_unverified_ssl_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def http_post_json(
    base_url: str,
    endpoint: str,
    body: dict[str, Any],
    cert_path: str | None,
    cert_password: str | None,
    timeout: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    payload_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        data=payload_bytes,
    )
    opener = make_https_opener(base_url, cert_path, cert_password)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def http_get_json(
    base_url: str,
    endpoint: str,
    query: dict[str, Any] | None,
    cert_path: str | None,
    cert_password: str | None,
    timeout: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    if query:
        pairs = []
        for key, value in query.items():
            if value in (None, ""):
                continue
            pairs.append((str(key), str(value)))
        if pairs:
            url = f"{url}?{urllib.parse.urlencode(pairs)}"
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    opener = make_https_opener(base_url, cert_path, cert_password)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def resolve_base_url(args: argparse.Namespace, prepare: dict[str, Any]) -> str:
    return normalize_text(args.tracking_base_url or prepare.get("tracking_base_url"))


def resolve_cert(args: argparse.Namespace) -> tuple[str | None, str | None]:
    skill_root = Path(__file__).resolve().parent.parent
    skill_config = safe_json_load(skill_root / "config.json")
    shared_config = safe_json_load(Path.home() / ".skillhub-cli" / "config.json")
    cert_path = normalize_text(
        args.cert_path or skill_config.get("ssl_cert_file") or shared_config.get("ssl_cert_file")
    ) or None
    cert_password = normalize_text(
        args.cert_password or skill_config.get("ssl_cert_password") or shared_config.get("ssl_cert_password")
    ) or None
    return cert_path, cert_password


def infer_business_success(payload: dict[str, Any]) -> bool | None:
    if not isinstance(payload, dict):
        return None

    if payload.get("status_code") in (0, "0"):
        return True
    if payload.get("code") in (0, 200, "0", "200"):
        return True

    data = payload.get("data")
    if isinstance(data, dict) and data.get("success") is True:
        return True

    message = normalize_text(payload.get("status_msg") or payload.get("msg") or payload.get("message")).lower()
    if message in {"success", "ok"} or "成功" in message:
        return True
    if message:
        return False
    return None


def find_prepare_app_candidate(
    prepare: dict[str, Any],
    app_id: str | None,
    app_code: str | None,
) -> dict[str, Any]:
    app_rec = prepare.get("app_recommendation") if isinstance(prepare.get("app_recommendation"), dict) else {}
    candidates = app_rec.get("candidates") if isinstance(app_rec.get("candidates"), list) else []

    normalized_app_id = normalize_text(app_id)
    normalized_app_code = normalize_text(app_code)

    if normalized_app_id:
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if normalize_text(item.get("app_id")) == normalized_app_id:
                return item

    if normalized_app_code:
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if normalize_text(item.get("app_code")) == normalized_app_code:
                return item

    return {}


def load_catalog_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = safe_json_load(path)
    if isinstance(raw.get("items"), list):
        return [item for item in raw.get("items") if isinstance(item, dict)]
    return []


def resolve_app_catalog_path(prepare: dict[str, Any]) -> Path:
    catalog = prepare.get("app_catalog") if isinstance(prepare.get("app_catalog"), dict) else {}
    catalog_path = normalize_text(catalog.get("path"))
    if catalog_path:
        return Path(catalog_path).expanduser().resolve()
    workspace_dir = Path(str(prepare.get("workspace_dir"))).expanduser().resolve()
    return workspace_dir / "all_apps_catalog.json"


def normalize_catalog_app(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "app_id": normalize_text(item.get("app_id") or item.get("appId") or item.get("id")) or None,
        "app_code": normalize_text(item.get("app_code") or item.get("appCode") or item.get("appSign")) or None,
        "app_name": normalize_text(item.get("app_name") or item.get("appName") or item.get("name")) or None,
        "app_key": normalize_text(item.get("app_key") or item.get("appKey")) or None,
    }


def find_catalog_app_candidate(
    prepare: dict[str, Any],
    app_id: str | None,
    app_code: str | None,
    app_name: str | None,
) -> dict[str, Any]:
    path = resolve_app_catalog_path(prepare)
    items = load_catalog_items(path)
    normalized_app_id = normalize_text(app_id)
    normalized_app_code = normalize_text(app_code).lower()
    normalized_app_name = normalize_text(app_name).lower()

    normalized_items = [normalize_catalog_app(item) for item in items if isinstance(item, dict)]
    if normalized_app_id:
        for item in normalized_items:
            if normalize_text(item.get("app_id")) == normalized_app_id:
                return item
    if normalized_app_code:
        for item in normalized_items:
            if normalize_text(item.get("app_code")).lower() == normalized_app_code:
                return item
    if normalized_app_name:
        for item in normalized_items:
            if normalize_text(item.get("app_name")).lower() == normalized_app_name:
                return item
    return {}


def resolve_weblog_app_key(
    manual_app_key: str | None,
    prepare: dict[str, Any],
    app_business: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    page_spec = draft.get("page_speculation") if isinstance(draft.get("page_speculation"), dict) else {}
    app_id = normalize_text(app_business.get("app_id") or page_spec.get("app_id")) or None
    app_code = normalize_text(app_business.get("app_code") or page_spec.get("app_code")) or None
    app_name = normalize_text(app_business.get("app_name") or page_spec.get("app_name")) or None
    app_key = normalize_text(manual_app_key) or None
    # Also check app_business directly for app_key (set by confirm_app_business.py)
    if not app_key:
        app_key = normalize_text(app_business.get("app_key")) or None
    result: dict[str, Any] = {
        "appId": app_id,
        "appCode": app_code,
        "appName": app_name,
        "appKey": app_key,
        "source": "manual_input" if app_key else None,
        "status": "manual_input" if app_key else "pending",
    }
    if app_key:
        return result

    catalog_candidate = find_catalog_app_candidate(prepare, app_id, app_code, app_name)
    catalog_id = normalize_text(catalog_candidate.get("app_id")) or None
    catalog_code = normalize_text(catalog_candidate.get("app_code")) or None
    catalog_name = normalize_text(catalog_candidate.get("app_name")) or None
    catalog_key = normalize_text(catalog_candidate.get("app_key")) or None
    if not app_id and catalog_id:
        app_id = catalog_id
    if not app_code and catalog_code:
        app_code = catalog_code
    if not app_name and catalog_name:
        app_name = catalog_name
    result.update({"appId": app_id, "appCode": app_code, "appName": app_name})
    if catalog_key:
        result.update(
            {
                "appKey": catalog_key,
                "source": "app_catalog",
                "status": "resolved_from_catalog",
            }
        )
        return result

    candidate = find_prepare_app_candidate(prepare, app_id, app_code)
    candidate_id = normalize_text(candidate.get("app_id")) or None
    candidate_code = normalize_text(candidate.get("app_code")) or None
    candidate_name = normalize_text(candidate.get("app_name")) or None
    candidate_key = normalize_text(candidate.get("app_key")) or None

    if not app_id and candidate_id:
        app_id = candidate_id
    if not app_code and candidate_code:
        app_code = candidate_code
    if not app_name and candidate_name:
        app_name = candidate_name
    result.update({"appId": app_id, "appCode": app_code, "appName": app_name})

    if candidate_key:
        result.update(
            {
                "appKey": candidate_key,
                "source": "prepare_candidates",
                "status": "resolved_from_prepare",
            }
        )
    elif app_id or app_code or app_name:
        result.update(
            {
                "source": "local_catalog",
                "status": "missing_app_key_in_catalog",
                "message": "App identified locally but app_key was not found in local catalog/candidates.",
            }
        )
    else:
        result.update({"status": "missing_app_identity", "message": "No app_id/app_code/app_name was available."})
    return result


def normalize_action_fields(raw_fields: Any, default_action: str) -> list[dict[str, Any]]:
    if not isinstance(raw_fields, list):
        return []
    fields: list[dict[str, Any]] = []
    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        field_code = ensure_camel_case(
            item.get("fieldCode") or item.get("field_code") or item.get("code") or item.get("name") or "content",
            fallback="content",
        )
        fields.append(
            {
                "fieldName": normalize_text(item.get("fieldName") or item.get("field_name") or field_code) or field_code,
                "fieldCode": field_code,
                "dataType": normalize_text(item.get("dataType") or item.get("data_type") or "string") or "string",
                "action": normalize_action(item.get("action"), fallback=default_action),
                "remark": normalize_text(item.get("remark") or item.get("description")) or "触发时实时读取",
            }
        )
    return fields


def read_llm_regions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    regions = payload.get("regions")
    if not isinstance(regions, list):
        return []
    return [item for item in regions if isinstance(item, dict)]


def infer_control_type(tag: str, role: str) -> str:
    role_text = normalize_text(role).lower()
    if role_text:
        return role_text
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag in {"input", "textarea"}:
        return "input"
    if tag == "select":
        return "select"
    return "element"


def build_anchor(nodes: list[Any], node: Any) -> dict[str, Any]:
    selectors = build_selector_candidates(node)
    role = node_role(node)
    control_type = infer_control_type(node.tag, role)
    text_exact = normalize_text(node.text) or normalize_text(node.attrs.get("aria-label")) or node.tag
    return {
        "data-ai-id": node.data_ai_id,
        "stable_attributes": {
            "data-ai-id": node.data_ai_id,
            "id": normalize_text(node.attrs.get("id")) or None,
            "data-testid": normalize_text(node.attrs.get("data-testid")) or None,
            "aria-label": normalize_text(node.attrs.get("aria-label")) or None,
            "role": normalize_text(node.attrs.get("role")) or None,
            "title": normalize_text(node.attrs.get("title")) or None,
            "placeholder": normalize_text(node.attrs.get("placeholder")) or None,
            "name": normalize_text(node.attrs.get("name")) or None,
            "type": normalize_text(node.attrs.get("type")) or None,
        },
        "selector_candidates": selectors,
        "inferred_role": control_type,
        "text_signature": {
            "exact": text_exact,
            "normalized": text_exact,
            "accessible_name": normalize_text(node.attrs.get("aria-label")) or text_exact,
            "visible_text": normalize_text(node.text),
            "title": normalize_text(node.attrs.get("title")),
            "placeholder": normalize_text(node.attrs.get("placeholder")),
            "alt": normalize_text(node.attrs.get("alt")),
        },
        "dom_signature": {
            "tag_name": node.tag,
            "class_tokens": node.class_tokens[:8],
            "sibling_index": None,
            "parent_chain": summarize_parent_chain(nodes, node, limit=4),
        },
        "extractor_version": "anchor_v2",
    }


def build_region(
    raw: dict[str, Any],
    index: int,
    nodes: list[Any],
    by_data_ai_id: dict[str, Any],
    page_name: str,
) -> dict[str, Any]:
    data_ai_id = normalize_text(raw.get("data_ai_id") or raw.get("dataAiId") or raw.get("data-ai-id"))
    if not data_ai_id:
        raise ValueError(f"Region #{index + 1} missing data_ai_id")
    node = by_data_ai_id.get(data_ai_id)
    if node is None:
        raise ValueError(f"data_ai_id not found in HTML: {data_ai_id}")

    role = node_role(node)
    control_type = infer_control_type(node.tag, role)
    section_name = normalize_text(raw.get("section_name") or raw.get("sectionName")) or "mainSection"
    section_code = ensure_camel_case(raw.get("section_code") or raw.get("sectionCode") or section_name, fallback="mainSection")
    element_name = normalize_text(raw.get("element_name") or raw.get("elementName")) or (
        normalize_text(node.text) or normalize_text(node.attrs.get("id")) or node.tag
    )
    element_code = ensure_camel_case(raw.get("element_code") or raw.get("elementCode") or element_name, fallback=f"element{index + 1}")
    action = normalize_action(raw.get("action"), fallback="click")
    action_id = ensure_camel_case(raw.get("action_id") or raw.get("actionId") or element_code, fallback=element_code)
    action_fields = normalize_action_fields(raw.get("action_fields"), action)
    anchor = build_anchor(nodes, node)

    return {
        "surface_id": normalize_text(raw.get("surface_id")) or "sf_main",
        "normalized_box": {
            "top_ratio": 0.0,
            "left_ratio": 0.0,
            "width_ratio": 0.0,
            "height_ratio": 0.0,
        },
        "section_name": section_name,
        "region_id": normalize_text(raw.get("region_id")) or f"reg_{action_id}",
        "semantic_context": {
            "page": page_name,
            "block": section_name,
            "element": f"{control_type}: {element_name}",
        },
        "element_id": raw.get("element_id"),
        "allow_action": [action],
        "section_id": raw.get("section_id"),
        "anchor": anchor,
        "function_desc": normalize_text(raw.get("function_desc") or raw.get("description")) or "",
        "action_fields": action_fields,
        "section_code": section_code,
        "id": raw.get("id"),
        "region": {"top": 0, "left": 0, "width": 0, "height": 0},
        "region_number": index + 1,
        "element_name": element_name,
        "element_code": element_code,
        "control_type": control_type,
        "status": "added",
        "data_ai_id": data_ai_id,
        "action": action,
        "action_id": action_id,
    }


def build_page_speculation(llm: dict[str, Any], app_business: dict[str, Any], workspace_html: Path) -> dict[str, Any]:
    page_name = normalize_text(llm.get("page_name")) or workspace_html.stem
    page_code = ensure_camel_case(llm.get("page_code") or to_camel_case(page_name, fallback="page"), fallback="page")
    return {
        "app_id": normalize_text(app_business.get("app_id")) or None,
        "app_name": normalize_text(app_business.get("app_name")) or None,
        "app_code": normalize_text(app_business.get("app_code")) or None,
        "business_line": normalize_text(app_business.get("business_code")) or None,
        "business_code": normalize_text(app_business.get("business_code")) or None,
        "business_line_name": normalize_text(app_business.get("business_line")) or None,
        "page_name": page_name,
        "page_code": page_code,
        "speculation_reason": "LLM parsed HTML and selected regions by data-ai-id.",
    }


def build_draft_document(
    llm: dict[str, Any],
    app_business: dict[str, Any],
    prepare: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace_html = Path(str(prepare.get("workspace_html"))).expanduser().resolve()
    nodes, by_data_ai_id, title = parse_html_dom(workspace_html)
    page_identity = {
        "origin": "file",
        "url": workspace_html.as_uri(),
        "route_key": workspace_html.name,
        "route_pattern": f"^{workspace_html.name}$",
        "title": title or workspace_html.stem,
        "page_signature": f"title={(title or workspace_html.stem).lower()}",
        "signature_version": "v2",
    }
    page_spec = build_page_speculation(llm, app_business, workspace_html)
    raw_regions = read_llm_regions(llm)
    regions = [
        build_region(raw, idx, nodes, by_data_ai_id, page_spec["page_name"])
        for idx, raw in enumerate(raw_regions)
    ]

    draft = {
        "schema_version": "tracking_design_v2",
        "page_speculation": page_spec,
        "regions": regions,
        "page_identity": page_identity,
        "surfaces": [
            {
                "surface_id": "sf_main",
                "surface_key": "main",
                "type": "main",
                "activation_hints": {
                    "role": "main",
                    "aria_label": None,
                    "data_testids": [],
                    "class_tokens": ["main"],
                    "text_signature": "main",
                },
            }
        ],
        "document_revision": 1,
    }
    change_set = empty_change_set()
    change_set["added_regions"] = [copy.deepcopy(region) for region in regions]
    return draft, change_set


def build_event_ids(page_spec: dict[str, Any], region: dict[str, Any]) -> tuple[str, str]:
    app_code = normalize_tracking_id_part(page_spec.get("app_code"), "app")
    biz_code = normalize_tracking_id_part(page_spec.get("business_code"), "biz")
    page_code = normalize_tracking_id_part(page_spec.get("page_code"), "page")
    section_code = normalize_tracking_id_part(region.get("section_code"), "section")
    element_code = normalize_tracking_id_part(region.get("element_code"), "element")
    region_event_id = f"{app_code}_{biz_code}_{page_code}_{section_code}_{element_code}"
    page_event_id = f"{app_code}_{biz_code}_{page_code}"
    return page_event_id, region_event_id


def build_tracking_schema(
    prepare: dict[str, Any],
    draft: dict[str, Any],
    app_key_resolution: dict[str, Any],
    weblog_debug: bool,
) -> dict[str, Any]:
    page_spec = draft.get("page_speculation") if isinstance(draft.get("page_speculation"), dict) else {}
    page_identity = draft.get("page_identity") if isinstance(draft.get("page_identity"), dict) else {}
    resolved_app_key = normalize_text(app_key_resolution.get("appKey")) or None
    resolved_app_id = normalize_text(app_key_resolution.get("appId") or page_spec.get("app_id")) or None
    app_key_source = normalize_text(app_key_resolution.get("source")) or ("manual_input" if resolved_app_key else None)
    app_key_status = normalize_text(app_key_resolution.get("status")) or ("manual_input" if resolved_app_key else "pending")
    page_event_id, _ = build_event_ids(page_spec, {})
    events: list[dict[str, Any]] = [
        {
            "id": page_event_id,
            "event_name": page_event_id,
            "action": "show",
            "selector_candidates": [],
            "logmap": {},
            "metadata": {
                "event_scope": "page",
                "event_rule": "app_code_business_line_page_code",
                "app_code": page_spec.get("app_code"),
                "business_line": page_spec.get("business_code"),
                "page_code": page_spec.get("page_code"),
                "page_name": page_spec.get("page_name"),
                "url": page_identity.get("url"),
                "title": page_identity.get("title"),
            },
            "extra_fields": [],
            "element_name": "页面展示",
            "source": "page_show",
            "scope": "page",
            "target": "page",
        }
    ]
    unresolved: list[dict[str, Any]] = []
    regions = draft.get("regions") if isinstance(draft.get("regions"), list) else []
    for region in regions:
        if not isinstance(region, dict):
            continue
        _, region_event_id = build_event_ids(page_spec, region)
        selectors = []
        anchor = region.get("anchor") if isinstance(region.get("anchor"), dict) else {}
        if isinstance(anchor.get("selector_candidates"), list):
            selectors.extend(anchor.get("selector_candidates"))
        if normalize_text(region.get("data_ai_id")):
            selectors.insert(0, f'[data-ai-id="{normalize_text(region.get("data_ai_id"))}"]')
        selectors = unique_strings(selectors)
        action_fields = region.get("action_fields") if isinstance(region.get("action_fields"), list) else []
        logmap = {
            normalize_text(field.get("fieldCode")): "触发时实时取值"
            for field in action_fields
            if isinstance(field, dict) and normalize_text(field.get("fieldCode"))
        }
        events.append(
            {
                "id": region_event_id,
                "event_name": region_event_id,
                "action": normalize_action(region.get("action"), fallback="click"),
                "selector_candidates": selectors,
                "logmap": logmap,
                "metadata": {
                    "event_rule": "app_code_business_line_page_code_section_code_element_code",
                    "region_id": region.get("region_id"),
                    "section_code": region.get("section_code"),
                    "section_name": region.get("section_name"),
                    "element_code": region.get("element_code"),
                    "element_name": region.get("element_name"),
                    "control_type": region.get("control_type"),
                    "surface_id": region.get("surface_id"),
                    "action_id": region.get("action_id"),
                },
                "extra_fields": action_fields,
                "region_id": region.get("region_id"),
                "source": "tracking_document",
                "element_name": region.get("element_name"),
            }
        )
        if not selectors:
            unresolved.append(
                {
                    "region_id": region.get("region_id"),
                    "id": region_event_id,
                    "reason": "No selector candidates were available for this region.",
                }
            )

    return {
        "schema_version": "openclaw_tracking_injection_v1",
        "generated_at": now_utc_iso(),
        "source_html": prepare.get("source_html"),
        "workspace_html": prepare.get("workspace_html"),
        "implementation_target_html": prepare.get("workspace_html"),
        "ai_data_id": {
            "attribute": "data-ai-id",
            "injected": True,
            "count": prepare.get("ai_data_id", {}).get("injected_count"),
        },
        "weblog_config": {
            "cdn": DEFAULT_WEBLOG_CDN,
            "appKey": resolved_app_key,
            "appId": resolved_app_id,
            "appKeySource": app_key_source,
            "appKeyLookupStatus": app_key_status,
            "debug": bool(weblog_debug),
            "domain": None,
            "logPrefix": None,
        },
        "page_identity": page_identity,
        "events": events,
        "unresolved_regions": unresolved,
    }


def markdown_cell(value: Any) -> str:
    return normalize_text(value).replace("|", "\\|").replace("\n", "<br>")


def render_implementation_guide(schema: dict[str, Any]) -> str:
    events = schema.get("events") if isinstance(schema.get("events"), list) else []
    weblog_config = schema.get("weblog_config") if isinstance(schema.get("weblog_config"), dict) else {}
    page_identity = schema.get("page_identity") if isinstance(schema.get("page_identity"), dict) else {}
    workspace_html = normalize_text(schema.get("workspace_html"))
    default_target_file = normalize_text(schema.get("implementation_target_html")) or workspace_html or "<workspace_html>"
    workspace_dir = str(Path(workspace_html).expanduser().resolve().parent) if workspace_html else ".workspace/<session>"
    lines = [
        "# OpenClaw 埋点代码改写说明",
        "",
        f"- 页面 URL：{page_identity.get('url') or '-'}",
        f"- 页面标题：{page_identity.get('title') or '-'}",
        "",
        "## SDK 配置",
        "",
        f"- CDN：{weblog_config.get('cdn') or DEFAULT_WEBLOG_CDN}",
        f"- appKey：{weblog_config.get('appKey') or '待配置'}",
        f"- debug：{bool(weblog_config.get('debug'))}",
        "",
        "## SDK 使用方式（必读）",
        "",
        "```html",
        f'<script src="{weblog_config.get("cdn") or DEFAULT_WEBLOG_CDN}"></script>',
        "<script>",
        "  window.weblog = window.weblog || {};",
        "  window.weblog.setConfig = window.weblog.setConfig || function () {};",
        "  window.weblog.report = window.weblog.report || function () {};",
        "",
        "  try {",
        "    window.weblog.setConfig({",
        f'      appKey: "{weblog_config.get("appKey") or "YOUR_APP_KEY"}",',
        f'      debug: {bool(weblog_config.get("debug"))}',
        "    });",
        "  } catch (error) {}",
        "</script>",
        "```",
        "",
        "```javascript",
        "// 埋点辅助函数",
        "function trackEvent(eventId, action, logmap) {",
        "  try {",
        "    window.weblog.report({ id: eventId, action: action, logmap: logmap || {} });",
        "  } catch (error) {}",
        "}",
        "",
        "function trackPageShow(eventId, logmap) {",
        "  trackEvent(eventId, 'show', logmap);",
        "}",
        "",
        "function trackClick(eventId, logmap) {",
        "  trackEvent(eventId, 'click', logmap);",
        "}",
        "```",
        "",
        "## 手写实现规则",
        "",
        "1. 只追加埋点逻辑，或在原有业务逻辑执行完成后补充上报；不要改写原有状态流、跳转、接口调用和 DOM 结构。",
        "2. 埋点代码必须 fail-open：`setConfig` / `report` / 字段读取异常时，不能阻断原功能。",
        "3. 不要只写 `try { window.weblog.setConfig(...) } catch {}`；请保留调用前 guard 或 no-op fallback，例如上面的 `window.weblog = window.weblog || {};` 与 `window.weblog.setConfig = window.weblog.setConfig || function () {};`。",
        "4. `report` 同理，推荐先兜底 `window.weblog.report = window.weblog.report || function () {};`，再在辅助函数里 `try/catch` 上报。",
        "5. 不要使用 `preventDefault`、`stopPropagation`、`return false`、直接覆盖 `onclick/onload/...` 这类会改变原交互语义的写法。",
        "6. 动态字段必须在触发时读取当前 DOM 或运行时状态，不要把业务值写死在代码里。",
        "7. 除非用户明确要求迁移到业务源码，否则默认只修改 `.workspace/<session>/` 工作副本。",
        "",
        "## 埋点清单",
        "",
        "| 控件/区域 | action | event_id | selector | logmap |",
        "| --- | --- | --- | --- | --- |",
    ]
    for event in events:
        if not isinstance(event, dict):
            continue
        selectors = event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else []
        logmap = event.get("logmap") if isinstance(event.get("logmap"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(event.get("element_name") or event.get("id")),
                    markdown_cell(event.get("action")),
                    markdown_cell(event.get("id")),
                    markdown_cell("<br>".join(selectors) if selectors else "-"),
                    markdown_cell(json.dumps(logmap, ensure_ascii=False)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 代码改写指引",
            "",
            "1. 在 `</head>` 前引入 SDK 并调用 `setConfig`",
            "2. 优先沿用现有业务事件入口，在原有逻辑之后补充 `trackEvent` / `trackPageShow` 调用",
            "3. 对 `show`、延迟渲染或异步更新区域，确保在真实展示/状态稳定后再上报",
            "4. 详见同目录下的 `tracking_schema.json`",
            "",
            "## 交付前 Review / 验证",
            "",
            "默认修改工作副本时运行：",
            "",
            "```bash",
            f'python3 scripts/review_tracking_implementation.py --workspace-dir "{workspace_dir}" --target-file "{default_target_file}" --json',
            "```",
            "",
            "若你把同样改法迁移到了业务源码 / JS 文件，改用：",
            "",
            "```bash",
            f'python3 scripts/review_tracking_implementation.py --workspace-dir "{workspace_dir}" --target-file "<edited_file>" --html-file "{workspace_html or "<workspace_html>"}" --json',
            "```",
            "",
            "如果保留了业务源码修改前备份，再额外传 `--baseline-file \"<edited_file>.bak\"`，让 reviewer 做精确 diff。",
            "",
            "- `status=passed`：可以认为埋点代码已完成。",
            "- `status=needs_review`：存在风险项，需要继续调整或人工确认。",
            "- `status=failed`：存在阻断问题，不能交付。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    prepare_path = Path(args.prepare_context).expanduser().resolve()
    app_business_path = Path(args.app_business).expanduser().resolve()
    llm_output_path = Path(args.llm_output).expanduser().resolve()

    prepare = safe_json_load(prepare_path)
    app_business = safe_json_load(app_business_path)
    llm_output = load_json_or_markdown_json(llm_output_path)
    if not prepare:
        raise SystemExit(f"Invalid prepare context: {prepare_path}")
    if not app_business:
        raise SystemExit(f"Invalid app/business file: {app_business_path}")
    if not llm_output:
        raise SystemExit(f"Invalid llm output: {llm_output_path}")

    draft, change_set = build_draft_document(llm_output, app_business, prepare)
    page_binding_id = normalize_text(args.page_binding_id) or "pb_local_file"
    project_id = normalize_text(args.project_id) or "ext-local-openclaw"
    base_revision = max(1, int(args.base_revision))

    payload = {
        "page_binding_id": page_binding_id,
        "project_id": project_id,
        "base_revision": base_revision,
        "draft_document": draft,
        "change_set": change_set,
    }

    workspace_dir = Path(str(prepare.get("workspace_dir"))).expanduser().resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    payload_path = (
        Path(args.output).expanduser().resolve()
        if normalize_text(args.output)
        else workspace_dir / "page_document_save_payload.json"
    )
    draft_path = workspace_dir / "draft_document.json"
    change_set_path = workspace_dir / "change_set.json"
    schema_path = workspace_dir / "tracking_schema.json"
    guide_path = workspace_dir / "openclaw_tracking_implementation.md"

    save_api_called = False
    save_api_disabled = bool(args.skip_save)
    save_api_base_url = resolve_base_url(args, prepare)
    save_api_endpoint = normalize_text(args.save_endpoint) or "tracking/page_document/save"
    save_api_business_success: bool | None = None
    save_api_error: str | None = None
    cert_path, cert_password = resolve_cert(args)
    app_key_resolution = resolve_weblog_app_key(
        manual_app_key=normalize_text(args.weblog_app_key) or None,
        prepare=prepare,
        app_business=app_business,
        draft=draft,
    )

    page_spec = draft.get("page_speculation") if isinstance(draft.get("page_speculation"), dict) else {}
    resolved_app_id = normalize_text(app_key_resolution.get("appId"))
    resolved_app_code = normalize_text(app_key_resolution.get("appCode"))
    resolved_app_name = normalize_text(app_key_resolution.get("appName"))
    if resolved_app_id and not normalize_text(page_spec.get("app_id")):
        page_spec["app_id"] = resolved_app_id
    if resolved_app_code and not normalize_text(page_spec.get("app_code")):
        page_spec["app_code"] = resolved_app_code
    if resolved_app_name and not normalize_text(page_spec.get("app_name")):
        page_spec["app_name"] = resolved_app_name

    schema = build_tracking_schema(prepare, draft, app_key_resolution, bool(args.weblog_debug))
    guide = render_implementation_guide(schema)

    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    change_set_path.write_text(json.dumps(change_set, ensure_ascii=False, indent=2), encoding="utf-8")
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    guide_path.write_text(guide, encoding="utf-8")

    save_response_path = workspace_dir / "save_api_response.json"

    if not args.skip_save:
        if not save_api_base_url:
            save_api_error = "tracking_base_url is missing (provide --tracking-base-url or prepare_context.tracking_base_url)."
        else:
            try:
                save_api_called = True
                save_response = http_post_json(
                    base_url=save_api_base_url,
                    endpoint=save_api_endpoint,
                    body=payload,
                    cert_path=cert_path,
                    cert_password=cert_password,
                    timeout=max(1, int(args.save_timeout)),
                )
                save_api_business_success = infer_business_success(save_response)
                save_response_path.write_text(json.dumps(save_response, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                save_api_error = str(exc)

    result = {
        "ok": True,
        "payload_path": str(payload_path),
        "draft_document_path": str(draft_path),
        "change_set_path": str(change_set_path),
        "tracking_schema_path": str(schema_path),
        "implementation_guide_path": str(guide_path),
        "save_api_called": save_api_called,
        "save_api_disabled": save_api_disabled,
        "save_api_base_url": save_api_base_url or None,
        "save_api_endpoint": save_api_endpoint,
        "save_api_response_path": str(save_response_path) if save_api_called and save_api_error is None else None,
        "save_api_business_success": save_api_business_success,
        "save_api_error": save_api_error,
        "weblog_app_key": normalize_text(app_key_resolution.get("appKey")) or None,
        "weblog_app_key_source": normalize_text(app_key_resolution.get("source")) or None,
        "weblog_app_key_status": normalize_text(app_key_resolution.get("status")) or None,
        "weblog_app_key_error": normalize_text(app_key_resolution.get("error")) or None,
        "region_count": len(draft.get("regions") or []),
        "event_count": len(schema.get("events") or []),
        "unresolved_count": len(schema.get("unresolved_regions") or []),
    }
    if save_api_error or save_api_business_success is False:
        result["ok"] = False
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
