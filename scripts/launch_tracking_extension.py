#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html as html_lib
from html.parser import HTMLParser
import json
import mimetypes
import os
import re
import resource
import select
import signal
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
import urllib.error
import urllib.request


DEFAULT_CHROME_APP = "/Applications/Google Chrome.app"
DEFAULT_BROWSER_TIMEOUT = 20.0
DEFAULT_LOCAL_SAVE_TIMEOUT = 3600.0
DEFAULT_TRACKING_ENVIRONMENTS = {
    "dev": "http://localhost:9854",
    "test": "http://localhost:9854",
    "prod": "https://phonestat.hexin.cn/maidian/server",
    "dreamface": "https://115.236.100.148:7553/maidian/server",
    "ainvest": "https://cbas-gateway.ainvest.com:1443/maidian/server",
}
DEFAULT_TRACKING_ENV = "dev"
DEFAULT_AGENT_API_BASE_URL = "https://phonestat.hexin.cn/sdmp/claudableApi"
DEFAULT_WEBLOG_CDN = "https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js"
DEFAULT_TRACKING_CODE_REFERENCE = "references/weblog_sdk_reference.md"
DEFAULT_HTML_INJECTION_ENABLED = False
DEFAULT_WEBLOG_DOMAINS = {
    "dev": "10.217.136.10:8080",
    "test": "10.217.136.10:8080",
    "prod": None,
    "dreamface": "track.aidreamface.com",
    "ainvest": "stat.ainvest.com",
}
INSTALL_BOOTSTRAP_URL = "about:blank"
EXTENSIONS_PAGE_URL = "chrome://extensions/"
LOCAL_TRACKING_TOKEN_PARAM = "openclaw_tracking_token"
LOCAL_GATEWAY_PARAM = "openclaw_tracking_gateway"
OPENCLAW_SNIPPET_START = "<!-- OpenClaw tracking injection start -->"
OPENCLAW_SNIPPET_END = "<!-- OpenClaw tracking injection end -->"
AI_DATA_ID_ATTRIBUTE = "data-ai-id"
AI_DATA_ID_PREFIX = "ai"
STATUS_STARTUP_READY = {"waiting_for_save", "saved", "error", "timeout"}

SESSION_STATUS_FILE: Path | None = None
SERVICE_LOG_FILE: Path | None = None

DEVELOPER_MODE_STATE_JS = """
(() => {
    const toggle = document
        .querySelector("extensions-manager")
        ?.shadowRoot?.querySelector("extensions-toolbar")
        ?.shadowRoot?.querySelector("#devMode");
    if (!toggle) {
        return null;
    }
    return {
        checked: Boolean(toggle.checked),
        disabled: Boolean(toggle.disabled),
    };
})()
"""

