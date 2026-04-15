---
name: tracking-design-chrome-launcher
name_zh: 埋点设计 Chrome 启动器
artifact_type: skill
description: 当用户说"帮我设计某个 URL 的埋点"、"设计某个页面的埋点"或要求对本地 HTML 文件做埋点设计，并且希望 OpenClaw 自动打开 Google Chrome、确保当前 tracking-design 插件可用并打开目标页面时使用。
---

# 埋点设计 Chrome 启动器

当用户希望在某个特定页面或本地 HTML 文件上启动当前 Chrome 扩展并开展埋点设计工作时，使用此技能。

## 自然语言触发条件

这个技能不仅应在用户明确提出启动器请求时触发，也应在用户提出"埋点设计"请求且已经包含 URL 时触发，例如：

- `帮我设计 https://example.com 的埋点`
- `帮我看一下 https://example.com 这个页面怎么做埋点`
- `打开 https://example.com 并启动埋点插件`

当用户意图是埋点设计且输入中包含 URL 或本地 HTML 文件路径时，第一步应先完成浏览器启动准备：

1. 启动 Chrome
2. 如果该技能专用的 Chrome 配置文件中尚未安装扩展，则安装扩展到这个配置文件
3. 如果 Chrome 将这个已解压扩展显示为已禁用，则优先让启动器通过 `chrome://extensions` 自动恢复开发者模式；如果仍失败，则在最终可见的 Chrome 会话中打开 `chrome://extensions`
4. 打开目标 URL；如果输入是本地 HTML 文件，启动器会先开一个只绑定 `127.0.0.1` 的临时本地服务，再用 HTTP URL 打开该文件

只有完成这些步骤后，才继续更广义的埋点工作流。

启动器会为本次会话启动一个本地网关。插件中的 page document、project、asset、act、project data 等 HTTP 请求会先打到本地网关，再由 Python 进程按指定环境和证书访问真实服务。默认技能工作流使用后台模式：脚本在 Chrome 和本地网关准备好后立即返回，后台 Python 进程继续等待插件保存埋点结果，并把进度持续写入会话状态文件和服务日志。

## 输入

- 一个 `http://` 或 `https://` URL
- 或一个本地 HTML 文件路径，例如 `/tmp/openclaw/page/index.html`

## 扩展源码目录结构

如果没有传入 `--extension-dir`，启动器会从附近目录自动发现扩展源码。

OpenClaw 中推荐的目录结构如下：

```text
<current-skill-dir>/
├── SKILL.md
├── scripts/
└── chrome-extension/
    ├── manifest.json
    ├── content.js
    ├── background.js
    ├── popup/
    └── icons/
```

这意味着你可以把整个本地扩展仓库内容放到 skill 目录下的 `chrome-extension/` 文件夹中，脚本会自动识别并使用它。

## 默认工作流

1. 运行内置脚本 `scripts/launch_tracking_extension.py`，传入目标 URL 或本地 HTML 文件路径，并带上 `--json`。脚本默认使用后台非阻塞模式，不需要再传 `--background`。
   - 默认本地网关端口是 `8989`。
   - 启动器会创建 `.workspace/<session>/session_status.json` 和 `.workspace/<session>/service.log`，父进程只等到 Chrome 和本地网关准备好后返回，不再阻塞到用户点击“确认保存”。
   - 默认埋点服务环境是 `dev`，即 `http://localhost:9854`。
   - 可用 `--tracking-env dev|test|prod|dreamface|ainvest` 切换环境。
   - 可用 `--tracking-base-url` 覆盖埋点服务地址。
   - 可用 `--cert-path` 和 `--cert-password` 指定 P12 证书。上游是 HTTPS 时，本地网关会用该证书发请求；未指定证书时会使用不校验证书链的 HTTPS context。
   - 可用 `--agent-api-base-url` 覆盖 Agent API 地址。
   - 本地 HTML 模式会先把原始 HTML 复制到 `.workspace/<session>/` 做沙箱隔离，并默认给工作副本中的页面元素写入 `data-ai-id`，方便插件和后续代码改写使用稳定选择器。
   - 默认不直接改写 HTML 注入运行时代码，而是走 fallback：生成 `openclaw_tracking_implementation.md`，交给 OpenClaw 按项目源码和代码规范改写。
   - fallback 文档会声明 `代码注入状态：false`，列出代码规范参考、哪些控件在什么时机上报什么埋点、固定 `logmap`、额外属性以及取值说明。
   - 可用 `--tracking-code-reference` 或环境变量 `OPENCLAW_TRACKING_CODE_REFERENCE` 指定代码规范参考文档，默认是 `references/weblog_sdk_reference.md`。
   - 可用 `--enable-html-injection` 或环境变量 `OPENCLAW_ENABLE_HTML_INJECTION=true` 临时恢复直接 HTML 注入。注入逻辑会按 weblog SDK 规范写入 `<script src="https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js"></script>`，再调用 `window.weblog.setConfig(...)` 和 `window.weblog.report({ id, action, logmap })`。
   - 可用 `--weblog-app-key` 或环境变量 `OPENCLAW_WEBLOG_APP_KEY` 指定 SDK 必需的 `appKey`。
   - 可用 `--weblog-debug` 或环境变量 `OPENCLAW_WEBLOG_DEBUG=true` 开启 SDK 调试输出。
   - 可用 `--weblog-cdn` 或环境变量 `OPENCLAW_WEBLOG_CDN` 覆盖 weblog SDK CDN，默认是 `https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js`。
   - 可用 `--weblog-log-prefix` 或环境变量 `OPENCLAW_WEBLOG_LOG_PREFIX` 指定 SDK 的 `logPrefix`，用于二段式埋点 id 自动拼接。
   - weblog 上报 `domain` 会按 `--tracking-env` 推导：`prod` 不传 domain，`ainvest` 使用 `stat.ainvest.com`，`dreamface` 使用 `track.aidreamface.com`，`dev/test` 使用 `10.217.136.10:8080`。如需临时覆盖，可设置环境变量 `OPENCLAW_WEBLOG_DOMAIN`。
