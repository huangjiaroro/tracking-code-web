#!/usr/bin/env python3
"""
Prepare tracking design context for a local HTML file.

What this script does:
1. Copy source HTML into workspace directory (default: .workspace/).
2. Inject stable data-ai-id attributes into HTML elements.
3. Query app list and business lines from tracking management APIs.
4. Recommend app/business line by matching HTML content against API metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as html_escape
from html import unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from tracking_runtime_config import DEFAULT_TRACKING_ENV, resolve_runtime_config

AI_DATA_ID_ATTRIBUTE = "data-ai-id"
AI_DATA_ID_PREFIX = "ai"


def read_html_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def collect_existing_data_ai_ids(html_text: str) -> set[str]:
    pattern = re.compile(
        rf"\b{re.escape(AI_DATA_ID_ATTRIBUTE)}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        re.IGNORECASE,
    )
    values: set[str] = set()
    for match in pattern.finditer(html_text):
        value = next((group for group in match.groups() if group is not None), "")
        if value:
            values.add(html_unescape(value))
    return values


class DataAiIdInjector(HTMLParser):
    SKIP_TAGS = {
        "html",
        "head",
        "meta",
        "title",
        "base",
        "link",
        "script",
        "style",
        "noscript",
        "template",
    }

    def __init__(self, used_ids: set[str] | None = None):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.injected_count = 0
        self._next_index = 1
        self._used_ids: set[str] = set(used_ids or set())

    def _next_ai_data_id(self) -> str:
        while True:
            value = f"{AI_DATA_ID_PREFIX}-{self._next_index}"
            self._next_index += 1
            if value not in self._used_ids:
                self._used_ids.add(value)
                return value

    def _render_attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        if not attrs:
            return ""
        rendered: list[str] = []
        for name, value in attrs:
            if value is None:
                rendered.append(name)
            else:
                rendered.append(f'{name}="{html_escape(value, quote=True)}"')
        return " " + " ".join(rendered)

    def _attrs_with_ai_data_id(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        normalized_tag = tag.lower()
        result = list(attrs)
        existing_value: str | None = None
        for name, value in result:
            if name.lower() == AI_DATA_ID_ATTRIBUTE:
                existing_value = value or ""
                break

        if existing_value:
            self._used_ids.add(existing_value)
            return result

        if normalized_tag in self.SKIP_TAGS:
            return result

        result.append((AI_DATA_ID_ATTRIBUTE, self._next_ai_data_id()))
        self.injected_count += 1
        return result

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        rendered_attrs = self._render_attrs(self._attrs_with_ai_data_id(tag, attrs))
        self.parts.append(f"<{tag}{rendered_attrs}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        rendered_attrs = self._render_attrs(self._attrs_with_ai_data_id(tag, attrs))
        self.parts.append(f"<{tag}{rendered_attrs} />")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(f"<![{data}]>")

    @property
    def html(self) -> str:
        return "".join(self.parts)


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._in_skip_text = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "title":
            self._in_title = True
            return
        if normalized_tag in {"script", "style", "noscript"}:
            self._in_skip_text = True
            return
        if normalized_tag == "meta":
            attr_map = {name.lower(): (value or "") for name, value in attrs}
            for key in ("content",):
                value = (attr_map.get(key) or "").strip()
                if value:
                    self.meta_parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "title":
            self._in_title = False
            return
        if normalized_tag in {"script", "style", "noscript"}:
            self._in_skip_text = False

    def handle_data(self, data: str) -> None:
        if self._in_skip_text:
            return
        text = (data or "").strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.text_parts.append(text)


def copy_html_with_data_ai_ids(source_file: Path, workspace_html: Path) -> int:
    original_html = read_html_text(source_file)
    injector = DataAiIdInjector(collect_existing_data_ai_ids(original_html))
    injector.feed(original_html)
    injector.close()
    workspace_html.write_text(injector.html, encoding="utf-8")
    return injector.injected_count


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def tokenize(value: Any) -> set[str]:
    text = normalize_text(value).lower()
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text)
    stop = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "have",
        "your",
        "页面",
        "业务",
        "埋点",
        "管理",
    }
    return {token for token in tokens if len(token) >= 2 and token not in stop}


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

    try:
        p12_data = cert_file.read_bytes()
        private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
            p12_data,
            cert_password.encode("utf-8"),
        )
        if private_key is None or certificate is None:
            raise RuntimeError("P12 certificate did not contain both certificate and private key.")

        fd, temp_path = tempfile.mkstemp(suffix=".pem", prefix="tracking_prepare_cert_")
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
    except Exception as exc:
        raise RuntimeError(f"Failed to load P12 certificate: {exc}")

    ctx = make_unverified_ssl_context()
    try:
        ctx.load_cert_chain(certfile=temp_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to load certificate chain: {exc}")
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


def http_get_json(
    base_url: str,
    endpoint: str,
    params: dict[str, Any] | None,
    cert_path: str | None,
    cert_password: str | None,
    timeout: int = 30,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}, doseq=True)
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    opener = make_https_opener(base_url, cert_path, cert_password)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {"data": payload}


def http_post_json(
    base_url: str,
    endpoint: str,
    body: dict[str, Any] | None,
    cert_path: str | None,
    cert_password: str | None,
    timeout: int = 30,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    payload_bytes = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=payload_bytes,
    )
    opener = make_https_opener(base_url, cert_path, cert_password)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {"data": payload}


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("records", "rows", "items", "list", "result", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for key in ("data", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            records = extract_records(value)
            if records:
                return records
    return []


def extract_tree_nodes(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        result: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                result.append(item)
                for key in ("children", "nodes", "list", "records"):
                    result.extend(extract_tree_nodes(item.get(key)))
        return result
    if isinstance(payload, dict):
        result: list[dict[str, Any]] = [payload]
        for key in ("children", "nodes", "list", "records", "data", "result"):
            result.extend(extract_tree_nodes(payload.get(key)))
        return result
    return []


def read_api_data(payload: dict[str, Any]) -> Any:
    if "data" in payload:
        return payload.get("data")
    return payload


def extract_total_count(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("total", "count"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        for key in ("data", "result"):
            nested_total = extract_total_count(payload.get(key))
            if nested_total is not None:
                return nested_total
    return None


def pick_first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record.get(key) not in (None, ""):
            return record.get(key)
    return None


@dataclass
class AppCandidate:
    app_id: str | None
    app_name: str
    app_code: str
    app_key: str
    raw: dict[str, Any]
    score: int = 0


@dataclass
class BusinessCandidate:
    business_line: str
    business_code: str
    app_id: str | None
    raw: dict[str, Any]
    score: int = 0


@dataclass
class SectionCandidate:
    section_id: str | None
    section_name: str
    section_code: str
    raw: dict[str, Any]


@dataclass
class ElementCandidate:
    element_id: str | None
    element_name: str
    element_code: str
    raw: dict[str, Any]


@dataclass
class FieldCandidate:
    field_id: str | None
    field_name: str
    field_code: str
    data_type: str
    action: str | None
    remark: str | None
    track_id: str | None
    track_key: str
    track_name: str
    app_id: str | None
    page_id: str | None
    section_id: str | None
    element_id: str | None
    field_scope: str | None
    raw: dict[str, Any]


def normalize_app_records(records: list[dict[str, Any]]) -> list[AppCandidate]:
    result: list[AppCandidate] = []
    for record in records:
        app_id = pick_first(record, "id", "appId", "app_id")
        app_name = normalize_text(pick_first(record, "appName", "name", "app_name"))
        app_code = normalize_text(pick_first(record, "appSign", "appCode", "app_sign", "app_code"))
        app_key = normalize_text(pick_first(record, "appKey", "app_key"))
        if not app_name and not app_code and not app_key:
            continue
        result.append(
            AppCandidate(
                app_id=str(app_id) if app_id not in (None, "") else None,
                app_name=app_name,
                app_code=app_code,
                app_key=app_key,
                raw=record,
            )
        )
    return result


def normalize_business_records(records: list[dict[str, Any]]) -> list[BusinessCandidate]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[BusinessCandidate] = []
    for record in records:
        app_id = pick_first(record, "appId", "app_id")
        business_line = normalize_text(
            pick_first(
                record,
                "businessLine",
                "business_line",
                "businessName",
                "business_name",
                "name",
                "label",
                "text",
            )
        )
        business_code = normalize_text(
            pick_first(
                record,
                "businessCode",
                "business_code",
                "bizCode",
                "biz_code",
                "value",
                "code",
            )
        )
        if not business_line and not business_code:
            continue
        key = (
            business_line.lower(),
            business_code.lower(),
            str(app_id) if app_id not in (None, "") else None,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(
            BusinessCandidate(
                business_line=business_line,
                business_code=business_code,
                app_id=str(app_id) if app_id not in (None, "") else None,
                raw=record,
            )
        )
    return result


def normalize_section_records(records: list[dict[str, Any]]) -> list[SectionCandidate]:
    seen: set[tuple[str | None, str, str]] = set()
    result: list[SectionCandidate] = []
    for record in records:
        section_id = pick_first(record, "id", "functionId", "function_id")
        section_name = normalize_text(
            pick_first(record, "functionName", "sectionName", "name", "function_name", "section_name")
        )
        section_code = normalize_text(
            pick_first(record, "functionCode", "sectionCode", "code", "function_code", "section_code")
        )
        if not section_name and not section_code:
            continue
        key = (
            str(section_id) if section_id not in (None, "") else None,
            section_name.lower(),
            section_code.lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(
            SectionCandidate(
                section_id=str(section_id) if section_id not in (None, "") else None,
                section_name=section_name,
                section_code=section_code,
                raw=record,
            )
        )
    return result


def normalize_element_records(records: list[dict[str, Any]]) -> list[ElementCandidate]:
    seen: set[tuple[str | None, str, str]] = set()
    result: list[ElementCandidate] = []
    for record in records:
        element_id = pick_first(record, "id", "controlId", "control_id")
        element_name = normalize_text(
            pick_first(record, "controlName", "elementName", "name", "control_name", "element_name")
        )
        element_code = normalize_text(
            pick_first(record, "controlCode", "elementCode", "code", "control_code", "element_code")
        )
        if not element_name and not element_code:
            continue
        key = (
            str(element_id) if element_id not in (None, "") else None,
            element_name.lower(),
            element_code.lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ElementCandidate(
                element_id=str(element_id) if element_id not in (None, "") else None,
                element_name=element_name,
                element_code=element_code,
                raw=record,
            )
        )
    return result


def normalize_field_records(tracks: list[dict[str, Any]]) -> list[FieldCandidate]:
    seen: set[tuple[str | None, str | None, str, str, str | None, str | None]] = set()
    result: list[FieldCandidate] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = pick_first(track, "id", "trackId", "track_id")
        track_key = normalize_text(pick_first(track, "trackKey", "track_key"))
        track_name = normalize_text(pick_first(track, "trackName", "track_name", "name"))
        app_id = pick_first(track, "appId", "app_id")
        page_id = pick_first(track, "pageId", "page_id")
        section_id = pick_first(track, "functionId", "sectionId", "section_id")
        element_id = pick_first(track, "controlId", "elementId", "element_id")

        for field_scope, field_key in (("action", "actionFields"), ("public", "publicFields"), ("generic", "fields")):
            raw_fields = track.get(field_key)
            if not isinstance(raw_fields, list):
                continue
            for field in raw_fields:
                if not isinstance(field, dict):
                    continue
                field_id = pick_first(field, "id", "fieldId", "field_id")
                field_name = normalize_text(pick_first(field, "fieldName", "field_name", "name"))
                field_code = normalize_text(pick_first(field, "fieldCode", "field_code", "code"))
                if not field_name and not field_code:
                    continue
                data_type = normalize_text(pick_first(field, "dataType", "data_type", "type")) or "string"
                action = normalize_text(pick_first(field, "action", "actionName", "action_name")) or None
                remark = normalize_text(pick_first(field, "remark", "description")) or None
                key = (
                    str(track_id) if track_id not in (None, "") else None,
                    str(field_id) if field_id not in (None, "") else None,
                    field_name.lower(),
                    field_code.lower(),
                    action.lower() if action else None,
                    field_scope,
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append(
                    FieldCandidate(
                        field_id=str(field_id) if field_id not in (None, "") else None,
                        field_name=field_name,
                        field_code=field_code,
                        data_type=data_type,
                        action=action,
                        remark=remark,
                        track_id=str(track_id) if track_id not in (None, "") else None,
                        track_key=track_key,
                        track_name=track_name,
                        app_id=str(app_id) if app_id not in (None, "") else None,
                        page_id=str(page_id) if page_id not in (None, "") else None,
                        section_id=str(section_id) if section_id not in (None, "") else None,
                        element_id=str(element_id) if element_id not in (None, "") else None,
                        field_scope=field_scope,
                        raw=field,
                    )
                )
    return result


def extract_html_features(html_text: str) -> dict[str, Any]:
    extractor = HtmlTextExtractor()
    extractor.feed(html_text)
    extractor.close()

    title = normalize_text(" ".join(extractor.title_parts))
    meta_text = normalize_text(" ".join(extractor.meta_parts))
    body_sample = normalize_text(" ".join(extractor.text_parts[:400]))

    all_text = "\n".join(part for part in (title, meta_text, body_sample) if part)
    return {
        "title": title,
        "meta": meta_text,
        "body_sample": body_sample,
        "tokens": tokenize(all_text),
    }


def score_candidate(tokens: set[str], fields: list[str]) -> int:
    score = 0
    normalized_fields = [normalize_text(field).lower() for field in fields if normalize_text(field)]
    if not normalized_fields:
        return score

    for field in normalized_fields:
        field_tokens = tokenize(field)
        overlap = len(tokens & field_tokens)
        score += overlap * 3
        for token in tokens:
            if token in field and len(token) >= 3:
                score += 1
    return score


def choose_app_recommendations(apps: list[AppCandidate], html_tokens: set[str], top_n: int = 5) -> list[AppCandidate]:
    for app in apps:
        app.score = score_candidate(
            html_tokens,
            [app.app_name, app.app_code, app.app_key],
        )
    return sorted(apps, key=lambda item: (item.score, item.app_name), reverse=True)[:top_n]


def choose_business_recommendations(
    businesses: list[BusinessCandidate],
    html_tokens: set[str],
    top_n: int = 5,
) -> list[BusinessCandidate]:
    for business in businesses:
        business.score = score_candidate(
            html_tokens,
            [business.business_line, business.business_code],
        )
    return sorted(
        businesses,
        key=lambda item: (item.score, item.business_line, item.business_code),
        reverse=True,
    )[:top_n]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def serialize_app_candidate(app: AppCandidate) -> dict[str, Any]:
    return {
        "app_id": app.app_id,
        "app_name": app.app_name,
        "app_code": app.app_code,
        "app_key": app.app_key,
        "score": app.score,
    }


def serialize_business_candidate(item: BusinessCandidate) -> dict[str, Any]:
    return {
        "business_line": item.business_line,
        "business_code": item.business_code,
        "app_id": item.app_id,
        "score": item.score,
    }


def serialize_section_candidate(item: SectionCandidate) -> dict[str, Any]:
    return {
        "section_id": item.section_id,
        "section_name": item.section_name,
        "section_code": item.section_code,
    }


def serialize_element_candidate(item: ElementCandidate) -> dict[str, Any]:
    return {
        "element_id": item.element_id,
        "element_name": item.element_name,
        "element_code": item.element_code,
    }


def serialize_field_candidate(item: FieldCandidate) -> dict[str, Any]:
    return {
        "field_id": item.field_id,
        "field_name": item.field_name,
        "field_code": item.field_code,
        "data_type": item.data_type,
        "action": item.action,
        "remark": item.remark,
        "track_id": item.track_id,
        "track_key": item.track_key,
        "track_name": item.track_name,
        "app_id": item.app_id,
        "page_id": item.page_id,
        "section_id": item.section_id,
        "element_id": item.element_id,
        "field_scope": item.field_scope,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_output(
    source_html: Path,
    workspace_html: Path,
    workspace_dir: Path,
    injected_count: int,
    html_features: dict[str, Any],
    app_recommendations: list[AppCandidate],
    business_recommendations: list[BusinessCandidate],
    tracking_base_url: str,
    app_catalog_path: Path | None = None,
    business_catalog_path: Path | None = None,
    section_catalog_path: Path | None = None,
    element_catalog_path: Path | None = None,
    field_catalog_path: Path | None = None,
    app_catalog_total: int | None = None,
    business_catalog_total: int | None = None,
    section_catalog_total: int | None = None,
    element_catalog_total: int | None = None,
    field_catalog_total: int | None = None,
) -> dict[str, Any]:
    top_app = app_recommendations[0] if app_recommendations else None
    top_business = business_recommendations[0] if business_recommendations else None
    return {
        "ok": True,
        "source_html": str(source_html),
        "workspace_dir": str(workspace_dir),
        "workspace_html": str(workspace_html),
        "ai_data_id": {
            "attribute": AI_DATA_ID_ATTRIBUTE,
            "injected_count": injected_count,
        },
        "html_summary": {
            "title": html_features.get("title"),
            "meta": html_features.get("meta"),
            "body_sample": html_features.get("body_sample"),
            "token_count": len(html_features.get("tokens") or set()),
        },
        "tracking_base_url": tracking_base_url,
        "app_catalog": {
            "path": str(app_catalog_path) if app_catalog_path else None,
            "total": app_catalog_total if app_catalog_total is not None else 0,
            "note": "全量应用列表，供本地筛选 app_id/app_code。",
        },
        "business_catalog": {
            "path": str(business_catalog_path) if business_catalog_path else None,
            "total": business_catalog_total if business_catalog_total is not None else 0,
            "note": "全量业务线列表，供本地筛选 business_code。",
        },
        "section_catalog": {
            "path": str(section_catalog_path) if section_catalog_path else None,
            "total": section_catalog_total if section_catalog_total is not None else 0,
            "note": "全量区块列表，生成 llm_output 时优先复用已有 section_id/section_code。",
        },
        "element_catalog": {
            "path": str(element_catalog_path) if element_catalog_path else None,
            "total": element_catalog_total if element_catalog_total is not None else 0,
            "note": "全量元素列表，生成 llm_output 时优先复用已有 element_id/element_code。",
        },
        "field_catalog": {
            "path": str(field_catalog_path) if field_catalog_path else None,
            "total": field_catalog_total if field_catalog_total is not None else 0,
            "note": "全量字段列表，生成 llm_output 时优先复用已有 field_id/fieldCode。",
        },
        "app_recommendation": {
            "recommended": {
                "app_id": top_app.app_id if top_app else None,
                "app_name": top_app.app_name if top_app else None,
                "app_code": top_app.app_code if top_app else None,
                "score": top_app.score if top_app else None,
            },
            "candidates": [
                {
                    "app_id": app.app_id,
                    "app_name": app.app_name,
                    "app_code": app.app_code,
                    "app_key": app.app_key,
                    "score": app.score,
                }
                for app in app_recommendations
            ],
            "note": "如果推荐不准确，用户可手动指定 app_id / app_code。",
        },
        "business_line_recommendation": {
            "recommended": {
                "business_line": top_business.business_line if top_business else None,
                "business_code": top_business.business_code if top_business else None,
                "app_id": top_business.app_id if top_business else None,
                "score": top_business.score if top_business else None,
            },
            "candidates": [
                {
                    "business_line": item.business_line,
                    "business_code": item.business_code,
                    "app_id": item.app_id,
                    "score": item.score,
                }
                for item in business_recommendations
            ],
            "note": "如果推荐不准确，用户可手动指定业务线 code/name。",
        },
        "next_action": "确认/覆盖应用与业务线后，结合本地 section/element/field catalogs 优先复用已有元数据，再生成 llm_output 并保存。",
    }


def fetch_paginated_records(
    tracking_base_url: str,
    endpoint: str,
    cert_path: str | None,
    cert_password: str | None,
    page_size: int,
    *,
    method: str = "GET",
    extra_params: dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current_page = 1
    normalized_method = method.upper()

    while True:
        if normalized_method == "POST":
            payload = http_post_json(
                tracking_base_url,
                endpoint,
                {"page": current_page, "size": page_size, **(extra_body or {})},
                cert_path,
                cert_password,
            )
        else:
            payload = http_get_json(
                tracking_base_url,
                endpoint,
                {"page": current_page, "size": page_size, **(extra_params or {})},
                cert_path,
                cert_password,
            )
        data = read_api_data(payload)
        batch = extract_records(data)
        if not batch:
            break
        records.extend(batch)

        total = extract_total_count(data)
        if total is None:
            total = extract_total_count(payload)
        if total is not None and len(records) >= total:
            break
        if len(batch) < page_size:
            break
        current_page += 1
        if current_page > 10000:
            raise RuntimeError(f"Pagination exceeded safety limit for endpoint: {endpoint}")

    return records


def fetch_app_candidates(
    tracking_base_url: str,
    cert_path: str | None,
    cert_password: str | None,
    page_size: int,
) -> list[AppCandidate]:
    records = fetch_paginated_records(
        tracking_base_url,
        "appInfo/page",
        cert_path,
        cert_password,
        page_size,
    )
    return normalize_app_records(records)


def fetch_business_candidates(
    tracking_base_url: str,
    cert_path: str | None,
    cert_password: str | None,
    user_name: str | None,
) -> list[BusinessCandidate]:
    payload = http_post_json(
        tracking_base_url,
        "constant/business",
        {"userName": user_name} if user_name else {},
        cert_path,
        cert_password,
    )
    data = payload.get("data")
    records = extract_records(data)
    return normalize_business_records(records)


def fetch_section_candidates(
    tracking_base_url: str,
    cert_path: str | None,
    cert_password: str | None,
    page_size: int,
) -> list[SectionCandidate]:
    records = fetch_paginated_records(
        tracking_base_url,
        "functionInfo/page",
        cert_path,
        cert_password,
        page_size,
    )
    return normalize_section_records(records)


def fetch_element_candidates(
    tracking_base_url: str,
    cert_path: str | None,
    cert_password: str | None,
    page_size: int,
) -> list[ElementCandidate]:
    records = fetch_paginated_records(
        tracking_base_url,
        "controlInfo/page",
        cert_path,
        cert_password,
        page_size,
    )
    return normalize_element_records(records)


def fetch_field_candidates(
    tracking_base_url: str,
    cert_path: str | None,
    cert_password: str | None,
) -> list[FieldCandidate]:
    payload = http_get_json(
        tracking_base_url,
        "trackInfo/all",
        None,
        cert_path,
        cert_password,
    )
    data = read_api_data(payload)
    tracks = extract_records(data)
    return normalize_field_records(tracks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare tracking context for local HTML.")
    parser.add_argument("target", help="Local HTML file path.")
    parser.add_argument(
        "--workspace-dir",
        default=".workspace",
        help="Workspace directory for generated files (default: .workspace).",
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Optional session id metadata. Does not affect workspace directory.",
    )
    parser.add_argument(
        "--tracking-env",
        default="",
        help=f"Tracking environment: dev/test/prod/dreamface/ainvest (default: {DEFAULT_TRACKING_ENV}).",
    )
    parser.add_argument("--tracking-base-url", default="", help="Override tracking API base URL.")
    parser.add_argument("--cert-path", default="", help="Deprecated. Certificate settings are loaded from config files only.")
    parser.add_argument("--cert-password", default="", help="Deprecated. Certificate settings are loaded from config files only.")
    parser.add_argument("--user-name", default="", help="User email for business line API payload.userName.")
    parser.add_argument("--app-page-size", type=int, default=200, help="Page size for paginated metadata catalogs.")
    parser.add_argument("--output", default="", help="Optional output path for prepared context JSON.")
    parser.add_argument("--json", action="store_true", help="Output JSON only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_html = Path(args.target).expanduser().resolve()
    if not source_html.is_file():
        raise SystemExit(f"HTML file does not exist: {source_html}")

    skill_root = Path(__file__).resolve().parent.parent

    output_path_text = normalize_text(args.output)
    workspace_root = Path(args.workspace_dir).expanduser().resolve()
    workspace_dir = (
        Path(output_path_text).expanduser().resolve().parent
        if output_path_text
        else workspace_root
    )
    session_id = normalize_text(args.session_id) or workspace_dir.name
    workspace_dir.mkdir(parents=True, exist_ok=True)
    workspace_html = workspace_dir / source_html.name
    injected_count = copy_html_with_data_ai_ids(source_html, workspace_html)

    html_features = extract_html_features(read_html_text(workspace_html))

    runtime_config = resolve_runtime_config(
        skill_root,
        overrides={
            "tracking_env": normalize_text(args.tracking_env),
            "tracking_base_url": normalize_text(args.tracking_base_url),
            "user_name": normalize_text(args.user_name),
        },
    )
    tracking_env = normalize_text(runtime_config.get("tracking_env") or DEFAULT_TRACKING_ENV).lower()
    tracking_base_url = normalize_text(runtime_config.get("tracking_base_url"))
    cert_path = normalize_text(runtime_config.get("cert_path")) or None
    cert_password = normalize_text(runtime_config.get("cert_password")) or None
    user_name = normalize_text(runtime_config.get("user_name")) or None

    warnings: list[str] = []
    apps: list[AppCandidate] = []
    businesses: list[BusinessCandidate] = []
    sections: list[SectionCandidate] = []
    elements: list[ElementCandidate] = []
    fields: list[FieldCandidate] = []
    try:
        apps = fetch_app_candidates(
            tracking_base_url=tracking_base_url,
            cert_path=cert_path,
            cert_password=cert_password,
            page_size=max(1, args.app_page_size),
        )
    except Exception as exc:
        warnings.append(f"Fetch apps failed: {exc}")

    top_apps = choose_app_recommendations(apps, html_features.get("tokens") or set(), top_n=5)

    try:
        businesses = fetch_business_candidates(
            tracking_base_url=tracking_base_url,
            cert_path=cert_path,
            cert_password=cert_password,
            user_name=user_name,
        )
    except Exception as exc:
        warnings.append(f"Fetch business lines failed: {exc}")

    top_businesses = choose_business_recommendations(
        businesses,
        html_features.get("tokens") or set(),
        top_n=5,
    )

    try:
        sections = fetch_section_candidates(
            tracking_base_url=tracking_base_url,
            cert_path=cert_path,
            cert_password=cert_password,
            page_size=max(1, args.app_page_size),
        )
    except Exception as exc:
        warnings.append(f"Fetch sections failed: {exc}")

    try:
        elements = fetch_element_candidates(
            tracking_base_url=tracking_base_url,
            cert_path=cert_path,
            cert_password=cert_password,
            page_size=max(1, args.app_page_size),
        )
    except Exception as exc:
        warnings.append(f"Fetch elements failed: {exc}")

    try:
        fields = fetch_field_candidates(
            tracking_base_url=tracking_base_url,
            cert_path=cert_path,
            cert_password=cert_password,
        )
    except Exception as exc:
        warnings.append(f"Fetch fields failed: {exc}")

    app_catalog_path = workspace_dir / "all_apps_catalog.json"
    business_catalog_path = workspace_dir / "all_business_lines_catalog.json"
    section_catalog_path = workspace_dir / "all_sections_catalog.json"
    element_catalog_path = workspace_dir / "all_elements_catalog.json"
    field_catalog_path = workspace_dir / "all_fields_catalog.json"
    app_catalog_warning = next((item for item in warnings if item.startswith("Fetch apps failed:")), None)
    business_catalog_warning = next(
        (item for item in warnings if item.startswith("Fetch business lines failed:")),
        None,
    )
    section_catalog_warning = next((item for item in warnings if item.startswith("Fetch sections failed:")), None)
    element_catalog_warning = next((item for item in warnings if item.startswith("Fetch elements failed:")), None)
    field_catalog_warning = next((item for item in warnings if item.startswith("Fetch fields failed:")), None)
    write_json(
        app_catalog_path,
        {
            "ok": app_catalog_warning is None,
            "generated_at": now_utc_iso(),
            "session_id": session_id,
            "tracking_base_url": tracking_base_url,
            "total": len(apps),
            "items": [serialize_app_candidate(app) for app in apps],
            "warning": app_catalog_warning,
        },
    )
    write_json(
        business_catalog_path,
        {
            "ok": business_catalog_warning is None,
            "generated_at": now_utc_iso(),
            "session_id": session_id,
            "tracking_base_url": tracking_base_url,
            "total": len(businesses),
            "items": [serialize_business_candidate(item) for item in businesses],
            "warning": business_catalog_warning,
        },
    )
    write_json(
        section_catalog_path,
        {
            "ok": section_catalog_warning is None,
            "generated_at": now_utc_iso(),
            "session_id": session_id,
            "tracking_base_url": tracking_base_url,
            "total": len(sections),
            "items": [serialize_section_candidate(item) for item in sections],
            "warning": section_catalog_warning,
        },
    )
    write_json(
        element_catalog_path,
        {
            "ok": element_catalog_warning is None,
            "generated_at": now_utc_iso(),
            "session_id": session_id,
            "tracking_base_url": tracking_base_url,
            "total": len(elements),
            "items": [serialize_element_candidate(item) for item in elements],
            "warning": element_catalog_warning,
        },
    )
    write_json(
        field_catalog_path,
        {
            "ok": field_catalog_warning is None,
            "generated_at": now_utc_iso(),
            "session_id": session_id,
            "tracking_base_url": tracking_base_url,
            "total": len(fields),
            "items": [serialize_field_candidate(item) for item in fields],
            "warning": field_catalog_warning,
        },
    )

    output = build_output(
        source_html=source_html,
        workspace_html=workspace_html,
        workspace_dir=workspace_dir,
        injected_count=injected_count,
        html_features=html_features,
        app_recommendations=top_apps,
        business_recommendations=top_businesses,
        tracking_base_url=tracking_base_url,
        app_catalog_path=app_catalog_path,
        business_catalog_path=business_catalog_path,
        section_catalog_path=section_catalog_path,
        element_catalog_path=element_catalog_path,
        field_catalog_path=field_catalog_path,
        app_catalog_total=len(apps),
        business_catalog_total=len(businesses),
        section_catalog_total=len(sections),
        element_catalog_total=len(elements),
        field_catalog_total=len(fields),
    )
    if warnings:
        output["warnings"] = warnings
    output["session_id"] = session_id

    if output_path_text:
        output_path = Path(output_path_text).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        output["output_path"] = str(output_path)

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"source_html: {output['source_html']}")
        print(f"workspace_html: {output['workspace_html']}")
        print(f"ai_data_id_count: {output['ai_data_id']['injected_count']}")
        print(f"tracking_base_url: {output['tracking_base_url']}")
        print("recommended_app:")
        print(json.dumps(output["app_recommendation"]["recommended"], ensure_ascii=False, indent=2))
        print("recommended_business_line:")
        print(json.dumps(output["business_line_recommendation"]["recommended"], ensure_ascii=False, indent=2))
        if warnings:
            print("warnings:")
            for item in warnings:
                print(f"- {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
