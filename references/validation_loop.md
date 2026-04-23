# Validation Loop

本页定义正式闭环流程。对手写埋点实现，不再只做一次性 `review`；而是必须进入“校验失败 -> 修复 -> 复跑”的循环，直到 gate 通过或确认存在真实阻塞。

## 正式入口

默认优先使用 closed-loop wrapper：

```bash
python3 scripts/run_tracking_closed_loop.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

它默认会：

- 运行 `run_tracking_validation_gate.py`
- 先检查 `implementation_review.json`
- 再检查 `runtime_browser_session` 产物是否已覆盖 schema 事件

若你只想手动调试底层命令，再分别使用：

```bash
python3 scripts/setup_runtime_verify_env.py --json
python3 scripts/run_tracking_validation_gate.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

首次在一台机器上执行 `runtime_browser_session.py` 前，建议先运行一次上面的环境初始化命令。它会创建项目内 `.workspace/runtime-verify-venv`，避免把 `playwright` 安装到系统或 Homebrew 管理的 Python 里。

## 通过条件

只有满足以下条件，才可声明“实现完成”：

- `implementation_review.json.status == "passed"`
- `implementation_review.json.status == "needs_review"` 仍然视为未通过，必须修复或明确人工确认后复跑
- `runtime_browser_verification.json.status == "passed"`
- `validation_gate.json.status == "passed"`

## 闭环规则

当 `validation_gate.json.status != "passed"` 时：

1. 读取 `validation_gate.json`
2. 根据失败来源进入对应产物
3. 只修改 `.workspace/<session>/` 工作副本
4. 修复后重新运行 `run_tracking_validation_gate.py`
5. 重复直到 `status=passed`，或发现真实阻塞并明确说明

### 失败来源定位

- `review.status != passed`
  - 读取 `implementation_review.json`
  - 重点看 `findings`
- `runtime_verification.status != passed`
  - 默认 runtime gate 读取 `runtime_browser_verification.json`
  - 在首次启动 `runtime_browser_session.py` 前，先运行 `prepare_runtime_browser_preflight.py` 并读取 `runtime_browser_preflight.json`
  - 若失败原因是 `no_runtime_browser_sessions`，先运行 `setup_runtime_verify_env.py` 和 `runtime_browser_session.py start`
  - 若失败原因是 `no_reports_captured` 或 `schema_events_not_covered`，先回看 `runtime_browser_preflight.json` 里对应 `event_id` 的源码预定位结果，再继续用 `runtime_browser_session.py act/assert` 触发真实交互，直到未覆盖事件补齐
  - 补齐后重跑 `run_tracking_validation_gate.py`

## 手动 Runtime Browser Session

```bash
python3 scripts/setup_runtime_verify_env.py --json
python3 scripts/prepare_runtime_browser_preflight.py --workspace-dir ".workspace/<session>" --json
.workspace/runtime-verify-venv/bin/python scripts/runtime_browser_session.py start --workspace-dir ".workspace/<session>" --session-id agent-loop --reset --json
```

然后根据 `runtime_browser_preflight.json` 和当前 state 决定下一步 `act` / `assert`。当你重新运行 `run_tracking_validation_gate.py` 时，它会读取这些 session 产物并生成 `runtime_browser_verification.json`。

## 环境说明

默认运行时验证需要：

- Python `playwright`
- 本机 Google Chrome，或 Playwright 管理的 Chromium
