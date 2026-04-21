# Prepare And Confirm

本阶段目标是产出可确认的推荐值，并在继续前拿到用户明确确认。

## 1. 准备工作副本

```bash
scripts/run_tracking_harness.sh \
  --html "<html_path>" \
  --session-id "<session>" \
  --stop-after-prepare
```

## 2. 检查准备结果

读取 `.workspace/<session>/harness_result.json` 并确认：
- `status=awaiting_app_business_confirmation`
- `artifacts.prepare_context_json` 存在
- `artifacts.workspace_html` 存在

## 3. 展示推荐与映射依据

读取以下文件并展示给用户：
- `.workspace/<session>/prepare_context.json`
- `.workspace/<session>/all_apps_catalog.json`
- `.workspace/<session>/all_business_lines_catalog.json`

必须展示：
- 推荐应用与业务线
- 用户口述名称到真实 `app_id/app_code/business_code` 的映射依据

## 4. 硬 gate

确认前禁止：
- 生成 `llm_output.json`
- 生成保存 payload
- 手写埋点代码
- 调用真实保存接口

## 5. 继续执行的输入要求

继续执行前必须拿到最终值：
- `app_id`
- `app_code`
- `business_code`

不要只依赖 “按推荐” 语义，执行命令时必须显式传入以上三个参数。
