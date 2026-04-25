# Prepare And Confirm

本阶段目标是产出可确认的推荐值，并在继续前拿到用户明确确认。

## 1. 准备工作副本

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --json
```

## 2. 检查准备结果

读取 `.workspace/<session>/harness_result.json` 并确认：
- `status=WAITING_AGENT`
- `current_stage=app_business_guess`
- `artifacts.prepare_context_json` 存在

如果停在 `WAITING_USER/confirm_runtime_config`：
- `tracking_env` / `tracking_base_url` 可按 `submit_via` 继续提交
- 证书路径或证书密码有问题时，不要追加命令行参数；直接修改 `required_reads` 中列出的配置文件后重跑

## 3. 展示推荐与映射依据

由 agent 阅读 `harness_result.json.next_action.required_reads`，产出推荐 JSON 后提交：

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --agent-app-business-json "<agent_json_path>" \
  --json
```

提交成功后状态应进入 `WAITING_USER/confirm_app_business`。

## 4. 硬 gate

确认前禁止：
- 生成 `llm_output.json`
- 生成保存 payload
- 手写埋点代码
- 调用真实保存接口

## 5. 继续执行的输入要求

用户确认后提交：

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --confirm-app-id "<id>" \
  --confirm-app-code "<code>" \
  --confirm-business-code "<code>" \
  --json
```

或在允许直接采用推荐时使用 `--accept-recommendation`。
