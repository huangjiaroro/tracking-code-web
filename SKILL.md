---
name: tracking-design-llm
description: "Use when Codex needs to design and implement Weblog tracking for a local HTML page. Follow a harness-first workflow: prepare workspace, confirm app/business IDs from catalogs, generate llm_output.json, run run_tracking_harness, manually implement tracking code in the workspace copy, then close the loop with review plus runtime_browser_session-based runtime verification until validation_gate passes."
---

# LLM 自动埋点设计

## 定位

这个技能用于本地 HTML 页面的 Weblog 埋点设计与实现，执行策略是 `harness-first`，不是单脚本拼装流程。

## 硬规则（必须满足）

- 原始 HTML 只读；只改 `.workspace/<session>/` 下的工作副本与产物。
- `app_id/app_code/business_code` 确认是硬 gate。确认前禁止生成 `llm_output.json`、保存 payload、手写埋点代码。
- 应用和业务线值必须来自 `all_apps_catalog.json` 与 `all_business_lines_catalog.json`，不能直接使用用户口述名称。
- 默认 dry-run。只有用户明确同意后才允许 `--save` 调用真实保存接口。
- 优先使用 `[data-ai-id="..."]` 作为锚点；`logmap` 字段值必须在触发时实时读取。
- 手写埋点必须 fail-open，且不改写原业务行为（事件链路、导航、状态机、接口调用、DOM 结构）。
- `setConfig/report` 推荐使用统一 guard 模板：先兜底 `window.weblog = window.weblog || {};` 与 no-op fallback，再用 `try/catch` 包住真实调用；不要只写裸 `try { window.weblog.setConfig(...) } catch {}`。
- 手写实现后的默认闭环入口是 `python3 scripts/run_tracking_closed_loop.py --workspace-dir ".workspace/<session>" --json`；只有 `validation_gate.json.status=passed` 才算完成。
- `implementation_review.json.status` 必须是 `passed`；`needs_review` 也不算通过，不能交付。
- 正式运行时验证统一使用 `runtime_browser_session` 探索式触发 + `runtime_browser_verification.json` 收口。
- 首次在一台机器上执行 `runtime_browser_session.py` 前，先运行 `python3 scripts/setup_runtime_verify_env.py --json`，统一使用项目内 `.workspace/runtime-verify-venv`。

## Routing Table

根据任务类型选择最小参考集合，默认都走 `run_tracking_harness.sh`。

| 任务类型 | 触发信号 | 读取文档 | 执行路径 |
|---|---|---|---|
| 新页面全流程埋点（默认） | 用户要“做埋点设计并改代码” | `references/prepare_and_confirm.md` + `references/llm_output_spec.md` + `references/apply_and_save.md` + `references/manual_implementation_rules.md` + `references/review_protocol.md` + `references/runtime_verification.md` + `references/validation_loop.md` | `harness --stop-after-prepare` -> 确认 app/business -> 生成 `llm_output.json` -> `harness` 全流程 -> 手写实现 -> `run_tracking_closed_loop.py` 闭环 |
| 已确认 app/business 后继续 | 用户已给 `app_id/app_code/business_code` | `references/llm_output_spec.md` + `references/apply_and_save.md` + `references/manual_implementation_rules.md` + `references/review_protocol.md` + `references/runtime_verification.md` + `references/validation_loop.md` | 跳过确认对话，直接传显式 `--app-id --app-code --business-code` 执行后续流程 -> `run_tracking_closed_loop.py` 闭环 |
| 只生成 `llm_output.json` | 用户只要设计草案 | `references/llm_output_spec.md` | 只输出 `.workspace/<session>/llm_output.json`，不落库，不自动实现 |
| 只做 review/verify | 用户已有实现并要求校验 | `references/review_protocol.md` | 运行 review 工具并输出风险和结论 |
| 真实保存（带 `--save`） | 用户明确要求落库 | `references/apply_and_save.md` | 在全流程基础上加 `--save`，并返回保存结果 |
| harness 失败排障 | `harness_result.json` 非 succeeded | `references/troubleshooting.md` | 按故障类型局部重跑或修复后再回到 harness 主线 |

## 默认流程（Harness First）

### 1) 准备并暂停确认

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --stop-after-prepare
```

读取 `.workspace/<session>/harness_result.json`，确认 `status=awaiting_app_business_confirmation`，并检查 prepare 产物存在。

### 2) 展示推荐并等待确认

读取 `prepare_context.json` 与 catalog，展示推荐值和映射依据。只有用户明确确认后才能进入下一步。

### 3) 生成 `llm_output.json`

按 `references/llm_output_spec.md` 生成 `.workspace/<session>/llm_output.json`，必须遵循 `templates/llm_tracking_output_template.json`。

### 4) 执行 harness 全流程（默认 dry-run）

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --app-id "<confirmed_app_id>" \
  --app-code "<confirmed_app_code>" \
  --business-code "<confirmed_business_code>" \
  --llm-output ".workspace/<session>/llm_output.json"
```

若用户明确要求真实落库，再加 `--save` 和必要连接参数。

### 5) 按 schema 手写实现（禁止自动注入）

读取 `tracking_schema.json` 与 `openclaw_tracking_implementation.md`，只在工作副本手写埋点代码，并满足 fail-open 与行为不变。

### 6) Validation Gate（必做）

默认优先运行 closed-loop wrapper：

