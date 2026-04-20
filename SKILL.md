---
name: tracking-design-llm
name_zh: LLM自动埋点设计
artifact_type: skill
description: 当用户要求对本地 HTML 页面做自动埋点设计时使用。流程包含：准备工作副本并注入 data-ai-id、确认应用与业务线、LLM 按模板输出埋点草案、归一化为 draft_document/change_set、产出保存 payload 与埋点实现指引。
---

# LLM 自动埋点设计

## 使用场景

当用户提出“帮我对某个本地 HTML 页面做埋点设计/生成埋点保存参数/输出埋点代码指引”时使用本技能。

## 必走流程

1. 准备上下文与工作副本

```bash
python3 scripts/prepare_tracking_context.py "<html_path>" \
  --output ".workspace/<session>/prepare_context.json" \
  --json
```

2. 应用与业务线确认
- 先展示推荐结果（来自 `prepare_context.json`），再向用户发起确认。
- `prepare` 阶段会额外产出全量 catalog：`all_apps_catalog.json`、`all_business_lines_catalog.json`，推荐不准确时可先本地检索再确认。
- **必须先读取 catalog 文件，用真实存在的 app_id/app_code 调用脚本。**
  - 用户说"埋点管理平台" → 读 catalog 找到 `app_id=25, app_code=maidian` → 用这些值调用脚本
  - 不能直接把用户说的名称当 app_id 用，那样会绕过 catalog 导致用错
- 必须等待用户明确回复后再继续：`按推荐` 或明确覆盖 `app_id/app_code/business_code`。
- 未收到用户确认前，禁止执行第 3/4/5 步。
- 即使用户回复 `按推荐`，也必须把最终使用的 `app_id/app_code/business_code` 显式传入命令参数，禁止依赖脚本默认回填。
- 将确认结果写入 `app_business_confirm.json`。

```bash
python3 scripts/confirm_app_business.py \
  --prepare-context ".workspace/<session>/prepare_context.json" \
  --app-id <user_confirmed_app_id> \
  --app-code <user_confirmed_app_code> \
  --business-code <user_confirmed_business_code> \
  --output ".workspace/<session>/app_business_confirm.json" \
  --json
```

3. LLM 按模板输出草案 JSON
- 模板文件：`references/llm_tracking_output_template.json`。
- 让 LLM 读取 `workspace_html`（已写入 `data-ai-id`）后，直接产出 `llm_output.json`。
- 输出必须包含：`page_name`、`page_code`、`regions`。
- 每个 region 必须包含：`data_ai_id`、`action`、`action_id`、`action_fields`。
- 不需要输出 `change_set`，由 `apply_llm_output.py` 自动生成，且所有 region 自动归入 `added_regions`。

4. 归一化并产出保存参数

```bash
python3 scripts/apply_llm_output.py \
  --prepare-context ".workspace/<session>/prepare_context.json" \
  --app-business ".workspace/<session>/app_business_confirm.json" \
  --llm-output ".workspace/<session>/llm_output.json" \
  --output ".workspace/<session>/page_document_save_payload.json" \
  --json
```

- 第 4 步默认调用真实保存接口：`POST {tracking_base_url}/tracking/page_document/save`。
- 第 4 步会优先从 `confirm_app_business.py` 确认结果中读取 `app_key`（来源于本地 `all_apps_catalog.json`），并写入 `tracking_schema.json` 与 `openclaw_tracking_implementation.md`。
- 如果只想本地联调、不落库，执行时增加 `--skip-save`。

5. 保存结果校验与开发
- 检查 `apply_llm_output.py` 返回字段：`save_api_called`、`save_api_business_success`、`save_api_error`。
- 代码开发按 `tracking_schema.json` 与 `openclaw_tracking_implementation.md` 执行。
- 测试阶段 `debug=true`，用户确认通过后改为 `debug=false`。

5.5 自动生成埋点代码
- 优先使用 `python3 scripts/generate_tracking_code.py --workspace-dir ".workspace/<session>" --json` 自动生成含埋点的工作副本 HTML。
- 若自动脚本不满足需求，**必须先阅读** `references/weblog_sdk_reference.md` 了解 SDK 正确使用方式，再手动编写埋点代码。
- SDK 初始化方式：`window.weblog.setConfig({ appKey, debug })`
- 埋点上报方式：`window.weblog.report({ id: eventId, action: 'click', logmap: {} })`
- **不要使用** `__weblog_config`、`WL.trackEvent`、`WL.trackPageShow` 等错误 API。

## 命名规范

- `appCode_businessLine_pageCode`
- `appCode_businessLine_pageCode_sectionCode_elementCode`
- `page_code`、`section_code`、`element_code`、`action_fields.fieldCode` 必须是 `camelCase`
- `page_code`、`section_code`、`element_code`、`action_fields.fieldCode` 仅允许字母和数字（不允许空格、下划线、短横线、中文及其他特殊字符）
- `page_name`、`section_name`、`element_name`、`action_fields.fieldName` 可使用中文名称；不允许空格和特殊字符
- `action_id` 建议 `camelCase`，且仅允许字母和数字
- `action` 仅允许：`click/slide/show/hover/stay/dis/pull/dclick/start/press/end`
- `logmap` 只放额外字段，字段值必须触发时实时读取

## 标准产物

- `.workspace/<session>/<source-name>.html`：注入 data-ai-id 的工作副本
- `.workspace/<session>/prepare_context.json`：准备阶段上下文
- `.workspace/<session>/all_apps_catalog.json`：全量应用列表（用于本地筛选 app_id/app_code）
- `.workspace/<session>/all_business_lines_catalog.json`：全量业务线列表（用于本地筛选 business_code）
- `.workspace/<session>/app_business_confirm.json`：应用与业务线确认信息
- `.workspace/<session>/llm_output.json`：LLM 设计输出
- `.workspace/<session>/draft_document.json`：归一化草稿文档
- `.workspace/<session>/change_set.json`：归一化变更集
- `.workspace/<session>/page_document_save_payload.json`：保存接口请求体
- `.workspace/<session>/save_api_response.json`：保存接口响应
- `.workspace/<session>/tracking_schema.json`：结构化埋点 schema
- `.workspace/<session>/openclaw_tracking_implementation.md`：代码改写说明

## 约束与边界

- 原始 HTML 只读，不直接改；改动只发生在 `.workspace/` 工作副本
- 优先使用 `[data-ai-id="..."]` 作为锚点与 selector
- 推荐接口失败时，必须允许用户手动指定应用与业务线
- 未经用户明确确认，不能直接采用推荐的 `app_id/app_code/business_code`
