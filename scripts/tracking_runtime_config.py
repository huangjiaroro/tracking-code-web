#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, safe_json_load

DEFAULT_TRACKING_ENV = "ainvest"
DEFAULT_TRACKING_ENVIRONMENTS = {
    "dev": "http://localhost:9854",
    "test": "http://localhost:9854",
    "prod": "https://phonestat.hexin.cn/maidian/server",
    "dreamface": "https://115.236.100.148:7553/maidian/server",
    "ainvest": "https://cbas-gateway.ainvest.com:1443/maidian/server",
}


def config_paths(skill_root: Path) -> dict[str, Path]:
    return {
        "local_session": (skill_root / "session.json").resolve(),
        "local_config": (skill_root / "config.json").resolve(),
        "shared_config": (Path.home() / ".skillhub-cli" / "config.json").resolve(),
    }


def load_config_stack(skill_root: Path) -> dict[str, dict[str, Any]]:
    paths = config_paths(skill_root)
    return {name: safe_json_load(path) for name, path in paths.items()}


def pick_first_config_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = normalize_text(payload.get(key))
        if value:
            return value
    return ""


def first_non_empty(candidates: list[tuple[str, str]]) -> tuple[str, str]:
    for value, source in candidates:
        text = normalize_text(value)
        if text:
            return text, source
    return "", ""


def infer_env_from_base_url(base_url: str) -> str:
    normalized = normalize_text(base_url).rstrip("/")
    for name, default_url in DEFAULT_TRACKING_ENVIRONMENTS.items():
        if default_url.rstrip("/") == normalized:
            return name
    return ""


def resolve_runtime_config(
    skill_root: Path,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payloads = load_config_stack(skill_root)
    paths = config_paths(skill_root)
    override_payload = overrides or {}

    tracking_env, env_source = first_non_empty(
        [
            (pick_first_config_value(override_payload, "tracking_env", "environment", "env"), "override"),
            (pick_first_config_value(payloads["local_session"], "tracking_env", "environment", "env"), "local_session"),
            (pick_first_config_value(payloads["local_config"], "tracking_env", "environment", "env"), "local_config"),
            (pick_first_config_value(payloads["shared_config"], "tracking_env", "environment", "env"), "shared_config"),
        ]
    )
    tracking_env = tracking_env.lower()

    tracking_base_url, base_url_source = first_non_empty(
        [
            (pick_first_config_value(override_payload, "tracking_base_url", "base_url", "url"), "override"),
            (pick_first_config_value(payloads["local_session"], "tracking_base_url", "base_url", "url"), "local_session"),
            (pick_first_config_value(payloads["local_config"], "tracking_base_url", "base_url", "url"), "local_config"),
            (pick_first_config_value(payloads["shared_config"], "tracking_base_url", "base_url", "url"), "shared_config"),
        ]
    )
    tracking_base_url = tracking_base_url.rstrip("/")

    if not tracking_env and tracking_base_url:
        inferred_env = infer_env_from_base_url(tracking_base_url)
        if inferred_env:
            tracking_env = inferred_env
            env_source = "inferred_from_base_url"

    if not tracking_env:
        tracking_env = DEFAULT_TRACKING_ENV
        env_source = "default"

    if not tracking_base_url:
        tracking_base_url = DEFAULT_TRACKING_ENVIRONMENTS.get(
            tracking_env,
            DEFAULT_TRACKING_ENVIRONMENTS[DEFAULT_TRACKING_ENV],
        ).rstrip("/")
        base_url_source = "default_from_env" if env_source == "default" else "derived_from_env"

    cert_path, cert_path_source = first_non_empty(
        [
            (pick_first_config_value(override_payload, "cert_path", "ssl_cert_file"), "override"),
            (pick_first_config_value(payloads["local_session"], "cert_path", "ssl_cert_file"), "local_session"),
            (pick_first_config_value(payloads["local_config"], "cert_path", "ssl_cert_file"), "local_config"),
            (pick_first_config_value(payloads["shared_config"], "cert_path", "ssl_cert_file"), "shared_config"),
        ]
    )
    cert_password, cert_password_source = first_non_empty(
        [
            (pick_first_config_value(override_payload, "cert_password", "ssl_cert_password"), "override"),
            (pick_first_config_value(payloads["local_session"], "cert_password", "ssl_cert_password"), "local_session"),
            (pick_first_config_value(payloads["local_config"], "cert_password", "ssl_cert_password"), "local_config"),
            (pick_first_config_value(payloads["shared_config"], "cert_password", "ssl_cert_password"), "shared_config"),
        ]
    )
    user_name, user_name_source = first_non_empty(
        [
            (pick_first_config_value(override_payload, "user_name", "user_email", "email"), "override"),
            (pick_first_config_value(payloads["local_session"], "user_name", "user_email", "email"), "local_session"),
            (pick_first_config_value(payloads["local_config"], "user_name", "user_email", "email"), "local_config"),
            (pick_first_config_value(payloads["shared_config"], "user_name", "user_email", "email"), "shared_config"),
        ]
    )

    return {
        "tracking_env": tracking_env,
        "tracking_base_url": tracking_base_url,
        "cert_path": cert_path or None,
        "cert_password": cert_password or None,
        "user_name": user_name or None,
        "sources": {
            "tracking_env": env_source or "missing",
            "tracking_base_url": base_url_source or "missing",
            "cert_path": cert_path_source or "missing",
            "cert_password": cert_password_source or "missing",
            "user_name": user_name_source or "missing",
            "paths": {name: str(path) for name, path in paths.items()},
        },
    }


def runtime_config_issues(
    config: dict[str, Any],
    *,
    require_user_name: bool = False,
) -> list[str]:
    issues: list[str] = []
    sources = config.get("sources") if isinstance(config.get("sources"), dict) else {}

    if sources.get("tracking_env") == "default" and sources.get("tracking_base_url") == "default_from_env":
        issues.extend(["tracking_env", "tracking_base_url"])

    cert_path = normalize_text(config.get("cert_path"))
    cert_password = normalize_text(config.get("cert_password"))
    if not cert_path:
        issues.append("cert_path")
    elif not Path(cert_path).expanduser().exists():
        issues.append("cert_path")
    if not cert_password:
        issues.append("cert_password")
    if require_user_name and not normalize_text(config.get("user_name")):
        issues.append("user_name")

    deduped: list[str] = []
    for item in issues:
        if item not in deduped:
            deduped.append(item)
    return deduped


def runtime_config_required_reads(skill_root: Path) -> list[str]:
    paths = config_paths(skill_root)
    return [str(paths["local_session"]), str(paths["local_config"]), str(paths["shared_config"])]
