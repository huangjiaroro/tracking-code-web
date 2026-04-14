# 智能埋点 Chrome 插件历史恢复方案 — 基于 DDD 的三系统协作架构设计

## 1. 背景与目标

### 1.1 背景

当前项目已经具备以下能力：

- Chrome 插件可以在页面中提取语义快照，识别一批可交互元素。
- 用户可以继续框选新增元素，也可以删除已有标注。
- 点击 `Generate Tracking Plan` 后，插件会创建一个后端 Tracking Agent Project，并把截图、语义上下文发送给 Agent。
- Agent 会与埋点管理平台交互，查询元数据并生成设计文档；后续插件再围绕该设计文档继续做新增、修改、删除与保存。

当前项目的核心问题不在“生成埋点方案”，而在“无法恢复历史设计上下文”。

### 1.2 当前现状（As-Is）

从现有代码看，插件的关键运行状态主要保存在内存中：

- `popup.js` 中的 `currentProjectId`
- `popup.js` 中的 `trackingState`
- `popup.js` 中的 `currentExtractedNodes`

这意味着：

- 关闭 side panel 后，这些状态全部丢失。
- 插件再次打开时，只能重新提取页面语义快照，但无法知道当前页面是否已经设计过。
- 即使后端 Agent Project 仍然存在，插件也缺少把“当前页面”与“历史 Project”重新绑定的机制。
- 当前实现已经具备“把一批节点绘制回页面”的基础能力，但仍缺少三项关键基础设施：
  - 页面绑定索引（page-project binding）
  - surface 上下文识别（主页面 / 弹窗 / 抽屉 / popover）
  - DOM 改版后的失配重绑机制

### 1.3 目标（To-Be）

本方案的目标是建立一套可恢复、可持续编辑、可应对页面改版的历史标注机制：

- 当用户再次打开同一个页面时，插件能够找回该页面对应的历史 Agent Project。
- 插件能够拉取项目内的设计文档和恢复渲染信息，并把历史标注重新渲染到当前页面。
- 当页面上出现弹窗、抽屉等动态 UI 时，插件能够识别当前激活的 surface，并只渲染该 surface 下的埋点。
- 当页面 DOM 结构发生变化时，插件能够尝试自动重找；若置信度不足，则把该埋点放入“待重新绑定”队列，由用户重新圈选更新。

### 4.5 `PageIdentity` 的实现约束

`PageIdentity` 是“逻辑页面身份”的值对象，只用于回答“当前页面应该找回哪个 Project”。

它不承担以下职责：

- 不负责识别弹窗、抽屉、popover；这属于 `Surface`。
- 不负责匹配页面中的具体组件；这属于 `RegionAnchor`。
- 不负责保存业务埋点设计；这属于 `DesignDocument` 与 `Region`。

#### 建议字段

| 字段 | 含义 | 说明 |
| --- | --- | --- |
| `origin` | 站点来源 | 例如 `https://phonestat.hexin.cn`，用于区分不同环境或不同站点。 |
| `url` | 当前原始 URL | 作为调试信息保留，不作为主匹配键。 |
| `route_key` | 路由归一化结果 | 把动态路径段替换成占位符后的稳定页面键。 |
| `route_pattern` | 路由模式 | 保存首次识别出的模板，后续恢复按模板匹配。 |
| `title` | 页面标题 | 用作签名构造的辅助字段。 |
| `page_signature` | 页面内容签名 | 基于页面稳定语义特征生成，用于二次校验。 |
| `signature_version` | 签名版本 | 便于后续算法升级。 |

#### 生成流程

1. 从浏览器读取当前 `location.href`、`location.origin`、`location.pathname`、`document.title`。
2. 对 `pathname` 做按段切分。
3. 对每个 path segment 做“动态段识别”。
4. 将识别出的动态段替换为占位符，生成 `route_key`。
5. 根据 `route_key` 进一步生成 `route_pattern`。
6. 从页面中抽取稳定语义特征，生成 `page_signature`。
7. 将 `origin + route_key + page_signature` 发送给后端做 `resolve_page_project`。

#### 动态段识别规则

以下 segment 默认视为变量段：

- 纯数字且长度较长，例如订单号、项目 ID、时间戳。
- UUID、hash、长随机串。
- 字母与数字混合、熵高且缺少稳定可读语义的字符串。
- 具备稳定前缀加动态后缀的 segment，例如 `project-xxxxx`、`task-xxxxx`、`ext-clhe9gf3`。

