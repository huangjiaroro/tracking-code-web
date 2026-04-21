#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/run_tracking_harness.sh --html <path> [options]

Required:
  --html <path>                     Source HTML file path.

App/Business confirmation (choose one):
  --app-id <id> --app-code <code> --business-code <code>
  --accept-recommendation           Use prepare_context recommended values.

LLM step:
  --llm-output <path>               LLM output JSON file.
  --stop-after-prepare              Stop after step 1, wait for app/business confirmation.
  --stop-after-confirm              Stop after step 2, wait for llm_output.

Save behavior:
  --save                            Call real save API. Default is dry-run (--skip-save).

Other options:
  --session-id <id>                 Session id. Default: tracking-YYYYmmdd-HHMMSS
  --workspace-root <dir>            Workspace root. Default: .workspace
  --tracking-env <env>              dev/test/prod/dreamface/ainvest
  --tracking-base-url <url>         Override tracking API base URL
  --cert-path <path>                P12 certificate path
  --cert-password <text>            P12 certificate password
  --user-name <email>               User email for business API
  --app-page-size <n>               App page size for prepare step (default: 200)
  --weblog-app-key <key>            Override weblog appKey in apply step
  --weblog-debug                    Enable weblog debug in apply step
  --page-binding-id <id>            Optional page binding id
  --project-id <id>                 Optional project id
  --base-revision <n>               Base revision (default: 1)
  --save-endpoint <path>            Save endpoint (default: tracking/page_document/save)
  --save-timeout <sec>              Save timeout seconds (default: 30)
  -h, --help                        Show help
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

abs_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

