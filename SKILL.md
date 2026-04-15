---
name: tracking-design-chrome-launcher
name_zh: 埋点设计 Chrome 启动器
artifact_type: skill
description: 当用户要求为 URL 或本地 HTML 页面设计埋点、生成埋点方案或实现埋点代码时使用。此技能负责环境预检、启动 Chrome 与 OpenClaw tracking-design 扩展、打开目标页面，并在用户确认插件已保存后读取 tracking_schema 和 implementation_guide，再按用户下一步指令改写埋点代码。
---

# 埋点设计 Chrome 启动器

当用户希望为某个远程页面或本地 HTML 文件开展埋点设计、读取插件保存的设计结果，并继续完成埋点代码实现时，使用此技能。

## 自然语言触发条件

当用户提出埋点设计、埋点方案生成或埋点代码实现请求，并且输入中包含 URL 或本地 HTML 文件路径时，触发此技能。即使用户没有明确要求打开 Chrome，只要任务需要在目标页面上使用 tracking-design 插件完成设计，也应先使用本技能，例如：

- `帮我设计 https://example.com 的埋点`
- `帮我看一下 https://example.com 这个页面怎么做埋点`
- `打开 https://example.com 并启动埋点插件`
- `给 /tmp/openclaw/page/index.html 加埋点`

当用户意图是埋点设计且输入中包含 URL 或本地 HTML 文件路径时，执行任何页面分析、浏览器启动或埋点设计前，必须先运行环境预检：

```bash
python3 scripts/preflight_check.py --install-deps --json
```

只有预检返回 `ok: true` 后，才进入浏览器启动准备：

1. 启动 Chrome
2. 如果该技能专用的 Chrome 配置文件中尚未安装扩展，则安装扩展到这个配置文件
3. 如果 Chrome 将这个已解压扩展显示为已禁用，则优先让启动器通过 `chrome://extensions` 自动恢复开发者模式；如果仍失败，则在最终可见的 Chrome 会话中打开 `chrome://extensions`
4. 打开目标 URL；如果输入是本地 HTML 文件，启动器会先开一个只绑定 `127.0.0.1` 的临时本地服务，再用 HTTP URL 打开该文件

如果预检返回 `ok: false`，应先汇报 `errors` 并停止启动或设计流程；只有完成预检和浏览器启动准备后，才继续更广义的埋点工作流。

启动器会为本次会话启动一个本地网关。插件中的 page document、project、asset、act、project data 等 HTTP 请求会先打到本地网关，再由 Python 进程按指定环境和证书访问真实服务。默认技能工作流使用后台模式：脚本在 Chrome 和本地网关准备好后立即返回，后台 Python 进程负责接收插件保存结果，并把进度写入会话状态文件和服务日志。

## 输入

- 一个 `http://` 或 `https://` URL
- 或一个本地 HTML 文件路径，例如 `/tmp/openclaw/page/index.html`

## 标准执行流程

使用这个技能时按阶段执行：

1. 先运行 `scripts/preflight_check.py` 检测依赖环境。预检失败时汇报 `errors` 并停止，不启动浏览器、不进入设计。
2. 预检成功后运行 `scripts/launch_tracking_extension.py` 启动本地服务并打开 Chrome。脚本返回可设计状态后，输出本阶段总结，提示用户：`浏览器已打开，可以在浏览器中使用插件进行埋点设计；设计完成并在插件中确认保存后告诉我。` 然后结束本轮，等待用户回复，不要重新启动脚本。
3. 用户回复设计完成后，只读取一次本次会话的 `session_status_file`。如果状态是 `saved`，读取设计结果并输出埋点总揽，说明需要设计哪些埋点，然后结束本轮，等待用户确认是否开始编码；如果状态仍是 `waiting_for_save`，提示用户插件侧尚未保存成功并结束本轮，等待用户保存后再次输入。
4. 用户确认开始编码后，先检查 weblog `appKey`。如果无法从 `tracking_schema`、`implementation_guide`、已有业务代码或配置文件中确认真实 `appKey`，或只能看到 `YOUR_APP_KEY_HERE`、`xxxx`、`待配置`、空值这类占位值，必须先询问用户提供 `appKey`，输出阶段总结后停止当前轮次；拿到用户提供的真实 `appKey` 后，才继续编写埋点代码。本地 HTML 模式下，编码只能修改 `.workspace/<session>/` 中的 `workspace_html` 工作副本，不能修改用户传入的原始 HTML；这个工作副本已经写入 `data-ai-id`。测试前的代码版本必须把埋点 debug 打开为 `true`。代码改完后，告诉用户代码已经修改完成，并直接用本地文件路径在浏览器中打开写入埋点后的工作副本 HTML，提示用户进行测试；不要再通过 `127.0.0.1` 临时本地服务打开改写后的 HTML。如果用户反馈不正确，按用户输入继续修改代码并重新直接打开本地 HTML 文件验证。用户明确说明测试完成或测试通过后，再把埋点 debug 改为 `false`。

