#!/usr/bin/env python3

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


SKILL_CONFIG_FILENAME = "config.json"
SHARED_CONFIG_PATH = Path.home() / ".skillhub-cli" / "config.json"
ENVIRONMENTS = {"dev", "test", "prod", "dreamface", "ainvest"}
DEFAULT_ENVIRONMENT = "prod"
REQUIRED_PACKAGES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "starlette": "starlette",
    "websockets": "websockets",
    "cryptography": "cryptography",
}


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def config_path() -> Path:
    return skill_root() / SKILL_CONFIG_FILENAME


def requirements_path() -> Path:
    return skill_root() / "requirements.txt"


def load_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def merge_shared_config(config: dict[str, object], shared: dict[str, object]) -> bool:
    changed = False
    if not config.get("cert_path") and shared.get("ssl_cert_file"):
        config["cert_path"] = shared["ssl_cert_file"]
        changed = True
    if not config.get("cert_password") and shared.get("ssl_cert_password"):
        config["cert_password"] = shared["ssl_cert_password"]
        changed = True
    if not config.get("user_email") and shared.get("user_email"):
        config["user_email"] = shared["user_email"]
        changed = True
    if "ssl_legacy_mode" not in config and "ssl_legacy_mode" in shared:
        config["ssl_legacy_mode"] = shared["ssl_legacy_mode"]
        changed = True
    return changed


def prompt_value(prompt: str, *, secret: bool, no_input: bool) -> str:
    if no_input:
        return ""
    if secret:
        return getpass.getpass(prompt).strip()
    return input(prompt).strip()


def prompt_environment(current: str | None, *, no_input: bool) -> str:
    if current in ENVIRONMENTS:
        return str(current)
    if no_input:
        return DEFAULT_ENVIRONMENT
    prompt = f"请选择 tracking 环境 {sorted(ENVIRONMENTS)}，默认 {DEFAULT_ENVIRONMENT}: "
    value = input(prompt).strip() or DEFAULT_ENVIRONMENT
    while value not in ENVIRONMENTS:
        value = input(f"环境无效，请输入 {sorted(ENVIRONMENTS)}: ").strip() or DEFAULT_ENVIRONMENT
    return value


def normalize_config(config: dict[str, object], *, no_input: bool) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    normalized = dict(config)

    cert_path = str(normalized.get("cert_path") or normalized.get("ssl_cert_file") or "").strip()
    if not cert_path:
        cert_path = prompt_value("请输入 P12 证书路径: ", secret=False, no_input=no_input)
    if cert_path:
        expanded_cert_path = str(Path(cert_path).expanduser())
        normalized["cert_path"] = expanded_cert_path
        if not Path(expanded_cert_path).expanduser().is_file():
            errors.append(f"证书文件不存在: {expanded_cert_path}")
    else:
        errors.append("缺少证书路径 cert_path")

    cert_password = str(normalized.get("cert_password") or normalized.get("ssl_cert_password") or "").strip()
    if not cert_password:
        cert_password = prompt_value("请输入 P12 证书密码: ", secret=True, no_input=no_input)
    if cert_password:
        normalized["cert_password"] = cert_password
    else:
        errors.append("缺少证书密码 cert_password")

    tracking_env = prompt_environment(
        str(normalized.get("tracking_env") or normalized.get("environment") or "").strip(),
        no_input=no_input,
    )
    if tracking_env:
        normalized["tracking_env"] = tracking_env
    else:
        errors.append("缺少 tracking_env")

    return normalized, errors


def missing_dependencies() -> list[str]:
    missing: list[str] = []
    for import_name, package_name in REQUIRED_PACKAGES.items():
        if importlib.util.find_spec(import_name) is None:
            missing.append(package_name)
    return missing


def install_dependencies(requirements: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def redact_config(config: dict[str, object]) -> dict[str, object]:
    redacted = dict(config)
    if redacted.get("cert_password"):
        redacted["cert_password"] = "********"
    if redacted.get("ssl_cert_password"):
        redacted["ssl_cert_password"] = "********"
    return redacted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check skill config and Python dependencies before launch.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    parser.add_argument("--install-deps", action="store_true", help="Install missing dependencies with pip.")
    parser.add_argument("--no-input", action="store_true", help="Fail instead of prompting for missing config.")
    return parser.parse_args()


def print_result(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = "OK" if payload.get("ok") else "ERROR"
    print(f"[{status}] {payload.get('message') or ''}")
    for key, value in payload.items():
        if key in {"ok", "message"}:
            continue
        print(f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}")


def main() -> int:
    args = parse_args()

    local_config_path = config_path()
    shared_config = load_json(SHARED_CONFIG_PATH)
    config = load_json(local_config_path)
    shared_used = merge_shared_config(config, shared_config)
    config, config_errors = normalize_config(config, no_input=args.no_input)

    if shared_used or not local_config_path.exists() or not config_errors:
        write_json_atomic(local_config_path, config)

    requirements = requirements_path()
    missing = missing_dependencies()
    dependency_install_result: dict[str, object] | None = None
    if missing and args.install_deps:
        if not requirements.is_file():
            dependency_install_result = {
                "ok": False,
                "error": f"requirements file not found: {requirements}",
            }
        else:
            result = install_dependencies(requirements)
            dependency_install_result = {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "output": result.stdout[-4000:],
            }
            if result.returncode == 0:
                missing = missing_dependencies()

    errors = list(config_errors)
    if missing:
        errors.append(f"缺少 Python 依赖: {', '.join(missing)}")
    if dependency_install_result and not dependency_install_result.get("ok"):
        errors.append("依赖安装失败")

    payload: dict[str, object] = {
        "ok": not errors,
        "message": "preflight check passed" if not errors else "preflight check failed",
        "skill_root": str(skill_root()),
        "config_file": str(local_config_path),
        "shared_config_file": str(SHARED_CONFIG_PATH),
        "shared_config_used": shared_used,
        "config": redact_config(config),
        "requirements_file": str(requirements),
        "missing_dependencies": missing,
        "dependency_install_result": dependency_install_result,
        "errors": errors,
    }
    print_result(payload, args.json)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
