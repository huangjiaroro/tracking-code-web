# Manual Implementation Rules

本阶段在 `.workspace/<session>/` 工作副本中手写埋点实现，不允许自动注入。

## 输入材料

- `.workspace/<session>/tracking_schema.json`
- `.workspace/<session>/openclaw_tracking_implementation.md`
- `.workspace/<session>/runtime_browser_sessions/`（用于默认运行时验证）
- `references/validation_loop.md`
- `references/weblog_sdk_reference.md`

## 实现原则

- 只追加埋点逻辑，不重写原业务逻辑。
- 不改变原事件顺序、状态机、导航、接口调用和 DOM 结构。
- 仅在原逻辑完成后追加上报，必要时加最小守卫。
- 除非原代码已使用，否则不新增 `preventDefault`、`stopPropagation`、`return false`、直接覆盖原生处理器等高风险操作。

## Fail-open 要求

以下场景都不能阻断原功能：
- `window.weblog` 不存在
- SDK 初始化失败
- 字段读取失败
- 上报异常

## SDK 使用要求

使用 `references/weblog_sdk_reference.md` 中定义的接口：
- 初始化：`window.weblog.setConfig({ appKey, debug })`
- 上报：`window.weblog.report({ id, action, logmap })`

禁止使用：
- `__weblog_config`
- `WL.trackEvent`
- `WL.trackPageShow`
