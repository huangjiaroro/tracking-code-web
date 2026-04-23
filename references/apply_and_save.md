# Apply And Save

本阶段目标是基于确认值和 `llm_output.json` 生成 schema、payload 和实现指引。

## 默认执行（dry-run）

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --app-id "<confirmed_app_id>" \
  --app-code "<confirmed_app_code>" \
  --business-code "<confirmed_business_code>" \
  --llm-output ".workspace/<session>/llm_output.json"
```

## 真实落库（需明确授权）

只有用户明确要求时才允许加 `--save`。

可选参数：
- `--tracking-base-url`
- `--cert-path`
- `--cert-password`
- `--weblog-app-key`
- `--weblog-debug`（调试用，验收前建议关闭）

## 必查结果

读取 `.workspace/<session>/harness_result.json`：
- `ok=true`
- `status=succeeded`
- `steps.prepare/confirm/apply` 为 `succeeded`
- `mode.manual_implementation_required=true`

检查产物：
- `page_document_save_payload.json`
- `tracking_schema.json`
- `openclaw_tracking_implementation.md`

后续手写实现完成后应默认运行 `run_tracking_validation_gate.py`。若 review 通过但 runtime gate 仍未通过，再继续使用 `runtime_browser_session.py start/act/assert` 补齐真实触发路径，直到 `runtime_browser_verification.json.status=passed`。

如果执行了 `--save`，还需检查：
- `save_api_response.json`
- 或 `apply_result.json` 中的 `save_api_error`

## 常见失败信号

- `unresolved_count != 0`：通常是 `data_ai_id` 与 HTML 不匹配。
- `weblog_app_key_status` 非已解析状态：需要确认 app key 并重跑。
