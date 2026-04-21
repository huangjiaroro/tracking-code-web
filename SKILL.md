---
name: tracking-design-llm
description: "Use when Codex needs to design and implement Weblog tracking for a local HTML page. Follow a harness-first workflow: prepare workspace, confirm app/business IDs from catalogs, generate llm_output.json, run run_tracking_harness, manually implement tracking code in workspace copy, and pass review verification."
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
- 手写实现后必须执行 `review_tracking_implementation.py`，且 `status=passed` 才算完成。

## Routing Table

根据任务类型选择最小参考集合，默认都走 `run_tracking_harness.sh`。

| 任务类型 | 触发信号 | 读取文档 | 执行路径 |
|---|---|---|---|
| 新页面全流程埋点（默认） | 用户要“做埋点设计并改代码” | `references/prepare_and_confirm.md` + `references/llm_output_spec.md` + `references/apply_and_save.md` + `references/manual_implementation_rules.md` + `references/review_protocol.md` | `harness --stop-after-prepare` -> 确认 app/business -> 生成 `llm_output.json` -> `harness` 全流程 -> 手写实现 -> review |
| 已确认 app/business 后继续 | 用户已给 `app_id/app_code/business_code` | `references/llm_output_spec.md` + `references/apply_and_save.md` + `references/manual_implementation_rules.md` + `references/review_protocol.md` | 跳过确认对话，直接传显式 `--app-id --app-code --business-code` 执行后续流程 |
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

读取 `tracking_schema.json` 和 `openclaw_tracking_implementation.md`，只在工作副本手写埋点代码，并满足 fail-open 与行为不变。

### 6) Review / Verify（必做）

```bash
python3 scripts/review_tracking_implementation.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

只有 `status=passed` 才可交付。若未通过，按 `references/review_protocol.md` 与 `references/troubleshooting.md` 处理后重试。

## Output Contracts

交付前必须全部满足：
- `app_business_confirm.json` 已写入用户确认的 `app_id/app_code/business_code`。
- `harness_result.json` 满足 `ok=true` 且 `status=succeeded`。
- `page_document_save_payload.json`、`tracking_schema.json`、`openclaw_tracking_implementation.md` 已生成。
- `tracking_schema.json` 的事件与页面元素可对应；`unresolved_regions` 为空或有明确说明。
- 手写代码仅修改 `.workspace/<session>/`；原始 HTML 未被修改。
- `implementation_review.json` 已生成且 `status=passed`。
- 若执行 `--save`，必须提供 `save_api_response.json` 或明确的失败原因。

## 参考文档索引

- `references/prepare_and_confirm.md`
- `references/llm_output_spec.md`
- `references/apply_and_save.md`
- `references/manual_implementation_rules.md`
- `references/review_protocol.md`
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
- `harness_result.json`
