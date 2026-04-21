# Review Protocol

手写埋点代码后必须执行 review，结果通过才可交付。

## 执行命令

```bash
python3 scripts/review_tracking_implementation.py \
  --workspace-dir ".workspace/<session>" \
  --json
```

## 结果判定

- `status=passed`：通过，可交付。
- `status=needs_review`：存在风险项，需修复或人工确认后复跑。
- `status=failed`：存在阻断问题，不可交付。

## 重点检查项

- `tracking_schema.json` 中的 `event_id` 是否在实现中落地。
- `selector_candidates` 与 `data-ai-id` 锚点是否仍存在。
- 是否误用错误 API（如 `WL.trackEvent`、`__weblog_config`）。
- 是否引入改变原行为的高风险改动（覆盖处理器、阻断默认行为、大段删除原逻辑）。

## 交付门槛

只有 `implementation_review.json` 生成且 `status=passed`，才能声明“实现完成”。
