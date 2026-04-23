# Runtime Verification

本阶段只保留 `runtime_browser_session` 这一条正式运行时验证路径。

## 入口约束

默认入口是 `scripts/run_tracking_harness.sh`。  
需要阅读哪些资料、下一步执行什么命令，统一以 `.workspace/<session>/harness_result.json.next_action.required_reads` 与 `next_action.submit_via` 为准。

默认路子是直接使用 `runtime_browser_session.py` 做“看当前页面状态 -> 决定下一步 act -> 检查当前 report / assert”的探索式运行时验证，不再生成或消费 case-based runtime artifacts。

`review_tracking_implementation.py` 仍然是正式静态 gate；`run_tracking_validation_gate.py` 会在 review 通过后继续读取 `runtime_browser_session` 产物，并生成 `runtime_browser_verification.json`。

## 默认 Runtime Browser Session

优先通过 harness 推进：

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

下方命令保留为底层脚本说明（排障或调试时可直接使用）。

首次在一台机器上使用浏览器态调试前，先初始化标准运行环境：

```bash
python3 scripts/setup_runtime_verify_env.py --json
```

然后先生成源码预定位结果：

```bash
python3 scripts/prepare_runtime_browser_preflight.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

`runtime_browser_session.py` 对这一步做了硬约束：如果 `runtime_browser_preflight.json` 不存在、不是 `prepared`、或其 schema / target file 指纹与当前工作副本不一致，`start/state/act/assert` 都会直接失败并要求先重做 preflight。

这一步会：

- 按 `event_id` 回读工作副本源码
- 给出 `trackClick(...)` / `trackPageShow(...)` 的命中位置
- 尽量推断真实触发 selector、所在 view 和前置条件
- 产出 `.workspace/<session>/runtime_browser_preflight.json`

然后直接启动浏览器 session：

```bash
.workspace/runtime-verify-venv/bin/python scripts/runtime_browser_session.py start \
  --workspace-dir ".workspace/<session>" \
  --session-id agent-loop \
  --reset \
  --json
```

后续可以按步驱动：

```bash
.workspace/runtime-verify-venv/bin/python scripts/runtime_browser_session.py act \
  --workspace-dir ".workspace/<session>" \
  --session-id agent-loop \
  --step-json '{"type":"click","selector":"[data-ai-id=\"ai-14\"]"}' \
  --json
```

```bash
.workspace/runtime-verify-venv/bin/python scripts/runtime_browser_session.py assert \
  --workspace-dir ".workspace/<session>" \
  --session-id agent-loop \
  --event-id "<event_id>" \
  --action click \
  --json
```

它会：

- 重放该 session 之前已经确认的历史步骤
- 在真实浏览器里执行新增步骤
- 产出新的 state JSON 与截图
- 汇总当前已捕获 report、未覆盖 schema event、匹配成功的 assertion

## 默认探索顺序

默认探索建议分两轮：

- 第一轮先读 `runtime_browser_preflight.json`，按源码预定位结果做有目的的浏览器验证；再结合当前 `clickable_elements`、页面状态和可见文案推进，尽快覆盖大多数事件
- 若首轮后 `runtime_browser_verification.json.summary.uncovered_event_ids` 仍非空，不要只做盲点重试；应按每个未覆盖 `event_id` 回读工作副本源码，或重新运行 `prepare_runtime_browser_preflight.py --event-id <id>`，搜索对应的 `trackClick('<event_id>'`、`trackPageShow('<event_id>'` 或同名埋点调用
- 从源码绑定位置反推出真实触发节点、所在 view、前置状态和必要交互，再针对这些未覆盖事件做一轮定向 `act` / `assert`

常见信号：

- schema 的 `selector_candidates` 指向的是区域容器，不一定是真实绑定 click 的叶子节点
- `runtime_browser_preflight.json` 里的 `preferred_runtime_selector` 优先级通常高于 schema 的容器型 selector

## 与 Validation Gate 的关系

运行：

```bash
python3 scripts/run_tracking_validation_gate.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

gate 会读取 `runtime_browser_sessions/` 并生成：

- `.workspace/<session>/runtime_browser_verification.json`
- `.workspace/<session>/validation_gate.json`

只有当 schema 事件已经被这些 session 产物覆盖时，默认 runtime gate 才会通过。

若只想单独检查浏览器态覆盖结果，可运行：

```bash
python3 scripts/verify_tracking_runtime_browser_session.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

## 关键产物

- `runtime_browser_preflight.json`
- `runtime_browser_sessions/<session_id>/session.json`
- `runtime_browser_sessions/<session_id>/states/state_*.json`
- `runtime_browser_sessions/<session_id>/screenshots/state_*.png`
- `runtime_browser_verification.json`