```bash
python3 scripts/run_tracking_closed_loop.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

它默认执行两段式 gate：
- 先跑 `review_tracking_implementation.py`
- 再检查 `runtime_browser_session` 产物是否已覆盖 schema 事件

也就是说，默认闭环不是“review 通过就结束”，而是 review 通过后还要补齐 `runtime_browser_session` 的探索式触发验证。

若你只想手动调试 validation gate，再单独执行：

```bash
python3 scripts/run_tracking_validation_gate.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

只有 `validation_gate.json.status=passed` 才可交付。若未通过，按 `references/validation_loop.md`、`references/review_protocol.md` 与 `references/troubleshooting.md` 处理后重试。

### 7) Runtime Browser Session（默认运行时验证）

当 review 已通过、需要补齐正式运行时验证时，直接使用 `runtime_browser_session.py`，不需要先生成 case：

```bash
python3 scripts/setup_runtime_verify_env.py --json
```

然后先生成源码预定位结果：

```bash
python3 scripts/prepare_runtime_browser_preflight.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

`runtime_browser_session.py` 现在会硬校验这份预定位：若 `runtime_browser_preflight.json` 缺失、格式失效，或其 schema / target file 指纹与当前工作副本不一致，会直接退出，要求先重新生成 preflight。

```bash
.workspace/runtime-verify-venv/bin/python scripts/runtime_browser_session.py start \
  --workspace-dir ".workspace/<session>" \
  --session-id agent-loop \
  --reset \
  --json
```

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

这条路子的特点是：
- 不要求先把整条路径写死成 case
- 在首次打开浏览器前，先通过 `runtime_browser_preflight.json` 按源码预定位真实绑定节点、view 和前置路径
- 会保留 `runtime_browser_sessions/` 下的状态快照、截图和历史步骤
- `run_tracking_validation_gate.py` 会默认读取这些 session 产物，并生成 `runtime_browser_verification.json`
- 第一轮浏览器探索前，先读取 `runtime_browser_preflight.json`，优先使用其中的 `preferred_runtime_selector`、`view_hint` 和 `prerequisite_hints` 做定向验证
- 首轮探索后若仍有 `uncovered_event_ids`，不要只做盲点重试；应按未覆盖 `event_id` 回读工作副本源码，或重新运行 `prepare_runtime_browser_preflight.py --event-id <id>` 做定向预定位，再对这些未覆盖事件做一次定向 `act` / `assert`

### 8) Validation Gate / Closed Loop（正式流程）

每次手写改动后，都应优先运行 closed-loop wrapper：

```bash
python3 scripts/run_tracking_closed_loop.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

规则：
- `validation_gate.json.status == passed` 前，不得声明“实现完成”。
- 若 gate 失败，必须读取 `validation_gate.json` 和对应失败产物，修复工作副本后复跑。
- 若 `review` 失败，读取 `implementation_review.json`。
- 若 `runtime_browser_verification` 失败，先读取 `runtime_browser_preflight.json` 与 `runtime_browser_verification.json`；首轮探索后仍未覆盖的事件，必须按 `event_id` 回读源码，或重新运行 `prepare_runtime_browser_preflight.py --event-id <id>` 确认真实触发节点 / view / 前置路径，再用 `runtime_browser_session.py start/act/assert` 做定向补测后复跑。
- 只有出现真实阻塞时，才允许停止闭环并向用户说明原因。

## Output Contracts

交付前必须全部满足：
- `app_business_confirm.json` 已写入用户确认的 `app_id/app_code/business_code`。
- `harness_result.json` 满足 `ok=true` 且 `status=succeeded`。
- `page_document_save_payload.json`、`tracking_schema.json`、`openclaw_tracking_implementation.md` 已生成。
- `tracking_schema.json` 的事件与页面元素可对应；`unresolved_regions` 为空或有明确说明。
- 手写代码仅修改 `.workspace/<session>/`；原始 HTML 未被修改。
- `implementation_review.json` 已生成且 `status=passed`。
- `runtime_browser_preflight.json` 已生成，可用于首次浏览器验证前的源码预定位。
- `runtime_browser_verification.json` 已生成且 `status=passed`。
- `validation_gate.json` 已生成且 `status=passed`。
- 若执行 `--save`，必须提供 `save_api_response.json` 或明确的失败原因。

## 参考文档索引

- `references/prepare_and_confirm.md`
- `references/llm_output_spec.md`
- `references/apply_and_save.md`
- `references/manual_implementation_rules.md`
- `references/review_protocol.md`
- `references/runtime_verification.md`
- `references/validation_loop.md`
- `references/troubleshooting.md`
- `templates/llm_tracking_output_template.json`
- `references/weblog_sdk_reference.md`

## 标准产物

会话产物统一放在 `.workspace/<session>/`，至少包括：
- `prepare_context.json`
- `all_apps_catalog.json`
- `all_business_lines_catalog.json`
- `app_business_confirm.json`
- `llm_output.json`
- `draft_document.json`
- `change_set.json`
- `page_document_save_payload.json`
- `tracking_schema.json`
- `openclaw_tracking_implementation.md`
- `implementation_baseline.html`
- `implementation_review.json`
- `runtime_browser_preflight.json`
- `runtime_browser_sessions/`
- `runtime_browser_verification.json`
- `validation_gate.json`
- `harness_result.json`