DEVELOPER_MODE_CLICK_JS = """
(() => {
    const toggle = document
        .querySelector("extensions-manager")
        ?.shadowRoot?.querySelector("extensions-toolbar")
        ?.shadowRoot?.querySelector("#devMode");
    if (!toggle) {
        return { ok: false, reason: "developer mode toggle not found" };
    }
    if (!toggle.checked) {
        toggle.click();
    }
    return {
        ok: true,
        checked: Boolean(toggle.checked),
        disabled: Boolean(toggle.disabled),
    };
})()
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install the current unpacked Chrome extension into a dedicated profile "
            "when needed, then launch Google Chrome with that profile."
        )
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Target http(s) URL or local HTML file path to open in Chrome.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help=(
            "Start the local gateway in a detached Python process, return after Chrome is ready, "
            "and write progress to the session status files."
        ),
    )
    parser.add_argument(
        "--background-start-timeout",
        type=float,
        default=90.0,
        help="Seconds to wait for the detached launcher to reach the save-waiting state.",
    )
    parser.add_argument(
        "--extension-dir",
        help="Path to the unpacked Chrome extension. Defaults to auto-discovery near the skill.",
    )
    parser.add_argument(
        "--profile-dir",
        help="Chrome user-data directory for the automation session.",
    )
    parser.add_argument(
        "--chrome-app",
        default=DEFAULT_CHROME_APP,
        help="Path to Google Chrome.app.",
    )
    parser.add_argument(
        "--page-wait",
        type=float,
        default=4.0,
        help="Seconds to wait after launching the final Chrome window before checking that it remains open.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable output.",
    )
    parser.add_argument(
        "--browser-timeout",
        type=float,
        default=DEFAULT_BROWSER_TIMEOUT,
        help="Seconds to wait for Chrome and the extension install step to become ready.",
    )
    parser.add_argument(
        "--local-server-port",
        type=int,
        default=8989,
        help="Port for the local gateway. Defaults to 8989.",
    )
    parser.add_argument(
        "--save-timeout",
        type=float,
        default=DEFAULT_LOCAL_SAVE_TIMEOUT,
        help="Seconds to wait for the extension to save tracking data in local HTML mode.",
    )
    parser.add_argument(
        "--tracking-env",
        choices=sorted(DEFAULT_TRACKING_ENVIRONMENTS),
        default=os.environ.get("OPENCLAW_TRACKING_ENV", DEFAULT_TRACKING_ENV),
        help="Tracking API environment for the local gateway.",
    )
    parser.add_argument(
        "--tracking-base-url",
        default=os.environ.get("OPENCLAW_TRACKING_BASE_URL"),
        help="Override the tracking API base URL used by the local gateway.",
    )
    parser.add_argument(
        "--agent-api-base-url",
        default=os.environ.get("OPENCLAW_AGENT_API_BASE_URL", DEFAULT_AGENT_API_BASE_URL),
        help="Agent API base URL proxied by the local gateway.",
    )
    parser.add_argument(
        "--cert-path",
        default=os.environ.get("OPENCLAW_CERT_PATH"),
        help="P12 certificate path used by the local gateway for HTTPS upstream calls.",
    )
    parser.add_argument(
        "--cert-password",
        default=os.environ.get("OPENCLAW_CERT_PASSWORD"),
        help="P12 certificate password used by the local gateway for HTTPS upstream calls.",
    )
    parser.add_argument(
        "--proxy-debug",
        action="store_true",
        help="Enable verbose urllib debug output for local gateway upstream calls.",
    )
    parser.add_argument(
        "--weblog-app-key",
        default=os.environ.get("OPENCLAW_WEBLOG_APP_KEY"),
        help="weblog SDK appKey used in window.weblog.setConfig.",
    )
    parser.add_argument(
        "--weblog-debug",
        action="store_true",
        default=os.environ.get("OPENCLAW_WEBLOG_DEBUG", "").lower() in {"1", "true", "yes", "on"},
        help="Enable weblog SDK debug output in the injected setConfig call.",
    )
    parser.add_argument(
        "--weblog-cdn",
        default=os.environ.get("OPENCLAW_WEBLOG_CDN", DEFAULT_WEBLOG_CDN),
        help="weblog SDK CDN URL injected into local HTML.",
    )
    parser.add_argument(
        "--weblog-log-prefix",
        default=os.environ.get("OPENCLAW_WEBLOG_LOG_PREFIX"),
        help="Optional weblog logPrefix for SDK automatic id prefix stitching.",
    )
    parser.add_argument(
        "--tracking-code-reference",
        default=os.environ.get("OPENCLAW_TRACKING_CODE_REFERENCE", DEFAULT_TRACKING_CODE_REFERENCE),
        help="Code convention document referenced by the OpenClaw implementation guide.",
    )
    parser.add_argument(
        "--enable-html-injection",
        action="store_true",
        default=(
            os.environ.get("OPENCLAW_ENABLE_HTML_INJECTION", "").lower() in {"1", "true", "yes", "on"}
            if "OPENCLAW_ENABLE_HTML_INJECTION" in os.environ
            else DEFAULT_HTML_INJECTION_ENABLED
        ),
        help="Enable direct HTML snippet injection. Disabled by default; fallback guide generation is the default.",
    )
    parser.add_argument("--workspace-dir", help=argparse.SUPPRESS)
    parser.add_argument("--session-status-file", help=argparse.SUPPRESS)
    parser.add_argument("--service-log-file", help=argparse.SUPPRESS)
    return parser.parse_args()


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def resolve_optional_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    return Path(raw_path).expanduser().resolve()


def configure_status_outputs(args: argparse.Namespace) -> None:
    global SESSION_STATUS_FILE, SERVICE_LOG_FILE
    SESSION_STATUS_FILE = resolve_optional_path(getattr(args, "session_status_file", None))
    SERVICE_LOG_FILE = resolve_optional_path(getattr(args, "service_log_file", None))


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def append_service_log(event: str, payload: dict[str, object] | None = None) -> None:
    if SERVICE_LOG_FILE is None:
        return
    SERVICE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, object] = {
        "timestamp": utc_now_iso(),
        "event": event,
    }
    if payload is not None:
        entry["payload"] = payload
    with SERVICE_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def emit_session_status(status: str, payload: dict[str, object]) -> dict[str, object]:
    status_payload = dict(payload)
    status_payload["status"] = status
    status_payload["updated_at"] = utc_now_iso()
    if SESSION_STATUS_FILE is not None:
        status_payload["session_status_file"] = str(SESSION_STATUS_FILE)
    if SERVICE_LOG_FILE is not None:
        status_payload["service_log"] = str(SERVICE_LOG_FILE)
    if SESSION_STATUS_FILE is not None:
        write_json_atomic(SESSION_STATUS_FILE, status_payload)
    append_service_log(status, status_payload)
    return status_payload


def read_status_payload(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path.resolve())
    return result


def default_extension_candidates() -> list[Path]:
    skill_dir = skill_root()
    skills_dir = skill_dir.parent
    return dedupe_paths([
        repo_root(),
        skill_dir,
        skill_dir / "chrome-extension",
        skills_dir / "tracking-design-chrome-extension",
        skills_dir / "tracking-design-chrome-extension" / "chrome-extension",
        skills_dir / "chrome-extension",
    ])


def resolve_extension_dir(override_dir: str | None) -> tuple[Path, list[str]]:
    if override_dir:
        resolved = Path(override_dir).expanduser().resolve()
        return resolved, [str(resolved)]

    candidates = default_extension_candidates()
    for candidate in candidates:
        if (candidate / "manifest.json").exists():
            return candidate, [str(path) for path in candidates]

    checked_paths = [str(path) for path in candidates]
    raise FileNotFoundError(
        "manifest.json not found for the Chrome extension. "
        f"Checked: {', '.join(checked_paths)}"
    )


def chrome_binary_path(chrome_app: Path) -> Path:
    if chrome_app.is_file():
        return chrome_app

    candidates = [
        chrome_app / "Contents" / "MacOS" / "Google Chrome",
        chrome_app / "Contents" / "MacOS" / chrome_app.stem,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    macos_dir = chrome_app / "Contents" / "MacOS"
    if macos_dir.is_dir():
        nested = sorted(path for path in macos_dir.iterdir() if path.is_file())
        if nested:
            return nested[0]

    raise FileNotFoundError(f"Chrome executable not found inside {chrome_app}")


class DevToolsPipeClient:
    def __init__(self, read_fd: int, write_fd: int):
        self._read_fd = read_fd
        self._write_fd = write_fd
        self._next_id = 1
        self._buffer = b""

    def close(self) -> None:
        for fd in (self._read_fd, self._write_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    def _read_message(self, timeout: float) -> dict[str, object]:
        deadline = time.time() + max(timeout, 0)
        while True:
            if b"\0" in self._buffer:
                raw_message, self._buffer = self._buffer.split(b"\0", 1)
                if not raw_message:
                    continue
                return json.loads(raw_message.decode("utf-8"))

            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for Chrome DevTools pipe response.")

            readable, _, _ = select.select([self._read_fd], [], [], remaining)
            if not readable:
                continue

            chunk = os.read(self._read_fd, 65536)
            if not chunk:
                raise RuntimeError("Chrome DevTools pipe closed unexpectedly.")
            self._buffer += chunk

    def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
        timeout: float,
    ) -> dict[str, object]:
        command_id = self._next_id
        self._next_id += 1

        payload: dict[str, object] = {
            "id": command_id,
            "method": method,
        }
        if params:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id

        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\0"
        os.write(self._write_fd, encoded)

        while True:
            message = self._read_message(timeout)
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError(f"Unexpected Chrome DevTools result: {result!r}")
            return result


def launch_chrome_with_pipe(
    chrome_binary: Path,
    profile_dir: Path,
    target_url: str,
) -> tuple[int, DevToolsPipeClient, list[str]]:
    profile_dir.mkdir(parents=True, exist_ok=True)

    parent_read, child_write = os.pipe()
    child_read, parent_write = os.pipe()

    command = [
        str(chrome_binary),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--enable-unsafe-extension-debugging",
        "--remote-debugging-pipe",
        "--new-window",
        target_url,
    ]

    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()

            devnull = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull, 0)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            if devnull > 2:
                os.close(devnull)

            os.dup2(child_read, 3)
            os.dup2(child_write, 4)
            os.set_inheritable(3, True)
            os.set_inheritable(4, True)

            max_fd = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            if max_fd == resource.RLIM_INFINITY:
                max_fd = 1024
            os.closerange(5, int(max_fd))

            os.execv(str(chrome_binary), command)
        except BaseException:
            os._exit(127)

    os.close(child_read)
    os.close(child_write)

    return pid, DevToolsPipeClient(parent_read, parent_write), command


def launch_chrome_normal(
    chrome_binary: Path,
    profile_dir: Path,
    target_urls: list[str],
) -> tuple[subprocess.Popen[bytes], list[str]]:
    profile_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(chrome_binary),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--new-window",
    ]
    command.extend(target_urls)

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return process, command


def wait_for_extensions_api(client: DevToolsPipeClient, timeout: float) -> None:
    deadline = time.time() + max(timeout, 0)
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            client.send_command("Browser.getVersion", timeout=2.0)
            return
        except Exception as exc:
            last_error = exc
        time.sleep(0.4)
    raise RuntimeError(f"Chrome browser was not ready in time: {last_error}")


def preference_paths_for_user_data_dir(user_data_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for pref_name in ("Preferences", "Secure Preferences"):
        direct = user_data_dir / pref_name
        if direct.is_file():
            candidates.append(direct)

    if user_data_dir.exists():
        for profile_dir in user_data_dir.iterdir():
            for pref_name in ("Preferences", "Secure Preferences"):
                pref_path = profile_dir / pref_name
                if pref_path.is_file():
                    candidates.append(pref_path)

    return dedupe_paths(candidates)


def detect_existing_install(
    extension_name: str,
    extension_dir: Path,
    user_data_dir: Path,
) -> list[dict[str, str]]:
    extension_path = str(extension_dir)
    matches: list[dict[str, str]] = []
    for pref_path in preference_paths_for_user_data_dir(user_data_dir):
        try:
            pref_data = json.loads(pref_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        settings = pref_data.get("extensions", {}).get("settings", {})
        if not isinstance(settings, dict):
            continue

        for extension_id, extension_data in settings.items():
            if not isinstance(extension_data, dict):
                continue
            manifest = extension_data.get("manifest")

            installed_path = str(extension_data.get("path") or "")
            installed_name = ""
            if isinstance(manifest, dict):
                installed_name = str(manifest.get("name") or "")
            if installed_path == extension_path or installed_name == extension_name:
                matches.append(
                    {
                        "profile": pref_path.parent.name,
                        "extension_id": extension_id,
                        "path": installed_path,
                        "name": installed_name,
                    }
                )
    return matches


def detect_developer_mode(user_data_dir: Path) -> tuple[bool | None, str | None]:
    for pref_path in preference_paths_for_user_data_dir(user_data_dir):
        try:
            pref_data = json.loads(pref_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        extensions = pref_data.get("extensions")
        if not isinstance(extensions, dict):
            continue

        ui = extensions.get("ui")
        if not isinstance(ui, dict):
            continue

        developer_mode = ui.get("developer_mode")
        if isinstance(developer_mode, bool):
            return developer_mode, str(pref_path)

    return None, None


def evaluate_runtime_value(
    client: DevToolsPipeClient,
    session_id: str,
    expression: str,
    timeout: float,
) -> object:
    result = client.send_command(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
        session_id=session_id,
        timeout=timeout,
    )
    remote_result = result.get("result")
    if not isinstance(remote_result, dict):
        raise RuntimeError(f"Unexpected Runtime.evaluate result: {remote_result!r}")

    if remote_result.get("type") == "undefined" or remote_result.get("subtype") == "null":
        return None
    if "value" not in remote_result:
        raise RuntimeError(f"Runtime.evaluate did not return a value: {remote_result!r}")
    return remote_result["value"]


def create_page_session(
    client: DevToolsPipeClient,
    page_url: str,
    timeout: float,
) -> str:
    deadline = time.time() + max(timeout, 0)
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            target_result = client.send_command(
                "Target.createTarget",
                {"url": page_url},
                timeout=2.0,
            )
            target_id = target_result.get("targetId")
            if not isinstance(target_id, str) or not target_id:
                raise RuntimeError(f"Chrome returned an invalid target id: {target_id!r}")

            attach_result = client.send_command(
                "Target.attachToTarget",
                {
                    "targetId": target_id,
                    "flatten": True,
                },
                timeout=2.0,
            )
            session_id = attach_result.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError(f"Chrome returned an invalid session id: {session_id!r}")

            client.send_command("Runtime.enable", session_id=session_id, timeout=2.0)
            client.send_command("Page.enable", session_id=session_id, timeout=2.0)
            return session_id
        except Exception as exc:
            last_error = exc
            time.sleep(0.3)

    raise RuntimeError(f"Failed to create a page session for {page_url}: {last_error}")


def wait_for_developer_mode_state(
    client: DevToolsPipeClient,
    session_id: str,
    timeout: float,
) -> dict[str, object]:
    deadline = time.time() + max(timeout, 0)
    last_state: object = None
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            last_state = evaluate_runtime_value(
                client,
                session_id,
                DEVELOPER_MODE_STATE_JS,
                timeout=2.0,
            )
            if isinstance(last_state, dict):
                return last_state
        except Exception as exc:
            last_error = exc
        time.sleep(0.3)

    if last_error is not None:
        raise RuntimeError(f"Developer Mode toggle was not ready in time: {last_error}")
    raise RuntimeError(f"Developer Mode toggle was not ready in time: {last_state!r}")


def enable_developer_mode(
    client: DevToolsPipeClient,
    timeout: float,
) -> bool:
    session_id = create_page_session(client, EXTENSIONS_PAGE_URL, timeout)
    state = wait_for_developer_mode_state(client, session_id, timeout)
    if bool(state.get("disabled")):
        raise RuntimeError("Chrome extensions Developer Mode toggle is disabled.")
    if bool(state.get("checked")):
        return False

    click_result = evaluate_runtime_value(
        client,
        session_id,
        DEVELOPER_MODE_CLICK_JS,
        timeout=2.0,
    )
    if not isinstance(click_result, dict) or not bool(click_result.get("ok")):
        raise RuntimeError(f"Failed to click the Developer Mode toggle: {click_result!r}")

    deadline = time.time() + max(timeout, 0)
    last_state = click_result
    while time.time() < deadline:
        state = wait_for_developer_mode_state(client, session_id, 2.0)
        last_state = state
        if bool(state.get("checked")):
            return True
        time.sleep(0.3)

    raise RuntimeError(f"Developer Mode toggle did not turn on in time: {last_state!r}")


def install_extension(
    client: DevToolsPipeClient,
    extension_dir: Path,
    timeout: float,
) -> str:
    deadline = time.time() + max(timeout, 0)
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            result = client.send_command(
                "Extensions.loadUnpacked",
                {
                    "path": str(extension_dir),
                },
                timeout=2.0,
            )
            extension_id = result.get("id")
            if isinstance(extension_id, str) and extension_id:
                return extension_id
            raise RuntimeError(f"Chrome returned an invalid extension id: {extension_id!r}")
        except Exception as exc:
            last_error = exc
            time.sleep(0.4)

    raise RuntimeError(f"Failed to install unpacked extension in time: {last_error}")


def wait_for_extension_persisted(
    extension_name: str,
    extension_dir: Path,
    user_data_dir: Path,
    timeout: float,
) -> list[dict[str, str]]:
    deadline = time.time() + max(timeout, 0)
    last_matches: list[dict[str, str]] = []
    while time.time() < deadline:
        last_matches = detect_existing_install(extension_name, extension_dir, user_data_dir)
        if last_matches:
            return last_matches
        time.sleep(0.4)
    raise RuntimeError(
        "Chrome reported that the extension was installed, but it did not appear in the profile preferences in time."
    )


def wait_for_pid_exit(pid: int, timeout: float) -> None:
    deadline = time.time() + max(timeout, 0)
    while time.time() < deadline:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    kill_deadline = time.time() + 5.0
    while time.time() < kill_deadline:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return
        time.sleep(0.2)


def close_browser_session(client: DevToolsPipeClient | None, pid: int | None) -> None:
    if client is not None:
        try:
            client.send_command("Browser.close", timeout=2.0)
        except Exception:
            pass
        finally:
            client.close()

    if pid is not None:
        wait_for_pid_exit(pid, 5.0)


class LocalTrackingSession:
    def __init__(
        self,
        *,
        source_file: Path | None,
        source_root: Path | None,
        workspace_dir: Path,
        workspace_html: Path | None,
        token: str,
        tracking_env: str,
        tracking_base_url: str,
        agent_api_base_url: str,
        cert_path: str | None,
        cert_password: str | None,
        proxy_debug: bool,
        ai_data_id_injected: bool,
        ai_data_id_count: int,
        ai_data_id_attribute: str,
        weblog_app_key: str | None,
        weblog_debug: bool,
        weblog_cdn: str,
        weblog_log_prefix: str | None,
        weblog_domain: str | None,
        tracking_code_reference: str,
        html_injection_enabled: bool,
    ):
        self.source_file = source_file
        self.source_root = source_root
        self.workspace_dir = workspace_dir
        self.workspace_html = workspace_html
        self.token = token
        self.tracking_env = tracking_env
        self.tracking_base_url = tracking_base_url.rstrip("/")
        self.agent_api_base_url = agent_api_base_url.rstrip("/")
        self.cert_path = cert_path
        self.cert_password = cert_password
        self.proxy_debug = proxy_debug
        self.ai_data_id_injected = ai_data_id_injected
        self.ai_data_id_count = ai_data_id_count
        self.ai_data_id_attribute = ai_data_id_attribute
        self.weblog_app_key = weblog_app_key
        self.weblog_debug = weblog_debug
        self.weblog_cdn = weblog_cdn
        self.weblog_log_prefix = weblog_log_prefix
        self.weblog_domain = weblog_domain
        self.tracking_code_reference = tracking_code_reference
        self.html_injection_enabled = html_injection_enabled
        self.saved_event = threading.Event()
        self.save_result: dict[str, object] | None = None
        self.save_error: str | None = None
        self.server_url: str | None = None
        self.target_url: str | None = None


class LocalTrackingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def resolve_local_html_target(raw_target: str) -> Path | None:
    parsed = urlparse(raw_target)
    if parsed.scheme == "file":
        file_path = Path(unquote(parsed.path)).expanduser()
        return file_path.resolve() if file_path.is_file() else None

    if parsed.scheme:
        return None

    file_path = Path(raw_target).expanduser()
    return file_path.resolve() if file_path.is_file() else None


def resolve_tracking_base_url(args: argparse.Namespace) -> str:
    if args.tracking_base_url:
        return str(args.tracking_base_url).rstrip("/")
    return DEFAULT_TRACKING_ENVIRONMENTS[args.tracking_env].rstrip("/")


def resolve_weblog_domain(args: argparse.Namespace) -> str | None:
    override = os.environ.get("OPENCLAW_WEBLOG_DOMAIN")
    if override is not None:
        return override.strip() or None
    return DEFAULT_WEBLOG_DOMAINS.get(args.tracking_env)


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
                rendered.append(f'{name}="{html_lib.escape(value, quote=True)}"')
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

    def close(self) -> None:
        super().close()

    @property
    def html(self) -> str:
        return "".join(self.parts)


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
            values.add(html_lib.unescape(value))
    return values


def copy_html_with_data_ai_ids(source_file: Path, workspace_html: Path) -> int:
    original_html = read_html_text(source_file)
    injector = DataAiIdInjector(collect_existing_data_ai_ids(original_html))
    injector.feed(original_html)
    injector.close()
    workspace_html.write_text(injector.html, encoding="utf-8")
    return injector.injected_count


def make_local_tracking_session(
    args: argparse.Namespace,
    source_file: Path | None = None,
) -> LocalTrackingSession:
    if getattr(args, "workspace_dir", None):
        workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    else:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        session_id = f"{timestamp}-{secrets.token_hex(4)}"
        workspace_dir = (skill_root() / ".workspace" / session_id).resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    workspace_html: Path | None = None
    source_root: Path | None = None
    ai_data_id_injected = False
    ai_data_id_count = 0
    if source_file is not None:
        workspace_html = workspace_dir / source_file.name
        ai_data_id_count = copy_html_with_data_ai_ids(source_file, workspace_html)
        ai_data_id_injected = True
        source_root = source_file.parent.resolve()

    return LocalTrackingSession(
        source_file=source_file,
        source_root=source_root,
        workspace_dir=workspace_dir,
        workspace_html=workspace_html,
        token=secrets.token_urlsafe(24),
        tracking_env=args.tracking_env,
        tracking_base_url=resolve_tracking_base_url(args),
        agent_api_base_url=args.agent_api_base_url,
        cert_path=args.cert_path,
        cert_password=args.cert_password,
        proxy_debug=args.proxy_debug,
        ai_data_id_injected=ai_data_id_injected,
        ai_data_id_count=ai_data_id_count,
        ai_data_id_attribute=AI_DATA_ID_ATTRIBUTE,
        weblog_app_key=args.weblog_app_key,
        weblog_debug=bool(args.weblog_debug),
        weblog_cdn=args.weblog_cdn,
        weblog_log_prefix=args.weblog_log_prefix,
        weblog_domain=resolve_weblog_domain(args),
        tracking_code_reference=args.tracking_code_reference,
        html_injection_enabled=bool(args.enable_html_injection),
    )


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_served_file(session: LocalTrackingSession, request_path: str) -> Path | None:
    if session.workspace_html is None:
        return None

    parsed_path = unquote(urlparse(request_path).path)
    relative_path = parsed_path.lstrip("/") or session.workspace_html.name
    if relative_path.endswith("/"):
        relative_path = f"{relative_path}index.html"

    roots = [session.workspace_dir]
    if session.source_root is not None:
        roots.append(session.source_root)
    for root in roots:
        candidate = (root / relative_path).resolve()
        if is_relative_to(candidate, root) and candidate.is_file():
            return candidate
    return None


def add_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-OpenClaw-Token")
    handler.send_header("Access-Control-Max-Age", "600")


def strip_tracking_token_from_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return raw_url
    parsed = urlparse(raw_url)
    if not parsed.query:
        return raw_url
    query = parse_qs(parsed.query, keep_blank_values=True)
    query.pop(LOCAL_TRACKING_TOKEN_PARAM, None)
    query.pop(LOCAL_GATEWAY_PARAM, None)
    cleaned_query = urlencode(query, doseq=True)
    return parsed._replace(query=cleaned_query).geturl()


def add_local_gateway_params(raw_url: str, session: LocalTrackingSession) -> str:
    if not session.server_url:
        return raw_url
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[LOCAL_TRACKING_TOKEN_PARAM] = [session.token]
    query[LOCAL_GATEWAY_PARAM] = [session.server_url]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def unique_strings(values: list[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def css_attribute_selector(name: str, value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{name}="{escaped}"]'


def tracking_id_from_region(region: dict[str, object], fallback: str) -> str:
    raw_value = (
        region.get("id")
        or region.get("tracking_id")
        or region.get("event_id")
        or region.get("log_id")
        or region.get("bi_event_id")
        or region.get("event_name")
        or region.get("event_code")
        or region.get("element_code")
        or region.get("element_name")
        or fallback
    )
    tracking_id = str(raw_value or "").strip()
    if tracking_id:
        return tracking_id
    return str(fallback or "tracking_event").strip() or "tracking_event"


def action_from_region(region: dict[str, object], fallback: str = "click") -> str:
    raw_value = (
        region.get("action")
        or region.get("action_type")
        or region.get("event_action")
        or fallback
    )
    action = str(raw_value or fallback).strip().lower()
    allowed_actions = {
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
    return action if action in allowed_actions else fallback


def region_selector_candidates(region: dict[str, object]) -> list[str]:
    anchor = region.get("anchor") if isinstance(region.get("anchor"), dict) else {}
    stable = anchor.get("stable_attributes") if isinstance(anchor.get("stable_attributes"), dict) else {}
    selectors: list[object] = []
    selectors.extend(anchor.get("selector_candidates") or [])
    selectors.append(css_attribute_selector(AI_DATA_ID_ATTRIBUTE, stable.get(AI_DATA_ID_ATTRIBUTE)))
    selectors.append(css_attribute_selector("id", stable.get("id") or region.get("element_dom_id")))
    selectors.append(css_attribute_selector("data-testid", stable.get("data-testid")))
    selectors.append(css_attribute_selector("aria-label", stable.get("aria-label")))
    return unique_strings(selectors)


def normalize_action_fields(raw_fields: object) -> list[dict[str, object]]:
    if not isinstance(raw_fields, list):
        return []

    fields: list[dict[str, object]] = []
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            continue
        field_code = (
            raw_field.get("fieldCode")
            or raw_field.get("field_code")
            or raw_field.get("code")
            or raw_field.get("name")
            or ""
        )
        value_source = raw_field.get("valueSource") or raw_field.get("value_source")
        if not isinstance(value_source, dict):
            value_source = {}
        normalized = {
            "id": raw_field.get("id") or raw_field.get("field_id") or raw_field.get("fieldId"),
            "fieldCode": str(field_code or "").strip(),
            "fieldName": raw_field.get("fieldName") or raw_field.get("field_name") or "",
            "dataType": raw_field.get("dataType") or raw_field.get("data_type") or "string",
            "action": action_from_region(raw_field, "click"),
            "remark": raw_field.get("remark") or raw_field.get("description") or "",
            "valueSource": value_source,
        }
        if normalized["fieldCode"]:
            fields.append(normalized)
    return fields


def compact_region_properties(region: dict[str, object]) -> dict[str, object]:
    property_keys = [
        "region_id",
        "region_number",
        "page_id",
        "page_name",
        "page_code",
        "section_id",
        "section_name",
        "section_code",
        "element_id",
        "element_name",
        "element_code",
        "control_type",
        "surface_id",
        "status",
    ]
    properties = {
        key: region.get(key)
        for key in property_keys
        if region.get(key) not in (None, "")
    }
    action_fields = normalize_action_fields(region.get("action_fields"))
    if action_fields:
        properties["action_fields"] = action_fields
    return properties


def normalize_payload_events(payload: dict[str, object]) -> list[dict[str, object]]:
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        return []

    events: list[dict[str, object]] = []
    for index, raw_event in enumerate(raw_events):
        if not isinstance(raw_event, dict):
            continue
        selectors = unique_strings(
            list(raw_event.get("selector_candidates") or [])
            + [raw_event.get("selector")]
        )
        tracking_id = tracking_id_from_region(raw_event, f"event_{index + 1}")
        logmap = raw_event.get("logmap")
        if not isinstance(logmap, dict):
            logmap = raw_event.get("properties") if isinstance(raw_event.get("properties"), dict) else {}
        action_fields = normalize_action_fields(
            raw_event.get("action_fields") or raw_event.get("extra_fields")
        )
        events.append({
            "id": tracking_id,
            "event_name": tracking_id,
            "action": action_from_region(raw_event),
            "selector_candidates": selectors,
            "logmap": logmap,
            "properties": logmap,
            "extra_fields": action_fields,
            "source": "events_payload",
        })
    return events


def build_tracking_schema(
    session: LocalTrackingSession,
    payload: dict[str, object],
) -> dict[str, object]:
    page_identity = payload.get("page_identity") if isinstance(payload.get("page_identity"), dict) else {}
    if page_identity.get("url"):
        page_identity = dict(page_identity)
        page_identity["url"] = strip_tracking_token_from_url(str(page_identity.get("url")))

    document = payload.get("draft_document") if isinstance(payload.get("draft_document"), dict) else {}
    change_set = payload.get("change_set") if isinstance(payload.get("change_set"), dict) else {}
    deleted_region_ids = set(change_set.get("deleted_region_ids") or [])

    events = normalize_payload_events(payload)
    unresolved_regions: list[dict[str, object]] = []

    if not events:
        raw_regions = document.get("regions") if isinstance(document.get("regions"), list) else []
        for index, raw_region in enumerate(raw_regions):
            if not isinstance(raw_region, dict):
                continue
            region_id = str(raw_region.get("region_id") or f"region_{index + 1}")
            if raw_region.get("status") == "deleted" or region_id in deleted_region_ids:
                continue

            selectors = region_selector_candidates(raw_region)
            tracking_id = tracking_id_from_region(raw_region, region_id)
            logmap = compact_region_properties(raw_region)
            action_fields = normalize_action_fields(raw_region.get("action_fields"))
            event = {
                "id": tracking_id,
                "event_name": tracking_id,
                "action": action_from_region(raw_region),
                "selector_candidates": selectors,
                "logmap": logmap,
                "properties": logmap,
                "extra_fields": action_fields,
                "region_id": region_id,
                "source": "tracking_document",
            }
            events.append(event)

            if not selectors:
                unresolved_regions.append({
                    "region_id": region_id,
                    "id": tracking_id,
                    "event_name": tracking_id,
                    "reason": "No selector candidates were available for this region.",
                })

    return {
        "schema_version": "openclaw_tracking_injection_v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_html": str(session.source_file) if session.source_file is not None else None,
        "ai_data_id": {
            "attribute": session.ai_data_id_attribute,
            "injected": session.ai_data_id_injected,
            "count": session.ai_data_id_count,
        },
        "weblog_config": {
            "cdn": session.weblog_cdn,
            "appKey": session.weblog_app_key,
            "debug": bool(session.weblog_debug),
            "domain": session.weblog_domain,
            "logPrefix": session.weblog_log_prefix,
        },
        "page_identity": page_identity,
        "events": events,
        "unresolved_regions": unresolved_regions,
    }


def render_tracking_snippet(schema: dict[str, object]) -> str:
    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    weblog_config = schema.get("weblog_config") if isinstance(schema.get("weblog_config"), dict) else {}
    weblog_cdn = str(weblog_config.get("cdn") or DEFAULT_WEBLOG_CDN).strip()
    weblog_script = (
        f'<script src="{html_lib.escape(weblog_cdn, quote=True)}"></script>\n'
        if weblog_cdn
        else ""
    )
    runtime_js = r"""
