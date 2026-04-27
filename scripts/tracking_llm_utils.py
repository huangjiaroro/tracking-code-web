#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

AI_DATA_ID_ATTRIBUTE = "data-ai-id"
DEFAULT_WEBLOG_CDN = "https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js"
DEFAULT_ALLOWED_ACTIONS = {
    "click",
    "slide",
    "show",
    "hover",
    "stay",
    "dis",
    "pull",
    "dclick",
    "start",
    "press",
    "end",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def to_camel_case(value: Any, fallback: str = "item") -> str:
    text = normalize_text(value)
    if not text:
        return fallback
    if re.fullmatch(r"[a-z][A-Za-z0-9]*", text):
        return text
    parts = re.findall(r"[A-Za-z0-9]+", text)
    if not parts:
        return fallback
    head = parts[0].lower()
    tail = [part[:1].upper() + part[1:] for part in parts[1:]]
    result = head + "".join(tail)
    return result if re.fullmatch(r"[a-z][A-Za-z0-9]*", result) else fallback


def ensure_camel_case(value: Any, fallback: str = "item", strict: bool = False) -> str:
    text = normalize_text(value)
    if re.fullmatch(r"[a-z][A-Za-z0-9]*", text):
        return text
    if strict and text:
        raise ValueError(f"Not camelCase: {text}")
    return to_camel_case(text, fallback=fallback)


def normalize_tracking_id_part(value: Any, fallback: str) -> str:
    text = normalize_text(value)
    if not text:
        text = fallback
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", text)
    text = text.strip("_")
    return text or fallback


def normalize_action(value: Any, fallback: str = "click") -> str:
    text = normalize_text(value).lower()
    if text in DEFAULT_ALLOWED_ACTIONS:
        return text
    return fallback


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def css_attribute_selector(name: str, value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{name}="{escaped}"]'


def safe_json_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def load_json_or_markdown_json(path: Path) -> dict[str, Any]:
    raw = read_text(path).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    fence_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

    first_obj = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if first_obj:
        try:
            parsed = json.loads(first_obj.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
    return {}


@dataclass
class DomNode:
    index: int
    tag: str
    attrs: dict[str, str]
    parent: int | None
    text_parts: list[str]
    children: list[int]
    source_line: int | None = None
    source_column: int | None = None

    @property
    def data_ai_id(self) -> str:
        return normalize_text(self.attrs.get(AI_DATA_ID_ATTRIBUTE))

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self.text_parts))

    @property
    def class_tokens(self) -> list[str]:
        return [token for token in normalize_text(self.attrs.get("class")).split(" ") if token]


class HtmlDomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[DomNode] = []
        self.stack: list[int] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def _attrs_map(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {str(k).lower(): (v or "") for k, v in attrs}

    def _push_node(self, tag: str, attrs: list[tuple[str, str | None]], push_stack: bool) -> None:
        parent_index = self.stack[-1] if self.stack else None
        node_index = len(self.nodes)
        line, column = self.getpos()
        node = DomNode(
            index=node_index,
            tag=tag.lower(),
            attrs=self._attrs_map(attrs),
            parent=parent_index,
            text_parts=[],
            children=[],
            source_line=line,
            source_column=column,
        )
        self.nodes.append(node)
        if parent_index is not None:
            self.nodes[parent_index].children.append(node_index)
        if push_stack:
            self.stack.append(node_index)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True
        self._push_node(tag, attrs, push_stack=True)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._push_node(tag, attrs, push_stack=False)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "title":
            self._in_title = False
        for idx in range(len(self.stack) - 1, -1, -1):
            stack_node = self.nodes[self.stack[idx]]
            if stack_node.tag == normalized_tag:
                del self.stack[idx:]
                break

    def handle_data(self, data: str) -> None:
        text = normalize_text(data)
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self.stack:
            self.nodes[self.stack[-1]].text_parts.append(text)


def parse_html_dom(path: Path) -> tuple[list[DomNode], dict[str, DomNode], str]:
    parser = HtmlDomParser()
    parser.feed(read_text(path))
    parser.close()
    by_data_ai_id: dict[str, DomNode] = {}
    for node in parser.nodes:
        data_ai_id = node.data_ai_id
        if data_ai_id and data_ai_id not in by_data_ai_id:
            by_data_ai_id[data_ai_id] = node
    title = normalize_text(" ".join(parser.title_parts))
    return parser.nodes, by_data_ai_id, title


def node_role(node: DomNode) -> str:
    role = normalize_text(node.attrs.get("role"))
    if role:
        return role.lower()
    tag = node.tag
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag in {"input", "textarea"}:
        return "textbox"
    if tag == "select":
        return "select"
    return "other"


def is_interactive_node(node: DomNode) -> bool:
    tag = node.tag
    role = node_role(node)
    attrs = node.attrs
    input_type = normalize_text(attrs.get("type")).lower()
    if tag in {"button", "a", "input", "select", "textarea", "summary"}:
        if tag == "input" and input_type in {"hidden"}:
            return False
        return True
    if role in {"button", "link", "tab", "menuitem", "checkbox", "radio", "switch"}:
        return True
    if normalize_text(attrs.get("onclick")):
        return True
    if normalize_text(attrs.get("href")):
        return True
    class_text = " ".join(node.class_tokens).lower()
    if any(token in class_text for token in ("btn", "button", "link", "tab", "action")):
        return True
    return False


def summarize_parent_chain(nodes: list[DomNode], node: DomNode, limit: int = 3) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current = node.parent
    while current is not None and len(chain) < limit:
        parent = nodes[current]
        chain.append(
            {
                "tag_name": parent.tag,
                "id": normalize_text(parent.attrs.get("id")),
                "class_tokens": parent.class_tokens[:6],
                "role": normalize_text(parent.attrs.get("role")),
                "text_hint": normalize_text(parent.text)[:120],
            }
        )
        current = parent.parent
    return chain


def infer_section_hint(nodes: list[DomNode], node: DomNode) -> str:
    for parent in summarize_parent_chain(nodes, node, limit=5):
        if parent.get("id"):
            return str(parent["id"])
        class_tokens = parent.get("class_tokens") or []
        for token in class_tokens:
            if len(token) >= 3:
                return str(token)
    return "mainSection"


def build_selector_candidates(node: DomNode) -> list[str]:
    selectors: list[str] = []
    data_ai_id = node.data_ai_id
    if data_ai_id:
        selectors.append(f'[data-ai-id="{data_ai_id}"]')
        selectors.append(f'{node.tag}[data-ai-id="{data_ai_id}"]')
    dom_id = normalize_text(node.attrs.get("id"))
    if dom_id:
        selectors.append(f"#{dom_id}")
    data_testid = normalize_text(node.attrs.get("data-testid"))
    if data_testid:
        selectors.append(f'[data-testid="{data_testid}"]')
    aria_label = normalize_text(node.attrs.get("aria-label"))
    if aria_label:
        selectors.append(f'[aria-label="{aria_label}"]')
    class_tokens = node.class_tokens
    if class_tokens:
        selectors.append(f"{node.tag}." + ".".join(class_tokens[:3]))
    return unique_strings(selectors)


def build_page_identity(workspace_html: Path, title: str) -> dict[str, Any]:
    route_key = workspace_html.name
    escaped = re.escape(route_key)
    return {
        "origin": "file",
        "url": workspace_html.resolve().as_uri(),
        "route_key": route_key,
        "route_pattern": f"^{escaped}$",
        "title": title or route_key,
        "page_signature": f"title={(title or route_key).lower()}",
        "signature_version": "v2",
    }
