# Apply And Save

本阶段目标是基于确认值和 `llm_output.json` 生成 schema、payload 和实现指引。

## 默认执行（dry-run）

在 `WAITING_AGENT/llm_output_design` 阶段提交 agent 输出：

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --agent-llm-output-json "<agent_llm_output_json_path>" \
  --json
```

## 真实落库（需明确授权）

只有用户明确要求时才允许在同一步加 `--save`：

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --agent-llm-output-json "<agent_llm_output_json_path>" \
  --save \
  --json
```

可选参数：
- `--tracking-env`（未显式传 `--tracking-base-url` 时，会自动使用对应环境的默认 baseUrl）
- `--tracking-base-url`
- `--weblog-app-key`
- `--weblog-debug`（调试用，验收前建议关闭）

证书路径和证书密码始终从配置栈读取；如有问题请修改 `session.json`、`config.json` 或 `~/.skillhub-cli/config.json`。

## 必查结果

读取 `.workspace/<session>/harness_result.json`：
- `ok=true`
- `status=WAITING_AGENT`
- `current_stage=manual_implementation`

检查产物：
- `llm_output.json`
- `apply_result.json`
- `page_document_save_payload.json`
- `tracking_schema.json`
- `openclaw_tracking_implementation.md`

后续手写实现完成后提交：

```bash
scripts/run_tracking_harness.sh --session-id "<session>" --implementation-done --json
```

若进入 `review_fix` 或 `runtime_fix`，按 `harness_result.json.next_action` 继续修复与验证。

如果执行了 `--save`，还需检查：
- `save_api_response.json`
- 或 `apply_result.json` 中的 `save_api_error`

## 常见失败信号

- `unresolved_count != 0`：通常是 `data_ai_id` 与 HTML 不匹配。
- `weblog_app_key_status` 非已解析状态：需要确认 app key 并重跑。