## 阶段检查点

每个阶段只做一次状态读取或结果读取。输出阶段总结后停止当前轮次，等待用户输入进入下一步。

| 阶段 | 观察来源 | 成功信号 | 异常信号 | 用户可见输出 | 停顿条件 |
| --- | --- | --- | --- | --- | --- |
| 环境预检 | `scripts/preflight_check.py --install-deps --json` 的 JSON 输出 | `ok: true` | `ok: false` 或存在 `errors` | 预检是否通过；失败时列出 `errors` | 失败时停止；成功后进入启动阶段 |
| 启动 Chrome 和网关 | `scripts/launch_tracking_extension.py "<target>" --json` 的首次返回 | `ok: true` 且 `status: waiting_for_save` | `ok: false`、`status: error`、`status: timeout`、`status: starting` | 目标 URL、`status`、`session_status_file`、`service_log`、`launcher_pid`、`extension_dir`、`profile_dir`、`next_action` | 输出启动总结后停止，等待用户完成插件设计 |
| 用户确认设计完成 | 同一个 `session_status_file`，只读取一次 | `status: saved` | `status: waiting_for_save`、`status: error`、`status: timeout` | 当前 `status`；失败或未保存时给出 `service_log` 和下一步动作 | 未保存或异常时停止；保存成功后进入设计结果总揽 |
| 设计结果总揽 | `implementation_guide` 和 `tracking_schema` | 能读取埋点清单和实现指引 | 文件缺失、JSON 无法读取、事件为空且无说明 | 需要实现的埋点总揽；本地 HTML 模式汇报 `workspace_html`；直接注入模式汇报 `modified_html` | 输出总揽后停止，等待用户确认开始编码 |
| 编码前配置检查 | `tracking_schema`、`implementation_guide`、已有业务代码或配置文件 | 能确认真实 weblog `appKey` | `appKey` 缺失、为空，或为 `YOUR_APP_KEY_HERE`、`xxxx`、`待配置` 等占位值 | 当前 `appKey` 确认状态；缺失时明确向用户询问 `appKey` | 缺失时停止，等待用户提供真实 `appKey` |
| 编码和测试打开 | 上一阶段的设计结果、目标源码或 `workspace_html` | 代码已改完，测试版本 debug 为 `true`，改写后的本地 HTML 文件已直接打开 | 无法定位代码位置、无法打开测试页面、实现指引和源码不匹配 | 修改范围、debug 状态、测试用本地 HTML 文件路径 | 输出编码总结后停止，等待用户测试反馈 |
| 测试通过收尾 | 用户明确说明测试完成或测试通过 | debug 已改为 `false` | 用户反馈仍有问题 | debug 关闭状态和最终交付说明 | 问题未通过时回到编码和测试打开阶段 |

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

1. 必须先运行环境检查脚本，且只在返回 `ok: true` 后继续：

   ```bash
   python3 scripts/preflight_check.py --install-deps --json
   ```

   这个脚本会检查并补齐 skill 根目录下的 `config.json`，并检查运行依赖是否已安装。
   - 如果当前 skill 目录没有 `config.json`，脚本会尝试读取共享配置 `$HOME/.skillhub-cli/config.json`。
   - 如果必要配置仍缺失，安装脚本执行结果询问用户信息并保存到本 skill 的 `config.json`。
   - `--install-deps` 会在依赖缺失时使用 `requirements.txt` 安装依赖；如果不希望自动安装，可去掉该参数，只做检查。

