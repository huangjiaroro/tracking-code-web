---
name: tracking-design-llm
description: "Use when Codex needs to design and implement Weblog tracking for a local HTML page: create a work copy with data-ai-id anchors, confirm app_id/app_code/business_code from tracking catalogs, generate llm_output.json, normalize draft_document/change_set/page_document_save_payload, optionally call tracking/page_document/save, and produce tracking_schema.json plus manual implementation guidance."
---

# LLM 自动埋点设计

## 工作原则

- 保持原始 HTML 只读；只改 `.workspace/<session>/` 下的工作副本和产物。
- 第 2 步是硬 gate：先展示推荐应用与业务线，再等待用户明确确认 `app_id/app_code/business_code`；确认前禁止生成 LLM 草案、保存 payload 或代码。
- 默认本地干跑；只有用户明确要求落库时才调用真实保存接口或传 `--save`。
- 使用 catalog 里真实存在的 `app_id/app_code/business_code`；不要把用户口述名称直接当作 ID 或 code。
- 优先使用 `[data-ai-id="..."]` 作为锚点与 selector；`logmap` 字段值必须在触发时实时读取。
- 手写埋点代码时必须保持原功能不变；只允许追加埋点、补充守卫或在原逻辑之后上报，不要改写原有业务分支、页面状态机、导航、接口调用和 DOM 结构。
- 埋点逻辑必须 fail-open：`window.weblog` 不存在、SDK 加载失败、字段读取失败、上报异常时，都不能阻断原功能。
- 除非原逻辑本身已经这样做，否则不要新增 `preventDefault`、`stopPropagation`、`return false`、直接覆盖 `onclick/onload/...` 之类高风险写法。

## 资源导航

- `scripts/prepare_tracking_context.py`：复制本地 HTML、注入 `data-ai-id`、拉取应用和业务线 catalog、输出推荐。
- `scripts/confirm_app_business.py`：把用户确认或覆盖的应用/业务线写入 `app_business_confirm.json`，并从 catalog 补齐 `app_key`。
- `references/llm_tracking_output_template.json`：生成 `llm_output.json` 时必须遵循的最小模板。
- `scripts/apply_llm_output.py`：把 LLM 输出归一化为 `draft_document`、`change_set`、保存 payload、`tracking_schema.json` 和实现说明。
- `references/weblog_sdk_reference.md`：手写或修正 SDK 代码前必须阅读。
- `scripts/run_tracking_harness.sh`：默认执行器；支持 `--stop-after-prepare` 暂停确认，也支持确认后串联全流程；默认干跑，只有传 `--save` 才落库。执行完成后由 agent 按 `tracking_schema.json` 和实现说明手写代码，不做自动注入。
- `scripts/review_tracking_implementation.py`：手写代码完成后的 review/verify 工具；校验事件覆盖、selector/anchor、危险改动和 fail-open 要求，生成 `implementation_review.json`。

## 默认流程：优先使用 harness

### 1. 准备并暂停确认

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --stop-after-prepare
```

检查 `.workspace/<session>/harness_result.json`：
- `status` 应为 `awaiting_app_business_confirmation`。
- `prepare_context_json` 和 `workspace_html` 必须存在。
- `all_apps_catalog.json` 和 `all_business_lines_catalog.json` 是应用/业务线确认的事实来源。

### 2. 展示推荐并等待确认

先读取并展示：
- `prepare_context.json` 中的推荐 `app_recommendation.recommended` 与 `business_line_recommendation.recommended`。
- catalog 文件中的匹配依据，尤其是用户口述名称对应的真实 `app_id/app_code/business_code`。

若推荐不准确，先本地检索 catalog，再让用户确认。例如用户说“埋点管理平台”，应从 catalog 找到对应记录后使用真实 `app_id` 和 `app_code`。

必须等待用户明确回复后再继续。即使用户回复“按推荐”，也要把最终 `app_id/app_code/business_code` 显式传给 harness；不要只传 `--accept-recommendation`，除非用户已经明确同意使用推荐值。

### 3. 生成 LLM 草案

读取 `workspace_html` 和 `references/llm_tracking_output_template.json`，选择有业务意义的页面展示、点击、切换、提交、选项选择等埋点区域，保存为 `.workspace/<session>/llm_output.json`。

`llm_output.json` 必须包含：
- 顶层：`page_name`、`page_code`、`regions`。
- 每个 region：`data_ai_id`、`action`、`action_id`、`action_fields`。

要求：
- `data_ai_id` 必须来自 `workspace_html`，不要虚构。
- 不要输出 `draft_document` 或 `change_set`；第 4 步会自动生成，所有 region 默认进入 `added_regions`。
- `action_fields` 可以为空数组；有额外字段时只描述字段，不要写死运行时业务值。

### 4. 用 harness 完整执行

默认干跑：

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --app-id "<confirmed_app_id>" \
  --app-code "<confirmed_app_code>" \
  --business-code "<confirmed_business_code>" \
  --llm-output ".workspace/<session>/llm_output.json"
```

需要真实落库时，必须先获得用户明确同意，再额外传 `--save`，必要时传 `--tracking-base-url`、`--cert-path`、`--cert-password`。测试阶段需要 debug 日志时传 `--weblog-debug`；验收后重跑并去掉该参数。

harness 会自动完成确认文件写入、LLM 输出校验、payload/schema/实现说明生成。代码实现由 agent 手写，不使用脚本自动注入。

### 5. 检查结果

