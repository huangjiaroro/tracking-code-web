# Troubleshooting

仅在 harness 主线失败时使用本页。修复后应回到 `run_tracking_harness.sh` 主线。

## 症状：等待确认阶段异常

现象：
- `harness_result.json.status` 不是 `WAITING_AGENT`
- `harness_result.json.current_stage` 不是 `app_business_guess`
- 缺少 `prepare_context.json`

排查：
- 确认 `--html` 和 `--session-id` 参数
- 重新执行初始化：`scripts/run_tracking_harness.sh --html "<html_path>" --session-id "<session>" --json`

## 症状：apply 阶段 unresolved_count 非 0

现象：
- `apply_result.json.unresolved_count != 0`

排查：
- 校验 `llm_output.json` 中 `data_ai_id` 是否真实存在于工作副本 HTML
- 删除虚构或失效 region 后重跑

## 症状：app key 未解析

现象：
- `weblog_app_key_status` 非已解析状态

排查：
- 确认 catalog 映射值
- 必要时显式传 `--weblog-app-key` 重跑

## 症状：save 失败

现象：
- `save_api_business_success=false` 或有 `save_api_error`

排查：
- 核对是否已获用户授权 `--save`
- 检查 `--tracking-base-url`、证书参数和环境可达性
- 先 dry-run 验证 schema 与 payload，再尝试 save

## 症状：review 未通过

现象：
- `implementation_review.json.status` 为 `needs_review` 或 `failed`

排查：
- 对照 `review_protocol.md` 修复风险项
- 重点回看事件是否落地、锚点是否存在、是否有行为破坏改动
- 修复后提交 `--implementation-done`

## 症状：runtime gate 未通过

现象：
- `validation_gate.json.status` 非 `passed`
- `harness_result.json.current_stage=runtime_fix`

排查：
- 先执行 `--runtime-start`
- 根据 `runtime_browser_preflight.json` 推荐步骤执行多次 `--runtime-act-json`
- 每个目标事件执行 `--runtime-assert-json '{"event_id":"...","action":"click"}'`
- 最后执行 `--runtime-check`

## 仅在必要时使用单脚本

当 harness 无法定位问题时，可局部重跑：
- `python3 scripts/prepare_tracking_context.py ...`
- `python3 scripts/confirm_app_business.py ...`
- `python3 scripts/apply_llm_output.py ...`