2. 运行内置脚本 `scripts/launch_tracking_extension.py`，传入目标 URL 或本地 HTML 文件路径，并带上 `--json`。脚本默认使用后台非阻塞模式，不需要再传 `--background`。
   - 最小命令格式如下，把 `<target>` 替换为用户给出的 URL 或本地 HTML 文件路径：

     ```bash
     python3 scripts/launch_tracking_extension.py "<target>" --json
     ```

   - 这个命令会先创建本次会话的 `.workspace/<session>/session_status.json`，再启动后台 Python 进程和 Chrome。命令结束时 stdout 最后输出的 JSON 对象就是“首次返回”；从这个 JSON 中读取 `ok`、`status`、`session_status_file`、`service_log`、`launcher_pid` 等字段。
   - 如果首次返回里 `ok: false`，或 `status` 是 `error` / `timeout`，立即汇报 `error` 并停止。若 `status: starting`，输出当前 `session_status_file`、`service_log`、已知 `status` 和 `next_action` 后结束本轮，等待用户下一步指令；不要重新运行 launcher。
   - 启动器会自动读取本 skill 的 `config.json`，不需要在技能说明中重复展开配置型参数。
   - 启动器会创建 `.workspace/<session>/session_status.json` 和 `.workspace/<session>/service.log`，父进程只等到 Chrome 和本地网关准备好后返回。
   - 本地 HTML 模式会把原始 HTML 复制到 `.workspace/<session>/` 做沙箱隔离，并给工作副本写入稳定选择器，方便后续代码改写。
   - 默认不直接注入运行时代码，而是在保存设计后生成 `openclaw_tracking_implementation.md` 和 `tracking_schema.json`，再由 OpenClaw 按指引改写 `.workspace/<session>/` 中的 `workspace_html` 工作副本。
   - 只有明确需要直接 HTML 注入时，才使用 `--enable-html-injection`。
3. 首次返回 `ok: true` 且 `status: waiting_for_save` 时，视为浏览器和本地网关已经准备好。此时不要读取或改写埋点代码，只输出本阶段总结并提示用户在 Chrome 插件中完成埋点设计：
   - `status`：当前会话状态
   - `session_status_file`：用户说明设计完成后读取一次的状态文件
   - `service_log`：排查启动、保存、失败等问题的日志文件
   - `launcher_pid`：后台 Python 进程 pid
   - `launch_urls`：最终 Chrome 会话打开的页面
   - `extension_dir`：自动发现后使用的扩展源码目录
   - `profile_dir`：本次启动 Chrome 使用的专用配置文件目录
   - `local_file_mode`：是否进入本地 HTML 文件模式
   - `next_action`：如果仍需用户手动处理扩展或开发者模式，按这里提示用户
4. 用户明确回复设计完成后，读取同一个 `session_status_file` 一次，不要重新启动 launcher：
   - `status: waiting_for_save`：说明插件侧尚未保存成功，提示用户回到插件中点击“确认保存”，输出本阶段总结后结束本轮
   - `status: saved`：设计已保存。读取 `implementation_guide` 和 `tracking_schema` 后，输出埋点总揽，说明需要实现哪些埋点，并明确等待用户确认开始编码。本地 HTML fallback 模式后续只能改写状态文件中的 `workspace_html` 工作副本，不要改写 `source_html` 原始文件；如果显式开启了 `--enable-html-injection`，则改用 `modified_html`
   - `status: error` 或 `status: timeout`：汇报 `error`，参考 `service_log`，输出本阶段总结后结束本轮
5. 只有用户明确要求开始编码后，才按上一步读取到的设计结果改写代码。埋点代码改写完成后，重新打开写入埋点后的 HTML 或页面供用户测试：
   - 打开页面测试前，必须把埋点代码中的 debug 配置设为 `true`，便于用户检查和排查
   - 本地 HTML fallback 模式下，按 `implementation_guide` 只改写 `workspace_html` 工作副本后，直接用本地文件路径打开这个工作副本 HTML 页面
   - 显式开启直接 HTML 注入时，直接用本地文件路径打开 `modified_html`
   - 编码完成后的测试打开不要使用启动器或 `127.0.0.1` 临时本地服务；只有首次进行插件设计时，本地 HTML 才由启动器通过临时服务打开
   - 打开页面前先告诉用户代码已经修改完成；打开后提示用户进行测试，如有不正确可以直接输入修改内容；如果用户反馈不正确，依据用户描述继续修改代码，并再次直接打开本地 HTML 文件验证
6. 用户明确说明测试完成或测试通过后，必须把埋点代码中的 debug 配置改为 `false`，并告知用户 debug 已关闭。


## 约束