读取 `.workspace/<session>/harness_result.json`：
- `ok` 必须为 `true`，`status` 应为 `succeeded`。
- `steps.prepare/confirm/apply` 应为 `succeeded`。
- `artifacts.page_document_save_payload_json`、`tracking_schema_json`、`implementation_guide_md` 必须存在。
- 若落库，检查 `save_api_response_json`；若失败，读 `apply_result.json` 的 `save_api_called`、`save_api_business_success`、`save_api_error`。
- `mode.manual_implementation_required` 应为 `true`，表示后续需要 agent 手写埋点代码。

若 `apply_result.json` 中 `unresolved_count` 不为 `0`，检查 `llm_output.json` 中的 `data_ai_id` 是否真实存在于 `workspace_html`。若 `weblog_app_key_status` 不是已解析状态，向用户确认并用 `--weblog-app-key` 重跑。

### 6. Agent 手写实现

读取 `.workspace/<session>/tracking_schema.json` 与 `.workspace/<session>/openclaw_tracking_implementation.md`，直接在工作副本 HTML 或业务源码里手写埋点代码：
- 严禁再调用自动注入脚本。
- 必须保持原功能不变，不得改写原有业务分支、事件顺序或页面状态机。
- 埋点逻辑必须 fail-open：SDK 不存在、上报异常、字段读取失败时，不能阻断原功能。
- 仅在用户明确要求时，再把同样改法迁移到业务源码；默认只改 `.workspace/<session>/` 工作副本。

### 7. Review / Verify

手写完成后，必须运行 review 工具：

```bash
python3 scripts/review_tracking_implementation.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

结果解释：
- `status=passed`：可以认为埋点代码已完成。
- `status=needs_review`：存在风险项，需要继续调整或人工确认。
- `status=failed`：存在阻断问题，不能交付。

review 工具会检查：
- `tracking_schema.json` 里的 `event_id` 是否已在实现代码中落地。
- `selector_candidates` / `data-ai-id` 锚点在目标 HTML 中是否还存在。
- 是否误用了 `WL.trackEvent`、`__weblog_config` 等错误 API。
- 是否引入了可能改变原行为的高风险改动，如覆盖原生事件处理器、`preventDefault`、`stopPropagation`、大段删除原逻辑。

只有当 review 结果为 `passed`，agent 才能认为“代码修改好了”。

## Fallback：局部重跑

只有在 harness 失败或需要定位单步问题时，才直接调用单个脚本：
- 准备上下文：`python3 scripts/prepare_tracking_context.py ...`
- 确认应用/业务线：`python3 scripts/confirm_app_business.py ...`
- 归一化和保存参数：`python3 scripts/apply_llm_output.py ...`

手写或修正 SDK 代码前必须阅读 `references/weblog_sdk_reference.md`：
- 初始化：`window.weblog.setConfig({ appKey, debug })`
- 上报：`window.weblog.report({ id: eventId, action, logmap })`
- 不要使用 `__weblog_config`、`WL.trackEvent`、`WL.trackPageShow` 等错误 API。

## 命名与格式

- 页面事件 ID：`appCode_businessCode_pageCode`
- 区域事件 ID：`appCode_businessCode_pageCode_sectionCode_elementCode`
- `page_code`、`section_code`、`element_code`、`action_id`、`action_fields.fieldCode` 使用 camelCase，且仅允许字母和数字。
- `page_name`、`section_name`、`element_name`、`action_fields.fieldName` 可用中文；不要包含空格或特殊字符。
- `action` 仅允许：`click/slide/show/hover/stay/dis/pull/dclick/start/press/end`。
- `logmap` 只放额外字段；字段值必须在触发时读取当前 DOM、URL、状态或数据。

## 标准产物

- `.workspace/<session>/<source-name>.html`：注入 `data-ai-id` 的工作副本。
- `.workspace/<session>/prepare_context.json`：准备阶段上下文。
- `.workspace/<session>/all_apps_catalog.json`：全量应用 catalog。
- `.workspace/<session>/all_business_lines_catalog.json`：全量业务线 catalog。
- `.workspace/<session>/app_business_confirm.json`：应用与业务线确认。
- `.workspace/<session>/llm_output.json`：LLM 设计输出。
- `.workspace/<session>/draft_document.json`：归一化草稿文档。
- `.workspace/<session>/change_set.json`：归一化变更集。
- `.workspace/<session>/page_document_save_payload.json`：保存接口请求体。
- `.workspace/<session>/save_api_response.json`：真实落库时的接口响应。
- `.workspace/<session>/tracking_schema.json`：结构化埋点 schema。
- `.workspace/<session>/openclaw_tracking_implementation.md`：代码改写说明。
- `.workspace/<session>/implementation_baseline.html`：手写埋点前的工作副本快照，用于 review diff。
- `.workspace/<session>/implementation_review.json`：手写埋点后的 review/verify 结果。
- `.workspace/<session>/harness_result.json`：harness 执行摘要。

## 最终交付检查

- 用户确认过的 `app_id/app_code/business_code` 已写入 `app_business_confirm.json`。
- `page_document_save_payload.json`、`tracking_schema.json`、`openclaw_tracking_implementation.md` 已生成。
- `tracking_schema.json` 中 `events` 数量合理，`unresolved_regions` 为空或已说明处理方式。
- 手写代码只改工作副本 HTML，原始 HTML 未被修改。
- `implementation_review.json` 已生成，且 `status` 为 `passed`。
- 若调用真实保存接口，已说明保存结果、响应路径和任何错误。