html_path=""
session_id="tracking-$(date '+%Y%m%d-%H%M%S')"
workspace_root="${ROOT_DIR}/.workspace"
tracking_env=""
tracking_base_url=""
cert_path=""
cert_password=""
user_name=""
app_page_size="200"
app_id=""
app_code=""
business_code=""
accept_recommendation=0
llm_output=""
stop_after_confirm=0
stop_after_prepare=0
skip_save=1
weblog_app_key=""
weblog_debug=0
page_binding_id=""
project_id=""
base_revision="1"
save_endpoint="tracking/page_document/save"
save_timeout="30"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --html)
      [[ $# -lt 2 ]] && { echo "Missing value for --html" >&2; exit 2; }
      html_path="$2"
      shift 2
      ;;
    --session-id)
      [[ $# -lt 2 ]] && { echo "Missing value for --session-id" >&2; exit 2; }
      session_id="$2"
      shift 2
      ;;
    --workspace-root)
      [[ $# -lt 2 ]] && { echo "Missing value for --workspace-root" >&2; exit 2; }
      workspace_root="$2"
      shift 2
      ;;
    --tracking-env)
      [[ $# -lt 2 ]] && { echo "Missing value for --tracking-env" >&2; exit 2; }
      tracking_env="$2"
      shift 2
      ;;
    --tracking-base-url)
      [[ $# -lt 2 ]] && { echo "Missing value for --tracking-base-url" >&2; exit 2; }
      tracking_base_url="$2"
      shift 2
      ;;
    --cert-path)
      [[ $# -lt 2 ]] && { echo "Missing value for --cert-path" >&2; exit 2; }
      cert_path="$2"
      shift 2
      ;;
    --cert-password)
      [[ $# -lt 2 ]] && { echo "Missing value for --cert-password" >&2; exit 2; }
      cert_password="$2"
      shift 2
      ;;
    --user-name)
      [[ $# -lt 2 ]] && { echo "Missing value for --user-name" >&2; exit 2; }
      user_name="$2"
      shift 2
      ;;
    --app-page-size)
      [[ $# -lt 2 ]] && { echo "Missing value for --app-page-size" >&2; exit 2; }
      app_page_size="$2"
      shift 2
      ;;
    --app-id)
      [[ $# -lt 2 ]] && { echo "Missing value for --app-id" >&2; exit 2; }
      app_id="$2"
      shift 2
      ;;
    --app-code)
      [[ $# -lt 2 ]] && { echo "Missing value for --app-code" >&2; exit 2; }
      app_code="$2"
      shift 2
      ;;
    --business-code)
      [[ $# -lt 2 ]] && { echo "Missing value for --business-code" >&2; exit 2; }
      business_code="$2"
      shift 2
      ;;
    --accept-recommendation)
      accept_recommendation=1
      shift
      ;;
    --llm-output)
      [[ $# -lt 2 ]] && { echo "Missing value for --llm-output" >&2; exit 2; }
      llm_output="$2"
      shift 2
      ;;
    --stop-after-prepare)
      stop_after_prepare=1
      shift
      ;;
    --stop-after-confirm)
      stop_after_confirm=1
      shift
      ;;
    --save)
      skip_save=0
      shift
      ;;
    --weblog-app-key)
      [[ $# -lt 2 ]] && { echo "Missing value for --weblog-app-key" >&2; exit 2; }
      weblog_app_key="$2"
      shift 2
      ;;
    --weblog-debug)
      weblog_debug=1
      shift
      ;;
    --page-binding-id)
      [[ $# -lt 2 ]] && { echo "Missing value for --page-binding-id" >&2; exit 2; }
      page_binding_id="$2"
      shift 2
      ;;
    --project-id)
      [[ $# -lt 2 ]] && { echo "Missing value for --project-id" >&2; exit 2; }
      project_id="$2"
      shift 2
      ;;
    --base-revision)
      [[ $# -lt 2 ]] && { echo "Missing value for --base-revision" >&2; exit 2; }
      base_revision="$2"
      shift 2
      ;;
    --save-endpoint)
      [[ $# -lt 2 ]] && { echo "Missing value for --save-endpoint" >&2; exit 2; }
      save_endpoint="$2"
      shift 2
      ;;
    --save-timeout)
      [[ $# -lt 2 ]] && { echo "Missing value for --save-timeout" >&2; exit 2; }
      save_timeout="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

[[ -z "$html_path" ]] && { echo "--html is required" >&2; usage; exit 2; }
html_path="$(abs_path "$html_path")"
[[ ! -f "$html_path" ]] && { echo "HTML file not found: $html_path" >&2; exit 2; }

workspace_root="$(abs_path "$workspace_root")"
workspace_dir="${workspace_root}/${session_id}"
logs_dir="${workspace_dir}/logs"
mkdir -p "$logs_dir"

prepare_json="${workspace_dir}/prepare_context.json"
confirm_json="${workspace_dir}/app_business_confirm.json"
workspace_llm_output="${workspace_dir}/llm_output.json"
payload_json="${workspace_dir}/page_document_save_payload.json"
apply_result_json="${workspace_dir}/apply_result.json"
implementation_baseline_html="${workspace_dir}/implementation_baseline.html"
harness_result_json="${workspace_dir}/harness_result.json"

step_prepare="pending"
step_confirm="pending"
step_apply="pending"
harness_status="running"
harness_message=""

write_summary() {
  export HARNESS_STATUS="$harness_status"
  export HARNESS_MESSAGE="$harness_message"
  export SESSION_ID="$session_id"
  export WORKSPACE_DIR="$workspace_dir"
  export STEP_PREPARE="$step_prepare"
  export STEP_CONFIRM="$step_confirm"
  export STEP_APPLY="$step_apply"
  export PREPARE_JSON="$prepare_json"
  export CONFIRM_JSON="$confirm_json"
  export LLM_OUTPUT_JSON="$workspace_llm_output"
  export PAYLOAD_JSON="$payload_json"
  export APPLY_RESULT_JSON="$apply_result_json"
  export IMPLEMENTATION_BASELINE_HTML="$implementation_baseline_html"
  export SKIP_SAVE="$skip_save"
  export STOP_AFTER_PREPARE="$stop_after_prepare"
  export STOP_AFTER_CONFIRM="$stop_after_confirm"

  python3 - "$harness_result_json" <<'PY'
import json
import os
import sys
from pathlib import Path

def env(name: str) -> str:
    return os.environ.get(name, "")

def optional(path_text: str) -> str | None:
    return path_text if path_text else None

def existing(path_text: str) -> str | None:
    text = optional(path_text)
    if not text:
        return None
    path = Path(text).expanduser()
    return str(path.resolve()) if path.exists() else None

result_path = Path(sys.argv[1]).expanduser().resolve()
result_path.parent.mkdir(parents=True, exist_ok=True)

status = env("HARNESS_STATUS")
ok = status in {"succeeded", "awaiting_app_business_confirmation", "awaiting_llm_output"}

summary = {
    "ok": ok,
    "status": status,
    "message": optional(env("HARNESS_MESSAGE")),
    "session_id": env("SESSION_ID"),
    "workspace_dir": env("WORKSPACE_DIR"),
    "mode": {
        "save_enabled": env("SKIP_SAVE") != "1",
        "manual_implementation_required": True,
        "stop_after_prepare": env("STOP_AFTER_PREPARE") == "1",
        "stop_after_confirm": env("STOP_AFTER_CONFIRM") == "1",
    },
    "steps": {
        "prepare": env("STEP_PREPARE"),
        "confirm": env("STEP_CONFIRM"),
        "apply": env("STEP_APPLY"),
    },
    "artifacts": {
        "prepare_context_json": existing(env("PREPARE_JSON")),
        "app_business_confirm_json": existing(env("CONFIRM_JSON")),
        "llm_output_json": existing(env("LLM_OUTPUT_JSON")),
        "page_document_save_payload_json": existing(env("PAYLOAD_JSON")),
        "apply_result_json": existing(env("APPLY_RESULT_JSON")),
        "implementation_baseline_html": existing(env("IMPLEMENTATION_BASELINE_HTML")),
        "implementation_review_json": existing(str(Path(env("WORKSPACE_DIR")) / "implementation_review.json")),
        "tracking_schema_json": existing(str(Path(env("WORKSPACE_DIR")) / "tracking_schema.json")),
        "implementation_guide_md": existing(str(Path(env("WORKSPACE_DIR")) / "openclaw_tracking_implementation.md")),
        "save_api_response_json": existing(str(Path(env("WORKSPACE_DIR")) / "save_api_response.json")),
        "logs_dir": existing(str(Path(env("WORKSPACE_DIR")) / "logs")),
    },
}
result_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

die() {
  harness_status="failed"
  harness_message="$1"
  write_summary
  echo "ERROR: $1" >&2
  echo "Harness summary: ${harness_result_json}" >&2
  exit 1
}

log "Session: ${session_id}"
log "Workspace: ${workspace_dir}"

log "Step 1/3: prepare_tracking_context.py"
prepare_cmd=(
  python3 "${SCRIPT_DIR}/prepare_tracking_context.py"
  "$html_path"
  --workspace-dir "$workspace_dir"
  --session-id "$session_id"
  --app-page-size "$app_page_size"
  --output "$prepare_json"
  --json
)
[[ -n "$tracking_env" ]] && prepare_cmd+=(--tracking-env "$tracking_env")
[[ -n "$tracking_base_url" ]] && prepare_cmd+=(--tracking-base-url "$tracking_base_url")
[[ -n "$cert_path" ]] && prepare_cmd+=(--cert-path "$cert_path")
[[ -n "$cert_password" ]] && prepare_cmd+=(--cert-password "$cert_password")
[[ -n "$user_name" ]] && prepare_cmd+=(--user-name "$user_name")

if ! "${prepare_cmd[@]}" > "${logs_dir}/step1_prepare.stdout.json" 2> "${logs_dir}/step1_prepare.stderr.log"; then
  step_prepare="failed"
  die "Step 1 failed. Check ${logs_dir}/step1_prepare.stderr.log"
fi

if ! python3 - "$prepare_json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser().resolve()
payload = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit("prepare_context.json is not an object")
if payload.get("ok") is not True:
    raise SystemExit("prepare_context.ok is not true")
workspace_html = payload.get("workspace_html")
if not workspace_html:
    raise SystemExit("prepare_context.workspace_html is missing")
if not Path(str(workspace_html)).expanduser().exists():
    raise SystemExit("workspace_html file does not exist")
PY
then
  step_prepare="failed"
  die "Step 1 gate check failed. Check ${prepare_json}"
fi
step_prepare="succeeded"

if [[ "$stop_after_prepare" -eq 1 ]]; then
  step_confirm="skipped"
  step_apply="skipped"
  harness_status="awaiting_app_business_confirmation"
  harness_message="Stopped after prepare. Confirm app_id/app_code/business_code, create llm_output, then rerun without --stop-after-prepare."
  write_summary
  log "Stopped by --stop-after-prepare."
  log "Harness summary: ${harness_result_json}"
  exit 0
fi

if [[ -n "$app_id" || -n "$app_code" || -n "$business_code" ]]; then
  if [[ -z "$app_id" || -z "$app_code" || -z "$business_code" ]]; then
    die "Provide all of --app-id --app-code --business-code together."
  fi
elif [[ "$accept_recommendation" -eq 1 ]]; then
  mapfile -t recommended_values < <(python3 - "$prepare_json" <<'PY'
import json
import sys

path = sys.argv[1]
payload = json.loads(open(path, "r", encoding="utf-8").read())
app = payload.get("app_recommendation", {}).get("recommended", {})
biz = payload.get("business_line_recommendation", {}).get("recommended", {})
app_id = str(app.get("app_id") or "").strip()
app_code = str(app.get("app_code") or "").strip()
business_code = str(biz.get("business_code") or "").strip()
if not (app_id and app_code and business_code):
    raise SystemExit("recommended app/business is incomplete")
print(app_id)
print(app_code)
print(business_code)
PY
  ) || die "Failed to read recommended app/business from ${prepare_json}"
  [[ "${#recommended_values[@]}" -eq 3 ]] || die "Failed to parse recommended app/business values."
  app_id="${recommended_values[0]}"
  app_code="${recommended_values[1]}"
  business_code="${recommended_values[2]}"
else
  die "Missing app/business confirmation. Provide --app-id --app-code --business-code or pass --accept-recommendation."
fi

log "Step 2/3: confirm_app_business.py"
confirm_cmd=(
  python3 "${SCRIPT_DIR}/confirm_app_business.py"
  --prepare-context "$prepare_json"
  --app-id "$app_id"
  --app-code "$app_code"
  --business-code "$business_code"
  --output "$confirm_json"
  --json
)
if ! "${confirm_cmd[@]}" > "${logs_dir}/step2_confirm.stdout.json" 2> "${logs_dir}/step2_confirm.stderr.log"; then
  step_confirm="failed"
  die "Step 2 failed. Check ${logs_dir}/step2_confirm.stderr.log"
fi

if ! python3 - "$confirm_json" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
if not isinstance(payload, dict):
    raise SystemExit("app_business_confirm.json is not an object")
required = ("app_id", "app_code", "business_code")
missing = [key for key in required if not str(payload.get(key) or "").strip()]
if missing:
    raise SystemExit(f"Missing confirmed fields: {', '.join(missing)}")
PY
then
  step_confirm="failed"
  die "Step 2 gate check failed. Check ${confirm_json}"
fi
step_confirm="succeeded"

if [[ "$stop_after_confirm" -eq 1 ]]; then
  step_apply="skipped"
  harness_status="awaiting_llm_output"
  harness_message="Stopped after confirm. Provide llm_output and rerun without --stop-after-confirm."
  write_summary
  log "Stopped by --stop-after-confirm."
  log "Harness summary: ${harness_result_json}"
  exit 0
fi

[[ -z "$llm_output" ]] && die "Missing --llm-output. Or use --stop-after-confirm to end at step 2."
llm_output="$(abs_path "$llm_output")"
[[ ! -f "$llm_output" ]] && die "llm_output file not found: $llm_output"
if [[ "$llm_output" != "$workspace_llm_output" ]]; then
  cp "$llm_output" "$workspace_llm_output"
fi

if ! python3 - "$workspace_llm_output" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
if not isinstance(payload, dict):
    raise SystemExit("llm_output is not an object")
for key in ("page_name", "page_code", "regions"):
    if key not in payload:
        raise SystemExit(f"llm_output missing top-level key: {key}")
if not isinstance(payload.get("regions"), list) or len(payload["regions"]) == 0:
    raise SystemExit("llm_output.regions must be a non-empty list")
for idx, region in enumerate(payload["regions"], start=1):
    if not isinstance(region, dict):
        raise SystemExit(f"region #{idx} is not an object")
    for key in ("data_ai_id", "action", "action_id", "action_fields"):
        if key not in region:
            raise SystemExit(f"region #{idx} missing key: {key}")
    if not isinstance(region.get("action_fields"), list):
        raise SystemExit(f"region #{idx} action_fields must be a list")
PY
then
  die "llm_output validation failed. Check ${workspace_llm_output}"
fi

log "Step 3/3: apply_llm_output.py"
apply_cmd=(
  python3 "${SCRIPT_DIR}/apply_llm_output.py"
  --prepare-context "$prepare_json"
  --app-business "$confirm_json"
  --llm-output "$workspace_llm_output"
  --output "$payload_json"
  --base-revision "$base_revision"
  --save-endpoint "$save_endpoint"
  --save-timeout "$save_timeout"
  --json
)
[[ -n "$page_binding_id" ]] && apply_cmd+=(--page-binding-id "$page_binding_id")
[[ -n "$project_id" ]] && apply_cmd+=(--project-id "$project_id")
[[ -n "$tracking_base_url" ]] && apply_cmd+=(--tracking-base-url "$tracking_base_url")
[[ -n "$cert_path" ]] && apply_cmd+=(--cert-path "$cert_path")
[[ -n "$cert_password" ]] && apply_cmd+=(--cert-password "$cert_password")
[[ -n "$weblog_app_key" ]] && apply_cmd+=(--weblog-app-key "$weblog_app_key")
[[ "$weblog_debug" -eq 1 ]] && apply_cmd+=(--weblog-debug)
[[ "$skip_save" -eq 1 ]] && apply_cmd+=(--skip-save)

if ! "${apply_cmd[@]}" > "$apply_result_json" 2> "${logs_dir}/step3_apply.stderr.log"; then
  step_apply="failed"
  die "Step 3 failed. Check ${logs_dir}/step3_apply.stderr.log"
fi

if ! python3 - "$apply_result_json" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
if payload.get("ok") is not True:
    raise SystemExit("apply result ok=false")
PY
then
  step_apply="failed"
  die "Step 3 gate check failed. Check ${apply_result_json}"
fi
step_apply="succeeded"

workspace_html_for_review="$(python3 - "$prepare_json" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
path = str(payload.get("workspace_html") or "").strip()
if not path:
    raise SystemExit("workspace_html missing in prepare_context.json")
print(path)
PY
)" || die "Failed to resolve workspace_html from ${prepare_json}"

if [[ ! -f "$workspace_html_for_review" ]]; then
  die "workspace_html not found for baseline snapshot: ${workspace_html_for_review}"
fi
cp "$workspace_html_for_review" "$implementation_baseline_html"

harness_status="succeeded"
harness_message="Pipeline finished. Review tracking_schema.json and openclaw_tracking_implementation.md, hand-write tracking changes, then run review_tracking_implementation.py until status=passed."
write_summary

log "Done."
log "Harness summary: ${harness_result_json}"