- 优先使用内置脚本，而不是手动在 `chrome://extensions/` 中逐步点击
- 不要复用用户平时使用的普通 Chrome 会话。该脚本会有意使用位于 skill 目录 `.openclaw/chrome-profile` 下的专用配置文件
- 如果这个配置文件里还没有扩展，启动器会先把扩展引导安装到同一个配置文件，然后再用这个配置文件正常重新启动 Chrome
- 目标是让扩展真正出现在这个由 skill 管理的配置文件的 `chrome://extensions` 中，而不是只在某一次会话里临时加载
- 本地网关使用随机 token 保护代理接口，插件会从打开页面 URL 中读取 token 和 gateway 地址并回传；默认后台模式下，插件保存动作由后台 Python 进程接收，OpenClaw 只在用户确认进入下一步时读取一次 `session_status_file` 或查看 `service_log`
- 本地 HTML 模式不会直接改写上游传入的原始 HTML，而是在 skill 的 `.workspace/<session>/` 中生成副本。这个工作副本默认会写入 `data-ai-id`，该行为与 `--enable-html-injection` 无关；`--enable-html-injection` 只控制是否额外生成 weblog 上报 runtime。后续 fallback 编码必须修改 `workspace_html` 工作副本，禁止修改 `source_html` 指向的原始 HTML。
- 本地 HTML 模式保存后会在 `.workspace/<session>/` 生成 `tracking_schema.json` 和默认 fallback 的 `openclaw_tracking_implementation.md`。只有显式开启 `--enable-html-injection` 时才会额外生成 `*_with_tracking.html`
- 编码完成后重新打开改写后的 HTML 时，必须直接打开本地文件路径；不要再用 `127.0.0.1` 临时本地服务打开 `workspace_html` 或 `modified_html`
- 当输入是远程 URL 时，启动器会在目标 URL 上附加 `openclaw_tracking_token` 和 `openclaw_tracking_gateway` 查询参数，让插件识别本地网关。page identity 转发给后端前会去掉这些控制参数
- 不要因为首次返回时尚未出现 `implementation_guide` 就重新运行脚本。首次返回代表后台服务已准备接收保存结果；后续读取设计结果时必须使用同一个 `session_status_file`
- 首次返回 `waiting_for_save` 后，应等待用户明确说明设计完成，再读取一次设计结果；读取到 `saved` 后先输出埋点总揽并停止，等用户确认开始编码后再改写代码
- weblog `appKey` 是必填配置。写埋点代码前必须确认真实 `appKey`；如果缺失、为空，或仅有 `YOUR_APP_KEY_HERE`、`xxxx`、`待配置` 等占位值，必须先询问用户，不能把占位值写入测试代码或最终代码
- 测试前的代码版本必须保留 debug 为 `true`；只有用户明确说明测试完成或测试通过后，才把 debug 改为 `false`。最终交付代码不能保留 debug 为 `true`


## 输出要求

需要汇报：

- 目标 URL
- 首次后台返回时，需要汇报 `status`、`session_status_file`、`service_log`、`launcher_pid`、`extension_dir` 和 `profile_dir`
- 如果 `next_action` 提示需要用户手动处理扩展或开发者模式，需要明确告诉用户
- 首次后台返回可设计状态后，需要输出本阶段总结，提示用户在浏览器插件中完成设计，并在设计完成后告诉你；随后结束本轮
- 用户说明设计完成后，只读取一次 `session_status_file` 并汇报设计结果读取状态
- 读取到设计结果后，需要先输出埋点总揽，说明本次需要实现哪些埋点，并等待用户确认开始编码
- 读取到 `status: saved` 后，如果是本地 HTML fallback 模式，需要汇报 `implementation_guide`、`tracking_schema` 和 `workspace_html`；用户确认开始编码后，才能按 `implementation_guide` 改写 `workspace_html` 工作副本，不能改写原始 HTML
- 如果显式开启直接 HTML 注入并读取到 `status: saved`，需要汇报 `modified_html` 和 `tracking_schema`
- 用户确认开始编码后，需要先汇报 weblog `appKey` 确认状态；如果不知道真实 `appKey`，必须先向用户询问并停止当前轮次
- 埋点代码改写完成后，需要告诉用户代码已经修改完成，并说明当前测试版本 debug 为 `true`，再直接打开改写后的本地 HTML 文件并提示用户进行测试；如果用户反馈问题，继续修改代码并再次直接打开本地 HTML 文件验证
- 用户明确说明测试完成或测试通过后，需要把 debug 改为 `false`，并告诉用户 debug 已关闭
