# EXAMPLES

以下示例用于快速选择执行路径。默认路径始终是 `run_tracking_harness.sh` 主线。

## 示例 1：本地 HTML 从零开始（dry-run）

用户输入：
- “给这个本地 HTML 做一套埋点设计并实现代码，不要落库。”

前置条件：
- 提供 `html_path`
- 新建或指定 `session_id`

执行路径：
- `harness --stop-after-prepare` -> 展示推荐并确认 -> 生成 `llm_output.json` -> `harness` 全流程（不加 `--save`）-> 手写实现 -> `run_tracking_closed_loop.py` 闭环，直到通过

期望产物：
- `prepare_context.json`
- `app_business_confirm.json`
- `llm_output.json`
- `tracking_schema.json`
- `openclaw_tracking_implementation.md`
- `implementation_review.json`（`status=passed`）
- `validation_gate.json`（`status=passed`）

## 示例 2：用户已明确确认 app/business

用户输入：
- “app_id/app_code/business_code 就用这组值，继续做后面流程。”

前置条件：
- 已拿到明确的 `app_id/app_code/business_code`

执行路径：
- 跳过确认对话，直接用显式 `--app-id --app-code --business-code` 执行后续流程 -> `run_tracking_closed_loop.py` 闭环

期望产物：
- `harness_result.json.status=succeeded`
- `tracking_schema.json`
- `implementation_review.json`（`status=passed`）
- `validation_gate.json`（`status=passed`）

## 示例 3：仅生成 `llm_output.json`

用户输入：
- “先只给我出埋点草案 json，不要改代码。”

前置条件：
- 工作副本 HTML 已准备好

执行路径：
- 读取模板和工作副本，仅输出 `.workspace/<session>/llm_output.json`

期望产物：
- `llm_output.json`

## 示例 4：仅做手写实现

用户输入：
- “schema 已经有了，你只把埋点代码补上。”

前置条件：
- 已存在 `tracking_schema.json` 与实现说明

执行路径：
- 读取 schema 与实现说明 -> 手写埋点代码（仅工作副本）-> `run_tracking_closed_loop.py` 失败则修复并复跑

期望产物：
- 变更后的工作副本 HTML
- `implementation_review.json`（`status=passed`）
- `validation_gate.json`（`status=passed`）

## 示例 5：仅做 review / verify

用户输入：
- “帮我检查这次埋点改动有没有风险。”

前置条件：
- 工作副本已包含手写改动

执行路径：
- 优先运行 `run_tracking_closed_loop.py --json`
- 若 review 通过但 runtime gate 未过，再使用 `runtime_browser_session.py start/act/assert`

期望产物：
- `validation_gate.json`
- `implementation_review.json`
- `runtime_browser_verification.json`
- 风险结论（passed/failed）

## 示例 6：明确要求真实落库

用户输入：
- “确认可以落库，按正式接口保存。”

前置条件：
- 用户明确授权 `--save`
- 连接参数齐全

执行路径：
- 在全流程命令上增加 `--save`，完成后检查保存结果

期望产物：
- `save_api_response.json` 或可定位错误信息
- `harness_result.json.status=succeeded`（业务成功时）

## 示例 7：不用先写完整 case，改成 agent 动态探索

用户输入：
- “case 预生成不稳定，给我一个无头浏览器状态 + 操作能力，我想让 agent 动态决定下一步点哪里、测哪个埋点。”

前置条件：
- 工作副本 HTML 与 `tracking_schema.json` 已存在

执行路径：
- `runtime_browser_session.py start` 建立浏览器 session
- 读取 `state.clickable_elements`
- `runtime_browser_session.py act` 一步一步执行点击/等待/求值
- `runtime_browser_session.py assert` 检查当前目标事件是否已命中
- 运行 `run_tracking_validation_gate.py` 或 `run_tracking_closed_loop.py`，让 `runtime_browser_verification.json` 汇总 session 覆盖结果

期望产物：
- `.workspace/<session>/runtime_browser_sessions/<session_id>/session.json`
- `.workspace/<session>/runtime_browser_sessions/<session_id>/states/state_*.json`
- `.workspace/<session>/runtime_browser_sessions/<session_id>/screenshots/state_*.png`
- `.workspace/<session>/runtime_browser_verification.json`
