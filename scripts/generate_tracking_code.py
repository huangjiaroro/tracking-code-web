#!/usr/bin/env python3
"""
Auto-generate tracking code into the workspace HTML.

This script reads tracking_schema.json and injects proper weblog SDK code
into the workspace HTML file based on the tracking design.

Usage:
    python3 scripts/generate_tracking_code.py \
        --workspace-dir ".workspace/<session>" \
        --skip-save
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-generate tracking code into HTML.")
    parser.add_argument(
        "--workspace-dir",
        required=True,
        help="Path to workspace session directory.",
    )
    parser.add_argument(
        "--skip-save",
        action="store_true",
        help="Skip saving (debug only, outputs to stdout).",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_sdk_init_snippet(schema: dict[str, Any]) -> str:
    weblog_config = schema.get("weblog_config", {})
    cdn = weblog_config.get("cdn") or "https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js"
    app_key = weblog_config.get("appKey") or "YOUR_APP_KEY"
    debug = bool(weblog_config.get("debug"))

    return f'''  <script src="{cdn}"></script>
  <script>
    window.weblog.setConfig({{
      appKey: '{app_key}',
      debug: {debug}
    }});
  </script>
'''


def build_helper_functions() -> str:
    return '''  <script>
    function trackEvent(eventId, logmap) {{
      if (window.weblog && window.weblog.report) {{
        window.weblog.report({{ id: eventId, action: 'click', logmap: logmap || {{}} }});
      }}
    }}

    function trackPageShow(eventId) {{
      if (window.weblog && window.weblog.report) {{
        window.weblog.report({{ id: eventId, action: 'show', logmap: {{}} }});
      }}
    }}
  </script>
'''


def build_event_snippet(event: dict[str, Any]) -> str:
    event_id = event.get("id", "")
    action = event.get("action", "click")
    logmap = event.get("logmap") or {}

    logmap_str = json.dumps(logmap, ensure_ascii=False)

    if action == "show":
        return f"trackPageShow('{event_id}');"
    else:
        return f"trackEvent('{event_id}', {logmap_str});"


def find_head_end位置(html_content: str) -> int:
    """Find the position of </head> tag."""
    match = re.search(r'</head>', html_content, re.IGNORECASE)
    return match.start() if match else -1


def find_script_end位置(html_content: str) -> int:
    """Find the last </script> tag before </body>."""
    last_script = -1
    for m in re.finditer(r'</script>', html_content, re.IGNORECASE):
        last_script = m.start()
    return last_script


def inject_sdk_before_head_end(html: str, sdk_snippet: str) -> str:
    """Inject SDK init before </head>."""
    match = re.search(r'</head>', html, re.IGNORECASE)
    if match:
        pos = match.start()
        return html[:pos] + sdk_snippet + html[pos:]
    return html


def inject_helpers_after_sdk(html: str, helpers: str) -> str:
    """Inject helper functions after SDK init."""
    sdk_init_end = re.search(
        r"window\.weblog\.setConfig\(\{[^}]*\}\);",
        html,
    )
    if sdk_init_end:
        pos = sdk_init_end.end() + 1
        # Find next </script>
        end_script = re.search(r'</script>', html[pos:], re.IGNORECASE)
        if end_script:
            pos = pos + end_script.start() + len('</script>')
            return html[:pos] + '\n' + helpers + html[pos:]
    return html


def find_event_insertion_points(html: str, events: list[dict[str, Any]]) -> dict[str, list[str]]:
    """
    Find where to insert tracking code for each event.
    Returns a dict mapping event_id to list of code snippets.
    """
    insertions: dict[str, list[str]] = {}

    for event in events:
        event_id = event.get("id", "")
        action = event.get("action", "click")
        element_name = event.get("element_name", "")
        selectors = event.get("selector_candidates", [])
        logmap = event.get("logmap") or {}

        snippet = build_event_snippet(event)

        # Try to find the element by selector
        for selector in selectors:
            selector = selector.strip()
            if not selector or selector == "-":
                continue

            # Handle data-ai-id selector
            ai_id_match = re.search(r'data-ai-id="([^"]+)"', selector)
            if ai_id_match:
                ai_id = ai_id_match.group(1)
                # Find the element in HTML and determine insertion point
                # For buttons with id attributes, find the addEventListener
                id_match = re.search(r'id="([^"]+)"[^>]*data-ai-id="' + re.escape(ai_id) + r'"', html)
                if id_match:
                    element_id = id_match.group(1)
                    insertions.setdefault(event_id, [])
                    insertions[event_id].append((selector, element_id, snippet))

    return insertions


def generate_tracking_code(schema: dict[str, Any]) -> dict[str, str]:
    """Generate all tracking code snippets from schema."""
    events = schema.get("events", [])
    weblog_config = schema.get("weblog_config", {})

    sdk_init = build_sdk_init_snippet(schema)
    helpers = build_helper_functions()

    event_codes: dict[str, str] = {}
    for event in events:
        event_id = event.get("id", "")
        event_codes[event_id] = build_event_snippet(event)

    return {
        "sdk_init": sdk_init,
        "helpers": helpers,
        "event_codes": event_codes,
        "app_key": weblog_config.get("appKey", ""),
        "debug": weblog_config.get("debug", False),
    }


def main() -> int:
    args = parse_args()
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()

    schema_path = workspace_dir / "tracking_schema.json"
    html_path = workspace_dir / (workspace_dir.name.split("-")[-1] + ".html")

    # Try to find HTML file
    if not html_path.exists():
        # Try to find HTML by pattern
        html_files = list(workspace_dir.glob("*.html"))
        if html_files:
            html_path = html_files[0]

    schema = load_json(schema_path)
    if not schema:
        raise SystemExit(f"Schema not found: {schema_path}")

    result = generate_tracking_code(schema)

    if args.skip_save:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not html_path.exists():
        # Just output the code snippets
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    html_content = html_path.read_text(encoding="utf-8")

    # Inject SDK before </head>
    sdk_snippet = build_sdk_init_snippet(schema)
    helpers_snippet = build_helper_functions()

    html_content = inject_sdk_before_head_end(html_content, sdk_snippet)
    html_content = inject_helpers_after_sdk(html_content, helpers_snippet)

    # Write back
    backup_path = html_path.with_suffix(".html.bak")
    backup_path.write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")

    output = {
        "ok": True,
        "html_path": str(html_path),
        "backup_path": str(backup_path),
        "sdk_init": sdk_snippet,
        "event_codes": result["event_codes"],
    }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"Tracking code generated: {html_path}")
        print(f"Backup: {backup_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