(function () {
  if (window.__openclawTrackingRuntimeInstalled) return;
  window.__openclawTrackingRuntimeInstalled = true;

  function readSchema() {
    var node = document.getElementById('openclaw-tracking-schema');
    if (!node) return { events: [] };
    try {
      return JSON.parse(node.textContent || '{"events":[]}');
    } catch (error) {
      console.warn('[OpenClawTracking] Failed to parse tracking schema', error);
      return { events: [] };
    }
  }

  var schema = readSchema();

  function elementMatches(element, selector) {
    if (!element || element.nodeType !== 1) return false;
    var matcher = element.matches
      || element.msMatchesSelector
      || element.webkitMatchesSelector
      || element.mozMatchesSelector
      || element.oMatchesSelector;
    if (matcher) return matcher.call(element, selector);
    var nodes = (element.parentNode || document).querySelectorAll(selector);
    for (var index = 0; index < nodes.length; index += 1) {
      if (nodes[index] === element) return true;
    }
    return false;
  }

  function closestElement(element, selector) {
    var current = element;
    while (current && current.nodeType === 1) {
      try {
        if (elementMatches(current, selector)) return current;
      } catch (error) {
        console.warn('[OpenClawTracking] Ignoring invalid selector', selector, error);
        return null;
      }
      current = current.parentElement || current.parentNode;
    }
    return null;
  }

  function copyObject(input) {
    var output = {};
    if (!input || typeof input !== 'object') return output;
    for (var key in input) {
      if (Object.prototype.hasOwnProperty.call(input, key)) {
        output[key] = input[key];
      }
    }
    return output;
  }

  function ensureWeblogConfigured() {
    if (!window.weblog || typeof window.weblog.report !== 'function') {
      console.warn('[OpenClawTracking] window.weblog is not ready.');
      return false;
    }

    if (window.__openclawWeblogConfigured) return true;

    var config = schema.weblog_config || {};
    var setConfigPayload = {};
    if (config.appKey) setConfigPayload.appKey = config.appKey;
    if (config.domain) setConfigPayload.domain = config.domain;
    if (config.logPrefix) setConfigPayload.logPrefix = config.logPrefix;
    setConfigPayload.debug = Boolean(config.debug);

    if (!setConfigPayload.appKey) {
      console.warn('[OpenClawTracking] weblog appKey is empty. Reporting may fail.');
    }

    if (typeof window.weblog.setConfig === 'function') {
      window.weblog.setConfig(setConfigPayload);
    }
    window.__openclawWeblogConfigured = true;
    return true;
  }

  function emitFallback(definition, logmap) {
    if (window.dataLayer && typeof window.dataLayer.push === 'function') {
      var dataLayerPayload = copyObject(logmap);
      dataLayerPayload.event = definition.id || definition.event_name;
      window.dataLayer.push(dataLayerPayload);
      return;
    }
    console.log('[OpenClawTracking]', definition.id || definition.event_name, logmap);
  }

  function reportDefinition(definition, selector, target, nativeEvent) {
    var reportId = definition.id || definition.event_name;
    if (!reportId) return;

    var logmap = copyObject(definition.logmap || definition.properties || {});
    logmap.matched_selector = selector;
    logmap.native_event_type = nativeEvent ? nativeEvent.type : 'show';

    if (!ensureWeblogConfigured()) {
      emitFallback(definition, logmap);
      return;
    }

    window.weblog.report({
      id: reportId,
      action: definition.action || 'click',
      logmap: logmap
    });
  }

  function handleEvent(nativeEvent, actionName) {
    var source = nativeEvent.target || nativeEvent.srcElement;
    if (!source) return;

    var events = schema.events || [];
    for (var eventIndex = 0; eventIndex < events.length; eventIndex += 1) {
      var definition = events[eventIndex] || {};
      var definitionAction = definition.action || 'click';
      if (definitionAction !== actionName) continue;

      var selectors = definition.selector_candidates || [];
      for (var selectorIndex = 0; selectorIndex < selectors.length; selectorIndex += 1) {
        var selector = selectors[selectorIndex];
        var matched = closestElement(source, selector);
        if (!matched) continue;
        reportDefinition(definition, selector, matched, nativeEvent);
        return;
      }
    }
  }

  function setupShowReports() {
    var events = schema.events || [];
    var showDefinitions = [];
    for (var index = 0; index < events.length; index += 1) {
      if ((events[index].action || 'click') === 'show') {
        showDefinitions.push(events[index]);
      }
    }
    if (!showDefinitions.length) return;

    if (!('IntersectionObserver' in window)) {
      setTimeout(function () {
        for (var showIndex = 0; showIndex < showDefinitions.length; showIndex += 1) {
          var definition = showDefinitions[showIndex];
          var selectors = definition.selector_candidates || [];
          for (var selectorIndex = 0; selectorIndex < selectors.length; selectorIndex += 1) {
            var target = null;
            try {
              target = document.querySelector(selectors[selectorIndex]);
            } catch (error) {
              console.warn('[OpenClawTracking] Ignoring invalid selector', selectors[selectorIndex], error);
            }
            if (target) {
              reportDefinition(definition, selectors[selectorIndex], target, { type: 'show' });
              break;
            }
          }
        }
      }, 0);
      return;
    }

    var reported = {};
    var observer = new IntersectionObserver(function (entries) {
      for (var entryIndex = 0; entryIndex < entries.length; entryIndex += 1) {
        var entry = entries[entryIndex];
        if (!entry.isIntersecting) continue;
        var key = entry.target.getAttribute('data-openclaw-show-key');
        if (!key || reported[key]) continue;
        reported[key] = true;
        var parts = key.split('::');
        var definitionIndex = Number(parts[0]);
        var selector = parts.slice(1).join('::');
        reportDefinition(showDefinitions[definitionIndex], selector, entry.target, { type: 'show' });
        observer.unobserve(entry.target);
      }
    }, { threshold: 0.1 });

    for (var defIndex = 0; defIndex < showDefinitions.length; defIndex += 1) {
      var showDefinition = showDefinitions[defIndex];
      var showSelectors = showDefinition.selector_candidates || [];
      for (var showSelectorIndex = 0; showSelectorIndex < showSelectors.length; showSelectorIndex += 1) {
        var selector = showSelectors[showSelectorIndex];
        var nodes = [];
        try {
          nodes = document.querySelectorAll(selector);
        } catch (error) {
          console.warn('[OpenClawTracking] Ignoring invalid selector', selector, error);
          continue;
        }
        for (var nodeIndex = 0; nodeIndex < nodes.length; nodeIndex += 1) {
          nodes[nodeIndex].setAttribute('data-openclaw-show-key', defIndex + '::' + selector);
          observer.observe(nodes[nodeIndex]);
        }
      }
    }
  }

  document.addEventListener('click', function (event) { handleEvent(event, 'click'); }, true);
  document.addEventListener('change', function (event) { handleEvent(event, 'click'); }, true);
  document.addEventListener('dblclick', function (event) { handleEvent(event, 'dclick'); }, true);
  document.addEventListener('mouseover', function (event) { handleEvent(event, 'hover'); }, true);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupShowReports);
  } else {
    setupShowReports();
  }
})();
""".strip()

    return (
        f"\n{OPENCLAW_SNIPPET_START}\n"
        f'<script id="openclaw-tracking-schema" type="application/json">{schema_json}</script>\n'
        f"{weblog_script}"
        f'<script id="openclaw-tracking-runtime">\n{runtime_js}\n</script>\n'
        f"{OPENCLAW_SNIPPET_END}\n"
    )


def inject_tracking_snippet(html_text: str, snippet: str) -> str:
    existing_pattern = re.compile(
        rf"\s*{re.escape(OPENCLAW_SNIPPET_START)}.*?{re.escape(OPENCLAW_SNIPPET_END)}\s*",
        re.IGNORECASE | re.DOTALL,
    )
    html_text = existing_pattern.sub("\n", html_text)

    body_match = re.search(r"</body\s*>", html_text, flags=re.IGNORECASE)
    if body_match:
        return f"{html_text[:body_match.start()]}{snippet}{html_text[body_match.start():]}"

    html_match = re.search(r"</html\s*>", html_text, flags=re.IGNORECASE)
    if html_match:
        return f"{html_text[:html_match.start()]}{snippet}{html_text[html_match.start():]}"

    return f"{html_text.rstrip()}{snippet}\n"


def markdown_cell(value: object) -> str:
    if value in (None, ""):
        text = "-"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def describe_value_source(field: dict[str, object]) -> str:
    value_source = field.get("valueSource") if isinstance(field.get("valueSource"), dict) else {}
    source_type = str(value_source.get("type") or "").strip()
    selector = str(value_source.get("selector") or "").strip()
    root_selector = str(value_source.get("rootSelector") or value_source.get("root_selector") or "").strip()
    attr = str(value_source.get("attr") or value_source.get("attribute") or "").strip()
    key = str(value_source.get("key") or value_source.get("query") or "").strip()
    path = str(value_source.get("path") or "").strip()
    remark = str(field.get("remark") or "").strip()

    if source_type == "selector_text" and selector:
        return f"触发时读取 `{selector}` 的文本内容。"
    if source_type == "selector_attr" and selector and attr:
        return f"触发时读取 `{selector}` 的 `{attr}` 属性。"
    if source_type == "clicked_text":
        return "触发时读取当前点击元素的文本内容。"
    if source_type == "closest_text" and root_selector and selector:
        return f"触发时从点击元素向上匹配 `{root_selector}`，再读取其内部 `{selector}` 的文本内容。"
    if source_type == "url_query" and key:
        return f"触发时读取当前 URL query 参数 `{key}`。"
    if source_type == "local_storage" and key:
        return f"触发时读取 localStorage 中的 `{key}`。"
    if source_type == "session_storage" and key:
        return f"触发时读取 sessionStorage 中的 `{key}`。"
    if source_type == "window_path" and path:
        return f"触发时读取 `window.{path}`。"
    if value_source:
        return f"触发时按 valueSource 取值：`{json.dumps(value_source, ensure_ascii=False)}`。"
    if remark:
        return remark
    return "待 OpenClaw 根据页面 DOM、路由状态或前端状态管理在触发时实时读取。"


def render_extra_fields_summary(extra_fields: object) -> str:
    if not isinstance(extra_fields, list) or not extra_fields:
        return "无"
    parts: list[str] = []
    for field in extra_fields:
        if not isinstance(field, dict):
            continue
        code = str(field.get("fieldCode") or "").strip()
        name = str(field.get("fieldName") or "").strip()
        action = str(field.get("action") or "").strip()
        value_desc = describe_value_source(field)
        label = code
        if name:
            label = f"{label}（{name}）" if label else name
        if action:
            label = f"{label} / {action}"
        parts.append(f"{label}: {value_desc}")
    return "<br>".join(parts) if parts else "无"


def render_static_logmap_summary(logmap: object) -> str:
    if not isinstance(logmap, dict) or not logmap:
        return "{}"
    static_logmap = {
        key: value
        for key, value in logmap.items()
        if key != "action_fields"
    }
    if not static_logmap:
        return "{}"
    return json.dumps(static_logmap, ensure_ascii=False, separators=(",", ":"))


def render_openclaw_implementation_guide(
    session: LocalTrackingSession,
    schema: dict[str, object],
) -> str:
    weblog_config = schema.get("weblog_config") if isinstance(schema.get("weblog_config"), dict) else {}
    events = schema.get("events") if isinstance(schema.get("events"), list) else []
    page_identity = schema.get("page_identity") if isinstance(schema.get("page_identity"), dict) else {}
    code_reference = session.tracking_code_reference or DEFAULT_TRACKING_CODE_REFERENCE

    lines = [
        "# OpenClaw 埋点代码改写说明",
        "",
        "- 代码注入状态：false",
        "- 当前处理方式：fallback，由 OpenClaw 按本文档改写业务源码。",
        f"- 代码规范参考：{code_reference}",
        f"- 源 HTML：{session.source_file if session.source_file is not None else '-'}",
        f"- 页面 URL：{page_identity.get('url') or '-'}",
        f"- 页面标题：{page_identity.get('title') or '-'}",
        "",
        "## SDK 配置",
        "",
        f"- CDN：{weblog_config.get('cdn') or DEFAULT_WEBLOG_CDN}",
        f"- appKey：{weblog_config.get('appKey') or '待配置'}",
        f"- domain：{weblog_config.get('domain') or '国内默认，不显式传入'}",
        f"- logPrefix：{weblog_config.get('logPrefix') or '-'}",
        f"- debug：{bool(weblog_config.get('debug'))}",
        "",
        "## 改写要求",
        "",
        "1. 按代码规范参考文档改写项目源码，不使用本工具生成的 HTML runtime 注入方案。",
        "2. 在页面初始化位置复用或引入 weblog SDK，并调用 `window.weblog.setConfig(...)` 或 npm 包的 `setConfig(...)`。",
        "3. 在下表指定控件的触发时机调用 `report({ id, action, logmap })`。",
        "4. 额外属性必须在触发时实时读取，不要在页面初始化时缓存易变化的业务值。",
        "5. 如果额外属性取值说明仍为待确认，OpenClaw 需要结合页面 DOM、路由参数、接口数据或状态管理补齐。",
        "",
        "## 埋点清单",
        "",
        "| 控件/区域 | 触发时机 | 埋点 ID | 选择器参考 | 固定 logmap | 额外属性及取值 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    if not events:
        lines.append("| - | - | - | - | - | - |")
    for event in events:
        if not isinstance(event, dict):
            continue
        control_name = (
            event.get("element_name")
            or event.get("region_id")
            or event.get("id")
            or event.get("event_name")
            or "-"
        )
        selectors = event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else []
        lines.append(
            "| "
            + " | ".join([
                markdown_cell(control_name),
                markdown_cell(event.get("action") or "click"),
                markdown_cell(event.get("id") or event.get("event_name")),
                markdown_cell("<br>".join(str(selector) for selector in selectors) if selectors else "-"),
                markdown_cell(render_static_logmap_summary(event.get("logmap"))),
                markdown_cell(render_extra_fields_summary(event.get("extra_fields"))),
            ])
            + " |"
        )

    lines.extend([
        "",
        "## 参考代码形态",
        "",
        "```js",
        "window.weblog.report({",
        "  id: '埋点 ID',",
        "  action: 'click',",
        "  logmap: {",
        "    // 固定属性直接填入",
        "    // 额外属性在触发时实时读取后填入",
        "  }",
        "});",
        "```",
        "",
        "## 原始结构化数据",
        "",
        "详见同目录下的 `tracking_schema.json`。",
        "",
    ])
    return "\n".join(lines)


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

        fd, temp_path = tempfile.mkstemp(suffix=".pem", prefix="openclaw_cert_")
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


def make_proxy_opener(session: LocalTrackingSession, target_url: str) -> urllib.request.OpenerDirector:
    parsed = urlparse(target_url)
    if parsed.scheme != "https":
        return urllib.request.build_opener()

    if session.cert_path and session.cert_password:
        ctx = make_p12_ssl_context(session.cert_path, session.cert_password)
    else:
        ctx = make_unverified_ssl_context()
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx, debuglevel=1 if session.proxy_debug else 0)
    )


def should_forward_header(name: str) -> bool:
    return name.lower() not in {
        "host",
        "content-length",
        "origin",
        "referer",
        "connection",
        "accept-encoding",
    }


def build_proxy_headers(headers) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in headers.items():
        if should_forward_header(name):
            result[name] = value
    if "Accept" not in result:
        result["Accept"] = "application/json"
    return result


def build_upstream_url(base_url: str, route_prefix: str, request_path: str) -> str:
    parsed = urlparse(request_path)
    route = parsed.path.removeprefix(route_prefix).lstrip("/")
    upstream = f"{base_url.rstrip('/')}/{route}"
    if parsed.query:
        query = parse_qs(parsed.query, keep_blank_values=True)
        query.pop("token", None)
        query.pop(LOCAL_TRACKING_TOKEN_PARAM, None)
        query.pop(LOCAL_GATEWAY_PARAM, None)
        cleaned_query = urlencode(query, doseq=True)
        if cleaned_query:
            upstream = f"{upstream}?{cleaned_query}"
    return upstream


def read_proxy_body(handler: BaseHTTPRequestHandler) -> bytes | None:
    content_length = int(handler.headers.get("Content-Length") or "0")
    if content_length <= 0:
        return None
    if content_length > 50 * 1024 * 1024:
        raise ValueError("Proxy request body is too large.")
    return handler.rfile.read(content_length)


def proxy_upstream_request(
    session: LocalTrackingSession,
    *,
    method: str,
    upstream_url: str,
    headers,
    body: bytes | None,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        upstream_url,
        data=body if method.upper() not in {"GET", "HEAD"} else None,
        headers=build_proxy_headers(headers),
        method=method.upper(),
    )
    opener = make_proxy_opener(session, upstream_url)

    try:
        with opener.open(request, timeout=60) as response:
            response_body = response.read()
            response_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"transfer-encoding", "connection", "content-length"}
            }
            return response.status, response_headers, response_body
    except urllib.error.HTTPError as exc:
        response_body = exc.read()
        response_headers = {
            key: value
            for key, value in exc.headers.items()
            if key.lower() not in {"transfer-encoding", "connection", "content-length"}
        }
        return exc.code, response_headers, response_body
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}")


def send_bytes_response(
    handler: BaseHTTPRequestHandler,
    *,
    status: int,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> None:
    handler.send_response(status)
    add_cors_headers(handler)
    for name, value in (headers or {}).items():
        if name.lower() in {"content-length", "transfer-encoding", "connection"}:
            continue
        handler.send_header(name, value)
    if headers is None or not any(name.lower() == "content-type" for name in headers):
        handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, object],
    *,
    status: int = 200,
) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    send_bytes_response(
        handler,
        status=status,
        body=encoded,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def parse_json_body(body: bytes | None) -> dict[str, object]:
    if not body:
        return {}
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return payload


def sanitize_gateway_control_params(value):
    if isinstance(value, dict):
        return {
            key: sanitize_gateway_control_params(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_gateway_control_params(item) for item in value]
    if isinstance(value, str) and (
        LOCAL_TRACKING_TOKEN_PARAM in value or LOCAL_GATEWAY_PARAM in value
    ):
        return strip_tracking_token_from_url(value)
    return value


def sanitize_json_proxy_body(headers, body: bytes | None) -> bytes | None:
    if not body:
        return body
    content_type = headers.get("Content-Type") or headers.get("content-type") or ""
    if "application/json" not in content_type.lower():
        return body
    payload = json.loads(body.decode("utf-8"))
    sanitized = sanitize_gateway_control_params(payload)
    return json.dumps(sanitized, ensure_ascii=False).encode("utf-8")


def merge_save_response(
    remote_body: bytes,
    injection_result: dict[str, object],
) -> bytes:
    if not remote_body:
        return json.dumps(injection_result, ensure_ascii=False).encode("utf-8")

    try:
        remote_json = json.loads(remote_body.decode("utf-8"))
    except Exception:
        return remote_body

    if isinstance(remote_json, dict) and isinstance(remote_json.get("data"), dict):
        remote_json["data"].update(injection_result)
        return json.dumps(remote_json, ensure_ascii=False).encode("utf-8")

    if isinstance(remote_json, dict):
        remote_json.update(injection_result)
        return json.dumps(remote_json, ensure_ascii=False).encode("utf-8")

    return remote_body


def save_tracking_payload(
    session: LocalTrackingSession,
    payload: dict[str, object],
) -> dict[str, object]:
    if session.workspace_html is None:
        return {
            "success": True,
            "ok": True,
            "message": "Tracking design saved through local gateway.",
            "local_file_mode": False,
            "workspace_dir": str(session.workspace_dir),
            "ai_data_id_injected": session.ai_data_id_injected,
            "ai_data_id_attribute": session.ai_data_id_attribute,
            "ai_data_id_count": session.ai_data_id_count,
        }

    schema = build_tracking_schema(session, payload)
    source_suffix = session.workspace_html.suffix or ".html"
    schema_path = session.workspace_dir / "tracking_schema.json"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    if not session.html_injection_enabled:
        implementation_guide_path = session.workspace_dir / "openclaw_tracking_implementation.md"
        implementation_guide_path.write_text(
            render_openclaw_implementation_guide(session, schema),
            encoding="utf-8",
        )
        return {
            "success": True,
            "ok": True,
            "message": "Direct HTML injection is disabled; generated OpenClaw implementation guide.",
            "local_file_mode": True,
            "code_injection_enabled": False,
            "code_injection_performed": False,
            "code_reference": session.tracking_code_reference,
            "implementation_guide": str(implementation_guide_path),
            "tracking_schema": str(schema_path),
            "event_count": len(schema.get("events") or []),
            "unresolved_count": len(schema.get("unresolved_regions") or []),
            "workspace_dir": str(session.workspace_dir),
            "ai_data_id_injected": session.ai_data_id_injected,
            "ai_data_id_attribute": session.ai_data_id_attribute,
            "ai_data_id_count": session.ai_data_id_count,
        }

    snippet = render_tracking_snippet(schema)
    original_html = session.workspace_html.read_text(encoding="utf-8")
    modified_html = inject_tracking_snippet(original_html, snippet)
    modified_html_path = session.workspace_html.with_name(
        f"{session.workspace_html.stem}_with_tracking{source_suffix}"
    )
    modified_html_path.write_text(modified_html, encoding="utf-8")

    return {
        "success": True,
        "ok": True,
        "message": "Tracking design injected successfully.",
        "local_file_mode": True,
        "code_injection_enabled": True,
        "code_injection_performed": True,
        "modified_html": str(modified_html_path),
        "tracking_schema": str(schema_path),
        "event_count": len(schema.get("events") or []),
        "unresolved_count": len(schema.get("unresolved_regions") or []),
        "workspace_dir": str(session.workspace_dir),
        "ai_data_id_injected": session.ai_data_id_injected,
        "ai_data_id_attribute": session.ai_data_id_attribute,
        "ai_data_id_count": session.ai_data_id_count,
    }


def make_local_tracking_handler(session: LocalTrackingSession):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenClawLocalTracking/1.0"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            add_cors_headers(self)
            self.end_headers()

        def verify_gateway_token(self) -> bool:
            parsed = urlparse(self.path)
            token_values = parse_qs(parsed.query).get("token") or []
            supplied_token = self.headers.get("X-OpenClaw-Token") or (token_values[0] if token_values else "")
            if supplied_token == session.token:
                return True

            self.send_response(403)
            add_cors_headers(self)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return False

        def handle_session_config(self) -> None:
            send_json_response(self, {
                "ok": True,
                "token": session.token,
                "tracking_env": session.tracking_env,
                "tracking_base_url": session.tracking_base_url,
                "agent_api_base_url": session.agent_api_base_url,
                "html_injection_enabled": session.html_injection_enabled,
                "tracking_code_reference": session.tracking_code_reference,
                "weblog_config": {
                    "cdn": session.weblog_cdn,
                    "appKey": session.weblog_app_key,
                    "debug": bool(session.weblog_debug),
                    "domain": session.weblog_domain,
                    "logPrefix": session.weblog_log_prefix,
                },
                "local_file_mode": session.workspace_html is not None,
                "source_html": str(session.source_file) if session.source_file is not None else None,
                "workspace_html": str(session.workspace_html) if session.workspace_html is not None else None,
                "workspace_dir": str(session.workspace_dir),
                "ai_data_id_injected": session.ai_data_id_injected,
                "ai_data_id_attribute": session.ai_data_id_attribute,
                "ai_data_id_count": session.ai_data_id_count,
                "uses_client_cert": bool(session.cert_path and session.cert_password),
            })

        def handle_proxy(self, upstream_base_url: str, route_prefix: str) -> None:
            if not self.verify_gateway_token():
                return
            body = read_proxy_body(self)
            body = sanitize_json_proxy_body(self.headers, body)
            upstream_url = build_upstream_url(upstream_base_url, route_prefix, self.path)
            status, headers, response_body = proxy_upstream_request(
                session,
                method=self.command,
                upstream_url=upstream_url,
                headers=self.headers,
                body=body,
            )
            send_bytes_response(self, status=status, body=response_body, headers=headers)

        def handle_page_document_save(self) -> None:
            if not self.verify_gateway_token():
                return
            body = read_proxy_body(self)
            body = sanitize_json_proxy_body(self.headers, body)
            payload = parse_json_body(body)
            upstream_url = build_upstream_url(
                session.tracking_base_url,
                "/api/openclaw/page_document",
                self.path,
            )
            status, headers, response_body = proxy_upstream_request(
                session,
                method=self.command,
                upstream_url=upstream_url,
                headers=self.headers,
                body=body,
            )

            if 200 <= status < 300:
                injection_result = save_tracking_payload(session, payload)
                session.save_result = {
                    **injection_result,
                    "tracking_env": session.tracking_env,
                    "tracking_base_url": session.tracking_base_url,
                }
                append_service_log("tracking_save_received", session.save_result)
                session.saved_event.set()
                response_body = merge_save_response(response_body, injection_result)

            send_bytes_response(self, status=status, body=response_body, headers=headers)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/openclaw/session_config":
                self.handle_session_config()
                return
            if parsed.path.startswith("/api/openclaw/page_document/"):
                self.handle_proxy(session.tracking_base_url, "/api/openclaw/page_document")
                return
            if parsed.path.startswith("/api/openclaw/agent/"):
                self.handle_proxy(session.agent_api_base_url, "/api/openclaw/agent")
                return

            served_file = resolve_served_file(session, self.path)
            if served_file is None:
                self.send_response(404)
                add_cors_headers(self)
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            content_type = mimetypes.guess_type(str(served_file))[0] or "application/octet-stream"
            data = served_file.read_bytes()
            self.send_response(200)
            add_cors_headers(self)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/openclaw/session_config":
                self.handle_session_config()
                return
            if parsed.path == "/api/openclaw/page_document/tracking/page_document/save":
                self.handle_page_document_save()
                return
            if parsed.path.startswith("/api/openclaw/page_document/"):
                self.handle_proxy(session.tracking_base_url, "/api/openclaw/page_document")
                return
            if parsed.path.startswith("/api/openclaw/agent/"):
                self.handle_proxy(session.agent_api_base_url, "/api/openclaw/agent")
                return

            if parsed.path != "/api/save_tracking":
                self.send_response(404)
                add_cors_headers(self)
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            if not self.verify_gateway_token():
                return

            try:
                content_length = int(self.headers.get("Content-Length") or "0")
                if content_length <= 0 or content_length > 20 * 1024 * 1024:
                    raise ValueError("Invalid request body size.")
                raw_body = self.rfile.read(content_length)
                payload = json.loads(raw_body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")

                result = save_tracking_payload(session, payload)
                session.save_result = result
                append_service_log("tracking_save_received", result)
                session.saved_event.set()

                send_json_response(self, result)
            except Exception as exc:
                session.save_error = str(exc)
                append_service_log("tracking_save_error", {"error": str(exc)})
                session.saved_event.set()
                send_json_response(self, {
                    "success": False,
                    "ok": False,
                    "error": str(exc),
                }, status=500)

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/openclaw/page_document/"):
                self.handle_proxy(session.tracking_base_url, "/api/openclaw/page_document")
                return
            if parsed.path.startswith("/api/openclaw/agent/"):
                self.handle_proxy(session.agent_api_base_url, "/api/openclaw/agent")
                return
            send_json_response(self, {"ok": False, "error": "Not Found"}, status=404)

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/openclaw/page_document/"):
                self.handle_proxy(session.tracking_base_url, "/api/openclaw/page_document")
                return
            if parsed.path.startswith("/api/openclaw/agent/"):
                self.handle_proxy(session.agent_api_base_url, "/api/openclaw/agent")
                return
            send_json_response(self, {"ok": False, "error": "Not Found"}, status=404)

    return Handler


def start_local_tracking_server(
    session: LocalTrackingSession,
    requested_port: int,
) -> tuple[LocalTrackingHTTPServer, threading.Thread]:
    try:
        server = LocalTrackingHTTPServer(
            ("127.0.0.1", max(0, requested_port)),
            make_local_tracking_handler(session),
        )
    except OSError:
        if requested_port == 0:
            raise
        server = LocalTrackingHTTPServer(
            ("127.0.0.1", 0),
            make_local_tracking_handler(session),
        )

    port = int(server.server_address[1])
    session.server_url = f"http://127.0.0.1:{port}"
    if session.workspace_html is not None:
        token_query = urlencode({
            LOCAL_TRACKING_TOKEN_PARAM: session.token,
            LOCAL_GATEWAY_PARAM: session.server_url,
        })
        session.target_url = f"{session.server_url}/{quote(session.workspace_html.name)}?{token_query}"

    thread = threading.Thread(target=server.serve_forever, name="openclaw-local-tracking", daemon=True)
    thread.start()
    append_service_log("local_gateway_started", {
        "server_url": session.server_url,
        "target_url": session.target_url,
        "workspace_dir": str(session.workspace_dir),
        "local_file_mode": session.workspace_html is not None,
    })
    return server, thread


def shutdown_local_tracking_server(server: LocalTrackingHTTPServer | None, thread: threading.Thread | None) -> None:
    if server is None:
        return
    server.shutdown()
    server.server_close()
    if thread is not None:
        thread.join(timeout=2.0)


def print_result(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        print(f"{key}: {rendered}")


def fail(message: str, *, as_json: bool, details: dict[str, object] | None = None) -> int:
    payload: dict[str, object] = {}
    if details:
        payload.update(details)
    payload.update({"ok": False, "status": "error", "error": message})
    emit_session_status("error", payload)
    print_result(payload, as_json)
    return 1


def normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be a valid http(s) address.")
    return parsed.geturl()


def load_manifest(extension_dir: Path) -> dict[str, object]:
    manifest_path = extension_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found in {extension_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def make_background_workspace_dir() -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    session_id = f"{timestamp}-{secrets.token_hex(4)}"
    workspace_dir = (skill_root() / ".workspace" / session_id).resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def child_argv_without_background(argv: list[str]) -> list[str]:
    value_options = {
        "--workspace-dir",
        "--session-status-file",
        "--service-log-file",
    }
    result: list[str] = []
    skip_next = False
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if item == "--background":
            continue
        if item in value_options:
            skip_next = True
            continue
        if any(item.startswith(f"{option}=") for option in value_options):
            continue
        result.append(item)
    return result


def redact_sensitive_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    sensitive_options = {"--cert-password"}
    for item in command:
        if skip_next:
            redacted.append("********")
            skip_next = False
            continue
        if item in sensitive_options:
            redacted.append(item)
            skip_next = True
            continue
        if any(item.startswith(f"{option}=") for option in sensitive_options):
            option, _, _ = item.partition("=")
            redacted.append(f"{option}=********")
            continue
        redacted.append(item)
    return redacted


def wait_for_background_start(
    *,
    status_file: Path,
    process: subprocess.Popen[bytes],
    timeout: float,
) -> dict[str, object] | None:
    deadline = time.time() + max(timeout, 0)
    last_payload: dict[str, object] | None = None
    while time.time() < deadline:
        payload = read_status_payload(status_file)
        if payload is not None:
            last_payload = payload
            if str(payload.get("status") or "") in STATUS_STARTUP_READY:
                return payload

        exit_code = process.poll()
        if exit_code is not None:
            payload = read_status_payload(status_file)
            if payload is not None and str(payload.get("status") or "") in STATUS_STARTUP_READY:
                payload.setdefault("process_exit_code", exit_code)
                return payload
            return {
                "ok": False,
                "status": "error",
                "error": "Detached launcher exited before reaching the save-waiting state.",
                "process_exit_code": exit_code,
                "last_status": payload,
            }

        time.sleep(0.5)

    if last_payload is not None:
        return {
            "ok": False,
            "status": "error",
            "error": "Timed out waiting for the detached launcher to reach the save-waiting state.",
            "last_status": last_payload,
        }
    return None


def launch_background(args: argparse.Namespace) -> int:
    if not args.target:
        return fail("target is required unless reading an existing status file.", as_json=args.json)

    workspace_dir = make_background_workspace_dir()
    status_file = workspace_dir / "session_status.json"
    service_log_file = workspace_dir / "service.log"

    global SESSION_STATUS_FILE, SERVICE_LOG_FILE
    SESSION_STATUS_FILE = status_file
    SERVICE_LOG_FILE = service_log_file

    initial_payload: dict[str, object] = {
        "ok": True,
        "background": True,
        "workspace_dir": str(workspace_dir),
        "session_status_file": str(status_file),
        "service_log": str(service_log_file),
        "next_action": "Waiting for Chrome and the local tracking gateway to become ready.",
    }
    emit_session_status("starting", initial_payload)

    child_args = child_argv_without_background(sys.argv[1:])
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        *child_args,
        "--workspace-dir",
        str(workspace_dir),
        "--session-status-file",
        str(status_file),
        "--service-log-file",
        str(service_log_file),
    ]

    try:
        with service_log_file.open("ab") as log_stream:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        return fail(
            f"Failed to start detached launcher: {exc}",
            as_json=args.json,
            details={
                "background": True,
                "workspace_dir": str(workspace_dir),
                "session_status_file": str(status_file),
                "service_log": str(service_log_file),
            },
        )

    append_service_log("background_process_started", {"pid": process.pid, "command": redact_sensitive_command(command)})
    payload = wait_for_background_start(
        status_file=status_file,
        process=process,
        timeout=args.background_start_timeout,
    )
    if payload is None:
        return fail(
            "Timed out waiting for the detached launcher to write its first status.",
            as_json=args.json,
            details={
                "background": True,
                "launcher_pid": process.pid,
                "workspace_dir": str(workspace_dir),
                "session_status_file": str(status_file),
                "service_log": str(service_log_file),
            },
        )

    payload.update({
        "background": True,
        "launcher_pid": process.pid,
        "workspace_dir": str(payload.get("workspace_dir") or workspace_dir),
        "session_status_file": str(status_file),
        "service_log": str(service_log_file),
    })
    current_status = str(payload.get("status") or "")
    if current_status == "starting":
        payload["background_ready"] = False
        payload["next_action"] = "Detached launcher is still starting. Poll session_status_file until status changes."
    elif current_status in {"error", "timeout"}:
        payload["background_ready"] = False
    else:
        payload["background_ready"] = True
    if current_status == "waiting_for_save":
        payload["next_action"] = (
            "Continue the tracking design in Chrome. Poll session_status_file or service_log; "
            "when status becomes saved, read implementation_guide and tracking_schema."
        )

    print_result(payload, args.json)
    if payload.get("ok") is False or str(payload.get("status") or "") in {"error", "timeout"}:
        return 1
    return 0


def main() -> int:
    args = parse_args()
    configure_status_outputs(args)
    if args.background:
        return launch_background(args)
    if not args.target:
        return fail("target is required.", as_json=args.json)

    local_session: LocalTrackingSession | None = None
    local_server: LocalTrackingHTTPServer | None = None
    local_server_thread: threading.Thread | None = None

    try:
        local_target = resolve_local_html_target(args.target)
        local_session = make_local_tracking_session(args, local_target)
        local_server, local_server_thread = start_local_tracking_server(
            local_session,
            args.local_server_port,
        )
        if local_target is not None:
            if not local_session.target_url:
                raise RuntimeError("Local tracking server did not produce a target URL.")
            target_url = local_session.target_url
        else:
            target_url = add_local_gateway_params(normalize_url(args.target), local_session)
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc), as_json=args.json)

    searched_paths: list[str] = []
    try:
        extension_dir, searched_paths = resolve_extension_dir(args.extension_dir)
    except FileNotFoundError as exc:
        return fail(
            str(exc),
            as_json=args.json,
            details={"searched_paths": searched_paths or [str(path) for path in default_extension_candidates()]},
        )

    profile_dir = (
        Path(args.profile_dir).expanduser().resolve()
        if args.profile_dir
        else (skill_root() / ".openclaw" / "chrome-profile").resolve()
    )

    chrome_app_path = Path(args.chrome_app).expanduser().resolve()
    if not chrome_app_path.exists():
        return fail(
            f"Google Chrome.app not found: {chrome_app_path}",
            as_json=args.json,
        )

    try:
        chrome_binary = chrome_binary_path(chrome_app_path)
    except FileNotFoundError as exc:
        return fail(str(exc), as_json=args.json)

    try:
        manifest = load_manifest(extension_dir)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return fail(
            str(exc),
            as_json=args.json,
            details={"extension_dir": str(extension_dir), "searched_paths": searched_paths},
        )

    extension_name = str(manifest.get("name") or extension_dir.name)
    availability_mode = "persistent_unpacked_profile_install"

    existing_profiles = detect_existing_install(extension_name, extension_dir, profile_dir)
    existing_install_detected = bool(existing_profiles)
    developer_mode_initially_enabled, developer_mode_pref_path = detect_developer_mode(profile_dir)
    developer_mode_enabled = developer_mode_initially_enabled is True
    installed_now = False
    loaded_for_session = False
    extension_id: str | None = existing_profiles[0]["extension_id"] if existing_profiles else None
    bootstrap_launch_command: list[str] | None = None
    developer_mode_launch_command: list[str] | None = None
    launch_command: list[str] | None = None
    chrome_process: subprocess.Popen[bytes] | None = None
    developer_mode_needed = not developer_mode_enabled
    developer_mode_auto_toggle_attempted = False
    developer_mode_toggled_now = False
    developer_mode_auto_toggle_error: str | None = None
    launch_urls = [target_url]
    opened_extensions_page = False

    try:
        if not existing_install_detected:
            bootstrap_pid, client, bootstrap_launch_command = launch_chrome_with_pipe(
                chrome_binary,
                profile_dir,
                INSTALL_BOOTSTRAP_URL,
            )
            try:
                time.sleep(min(max(args.page_wait, 0), 2.0))
                wait_for_extensions_api(client, args.browser_timeout)
                install_extension(client, extension_dir, args.browser_timeout)
                installed_now = True
                existing_profiles = wait_for_extension_persisted(
                    extension_name,
                    extension_dir,
                    profile_dir,
                    args.browser_timeout,
                )
                existing_install_detected = True
                extension_id = existing_profiles[0]["extension_id"]
                developer_mode_initially_enabled, developer_mode_pref_path = detect_developer_mode(profile_dir)
                developer_mode_enabled = developer_mode_initially_enabled is True
                developer_mode_needed = not developer_mode_enabled
                if developer_mode_needed:
                    developer_mode_auto_toggle_attempted = True
                    developer_mode_toggled_now = enable_developer_mode(client, args.browser_timeout)
            finally:
                close_browser_session(client, bootstrap_pid)

            developer_mode_after_bootstrap, developer_mode_pref_path = detect_developer_mode(profile_dir)
            developer_mode_enabled = developer_mode_after_bootstrap is True
            developer_mode_needed = not developer_mode_enabled

        if developer_mode_needed:
            developer_mode_auto_toggle_attempted = True
            developer_mode_pid, developer_mode_client, developer_mode_launch_command = launch_chrome_with_pipe(
                chrome_binary,
                profile_dir,
                INSTALL_BOOTSTRAP_URL,
            )
            try:
                time.sleep(min(max(args.page_wait, 0), 2.0))
                wait_for_extensions_api(developer_mode_client, args.browser_timeout)
                developer_mode_toggled_now = enable_developer_mode(
                    developer_mode_client,
                    args.browser_timeout,
                )
            except Exception as exc:
                developer_mode_auto_toggle_error = str(exc)
            finally:
                close_browser_session(developer_mode_client, developer_mode_pid)

            developer_mode_after_toggle, developer_mode_pref_path = detect_developer_mode(profile_dir)
            developer_mode_enabled = developer_mode_after_toggle is True
            developer_mode_needed = not developer_mode_enabled

        if developer_mode_needed:
            launch_urls = [EXTENSIONS_PAGE_URL, target_url]
            opened_extensions_page = True

        chrome_process, launch_command = launch_chrome_normal(
            chrome_binary,
            profile_dir,
            launch_urls,
        )
        time.sleep(max(args.page_wait, 0))
        if chrome_process.poll() is not None:
            raise RuntimeError("Chrome exited immediately after launch.")
        loaded_for_session = True
    except RuntimeError as exc:
        return fail(
            str(exc),
            as_json=args.json,
            details={
                "url": target_url,
                "input_target": args.target,
                "extension_name": extension_name,
                "extension_id": extension_id,
                "extension_dir": str(extension_dir),
                "profile_dir": str(profile_dir),
                "availability_mode": availability_mode,
                "developer_mode_initially_enabled": developer_mode_initially_enabled,
                "developer_mode_enabled": developer_mode_enabled,
                "developer_mode_needed": developer_mode_needed,
                "developer_mode_auto_toggle_attempted": developer_mode_auto_toggle_attempted,
                "developer_mode_toggled_now": developer_mode_toggled_now,
                "developer_mode_auto_toggle_error": developer_mode_auto_toggle_error,
                "developer_mode_pref_path": developer_mode_pref_path,
                "opened_extensions_page": opened_extensions_page,
                "launch_urls": launch_urls,
            },
        )
    except Exception as exc:
        return fail(
            str(exc),
            as_json=args.json,
            details={
                "url": target_url,
                "input_target": args.target,
                "extension_name": extension_name,
                "extension_dir": str(extension_dir),
                "profile_dir": str(profile_dir),
                "availability_mode": availability_mode,
                "searched_paths": searched_paths,
                "developer_mode_initially_enabled": developer_mode_initially_enabled,
                "developer_mode_enabled": developer_mode_enabled,
                "developer_mode_needed": developer_mode_needed,
                "developer_mode_auto_toggle_attempted": developer_mode_auto_toggle_attempted,
                "developer_mode_toggled_now": developer_mode_toggled_now,
                "developer_mode_auto_toggle_error": developer_mode_auto_toggle_error,
                "developer_mode_pref_path": developer_mode_pref_path,
                "opened_extensions_page": opened_extensions_page,
                "launch_urls": launch_urls,
            },
        )

    payload: dict[str, object] = {
        "ok": True,
        "service_pid": os.getpid(),
        "url": target_url,
        "input_target": args.target,
        "extension_name": extension_name,
        "existing_install_detected": existing_install_detected,
        "installed_now": installed_now,
        "loaded_for_session": loaded_for_session,
        "extension_id": extension_id,
        "existing_profiles": existing_profiles,
        "availability_mode": availability_mode,
        "extension_dir": str(extension_dir),
        "profile_dir": str(profile_dir),
        "chrome_app": str(chrome_app_path),
        "chrome_binary": str(chrome_binary),
        "developer_mode_initially_enabled": developer_mode_initially_enabled,
        "developer_mode_enabled": developer_mode_enabled,
        "developer_mode_needed": developer_mode_needed,
        "developer_mode_auto_toggle_attempted": developer_mode_auto_toggle_attempted,
        "developer_mode_toggled_now": developer_mode_toggled_now,
        "developer_mode_auto_toggle_error": developer_mode_auto_toggle_error,
        "developer_mode_pref_path": developer_mode_pref_path,
        "opened_extensions_page": opened_extensions_page,
        "launch_urls": launch_urls,
        "next_action": (
            "Open chrome://extensions in this Chrome session, turn on Developer Mode in the browser UI, and confirm the unpacked extension is enabled before continuing."
            if developer_mode_needed
            else "Chrome launched with the target URL and the extension available in the dedicated profile."
        ),
        "searched_paths": searched_paths,
    }
    if bootstrap_launch_command is not None:
        payload["bootstrap_launch_command"] = bootstrap_launch_command
    if developer_mode_launch_command is not None:
        payload["developer_mode_launch_command"] = developer_mode_launch_command
    if launch_command is not None:
        payload["launch_command"] = launch_command
    if chrome_process is not None:
        payload["chrome_pid"] = chrome_process.pid
    if local_session is not None:
        payload.update({
            "local_gateway_mode": True,
            "local_file_mode": local_session.workspace_html is not None,
            "source_html": str(local_session.source_file) if local_session.source_file is not None else None,
            "workspace_html": str(local_session.workspace_html) if local_session.workspace_html is not None else None,
            "workspace_dir": str(local_session.workspace_dir),
            "local_server_url": local_session.server_url,
            "ai_data_id_injected": local_session.ai_data_id_injected,
            "ai_data_id_attribute": local_session.ai_data_id_attribute,
            "ai_data_id_count": local_session.ai_data_id_count,
            "tracking_env": local_session.tracking_env,
            "tracking_base_url": local_session.tracking_base_url,
            "agent_api_base_url": local_session.agent_api_base_url,
            "uses_client_cert": bool(local_session.cert_path and local_session.cert_password),
            "html_injection_enabled": local_session.html_injection_enabled,
            "tracking_code_reference": local_session.tracking_code_reference,
            "weblog_cdn": local_session.weblog_cdn,
            "weblog_app_key_configured": bool(local_session.weblog_app_key),
            "weblog_debug": bool(local_session.weblog_debug),
            "weblog_domain": local_session.weblog_domain,
            "weblog_log_prefix": local_session.weblog_log_prefix,
            "next_action": (
                "Continue the tracking design in Chrome. Poll session_status_file or service_log; when status becomes saved, read implementation_guide and tracking_schema."
            ),
        })
        emit_session_status("waiting_for_save", payload)

    local_save_result: dict[str, object] | None = None
    if local_session is not None:
        try:
            if not local_session.saved_event.wait(max(args.save_timeout, 0)):
                timeout_payload = dict(payload)
                timeout_payload.update({
                    "ok": False,
                    "error": "Timed out waiting for the extension to save tracking data.",
                    "save_timeout": args.save_timeout,
                })
                emit_session_status("timeout", timeout_payload)
                print_result(timeout_payload, args.json)
                return 1
            if local_session.save_error:
                return fail(
                    local_session.save_error,
                    as_json=args.json,
                    details=payload,
                )
            if not local_session.save_result:
                return fail(
                    "The local tracking service finished without a save result.",
                    as_json=args.json,
                    details=payload,
                )
            local_save_result = local_session.save_result
        finally:
            shutdown_local_tracking_server(local_server, local_server_thread)
    if local_save_result is not None:
        payload.update(local_save_result)
        if local_session is not None:
            payload["next_action"] = (
                "Tracking implementation guide was generated. Read implementation_guide and tracking_schema, then apply the tracking code changes to the source project."
                if local_session.workspace_html is not None and not local_session.html_injection_enabled
                else "Tracking design was saved locally. Use modified_html as the deployment input."
                if local_session.workspace_html is not None
                else "Tracking design was saved through the local gateway."
            )
            payload["status"] = "saved"
            emit_session_status("saved", payload)
    if bootstrap_launch_command is not None:
        payload["bootstrap_launch_command"] = bootstrap_launch_command
    if developer_mode_launch_command is not None:
        payload["developer_mode_launch_command"] = developer_mode_launch_command
    if launch_command is not None:
        payload["launch_command"] = launch_command
    if chrome_process is not None:
        payload["chrome_pid"] = chrome_process.pid

    print_result(payload, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