2. 首次返回 `ok: true` 且 `status: waiting_for_save` 时，视为浏览器和本地网关已经准备好。此时不要结束埋点工作流，继续让用户在 Chrome 插件中完成埋点设计，并按以下字段读取本次会话：
   - `background`：是否使用后台服务模式，默认技能工作流应为 `true`
   - `launcher_pid`：后台 Python 进程 pid
   - `background_ready`：父进程返回时后台服务是否已到达可设计状态
   - `status`：当前会话状态。常见值为 `starting`、`waiting_for_save`、`saved`、`error`、`timeout`
   - `session_status_file`：结构化状态文件路径，OpenClaw 应轮询读取这个 JSON 文件
   - `service_log`：JSONL 服务日志路径，OpenClaw 可用于查看网关启动、保存、失败等事件
   - `existing_install_detected`：该技能使用的 Chrome 配置文件中是否已存在该扩展
   - `installed_now`：本次运行中启动器是否将已解压扩展安装到了该配置文件
   - `availability_mode`：当前会话通过什么方式保证扩展可用。预期值是 `persistent_unpacked_profile_install`
   - `extension_dir`：自动发现后最终选中的扩展源码目录
   - `profile_dir`：本次启动 Chrome 所使用的 `user-data-dir`
   - `developer_mode_initially_enabled`：在启动器尝试修复前，这个专用 Chrome 配置文件是否已经开启开发者模式。如果 Chrome 还没有写入明确偏好值，这个字段可能为 `null`
   - `developer_mode_enabled`：启动器完成自动恢复后，开发者模式最终是否已开启
   - `developer_mode_auto_toggle_attempted`：启动器是否曾尝试通过 Chrome DevTools 操作 `chrome://extensions` 自动开启开发者模式
   - `developer_mode_toggled_now`：本次运行中启动器是否真的把开发者模式切换为开启
   - `developer_mode_needed`：启动器返回后，是否仍然需要手动开启开发者模式
   - `opened_extensions_page`：如果自动开启没有成功，启动器是否在最终可见的 Chrome 会话中退回到打开 `chrome://extensions/`
   - `launch_urls`：最终 Chrome 会话实际打开了哪些页面
   - `next_action`：启动器建议的下一步动作。如果开发者模式仍未恢复，这里会明确提示先在浏览器中完成手动修复
   - `local_file_mode`：是否进入本地 HTML 文件模式
   - `local_gateway_mode`：是否启用了本地网关。当前技能运行时预期为 `true`
   - `ai_data_id_injected`：本地 HTML 工作副本是否已经写入 `data-ai-id`
   - `ai_data_id_attribute`：稳定 ID 使用的属性名，当前为 `data-ai-id`
   - `ai_data_id_count`：本次复制到工作目录时写入的 `data-ai-id` 数量
   - `tracking_env`：本地网关使用的埋点服务环境
   - `tracking_base_url`：本地网关实际代理到的埋点服务地址
   - `uses_client_cert`：本地网关访问 HTTPS 上游时是否配置了 P12 客户端证书
   - `html_injection_enabled`：是否启用直接 HTML 注入。默认是 `false`
   - `tracking_code_reference`：fallback 文档中引用的代码规范文档
   - `code_injection_performed`：本次是否真的执行了代码注入。默认 fallback 下为 `false`
   - `implementation_guide`：默认 fallback 模式保存成功后生成的 OpenClaw 改写说明路径
   - `weblog_cdn`：本地 HTML 注入使用的 weblog SDK 地址
   - `weblog_app_key_configured`：是否已为注入代码配置 weblog `appKey`
   - `weblog_domain`：注入 `setConfig` 时使用的 weblog 上报 domain；如果是国内正式环境则通常为 `null`
   - `weblog_log_prefix`：注入 `setConfig` 时使用的 weblog `logPrefix`
   - `modified_html`：本地 HTML 模式保存成功后生成的改写后 HTML 绝对路径
   - `tracking_schema`：本地 HTML 模式保存成功后生成的埋点结构化清单路径
