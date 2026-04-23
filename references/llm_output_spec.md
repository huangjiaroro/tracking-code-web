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
- `page_runtime_hints`

## Region 结构

每个 region 必须包含：
- `data_ai_id`
- `action`
- `action_id`
- `action_fields`

每个 region 可选包含：
- `runtime_hints`

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

## runtime_hints（可选）

默认手动 `runtime_browser_session` 调试可以直接按 `state -> act -> assert` 推进，不要求先写完整 `runtime_hints`。但当你已经知道某个事件的典型前置路径、视图切换或等待条件时，仍然建议把这些信息写进 `runtime_hints`，方便后续源码预定位和定向补测。

支持字段：
- `case_id`
- `description`
- `pre_steps`
- `post_steps`
- `trigger`
- `settle_ms`
- `timeout_ms`
- `ordered`
- `expected_report`
- `expected_reports`
- `unexpected_reports`

### 什么时候建议让 agent 补 `runtime_hints`

出现以下任一情况时，直接补 `runtime_hints` 会更稳：

- 事件元素不是初始 DOM 中可直接点击的节点，而是点击入口后动态渲染出来
- 事件需要先切换视图、先完成一段答题流程、或等待结果页出现
- 事件是非页面级 `show`，且展示时机依赖用户动作或异步状态
- 同一次用户动作会派生多个埋点，需要指定同一个 `trigger` 并断言多条 report
- `logmap` 包含运行时字段，需要通过 `$from_dom` / `$from_eval` / `$regex` / `$non_empty` 断言

### 约束

- `runtime_hints.trigger.type` 与 `pre_steps/post_steps[].type` 仅允许：
  - `click`
  - `wait_selector`
  - `wait_function`
  - `evaluate`
  - `sleep`
  - `load`（仅 trigger）
- 不要输出 verifier 不支持的触发器类型，例如 `css_animation`、`view_show`
- 对非页面级 `show` 事件，不要把 “show 本身” 当 trigger；应使用“导致其出现的最后一个用户动作”作为 `trigger`，再用 `post_steps` 等待最终视图稳定

典型场景：
- 非初始可见的 `show` 事件，需要先做一段 `pre_steps` 再断言
- 主触发动作完成后，还要继续等待结果页或异步内容出现，可放到 `post_steps`
- 动态生成的 `click` 事件，需要显式给 `trigger.selector`
- 动态 `logmap` 需要用 `$from_dom` / `$regex` / `$non_empty` 做断言

示例：

```json
{
  "data_ai_id": "ai-33",
  "section_name": "答题页",
  "section_code": "quizView",
  "element_name": "题目选项",
  "element_code": "quizOptionItem",
  "action": "click",
  "action_id": "quizOptionClick",
  "action_fields": [
    {
      "fieldName": "题目标题",
      "fieldCode": "questionTitle",
      "dataType": "string",
      "action": "click",
      "remark": "触发时读取当前题目标题文本"
    }
  ],
  "runtime_hints": {
    "pre_steps": [
      { "type": "click", "selector": "[data-ai-id=\"ai-14\"]" },
      { "type": "wait_selector", "selector": "#quizView.active", "state": "visible", "timeout_ms": 3000 }
    ],
    "trigger": {
      "type": "click",
      "selector": "#options .option",
      "nth": 0
    },
    "expected_report": {
      "logmap": {
        "questionTitle": {
          "$from_dom": { "selector": "#questionTitle", "kind": "text", "when": "before_trigger" }
        }
      }
    }
  }
}
```

## 生成边界

- `llm_output.json` 只描述业务区域和动作，不直接输出 `draft_document` 或 `change_set`。
- 默认由后续 apply 步骤将 region 映射到 `added_regions`。