对类似 `project-1774358977737-bqcaxtt30` 的 segment，可识别出其后半段具备明显动态特征：

- `1774358977737` 视为长数字动态段。
- `bqcaxtt30` 视为随机后缀。
- 一旦 segment 被判定为动态段，整个 segment 统一归一化为 `:id`，不保留稳定前缀。

因此，这个 segment 的归一化结果应为：

- `:id`

#### `page_signature` 的生成规则

`page_signature` 不应只依赖 URL，而应来自页面内容的稳定语义特征。建议组合以下信息：

- `document.title`
- 页面主标题，如 `h1`
- 主要 landmark 的 `aria-label` 或文本
- 首页首屏核心交互元素的前若干个名称
- 页面主内容区的稳定业务关键词

签名目标不是唯一标识一个 DOM 版本，而是把“同一路由但不同页面内容”的情况区分开。

#### 示例

原始 URL：

- `https://phonestat.hexin.cn/sdmp/deep_analysis/project-1774358977737-bqcaxtt30/chat?initStart=true`

建议生成的 `PageIdentity`：

| 字段 | 示例值 |
| --- | --- |
| `origin` | `https://phonestat.hexin.cn` |
| `url` | `https://phonestat.hexin.cn/sdmp/deep_analysis/project-1774358977737-bqcaxtt30/chat?initStart=true` |
| `route_key` | `/sdmp/deep_analysis/:id/chat` |
| `route_pattern` | `^/sdmp/deep_analysis/[^/]+/chat$` |
| `title` | 当前页面标题 |
| `page_signature` | 由标题、主标题、landmark、核心交互名称生成的签名 |

#### 实现原则

- `route_key` 负责粗匹配。
- `page_signature` 负责二次校验。
- 两者同时命中，才认为找回的是同一个逻辑页面。
- 若 `route_key` 命中但 `page_signature` 差异过大，应进入候选匹配或人工确认，而不是直接恢复。

---


### 5.2 弹窗 / 抽屉如何识别并按上下文渲染

#### 问题

主页面与弹窗、抽屉、popover 共享同一个 DOM 容器时，如果没有显式上下文模型，历史标注会出现混画、误画、重复渲染。

#### 方案

- 一个 `Project` 内允许多个 `Surface`，而不是为每个弹窗单独建项目。
- 在插件页面代理层引入 `SurfaceDetector`。
- 使用以下信号识别动态 surface：
  - `MutationObserver`
  - `role=dialog`
  - `aria-modal=true`
  - 类名包含 `modal` / `dialog` / `drawer` / `popover` / `sheet`
  - 高 `z-index`
  - `visibility` 与 `display` 状态
- 每次 active surface 集合变化时：
  - 重新匹配当前 active surface 关联的 `RegionAnchor`
  - 只渲染当前 active surface 下的 markers
  - 已关闭 surface 的 markers 立即移除

#### 设计要点

- `main` 是默认 surface。
- surface 是恢复渲染的边界，不是业务埋点对象本身。
- 同一个 `Region` 不能同时属于多个 surface；若需要跨 surface 复用，应该建多个 `RegionAnchor`。

#### `Surface` 的识别规则

`Surface` 不能只靠某一个 className 识别，而应采用“候选容器发现 -> root 确认 -> 类型判定 -> 激活态判断”的四步法。

##### 第一步：候选容器发现

插件在页面侧持续运行 `MutationObserver`，重点监听以下变化：

- 新节点插入
- 节点样式变化，如 `display`、`visibility`、`opacity`
- 属性变化，如 `role`、`aria-modal`、`aria-hidden`
- `body` 是否出现滚动锁定类名

新出现或状态变化的节点中，满足以下条件之一的，进入 surface 候选集合：

- `role="dialog"` 或 `role="alertdialog"`
- `aria-modal="true"`
- `position` 为 `fixed` 或 `absolute`
- `z-index` 明显高于主内容层
- class 或 data 属性中包含 `modal`、`dialog`、`drawer`、`popover`、`sheet`、`overlay`
- 节点本身或其后代包含一批新的可交互元素

##### 第二步：surface root 确认

对候选节点向上寻找最合适的容器根节点，规则如下：

- 优先选择具备 `role` 或 `aria-modal` 的最近祖先节点
- 若没有显式语义容器，则选择最近的可见大容器
- 根节点必须满足：
  - 可见
  - 在视口内
  - 尺寸达到最低阈值
  - 至少包含一个可交互元素

以下节点默认不提升为独立 surface：

- tooltip
- toast
- 纯下拉菜单
- 纯 hover 浮层