3. 当首次返回 `status: waiting_for_save` 后，OpenClaw 应轮询读取 `session_status_file`，不要重新启动 launcher。建议每 3-5 秒读取一次 JSON：
   - `status: waiting_for_save`：继续等待用户在插件中点击“确认保存”
   - `status: saved`：设计已保存。读取 `implementation_guide` 和 `tracking_schema`，然后按照 `openclaw_tracking_implementation.md` 指引改写业务源码；如果显式开启了 `--enable-html-injection`，则改用 `modified_html`
   - `status: error` 或 `status: timeout`：停止等待，汇报 `error`，并参考 `service_log`


## 约束

- 优先使用内置脚本，而不是手动在 `chrome://extensions/` 中逐步点击
- 不要复用用户平时使用的普通 Chrome 会话。该脚本会有意使用位于 skill 目录 `.openclaw/chrome-profile` 下的专用配置文件
- 如果这个配置文件里还没有扩展，启动器会先把扩展引导安装到同一个配置文件，然后再用这个配置文件正常重新启动 Chrome
- 目标是让扩展真正出现在这个由 skill 管理的配置文件的 `chrome://extensions` 中，而不是只在某一次会话里临时加载
- 本地网关使用随机 token 保护代理接口，插件会从打开页面 URL 中读取 token 和 gateway 地址并回传；默认后台模式下，等待用户点击插件“确认保存”的动作发生在后台 Python 进程中，OpenClaw 通过轮询 `session_status_file` 或查看 `service_log` 获知结果
- 本地 HTML 模式不会直接改写上游传入的原始 HTML，而是在 skill 的 `.workspace/<session>/` 中生成副本。这个工作副本默认会写入 `data-ai-id`，该行为与 `--enable-html-injection` 无关；`--enable-html-injection` 只控制是否额外生成 weblog 上报 runtime。
- 本地 HTML 模式保存后会在 `.workspace/<session>/` 生成 `tracking_schema.json` 和默认 fallback 的 `openclaw_tracking_implementation.md`。只有显式开启 `--enable-html-injection` 时才会额外生成 `*_with_tracking.html`
- 当输入是远程 URL 时，启动器会在目标 URL 上附加 `openclaw_tracking_token` 和 `openclaw_tracking_gateway` 查询参数，让插件识别本地网关。page identity 转发给后端前会去掉这些控制参数
- 不要因为首次返回时尚未出现 `implementation_guide` 就重新运行脚本。首次返回代表后台服务已在等待保存；必须继续轮询同一个 `session_status_file`


## 输出要求

需要汇报：

- 目标 URL
- 是否检测到已有安装
- 本次运行是否安装了扩展
- 当前会话使用的是 `persistent_unpacked_profile_install`
- 解析得到的 `extension_dir`
- 正在使用的配置文件目录
- 这个专用配置文件中的 `developer_mode_initially_enabled` 是否一开始就是 true
- 启动器恢复后 `developer_mode_enabled` 是否最终为 true
- 如果 `opened_extensions_page` 为 true，需要同时汇报 `next_action`
- 首次后台返回时，需要汇报 `status`、`session_status_file`、`service_log` 和 `launcher_pid`
- 轮询到 `status: saved` 后，如果是本地 HTML fallback 模式，需要汇报 `implementation_guide` 和 `tracking_schema`，并继续按 `implementation_guide` 改写业务源码
- 如果显式开启直接 HTML 注入并轮询到 `status: saved`，需要汇报 `modified_html` 和 `tracking_schema`
