# Review Protocol

手写埋点代码后必须先执行 review。review 通过后，还要继续完成默认的 `runtime_browser_session` 运行时验证，最终以 `validation_gate.json.status=passed` 作为交付门槛。

## 执行命令

```bash
scripts/run_tracking_harness.sh \
  --session-id "<session>" \
  --implementation-done \
  --json
```

若需要单独调试 review，再使用 `python3 scripts/review_tracking_implementation.py ...`。

## 结果判定

- `status=passed`：通过 review gate，可继续 runtime gate。
- `status=needs_review`：存在风险项，需修复或人工确认后复跑。
- `status=failed`：存在阻断问题，不可交付。

## 重点检查项

- `tracking_schema.json` 中的 `event_id` 是否在实现中落地。
- `selector_candidates` 与 `data-ai-id` 锚点是否仍存在。
- 是否误用错误 API（如 `WL.trackEvent`、`__weblog_config`）。
- 是否引入改变原行为的高风险改动（覆盖处理器、阻断默认行为、大段删除原逻辑）。

## 交付门槛

`implementation_review.json.status=passed` 只是第一关；还需要继续完成 `runtime_browser_verification.json`，并让 `validation_gate.json.status=passed`。
