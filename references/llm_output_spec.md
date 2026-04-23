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

可选包含：
- 无

## Region 结构

每个 region 必须包含：
- `data_ai_id`
- `action`
- `action_id`
- `action_fields`

每个 region 可选包含：
- 无

## 字段约束

- `data_ai_id` 必须来自工作副本 HTML，禁止虚构。
- `action` 仅允许：`click/slide/show/hover/stay/dis/pull/dclick/start/press/end`。
- `page_code`、`section_code`、`element_code`、`action_id`、`action_fields.fieldCode` 使用 camelCase，且只含字母和数字；其中 `page_code`、`section_code`、`element_code` 禁止使用动词结尾（如 `click`、`show`、`submit` 等），应使用名词或动名词结尾（如 `button`、`quizView`、`optionClick` 应改为 `option` 或直接用名词 `quizOption`或`quizBtn`）。
- `page_name`、`section_name`、`element_name`、`action_fields.fieldName` 可用中文，不要包含空格或特殊字符。
- `action_fields` 可为空数组；如有字段只描述字段语义，不写死运行时业务值。
- **生成约束（不要生成）**：仅生成有实际业务意义的埋点，**禁止**为以下类型单独生成 `show` 事件：
  - 容器型元素（如 `*Container`、`*View` 等页面区块容器）
  - 纯展示型元素（如 `heroCast`、`questionNumber`、`dimensionPill`、`questionTitle`、`personaName` 等无交互的展示文本）
  - 除非业务方明确要求，否则 `show` 事件仅保留页面级展示和用户可直接感知的关键视图切换（如 `resultView` 结果页展示）
  - 典型应保留的事件：`click`（按钮/选项点击）、`show`（仅限页面级或视图切换）、携带动态业务字段的交互事件

## 阅读资料来源

流程中需要阅读哪些参考资料，不再通过 `llm_output` 字段表达。统一以 `run_tracking_harness.sh` 输出的 `harness_result.json.next_action.required_reads` 为准。

## 生成边界

- `llm_output.json` 只描述业务区域和动作，不直接输出 `draft_document` 或 `change_set`。
- 默认由后续 apply 步骤将 region 映射到 `added_regions`。
