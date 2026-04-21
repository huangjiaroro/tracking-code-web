# LLM Output Spec

本规范定义 `.workspace/<session>/llm_output.json` 的最小要求。

## 输入来源

- 工作副本 HTML：`.workspace/<session>/<source-name>.html`
- 模板：`templates/llm_tracking_output_template.json`

## 顶层结构

`llm_output.json` 必须包含：
- `page_name`
- `page_code`
- `regions`

## Region 结构

每个 region 必须包含：
- `data_ai_id`
- `action`
- `action_id`
- `action_fields`

## 字段约束

- `data_ai_id` 必须来自工作副本 HTML，禁止虚构。
- `action` 仅允许：`click/slide/show/hover/stay/dis/pull/dclick/start/press/end`。
- `page_code`、`section_code`、`element_code`、`action_id`、`action_fields.fieldCode` 使用 camelCase，且只含字母和数字。
- `page_name`、`section_name`、`element_name`、`action_fields.fieldName` 可用中文，不要包含空格或特殊字符。
- `action_fields` 可为空数组；如有字段只描述字段语义，不写死运行时业务值。

## 生成边界

- `llm_output.json` 只描述业务区域和动作，不直接输出 `draft_document` 或 `change_set`。
- 默认由后续 apply 步骤将 region 映射到 `added_regions`。
