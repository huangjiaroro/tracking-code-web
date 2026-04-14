/**
 * @file dom-utils/interactive-detector.js
 * @description 可交互元素检测工具，逻辑提取自 page-agent 项目
 * @see https://github.com/browser-use/browser-use (原始来源)
 *
 * 作为 content script 的前置脚本注入页面，暴露 window.DomUtils 全局对象。
 * 提供 isInteractiveElement、isElementVisible、extractInteractiveElements 等方法。
 */

; (function (global) {
  'use strict'

  // ─────────────────────────────────────────────
  // 计算样式缓存（一次 DOM 扫描内复用）
  // ─────────────────────────────────────────────
  const _styleCache = new WeakMap()

  function getCachedComputedStyle(element) {
    if (_styleCache.has(element)) return _styleCache.get(element)
    const style = window.getComputedStyle(element)
    _styleCache.set(element, style)
    return style
  }

  // ─────────────────────────────────────────────
  // 可见性判断
  // ─────────────────────────────────────────────

  /**
   * 判断元素是否可见（基本可见性，不检查 viewport 范围）
   * @param {HTMLElement} element
   * @returns {boolean}
   */
  function isElementVisible(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return false
    const style = getCachedComputedStyle(element)
    return (
      element.offsetWidth > 0 &&
      element.offsetHeight > 0 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none' &&
      style.opacity !== '0'
    )
  }

  // ─────────────────────────────────────────────
  // 可交互元素判断（核心逻辑，来自 page-agent）
  // ─────────────────────────────────────────────

  /** 可交互的 CSS cursor 值 */
  const INTERACTIVE_CURSORS = new Set([
    'pointer', 'move', 'text', 'grab', 'grabbing', 'cell', 'copy',
    'alias', 'all-scroll', 'col-resize', 'context-menu', 'crosshair',
    'e-resize', 'ew-resize', 'help', 'n-resize', 'ne-resize',
    'nesw-resize', 'ns-resize', 'nw-resize', 'nwse-resize',
    'row-resize', 's-resize', 'se-resize', 'sw-resize',
    'vertical-text', 'w-resize', 'zoom-in', 'zoom-out',
  ])

  /** 明确表示非交互的 cursor 值 */
  const NON_INTERACTIVE_CURSORS = new Set([
    'not-allowed', 'no-drop', 'wait', 'progress',
  ])

  /** 原生交互标签 */
  const INTERACTIVE_TAGS = new Set([
    'a', 'button', 'input', 'select', 'textarea',
    'details', 'summary', 'label', 'option', 'optgroup',
    'fieldset', 'legend',
  ])

  /** 明确的禁用属性 */
  const DISABLE_ATTRS = ['disabled', 'readonly']

  /** 可交互的 ARIA role */
  const INTERACTIVE_ROLES = new Set([
    'button', 'menu', 'menubar', 'menuitem', 'menuitemradio', 'menuitemcheckbox',
    'radio', 'checkbox', 'tab', 'switch', 'slider', 'spinbutton',
    'combobox', 'searchbox', 'textbox', 'listbox', 'option', 'scrollbar',
    'link', 'treeitem',
  ])

  /**
   * 检查元素是否可滚动
   * @param {HTMLElement} element
   * @returns {boolean}
   */
  function isScrollable(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return false
    const style = getCachedComputedStyle(element)
    const display = style.display
    if (display === 'inline' || display === 'inline-block') return false

    const overflowX = style.overflowX
    const overflowY = style.overflowY
    const scrollableX = overflowX === 'auto' || overflowX === 'scroll'
    const scrollableY = overflowY === 'auto' || overflowY === 'scroll'
    if (!scrollableX && !scrollableY) return false

    const THRESHOLD = 4
    const scrollWidth = element.scrollWidth - element.clientWidth
    const scrollHeight = element.scrollHeight - element.clientHeight
    return scrollWidth > THRESHOLD || scrollHeight > THRESHOLD
  }

  /**
   * 判断元素是否可交互
   *
   * 优先级顺序：
   *  1. CSS cursor 样式（最广泛覆盖）
   *  2. 原生交互标签（检查 disabled/readonly/inert 排除）
   *  3. contenteditable
   *  4. 常见交互 class / attribute 模式
   *  5. ARIA role 检查
   *  6. 内联事件属性（onclick 等）
   *  7. 可滚动元素
   *
   * @param {HTMLElement} element
   * @returns {boolean}
   */
  function isInteractiveElement(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return false

    const tagName = element.tagName.toLowerCase()

    // 跳过特定无意义标签
    const SKIP_TAGS = new Set(['html', 'body', 'script', 'style', 'link', 'meta', 'noscript', 'template', 'svg'])
    if (SKIP_TAGS.has(tagName)) return false

    // 跳过 aria-hidden 元素
    if (element.getAttribute('aria-hidden') === 'true') return false

    // 跳过扩展自身注入的高亮容器
    if (element.id === 'playwright-highlight-container') return false
    if (element.hasAttribute('data-cdp-extracted-id')) return false

    const style = getCachedComputedStyle(element)

    // ① CSS cursor 判断（"Genius fix" from page-agent）
    if (style.cursor && INTERACTIVE_CURSORS.has(style.cursor)) {
      return true
    }

    // ② 原生交互标签
    if (INTERACTIVE_TAGS.has(tagName)) {
      // 非交互 cursor 排除
      if (style.cursor && NON_INTERACTIVE_CURSORS.has(style.cursor)) return false
      // 显式禁用属性
      for (const attr of DISABLE_ATTRS) {
        if (element.hasAttribute(attr) || element.getAttribute(attr) === 'true') return false
      }
      // DOM 属性禁用
      if (element.disabled || element.readOnly || element.inert) return false
      return true
    }

    // ③ contenteditable
    if (element.getAttribute('contenteditable') === 'true' || element.isContentEditable) {
      return true
    }

    // ④ 常见交互 class/attribute 模式
    if (
      element.classList &&
      (element.classList.contains('button') ||
        element.classList.contains('dropdown-toggle') ||
        element.getAttribute('data-index') ||
        element.getAttribute('data-toggle') === 'dropdown' ||
        element.getAttribute('aria-haspopup') === 'true')
    ) {
      return true
    }

    // ⑤ ARIA role
    const role = element.getAttribute('role') || element.getAttribute('aria-role')
    if (role && INTERACTIVE_ROLES.has(role.toLowerCase())) return true

    // ⑥ 内联事件属性
    const INLINE_EVENTS = ['onclick', 'onmousedown', 'onmouseup', 'ondblclick']
    for (const attr of INLINE_EVENTS) {
      if (element.hasAttribute(attr) || typeof element[attr] === 'function') return true
    }

    // ⑦ 可滚动元素
    if (isScrollable(element)) return true

    return false
  }

  // ─────────────────────────────────────────────
  // 语义推断：Page / Block / Element
  // ─────────────────────────────────────────────

  /**
   * 推断元素所在的语义区块
   * @param {HTMLElement} element
   * @returns {{ page: string, block: string }}
   */
  function inferSemanticContext(element) {
    const page = document.title || 'Unknown Page'
    let block = 'Unknown Block'

    // 向上查找最近的 Landmark 元素
    let current = element.parentElement
    while (current && current !== document.body) {
      const tagName = current.tagName.toLowerCase()
      const role = (current.getAttribute('role') || '').toLowerCase()
      const ariaLabel = current.getAttribute('aria-label') || ''

      const landmarkTagMap = {
        'main': 'main', 'nav': 'navigation', 'header': 'banner',
        'footer': 'contentinfo', 'aside': 'complementary',
      }

      if (landmarkTagMap[tagName]) {
        block = `${landmarkTagMap[tagName]}${ariaLabel ? ': ' + ariaLabel : ''}`
        break
      }
      if (role && ['main', 'navigation', 'banner', 'contentinfo', 'complementary', 'search', 'region'].includes(role)) {
        block = `${role}${ariaLabel ? ': ' + ariaLabel : ''}`
        break
      }

      current = current.parentElement
    }

    // className 关键词推断（兜底）
    if (block === 'Unknown Block' || block === 'main') {
      const cls = element.className && typeof element.className === 'string'
        ? element.className.toLowerCase() : ''
      const parentCls = element.parentElement
        ? (element.parentElement.className || '').toString().toLowerCase() : ''
      const combined = cls + ' ' + parentCls

      if (combined.includes('header') || combined.includes('topbar') || combined.includes('navbar')) {
        block = 'Header (Class Inferred)'
      } else if (combined.includes('sidebar') || combined.includes('aside') || combined.includes('sidenav')) {
        block = 'Sidebar (Class Inferred)'
      } else if (combined.includes('footer')) {
        block = 'Footer (Class Inferred)'
      } else if (combined.includes('chat') || combined.includes('panel')) {
        block = 'ChatPanel (Class Inferred)'
      } else if (combined.includes('nav') || combined.includes('menu')) {
        block = 'Navigation (Class Inferred)'
      }
    }

    return { page, block }
  }

  // ─────────────────────────────────────────────
  // 语义角色推断
  // ─────────────────────────────────────────────

  /**
   * 从 DOM 元素推断语义角色字符串（用于显示/LLM）
   * @param {HTMLElement} element
   * @returns {string}
   */
  function inferRole(element) {
    const tagName = element.tagName.toLowerCase()
    const type = (element.getAttribute('type') || '').toLowerCase()
    const role = (element.getAttribute('role') || '').toLowerCase()

    if (role && role !== 'presentation' && role !== 'none') return role

    const tagRoleMap = {
      'a': 'link', 'button': 'button', 'select': 'combobox',
      'textarea': 'textbox', 'details': 'details', 'summary': 'summary',
    }
    if (tagRoleMap[tagName]) return tagRoleMap[tagName]

    if (tagName === 'input') {
      const inputRoleMap = {
        'checkbox': 'checkbox', 'radio': 'radio', 'range': 'slider',
        'number': 'spinbutton', 'search': 'searchbox', 'submit': 'button',
        'button': 'button', 'reset': 'button', 'image': 'button',
      }
      return inputRoleMap[type] || 'textbox'
    }

    if (element.isContentEditable) return 'textbox'
    if (isScrollable(element)) return 'scrollable'
    return 'generic'
  }

  /**
   * 获取元素的可读名称（aria-label > title > placeholder > innerText 截断）
   * @param {HTMLElement} element
   * @returns {string}
   */
  function inferName(element) {
    const ariaLabel = element.getAttribute('aria-label')
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim().substring(0, 50)

    const title = element.getAttribute('title')
    if (title && title.trim()) return title.trim().substring(0, 50)

    const placeholder = element.getAttribute('placeholder')
    if (placeholder && placeholder.trim()) return placeholder.trim().substring(0, 50)

    const alt = element.getAttribute('alt')
    if (alt && alt.trim()) return alt.trim().substring(0, 50)

    // innerText 截断取前50字符
    const text = (element.innerText || element.textContent || '').trim().replace(/\s+/g, ' ')
    if (text) return text.substring(0, 50)

    return ''
  }

  /**
   * 生成较稳定的 CSS selector（id 优先，否则用 tagName + class + nth）
   * @param {HTMLElement} element
   * @returns {string}
   */
  function buildSelector(element) {
    const dataAiId = element.getAttribute('data-ai-id')
    if (dataAiId) return `[data-ai-id="${CSS.escape(dataAiId)}"]`
    if (element.id) return `#${CSS.escape(element.id)}`

    // data-cdp-extracted-id 是我们自己注入的，用 tagName + class
    const tagName = element.tagName.toLowerCase()
    const classes = Array.from(element.classList)
      .filter(c => !c.startsWith('playwright-') && c.length < 40)
      .slice(0, 3)
      .map(c => `.${CSS.escape(c)}`)
      .join('')

    return `${tagName}${classes}` || tagName
  }

  // ─────────────────────────────────────────────
  // 主入口：提取页面所有可交互元素
  // ─────────────────────────────────────────────

  /**
   * 遍历整个 document，提取所有可交互且可见的元素
   *
   * @returns {Array<{
   *   role: string,
   *   name: string,
   *   selector: string,
   *   box: { x: number, y: number, width: number, height: number },
   *   semanticContext: { page: string, block: string },
   *   element: HTMLElement
   * }>}
   */
  function extractInteractiveElements() {
    const results = []
    const allElements = document.querySelectorAll('*')

    const vw = window.innerWidth || document.documentElement.clientWidth
    const vh = window.innerHeight || document.documentElement.clientHeight

    for (const element of allElements) {
      if (!isElementVisible(element)) continue
      if (!isInteractiveElement(element)) continue

      const rect = element.getBoundingClientRect()
      // 过滤掉尺寸为0的元素
      if (rect.width === 0 || rect.height === 0) continue

      // ── 过滤"通过 CSS Transform / 负定位推出视口外"的隐藏元素 ──
      // 例如未展开的侧边栏：transform: translateX(-280px) 会导致 rect.right <= 0
      // 允许 8px 的容差，避免边缘元素被误杀
      const VIEWPORT_MARGIN = 8
      const isOutsideViewport =
        rect.right < -VIEWPORT_MARGIN ||          // 完全在左侧视口外
        rect.bottom < -VIEWPORT_MARGIN ||         // 完全在上方视口外
        rect.left > vw + VIEWPORT_MARGIN ||       // 完全在右侧视口外
        rect.top > vh + VIEWPORT_MARGIN           // 完全在下方视口外
      if (isOutsideViewport) continue

      const role = inferRole(element)
      const name = inferName(element)
      const selector = buildSelector(element)
      const semanticContext = inferSemanticContext(element)

      results.push({
        role,
        name,
        selector,
        box: {
          x: Math.round(rect.left + window.scrollX),
          y: Math.round(rect.top + window.scrollY),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
        semanticContext,
        element, // 仅在 content script 上下文中使用，不会被序列化传输
      })
    }

    return results
  }


  // ─────────────────────────────────────────────
  // 暴露全局 API
  // ─────────────────────────────────────────────
  global.DomUtils = {
    isInteractiveElement,
    isElementVisible,
    isScrollable,
    inferRole,
    inferName,
    buildSelector,
    inferSemanticContext,
    extractInteractiveElements,
  }
})(window)