除非该节点已经关联过历史 `RegionAnchor`，否则这类轻量浮层仍归属于当前已有 surface。

##### 第三步：surface 类型判定

在 root 确认后，根据几何位置和语义信号判断类型：

- `main`
  - 页面主内容区，默认存在
  - 通常对应 `document.body` 下的主 landmark
- `dialog`
  - 常见信号是 `role="dialog"`、居中显示、存在遮罩层
- `drawer`
  - 常见信号是固定在左侧或右侧边缘，宽度较大，高度接近视口
- `popover`
  - 常见信号是相对某个触发元素展开，尺寸中小，z-index 高于主层

类型判定优先级：

- 显式语义属性优先于几何推断
- 几何推断优先于 className 推断

##### 第四步：激活态判断

并不是识别出的每个 surface 都要立即渲染 marker，只有激活态 surface 才参与恢复。

激活态判断规则：

- 节点当前可见，且未被 `aria-hidden` 隐藏
- 节点位于当前视口中
- 节点处于较高层级，未被其他 overlay 覆盖
- 节点内存在焦点，或最近一次 DOM 变化与该节点相关

当多个 surface 同时存在时：

- `main` 始终存在，但在全屏弹窗遮挡时不优先渲染
- 顶层 `dialog/drawer/popover` 优先于 `main`
- 若存在多层弹窗，只渲染最上层 active surface，除非下层 surface 仍有可见且未遮挡区域

##### `surface_key` 的生成

`surface_key` 不应直接使用临时 DOM id，而应由以下信息组合生成：

- `type`
- root 容器的稳定属性，如 `role`、`aria-label`、`data-testid`
- root 容器的文本签名
- root 容器的几何特征

例如：

- `main`
- `dialog:user-center`
- `drawer:filter-panel`
- `popover:date-picker`

##### 恢复时如何匹配历史 `Surface`

插件再次打开页面时，先检测当前页面存在的 runtime surfaces，再用历史 `Surface.activation_hints` 做匹配：

- 先比显式属性：`role`、`aria-label`、稳定 data 属性
- 再比 class token
- 再比文本签名
- 最后比几何特征和相对位置

只有匹配到的 surface 才会参与其下 `RegionAnchor` 的恢复。

##### 一个最小示例

如果页面上打开“筛选抽屉”：

- DOM 新增一个 `position: fixed`、靠右、`z-index` 很高的容器
- class 中包含 `drawer`
- 容器内新增多个按钮、checkbox、输入框

则插件应：

- 把这个容器识别为候选 surface
- 确认其 root
- 判定类型为 `drawer`
- 生成类似 `drawer:filter-panel` 的 `surface_key`
- 只恢复该 drawer 对应的历史 markers，而不是把主页面所有 marker 混画上去

### 5.3 页面 DOM 改版后如何找回旧组件

#### 问题

页面上线新版本后，旧的 DOM 路径、class、层级可能都变化，仅靠单一 selector 无法可靠找回原组件。

#### 方案

- 使用多策略匹配，不依赖单一 selector：
  - 稳定属性：`id`、`data-testid`、`aria-label`、`role`
  - selector 候选
  - 语义上下文：page / block / element
  - 几何位置：相对 surface 的归一化 box
- 匹配结果分级处理：
  - 高置信度：自动恢复并渲染
  - 中低置信度：进入 `UnmatchedRegion`
  - 无候选：直接进入 `UnmatchedRegion`
- 插件侧提供“待重新绑定”列表，用户重新圈选后更新原 `RegionAnchor`。

#### 设计要点

- 低置信度不做静默自动绑定，避免把旧埋点绑到错误组件。
- 重新绑定时更新的是原 `region_id` 的锚点，而不是新建一个业务 `Region`。

### 5.4 标注稳定标识如何设计

#### 问题

当前实现里，标注删除后会重排编号。这适合视觉展示，但不适合作为长期保存和恢复的稳定主键。

#### 方案

- 业务层使用不可变 `region_id` 作为稳定标识。
- 展示层继续保留可重排的 `region_number`，用于页面标签显示和用户操作。
- `Region`、`RegionAnchor`、`UnmatchedRegion` 统一通过 `region_id` 关联。

#### 改造方向

- 删除或重新绑定时，不再依赖当前页面上的显示编号做主关联。
- `region_number` 只承担展示序号职责，允许在 UI 中重排。
- `region_id` 才是后端保存、恢复匹配、草稿更新和最终平台同步时的长期身份标识。

#
