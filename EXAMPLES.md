# EXAMPLES

默认只使用 `scripts/run_tracking_harness.sh` 推进流程。每一步都先读取 `.workspace/<session>/harness_result.json`，按 `next_action` 执行。

## 示例 1：从零开始（dry-run）

```bash
scripts/run_tracking_harness.sh --html "<html_path>" --session-id "<session>" --json
```

当状态到 `WAITING_AGENT/app_business_guess`：

```bash
scripts/run_tracking_harness.sh --session-id "<session>" --agent-app-business-json "<agent_json>" --json
```

当状态到 `WAITING_USER/confirm_app_business`：

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --confirm-app-id "<app_id>" \
  --confirm-app-code "<app_code>" \
  --confirm-business-code "<business_code>" \
  --json
```

当状态到 `WAITING_AGENT/llm_output_design`：

```bash
scripts/run_tracking_harness.sh --session-id "<session>" --agent-llm-output-json "<agent_llm_output_json>" --json
```

当状态到 `WAITING_AGENT/manual_implementation`：

```bash
scripts/run_tracking_harness.sh --session-id "<session>" --implementation-done --json
```

若返回 `WAITING_AGENT/runtime_fix`，继续 runtime 步骤并反复 `--runtime-check`，直到 `DONE`。

## 示例 2：运行时补测闭环

```bash
scripts/run_tracking_harness.sh --session-id "<session>" --runtime-start --json
```

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --runtime-act-json '{"type":"click","selector":"[data-ai-id=\"ai-14\"]"}' \
  --json
```

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --runtime-assert-json '{"event_id":"<event_id>","action":"click"}' \
  --json
```

```bash
scripts/run_tracking_harness.sh --session-id "<session>" --runtime-check --json
```

## 示例 3：明确要求真实保存

在 `agent-llm-output-json` 步骤加 `--save`：

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --agent-llm-output-json "<agent_llm_output_json>" \
  --save \
  --json
```

完成后检查：

- `.workspace/<session>/save_api_response.json`
- `.workspace/<session>/harness_result.json`
