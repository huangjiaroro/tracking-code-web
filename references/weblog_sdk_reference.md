# Weblog 埋点 SDK 参考

本文档供 OpenClaw 改写业务源码时参考。来源为内部 weblog SDK 文档，按代码实现所需内容做了精简。

## 基本结论

- 默认参考版本：`0.0.5`
- 默认 CDN 地址：`https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js`
- `ainvest` 环境 CDN 地址：`https://cdn.ainvest.com/frontResources/offline/js/weblog/v0.0.3.js`
- npm 包：`@thsf2e/weblog`
- 浏览器兼容：移动端 Android 4.4+、iOS iPhone 8 已验证；PC 端 IE10+ 已验证；IE9 及以下不支持
- 默认上报策略：20s 上报一次，每批最多 20 条；SDK 内部队列为空时会暂停轮询
- 默认本地缓存：最多缓存 100 条未发送埋点到 localStorage

## SDK 引用地址

| 环境 | SDK 地址 |
| --- | --- |
| `ainvest` | `https://cdn.ainvest.com/frontResources/offline/js/weblog/v0.0.3.js` |
| 其他环境 | `https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js` |

`ainvest` 环境除了要显式传 `domain: 'stat.ainvest.com'`，还要切换到上面的专用 SDK 地址。

## 上报集群

| 场景 | domain / 集群 |
| --- | --- |
| 内网 | `10.217.136.10:8080` |
| 国内正式 | `cbasspider.10jqka.com.cn` |
| 海外 ainvest | `stat.ainvest.com` |
| 海外 dreamface | `track.dreamfaceapp.com` |
| dreamface 国内访问 | `track.aidreamface.com` |

国内正式场景通常不需要显式传 `domain`，海外场景需要在 `setConfig` 中传对应 domain。

## 初始化方式

### npm 引用

```js
import { setConfig, report } from '@thsf2e/weblog';

setConfig({
  appKey: 'xxxx'
});
```

海外场景：

```js
setConfig({
  appKey: 'xxxx',
  domain: 'stat.ainvest.com'
});
```

### CDN 引用

```html
<script src="https://s.thsi.cn/cb?cd/weblog/0.0.5/weblog.js"></script>
```

```js
window.weblog.setConfig({
  appKey: 'xxxx'
});
```

海外场景：

```js
window.weblog.setConfig({
  appKey: 'xxxx',
  domain: 'stat.ainvest.com'
});
```

`ainvest` 环境完整示例：

```html
<script src="https://cdn.ainvest.com/frontResources/offline/js/weblog/v0.0.3.js"></script>
<script>
window.weblog.setConfig({
  appKey: 'xxxx',
  domain: 'stat.ainvest.com'
});
</script>
```

## setConfig 参数

| 字段 | 类型 | 必传 | 默认值 | 用途 |
| --- | --- | --- | --- | --- |
| `appKey` | string | 是 | 空 | 业务方在埋点管理平台申请的 appKey |
| `debug` | boolean | 否 | `false` | 开启后，上报前会在控制台打印埋点参数 |
| `domain` | string | 否 | 国内正式集群 | 海外业务传对应上报域名，例如 `stat.ainvest.com` |
| `logPrefix` | string | 否 | 空 | 三段式前缀，用于组件二段式 id 自动拼接 |
| `maxQueueLimit` | number | 否 | `100` | localStorage 中最多缓存的未发送埋点数量 |
| `userAgent` | string | SSR 场景 | 非 SSR 默认 `navigator.userAgent` | SSR 上报时传 UA |
| `deviceId` | string | SSR 场景 | 空 | SSR 上报时传设备指纹 |
| `userId` | string | SSR 场景 | 空 | SSR 或特殊业务场景传用户 ID |

`logPrefix` 必须是三段式前缀，例如 `ths_tgpt_askdetail`。组件内可以只上报二段式 id，例如 `pageBot_checkAgree`，SDK 会拼成 `ths_tgpt_askdetail_pageBot_checkAgree`。

## report 参数

```js
report({
  id: 'ths_ifund_idcard_pageBot_checkAgree',
  action: 'click',
  logmap: {
    // 业务自定义扩展字段
  }
});
```

