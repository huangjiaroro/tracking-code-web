#!/usr/bin/env python3
"""
Create or refresh the local runtime verification Python environment.

This script standardizes the Playwright runtime used by runtime_browser_session.py
and the browser-session validation flow. It avoids installing packages into the
system or Homebrew-managed Python environment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso


DEFAULT_VENV_DIR = ".workspace/runtime-verify-venv"
DEFAULT_BROWSER = "chromium"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set up the local runtime verification venv.")
    parser.add_argument(
        "--venv-dir",
        default=DEFAULT_VENV_DIR,
        help=f"Virtual environment directory. Default: {DEFAULT_VENV_DIR}",
    )
    parser.add_argument(
        "--base-python",
        default=sys.executable,
        help="Base Python executable used to create the venv. Default: current Python.",
    )
    parser.add_argument(
        "--browser",
        default=DEFAULT_BROWSER,
        help=f"Browser bundle to install via Playwright. Default: {DEFAULT_BROWSER}",
    )
    parser.add_argument(
        "--skip-browser-install",
        action="store_true",
        help="Only install the Python package and skip `playwright install <browser>`.",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Force reinstall/upgrade Playwright even if the module is already importable.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_path(path_text: str, *, base_dir: Path) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def venv_python(venv_dir: Path) -> Path:
    return (venv_dir / "bin" / "python").resolve()


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def has_playwright(python_executable: Path) -> bool:
    completed = run_command([str(python_executable), "-c", "import playwright"])
    return completed.returncode == 0


def main() -> int:
    args = parse_args()
    root = repo_root()
    venv_dir = resolve_path(normalize_text(args.venv_dir) or DEFAULT_VENV_DIR, base_dir=root)
    base_python = resolve_path(normalize_text(args.base_python) or sys.executable, base_dir=Path.cwd())
    result: dict[str, Any] = {
        "ok": False,
        "generated_at": now_utc_iso(),
        "venv_dir": str(venv_dir),
        "venv_python": str(venv_python(venv_dir)),
        "base_python": str(base_python),
        "playwright_installed": False,
        "browser_install_requested": not bool(args.skip_browser_install),
        "browser": normalize_text(args.browser) or DEFAULT_BROWSER,
        "steps": [],
    }

    if not base_python.exists():
        result["error"] = f"Base Python not found: {base_python}"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    runtime_python = venv_python(venv_dir)
    if not runtime_python.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        completed = run_command([str(base_python), "-m", "venv", str(venv_dir)])
        result["steps"].append(
            {
                "name": "create_venv",
                "command": [str(base_python), "-m", "venv", str(venv_dir)],
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0 or not runtime_python.exists():
            result["error"] = f"Failed to create runtime verification venv at {venv_dir}"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

    should_install_playwright = bool(args.upgrade) or not has_playwright(runtime_python)
    if should_install_playwright:
        install_command = [str(runtime_python), "-m", "pip", "install"]
        if args.upgrade:
            install_command.append("--upgrade")
        install_command.append("playwright")
        completed = run_command(install_command)
        result["steps"].append(
            {
                "name": "install_playwright",
                "command": install_command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            result["error"] = "Failed to install Python package 'playwright' into the runtime verification venv."
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

    result["playwright_installed"] = has_playwright(runtime_python)
    if not result["playwright_installed"]:
        result["error"] = f"Python package 'playwright' is still unavailable in {runtime_python}"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if not args.skip_browser_install:
        browser_name = normalize_text(args.browser) or DEFAULT_BROWSER
        install_browser_command = [str(runtime_python), "-m", "playwright", "install", browser_name]
        completed = run_command(install_browser_command)
        result["steps"].append(
            {
                "name": "install_browser",
                "command": install_browser_command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            result["error"] = f"Failed to install Playwright browser bundle '{browser_name}'."
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

    result["ok"] = True
    result["message"] = (
        "Runtime verification environment is ready. "
        f"Gate can use {runtime_python} automatically."
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
