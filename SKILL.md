---
name: tracking-design-llm
description: "Use when Codex needs to design and implement Weblog tracking for a local HTML page. Use scripts/run_tracking_harness.sh as the only flow entry, reason only at WAITING_AGENT stages, and continue until validation_gate.json.status=passed."
---

# LLM 自动埋点设计

## 技能目的

用于本地 HTML 页面的 Weblog 埋点设计与实现。流程由 `scripts/run_tracking_harness.sh` 状态机脚本统一推进。

你不需要自己拼流程；只做两件事：

1. 调用 harness 推进到断点
2. 在断点按 `harness_result.json` 指引执行并回灌

## 关键路径

- 推进脚本：`scripts/run_tracking_harness.sh`
- 状态文件：`.workspace/<session>/harness_state.json`
- 对外结果：`.workspace/<session>/harness_result.json`

## 执行循环

1. 首次调用 harness 初始化会话
2. 读取 `harness_result.json.status/current_stage/next_action`
3. 若 `status=WAITING_AGENT`，按 `next_action.required_reads` 读取文件并产出 JSON
4. 用 `next_action.submit_via` 指示的命令回灌
5. 若 `status=WAITING_USER`，等待用户确认并按 `submit_via` 回灌
6. 循环直到 `DONE` 或 `FAILED`

## 状态码

| status | 含义 |
|---|---|
| `WAITING_AGENT` | 需要 agent 推理或实现；必须读取 `required_reads` |
| `WAITING_USER` | 需要用户确认；等待确认后再推进 |
| `DONE` | 闭环完成；以 `validation_gate.json.status=passed` 为准 |
| `FAILED` | 脚本执行失败；读取 `error` 与相关产物排查 |

## 常用命令

### 1) 初始化会话

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --json
```

### 2) 回灌 agent 的 app/business 推荐

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --agent-app-business-json "<agent_json_path>" \
  --json
```

### 3) 用户确认 app/business

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --confirm-app-id "<app_id>" \
  --confirm-app-code "<app_code>" \
  --confirm-business-code "<business_code>" \
  --json
```

### 4) 回灌 agent 的 llm_output

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --agent-llm-output-json "<agent_json_path>" \
  --json
```

### 5) 手写埋点完成后触发闭环

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --implementation-done \
  --json
```

### 6) Runtime 浏览器验证（按需）

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

## 必须遵守

- 原始 HTML 只读；只修改 `.workspace/<session>/` 工作副本
- `app_id/app_code/business_code` 未确认前，禁止推进 `llm_output` 与手写埋点
- app/business 必须来自 `all_apps_catalog.json` 与 `all_business_lines_catalog.json`
- 默认 dry-run；只有用户明确授权才使用 `--save`
- `llm_output` 只允许顶层 `page_name/page_code/regions`；不要求 `runtime_hints`
- 需要阅读的参考资料，统一以 `harness_result.json.next_action.required_reads` 为准
- 最终完成标准只有一个：`validation_gate.json.status=passed`

## 参考文档

- `references/prepare_and_confirm.md`
- `references/llm_output_spec.md`
- `references/apply_and_save.md`
- `references/manual_implementation_rules.md`
- `references/review_protocol.md`
- `references/runtime_verification.md`
- `references/validation_loop.md`
- `references/troubleshooting.md`
- `references/weblog_sdk_reference.md`