| 字段 | 类型 | 必传 | 默认值 | 用途 |
| --- | --- | --- | --- | --- |
| `id` | string | 是 | 空字符串 | BI 给出的四段式埋点 id，或配合 `logPrefix` 使用的二段式 id |
| `action` | string | 是 | `click` | 用户操作类型 |
| `logmap` | object | 否 | `{}` | 业务自定义扩展字段 |

支持的 `action`：

| action | 含义 |
| --- | --- |
| `click` | 点击 |
| `slide` | 滑动 |
| `show` | 展示 / 元素曝光 |
| `hover` | 悬浮 |
| `stay` | 停留 |
| `dis` | 曝光消失 |
| `pull` | 拉动 |
| `dclick` | 双击 |
| `start` | 应用启动 |
| `press` | 长按 |
| `end` | 应用关闭 |

## OpenClaw 改写规则

1. 页面或应用入口只初始化一次 `setConfig`，避免重复注册。
2. `appKey` 必须配置；国内正式环境通常不传 `domain`，海外环境按业务传 domain。
3. `ainvest` 环境的 CDN 要切到 `https://cdn.ainvest.com/frontResources/offline/js/weblog/v0.0.3.js`，不要继续使用默认地址。
4. 控件触发时调用 `report({ id, action, logmap })`，不要把易变化的业务值提前缓存在初始化阶段。
5. 额外属性必须在触发时实时读取，例如当前 tab、当前问题、当前列表项、URL 参数、页面状态或接口数据。
6. 页面跳转或重定向前如需要补报剩余埋点，使用 `reportLeft()`。
7. 开发和验收阶段可传 `debug: true`，方便在控制台查看上报参数；生产默认 `false`。
8. 非 SSR 项目建议把 `@thsf2e/weblog` 配成 external，避免打包进业务产物。
9. 对本仓库的手写埋点实现，建议在调用前加 no-op fallback，再用 `try/catch` 包住真实调用；不要只依赖 `catch` 兜底。

推荐模板：

```js
window.weblog = window.weblog || {};
window.weblog.setConfig = window.weblog.setConfig || function () {};
window.weblog.report = window.weblog.report || function () {};

try {
  window.weblog.setConfig({
    appKey: 'xxxx',
    debug: false
  });
} catch (error) {}
```

## 动态 logmap 例子

点击某个 tab 时读取当前问题标题：

```js
function getCurrentQuestion() {
  return document.querySelector('.question-title')?.textContent?.trim() || '';
}

report({
  id: 'ths_demo_questionTab_click',
  action: 'click',
  logmap: {
    question: getCurrentQuestion()
  }
});
```

点击列表项时读取当前项上的属性：

```js
function reportItemClick(event) {
  const item = event.target.closest('[data-question-id]');
  report({
    id: 'ths_demo_questionItem_click',
    action: 'click',
    logmap: {
      question_id: item?.getAttribute('data-question-id') || '',
      question_title: item?.querySelector('.question-title')?.textContent?.trim() || ''
    }
  });
}
```

## SSR 用法

SSR 场景需要显式传 `userAgent`、`deviceId`、`userId`。海外 SSR 还需要传 `domain`。

```js
import { setConfig, report, reportLeft } from '@thsf2e/weblog';

setConfig({
  appKey: 'xxxx',
  userAgent: context.userAgent,
  deviceId: 'xxxx',
  userId: 'xxxx',
  domain: 'stat.ainvest.com'
});

report({
  id: 'ths_ifund_idcard_pageBot_checkAgree',
  action: 'click',
  logmap: {}
});

reportLeft();
```

## external 配置

Vue：

```js
configureWebpack: {
  externals: {
    '@thsf2e/weblog': 'weblog'
  }
}
```

Vite / Rollup：

```js
const externalGlobals = require('rollup-plugin-external-globals');

export default {
  build: {
    rollupOptions: {
      external: ['@thsf2e/weblog'],
      plugins: [
        externalGlobals({
          '@thsf2e/weblog': 'weblog'
        })
      ]
    }
  }
};
```

## 常见问题

- `uid` 上报为 `none`：把当前用户 ID 写入 cookie 的 `userid` 字段，或在 SSR / 特殊场景显式传 `userId`。
- webpack 无法加载 `.mjs`：为 webpack 增加 `.mjs` 解析 loader。
- 验收看不到实时埋点：开启 `debug: true` 看控制台，或到埋点事件平台查看；默认批量上报有约 20s 延迟。
