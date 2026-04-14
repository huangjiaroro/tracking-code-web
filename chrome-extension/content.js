/**
 * UI元素完全动态分析与标记脚本 - Chrome扩展版
 * 完全基于页面实际内容，不预设任何类型
 */

// ============ 辅助函数 ============
function isInteractive(el) {
  if (!el || el === document.body || el === document.documentElement) return false;
  
  const tag = el.tagName;
  const role = el.getAttribute('role');
  const type = el.getAttribute('type');
  const hasOnclick = el.onclick || el.getAttribute('onclick') !== null;
  const cursor = window.getComputedStyle(el).cursor;

  // 基础可交互标签
  const interactiveTags = ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA', 'DETAILS', 'SUMMARY'];
  if (interactiveTags.includes(tag)) return true;

  // ARIA 角色
  const interactiveRoles = ['button', 'link', 'checkbox', 'radio', 'menuitem', 'tab', 'switch', 'menuitemcheckbox', 'menuitemradio'];
  if (interactiveRoles.includes(role)) return true;

  // 事件监听与样式特征
  if (hasOnclick || cursor === 'pointer') return true;

  // 特殊属性
  if (el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1') return true;
  if (el.hasAttribute('data-action') || el.hasAttribute('data-id') || el.hasAttribute('data-click')) return true;

  return false;
}

// ============ 框选功能相关变量 ============
let isSelecting = false;
let selectionStartX = 0;
let selectionStartY = 0;
let selectionBox = null;
let selectionOverlay = null;

// 颜色定义
const colors = {
  nav_back: '#FF0000',      // 导航-返回
  delete: '#FF4500',         // 删除
  upload: '#9932CC',         // 上传/导入
  tool: '#FF69B4',           // 工具
  view: '#00CED1',           // 查看
  refresh: '#9ACD32',         // 刷新
  settings: '#4169E1',        // 设置
  more: '#9370DB',           // 更多
  send: '#32CD32',           // 发送
  switch: '#FFA07A',         // 切换
  visualize: '#FF0000',       // 可视化（红色）
  info: '#FF0000',            // 信息（红色）
  stop: '#B22222',           // 停止
  publish: '#0066CC',        // 发布
  act: '#607D8B',           // Act
  chat: '#00BCD4',           // Chat
  correct: '#4CAF50',        // 正确
  error: '#F44336',          // 错误
  avatar: '#E91E63',         // 头像/负责人
  button: '#2196F3',         // 普通按钮（蓝色）
  link: '#00BFFF',           // 普通链接
  other: '#009688'           // 其他（青色）
};

const MARKER_DEBUG_PREFIX = '[MarkerDebug][content]';

function logMarkerDebug(stage, payload) {
  if (payload === undefined) {
    console.log(`${MARKER_DEBUG_PREFIX} ${stage}`);
    return;
  }
  console.log(`${MARKER_DEBUG_PREFIX} ${stage}`, payload);
}

function summarizeElementForMarkerDebug(element) {
  if (!element) return null;

  const text = String(element.innerText || element.textContent || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80);
  const rect = typeof element.getBoundingClientRect === 'function'
    ? element.getBoundingClientRect()
    : null;

  return {
    tag: element.tagName || null,
    id: element.id || '',
    className: typeof element.className === 'string' ? element.className.slice(0, 120) : '',
    role: element.getAttribute?.('role') || '',
    ariaLabel: element.getAttribute?.('aria-label') || '',
    text,
    rect: rect ? {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    } : null
  };
}

function summarizeNodeForMarkerDebug(node) {
  if (!node) return null;
  return {
    id: node.id ?? null,
    domBindingId: node.domBindingId || '',
    name: node.name || node.role || '',
    selector: node.selector || '',
    elementId: node.elementId || '',
    hasBox: Boolean(node.box),
    box: node.box ? {
      x: Math.round(node.box.x || 0),
      y: Math.round(node.box.y || 0),
      width: Math.round(node.box.width || 0),
      height: Math.round(node.box.height || 0)
    } : null
  };
}

function summarizeRegionForMarkerDebug(region) {
  if (!region) return null;
  return {
    regionId: region.region_id || null,
    regionNumber: region.region_number || null,
    status: region.status || '',
    surfaceId: region.surface_id || '',
    elementName: region.element_name || '',
    elementCode: region.element_code || ''
  };
}

function summarizeSurfaceForMarkerDebug(surface) {
  if (!surface) {
    return { type: 'main', state: 'default' };
  }
  return {
    type: surface.type || '',
    surfaceId: surface.surface_id || '',
    surfaceKey: surface.surface_key || '',
    rootTag: surface.root?.tagName || null,
    role: surface.root?.getAttribute?.('role') || ''
  };
}

function incrementMarkerDebugCount(counter, key) {
  const normalizedKey = key || 'unknown';
  counter[normalizedKey] = (counter[normalizedKey] || 0) + 1;
}

let lastPreviewMarkerSummarySignature = '';
let lastTrackedMarkerSummarySignature = '';

// 分类元素类型
function classifyElement(el) {
  const title = el.getAttribute('title') || '';
  const aria = el.getAttribute('aria-label') || '';
  const text = (el.textContent || el.innerText || '').trim().toLowerCase();
  const allText = `${text} ${title} ${aria}`.toLowerCase();
  const tag = el.tagName;

  if (allText.includes('back') || allText.includes('返回') || allText.includes('home') || (tag === 'A' && el.getAttribute('href'))) return 'nav_back';
  if (allText.includes('删除') || allText.includes('delete') || allText.includes('remove') || allText.includes('移除')) return 'delete';
  if (allText.includes('上传') || allText.includes('upload') || allText.includes('导入') || allText.includes('import') || allText.includes('文件') || allText.includes('file')) return 'upload';
  if (allText.includes('工具') || allText.includes('tool') || allText.includes('选择') || allText.includes('select')) return 'tool';
  if (allText.includes('查看') || allText.includes('view') || allText.includes('详情') || allText.includes('detail')) return 'view';
  if (allText.includes('刷新') || allText.includes('refresh') || allText.includes('reload') || allText.includes('重新')) return 'refresh';
  if (allText.includes('设置') || allText.includes('settings') || allText.includes('配置') || allText.includes('config')) return 'settings';
  if (allText.includes('更多') || allText.includes('more') || allText.includes('菜单') || allText.includes('menu') || el.getAttribute('aria-haspopup') === 'true') return 'more';
  if (allText.includes('发送') || allText.includes('send') || allText.includes('提交') || allText.includes('submit')) return 'send';
  if (allText.includes('切换') || allText.includes('switch') || allText.includes('模式') || allText.includes('mode')) return 'switch';
  if (allText.includes('正确') || allText.includes('correct')) return 'correct';
  if (allText.includes('错误') || allText.includes('error')) return 'error';
  if (allText.includes('可视化') || allText.includes('chart') || allText.includes('图表')) return 'visualize';
  if (allText.includes('信息') || allText.includes('info') || allText.includes('统计') || allText.includes('quota') || allText.includes('配额')) return 'info';
  if (allText.includes('停止') || allText.includes('stop') || allText.includes('pause')) return 'stop';
  if (allText.includes('发布') || allText.includes('publish') || allText.includes('共享') || allText.includes('share') || allText.includes('bi')) return 'publish';
  if (allText.includes('act') || allText.includes('执行') || allText.includes('run')) return 'act';
  if (allText.includes('chat') || allText.includes('对话') || allText.includes('聊天')) return 'chat';
  if (tag === 'BUTTON') return 'button';
  if (tag === 'A') return 'link';
  return 'other';
}

function hashString(input) {
  const text = String(input || '');
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash) + text.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash).toString(36);
}

function slugify(value, fallback = 'item') {
  const normalized = String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return normalized || fallback;
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isLikelyDynamicSegment(segment) {
  if (!segment) return false;
  if (/^\d{5,}$/.test(segment)) return true;
  if (/^[0-9a-f]{8,}$/i.test(segment)) return true;
  if (/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(segment)) return true;
  if (/^[a-z]+-\d+[a-z0-9-]*$/i.test(segment)) return true;
  if (/^(?:[a-z]+-)?[a-z0-9]{10,}$/i.test(segment) && /\d/.test(segment)) return true;

  const suffixMatch = segment.match(/^[a-z_-]+-(.+)$/i);
  if (suffixMatch) {
    const suffix = suffixMatch[1];
    if (/^\d{4,}$/.test(suffix)) return true;
    if (/^[0-9a-f]{8,}$/i.test(suffix)) return true;
    if (/^(?=.*[a-z])(?=.*\d)[a-z0-9-]{8,}$/i.test(suffix)) return true;
  }

  return false;
}

function normalizeRouteSegment(segment) {
  if (!segment) return segment;
  if (isLikelyDynamicSegment(segment)) {
    return ':id';
  }
  return segment;
}

function getEffectiveRoutePath() {
  const hash = window.location.hash || '';
  if (hash.startsWith('#/')) {
    return hash.slice(1).split('?')[0] || '/';
  }
  return window.location.pathname || '/';
}

function buildRoutePattern(routeKey) {
  const segments = String(routeKey || '/')
    .split('/')
    .filter(Boolean)
    .map((segment) => escapeRegExp(segment).replace(':id', '[^/]+'));

  return `^/${segments.join('/')}$`;
}

function getInteractiveNamesForSignature() {
  if (!window.DomUtils || typeof window.DomUtils.extractInteractiveElements !== 'function') {
    return [];
  }

  try {
    return window.DomUtils.extractInteractiveElements()
      .map((item) => item.name || item.role)
      .filter(Boolean)
      .slice(0, 10);
  } catch (error) {
    console.warn('生成 page_signature 时提取交互元素失败:', error);
    return [];
  }
}

function normalizeSignatureText(value, maxLength = 40) {
  return String(value || '')
    .replace(/[|=,]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .slice(0, maxLength);
}

function buildPageSignature(parts = {}) {
  const title = normalizeSignatureText(parts.title, 80);
  // 临时只保留 title，方便后端直接按标题匹配。
  // const headings = (parts.headings || []).map((item) => normalizeSignatureText(item, 40)).filter(Boolean);
  // const landmarks = (parts.landmarks || []).map((item) => normalizeSignatureText(item, 40)).filter(Boolean);
  // const interactiveNames = (parts.interactiveNames || []).map((item) => normalizeSignatureText(item, 24)).filter(Boolean);

  const signatureParts = [
    title ? `title=${title}` : ''
    // headings.length > 0 ? `headings=${headings.join(',')}` : '',
    // landmarks.length > 0 ? `landmarks=${landmarks.join(',')}` : '',
    // interactiveNames.length > 0 ? `actions=${interactiveNames.join(',')}` : ''
  ].filter(Boolean);

  return signatureParts.join('|') || 'title=untitled-page';
}

function buildPageIdentity() {
  const routePath = getEffectiveRoutePath();
  const segments = routePath.split('/').filter(Boolean);
  const normalizedSegments = segments.map(normalizeRouteSegment);
  const routeKey = `/${normalizedSegments.join('/') || ''}` || '/';
  const routePattern = buildRoutePattern(routeKey === '//' ? '/' : routeKey);

  const title = document.title || 'Untitled Page';
  const headings = Array.from(document.querySelectorAll('h1, h2'))
    .map((element) => (element.innerText || element.textContent || '').trim())
    .filter(Boolean)
    .slice(0, 2);
  const landmarks = Array.from(document.querySelectorAll('main, nav, header, aside, [role="main"], [role="navigation"], [role="banner"], [role="region"]'))
    .map((element) => {
      return (element.getAttribute('aria-label')
        || element.getAttribute('data-testid')
        || element.innerText
        || '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 40);
    })
    .filter(Boolean)
    .slice(0, 4);
  const interactiveNames = getInteractiveNamesForSignature();
  const pageSignature = buildPageSignature({
    title,
    headings,
    landmarks,
    interactiveNames
  });

  return {
    origin: window.location.origin || 'null',
    url: window.location.href,
    route_key: routeKey === '//' ? '/' : routeKey,
    route_pattern: routePattern,
    title,
    page_signature: pageSignature,
    signature_version: 'v2'
  };
}

function isVisibleSurfaceElement(element) {
  if (!element || element.nodeType !== Node.ELEMENT_NODE) return false;
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== 'none'
    && style.visibility !== 'hidden'
    && style.opacity !== '0'
    && rect.width > 0
    && rect.height > 0;
}

function isLikelyOverlaySurfaceRoot(element) {
  if (!isVisibleSurfaceElement(element)) return false;

  const role = (element.getAttribute('role') || '').toLowerCase();
  const ariaModal = element.getAttribute('aria-modal') === 'true';
  const tagName = element.tagName || '';
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  const viewportArea = Math.max((window.innerWidth || 1) * (window.innerHeight || 1), 1);
  const areaRatio = (rect.width * rect.height) / viewportArea;
  const isDialogLike = role === 'dialog' || role === 'alertdialog' || ariaModal;
  const inlineLikeDisplay = style.display === 'inline' || style.display === 'contents';
  const inlineLikeTag = ['SPAN', 'B', 'I', 'EM', 'STRONG', 'SMALL', 'LABEL'].includes(tagName);
  const isPositioned = ['fixed', 'absolute', 'sticky'].includes(style.position);

  if (isDialogLike) return true;
  if (inlineLikeDisplay || inlineLikeTag) return false;
  if (rect.width < 24 || rect.height < 24) return false;
  if (isPositioned) return true;

  return areaRatio >= 0.08;
}

function inferRuntimeSurfaceType(element) {
  const role = (element.getAttribute('role') || '').toLowerCase();
  const classText = `${element.className || ''}`.toLowerCase();
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);

  if (role === 'dialog' || role === 'alertdialog' || element.getAttribute('aria-modal') === 'true') {
    return 'dialog';
  }
  if (classText.includes('drawer')) {
    return 'drawer';
  }
  if (classText.includes('popover') || classText.includes('sheet')) {
    return 'popover';
  }
  if ((style.position === 'fixed' || style.position === 'absolute') && rect.width > window.innerWidth * 0.25) {
    if (rect.left <= 32 || rect.right >= window.innerWidth - 32) {
      return 'drawer';
    }
  }
  return 'dialog';
}

function buildRuntimeSurface(element, forcedType) {
  const type = forcedType || inferRuntimeSurfaceType(element);
  const ariaLabel = element.getAttribute('aria-label') || '';
  const dataTestId = element.getAttribute('data-testid') || '';
  const classTokens = `${element.className || ''}`
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .slice(0, 4);
  const textSignature = (element.innerText || element.textContent || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 60);
  const zIndex = Number.parseInt(window.getComputedStyle(element).zIndex, 10) || 0;
  const surfaceName = ariaLabel || dataTestId || classTokens[0] || textSignature || 'surface';

  return {
    root: element,
    type,
    surfaceKey: type === 'main' ? 'main' : `${type}:${slugify(surfaceName, type)}`,
    role: (element.getAttribute('role') || '').toLowerCase(),
    ariaLabel,
    dataTestId,
    classTokens,
    textSignature: textSignature.toLowerCase(),
    zIndex
  };
}

function getOverlaySurfaceScore(surface) {
  if (!surface?.root || surface.type === 'main') return Number.NEGATIVE_INFINITY;

  const element = surface.root;
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  const viewportArea = Math.max((window.innerWidth || 1) * (window.innerHeight || 1), 1);
  const areaRatio = Math.min((rect.width * rect.height) / viewportArea, 1);
  const tagName = element.tagName || '';
  let score = 0;

  if (surface.role === 'dialog' || surface.role === 'alertdialog') score += 60;
  if (element.getAttribute('aria-modal') === 'true') score += 60;
  if (['fixed', 'absolute', 'sticky'].includes(style.position)) score += 20;
  if (style.display !== 'inline' && style.display !== 'contents') score += 10;
  if (surface.type === 'drawer') score += 12;
  if (surface.type === 'popover') score += 8;
  if (['DIV', 'ASIDE', 'SECTION', 'UL', 'OL', 'NAV'].includes(tagName)) score += 6;
  if (['SPAN', 'B', 'I', 'EM', 'STRONG', 'SMALL', 'LABEL'].includes(tagName)) score -= 50;
  if (rect.width < 24 || rect.height < 24) score -= 30;
  score += Math.round(areaRatio * 40);

  return score;
}

function detectRuntimeSurfaces() {
  const runtimeSurfaces = [];
  const mainRoot = document.querySelector('main, [role="main"]') || document.body;
  runtimeSurfaces.push(buildRuntimeSurface(mainRoot, 'main'));

  const candidates = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"], [class*="dialog"], [class*="modal"], [class*="drawer"], [class*="popover"], [class*="sheet"]'));
  const visited = new Set();

  candidates.forEach((candidate) => {
    if (!isLikelyOverlaySurfaceRoot(candidate)) return;
    if (visited.has(candidate)) return;
    visited.add(candidate);
    runtimeSurfaces.push(buildRuntimeSurface(candidate));
  });

  return runtimeSurfaces.sort((a, b) => {
    const scoreDiff = getOverlaySurfaceScore(b) - getOverlaySurfaceScore(a);
    if (scoreDiff !== 0) return scoreDiff;
    return b.zIndex - a.zIndex;
  });
}

function getActiveRuntimeSurface(runtimeSurfaces) {
  const overlaySurface = runtimeSurfaces
    .filter((surface) => surface.type !== 'main' && isLikelyOverlaySurfaceRoot(surface.root))
    .map((surface) => ({ surface, score: getOverlaySurfaceScore(surface) }))
    .filter(({ score }) => score >= 20)
    .sort((a, b) => b.score - a.score || b.surface.zIndex - a.surface.zIndex)[0];

  if (overlaySurface) return overlaySurface.surface;
  return runtimeSurfaces.find((surface) => surface.type === 'main') || runtimeSurfaces[0] || null;
}

function isElementInsideSurface(element, surface) {
  if (!element || !surface?.root) return false;
  if (surface.type === 'main') {
    return true;
  }
  return surface.root === element || surface.root.contains(element);
}

function shouldRenderForActiveSurface(runtimeSurface, activeRuntimeSurface) {
  if (!runtimeSurface) return false;
  if (!activeRuntimeSurface) return true;
  if (activeRuntimeSurface.type === 'main') {
    return runtimeSurface.type === 'main';
  }
  return runtimeSurface.root === activeRuntimeSurface.root;
}

function matchSurfaceToRuntime(surface, runtimeSurfaces) {
  if (!surface || surface.surface_key === 'main' || surface.type === 'main') {
    return runtimeSurfaces.find((item) => item.type === 'main') || runtimeSurfaces[0] || null;
  }

  let bestMatch = null;
  let bestScore = -1;
  const hints = surface.activation_hints || {};

  runtimeSurfaces.forEach((runtimeSurface) => {
    let score = 0;
    if (surface.type && runtimeSurface.type === surface.type) score += 3;
    if (hints.role && runtimeSurface.role === hints.role) score += 3;
    if (hints.aria_label && runtimeSurface.ariaLabel.includes(hints.aria_label)) score += 3;
    if (Array.isArray(hints.data_testids) && hints.data_testids.some((token) => runtimeSurface.dataTestId === token)) score += 3;
    if (Array.isArray(hints.class_tokens)) {
      score += hints.class_tokens.filter((token) => runtimeSurface.classTokens.includes(token)).length;
    }
    if (hints.text_signature && runtimeSurface.textSignature.includes(String(hints.text_signature).toLowerCase())) score += 1;
    if (surface.surface_key && runtimeSurface.surfaceKey === surface.surface_key) score += 2;

    if (score > bestScore) {
      bestScore = score;
      bestMatch = runtimeSurface;
    }
  });

  return bestScore > 0 ? bestMatch : null;
}

function getRegionStatusColor(status) {
  const statusColors = {
    active: '#22c55e',
    added: '#2563eb',
    modified: '#f59e0b',
    unmatched: '#8b5cf6'
  };

  return statusColors[status] || statusColors.active;
}

function normalizeMarkerComparableText(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function limitMarkerComparableText(value, maxLength = 120) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, maxLength);
}

function escapeMarkerSelectorIdentifier(value) {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
    return CSS.escape(String(value || ''));
  }
  return String(value || '').replace(/[^a-zA-Z0-9_-]/g, '\\$&');
}

function escapeMarkerSelectorAttributeValue(value) {
  return String(value || '')
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"');
}

function getElementClassTokens(element, maxTokens = 4) {
  if (!(element instanceof Element)) return [];
  return Array.from(element.classList || [])
    .map((token) => String(token || '').trim())
    .filter((token) => token && !token.startsWith('playwright-') && token.length <= 60)
    .slice(0, maxTokens);
}

function uniqueMarkerValues(values = []) {
  return Array.from(new Set(
    values
      .map((value) => String(value || '').trim())
      .filter(Boolean)
  ));
}

function getElementSiblingIndex(element) {
  if (!(element instanceof Element) || !(element.parentElement instanceof Element)) return null;
  const tagName = (element.tagName || '').toLowerCase();
  if (!tagName) return null;

  let index = 0;
  for (const sibling of Array.from(element.parentElement.children)) {
    if ((sibling.tagName || '').toLowerCase() !== tagName) continue;
    index += 1;
    if (sibling === element) {
      return index;
    }
  }

  return index || null;
}

function buildElementParentChain(element, maxDepth = 2) {
  const parentChain = [];
  let current = element instanceof Element ? element.parentElement : null;

  while (current && current !== document.body && parentChain.length < maxDepth) {
    parentChain.push({
      tag_name: (current.tagName || '').toLowerCase(),
      role: current.getAttribute('role') || '',
      aria_label: current.getAttribute('aria-label') || '',
      class_tokens: getElementClassTokens(current, 3),
      text_hint: limitMarkerComparableText(
        current.getAttribute('aria-label')
        || current.getAttribute('title')
        || current.innerText
        || current.textContent
        || '',
        60
      )
    });

    current = current.parentElement;
  }

  return parentChain;
}

function buildElementSelectorCandidates(element) {
  if (!(element instanceof Element)) return [];

  const tagName = (element.tagName || '').toLowerCase();
  if (!tagName) return [];

  const candidates = [];
  const dataAiId = element.getAttribute('data-ai-id') || '';
  const elementId = element.id || '';
  const dataTestId = element.getAttribute('data-testid') || '';
  const ariaLabel = element.getAttribute('aria-label') || '';
  const role = element.getAttribute('role') || '';
  const classTokens = getElementClassTokens(element, 3);

  if (dataAiId) {
    const escapedDataAiId = escapeMarkerSelectorAttributeValue(dataAiId);
    candidates.push(`[data-ai-id="${escapedDataAiId}"]`);
    candidates.push(`${tagName}[data-ai-id="${escapedDataAiId}"]`);
  }

  if (elementId) {
    candidates.push(`#${escapeMarkerSelectorIdentifier(elementId)}`);
  }

  if (dataTestId) {
    const escapedDataTestId = escapeMarkerSelectorAttributeValue(dataTestId);
    candidates.push(`[data-testid="${escapedDataTestId}"]`);
    candidates.push(`${tagName}[data-testid="${escapedDataTestId}"]`);
  }

  if (ariaLabel) {
    const escapedAriaLabel = escapeMarkerSelectorAttributeValue(ariaLabel);
    candidates.push(`[aria-label="${escapedAriaLabel}"]`);
    candidates.push(`${tagName}[aria-label="${escapedAriaLabel}"]`);
    if (role) {
      candidates.push(`[role="${escapeMarkerSelectorAttributeValue(role)}"][aria-label="${escapedAriaLabel}"]`);
    }
  }

  if (classTokens.length > 0) {
    candidates.push(
      `${tagName}${classTokens.slice(0, 3).map((token) => `.${escapeMarkerSelectorIdentifier(token)}`).join('')}`
    );
    candidates.push(
      `${tagName}${classTokens.slice(0, 1).map((token) => `.${escapeMarkerSelectorIdentifier(token)}`).join('')}`
    );
  }

  const siblingIndex = getElementSiblingIndex(element);
  const parent = element.parentElement;
  if (parent instanceof Element && siblingIndex) {
    if (parent.id) {
      candidates.push(`#${escapeMarkerSelectorIdentifier(parent.id)} > ${tagName}:nth-of-type(${siblingIndex})`);
    }
    const parentTestId = parent.getAttribute('data-testid') || '';
    if (parentTestId) {
      candidates.push(`[data-testid="${escapeMarkerSelectorAttributeValue(parentTestId)}"] > ${tagName}:nth-of-type(${siblingIndex})`);
    }
  }

  return uniqueMarkerValues(candidates);
}

function buildElementAnchorSnapshot(element, options = {}) {
  if (!(element instanceof Element)) return null;

  const tagName = (element.tagName || '').toLowerCase();
  const visibleText = limitMarkerComparableText(element.innerText || element.textContent || '');
  const ariaLabel = limitMarkerComparableText(element.getAttribute('aria-label') || '');
  const title = limitMarkerComparableText(element.getAttribute('title') || '');
  const placeholder = limitMarkerComparableText(element.getAttribute('placeholder') || '');
  const alt = limitMarkerComparableText(element.getAttribute('alt') || '');
  const accessibleName = limitMarkerComparableText(
    options.name
    || element.getAttribute('aria-name')
    || element.getAttribute('name')
    || ariaLabel
    || title
    || placeholder
    || alt
    || visibleText
  );

  return {
    stable_attributes: {
      'data-ai-id': element.getAttribute('data-ai-id') || null,
      id: element.id || null,
      'data-testid': element.getAttribute('data-testid') || null,
      'aria-label': element.getAttribute('aria-label') || null,
      role: element.getAttribute('role') || options.role || null,
      title: element.getAttribute('title') || null,
      placeholder: element.getAttribute('placeholder') || null,
      name: element.getAttribute('name') || null,
      type: element.getAttribute('type') || null
    },
    selector_candidates: buildElementSelectorCandidates(element),
    text_signature: {
      exact: accessibleName,
      normalized: accessibleName,
      accessible_name: accessibleName,
      visible_text: visibleText,
      title,
      placeholder,
      alt
    },
    dom_signature: {
      tag_name: tagName,
      class_tokens: getElementClassTokens(element, 4),
      sibling_index: getElementSiblingIndex(element),
      parent_chain: buildElementParentChain(element, 2)
    },
    extractor_version: 'anchor_v2'
  };
}

function buildRegionComparableBoxFromRawRegion(region) {
  const rawRegion = region?.region || {};
  const rawWidth = Number(rawRegion.width) || 0;
  const rawHeight = Number(rawRegion.height) || 0;
  if (
    Number.isFinite(Number(rawRegion.left))
    && Number.isFinite(Number(rawRegion.top))
    && rawWidth > 0
    && rawHeight > 0
  ) {
    return {
      x: Number(rawRegion.left) || 0,
      y: Number(rawRegion.top) || 0,
      width: rawWidth,
      height: rawHeight
    };
  }

  return null;
}

function buildRegionComparableBoxFromNormalized(region) {
  const normalizedBox = region?.normalized_box || {};
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  if (!viewportWidth || !viewportHeight) return null;

  const width = Math.round((Number(normalizedBox.width_ratio) || 0) * viewportWidth);
  const height = Math.round((Number(normalizedBox.height_ratio) || 0) * viewportHeight);
  if (width <= 0 || height <= 0) return null;

  return {
    x: Math.round((Number(normalizedBox.left_ratio) || 0) * viewportWidth),
    y: Math.round((Number(normalizedBox.top_ratio) || 0) * viewportHeight),
    width,
    height
  };
}

function getRegionComparableBoxes(region) {
  const boxes = [
    buildRegionComparableBoxFromRawRegion(region),
    buildRegionComparableBoxFromNormalized(region)
  ].filter(Boolean);

  return boxes.filter((box, index) => (
    boxes.findIndex((candidate) => isSameMarkerBox(candidate, box)) === index
  ));
}

function getRegionComparableBox(region) {
  return getRegionComparableBoxes(region)[0] || null;
}

function isSameMarkerBox(boxA, boxB) {
  if (!boxA || !boxB) return false;
  return Math.abs((boxA.x || 0) - (boxB.x || 0)) <= 2
    && Math.abs((boxA.y || 0) - (boxB.y || 0)) <= 2
    && Math.abs((boxA.width || 0) - (boxB.width || 0)) <= 2
    && Math.abs((boxA.height || 0) - (boxB.height || 0)) <= 2;
}

function isRelatedMarkerText(textA, textB) {
  if (!textA || !textB) return false;
  return textA === textB || textA.includes(textB) || textB.includes(textA);
}

function collectComparableRegionTextHints(region) {
  const normalizedHints = new Set();
  const rawHints = [
    region?.anchor?.text_signature?.exact,
    region?.anchor?.text_signature?.normalized,
    region?.anchor?.text_signature?.accessible_name,
    region?.anchor?.text_signature?.visible_text,
    region?.anchor?.text_signature?.title,
    region?.anchor?.text_signature?.placeholder,
    region?.anchor?.text_signature?.alt,
    region?.anchor?.stable_attributes?.['aria-label'],
    region?.element_name,
    region?.element_code,
    region?.semantic_context?.element
  ];

  rawHints.forEach((value) => {
    const normalized = normalizeMarkerComparableText(value);
    if (!normalized) return;
    normalizedHints.add(normalized);

    const withoutRolePrefix = normalizeMarkerComparableText(
      normalized.replace(/^(button|link|input|checkbox|radio|menuitem|tab|switch|element)\s*:\s*/i, '')
    );
    if (withoutRolePrefix) {
      normalizedHints.add(withoutRolePrefix);
    }

    const withoutGenericSuffix = normalizeMarkerComparableText(
      withoutRolePrefix.replace(/(按钮|链接|图标|icon|button|link)$/i, '')
    );
    if (withoutGenericSuffix) {
      normalizedHints.add(withoutGenericSuffix);
    }
  });

  return Array.from(normalizedHints);
}

function getElementComparableText(element) {
  if (!element) return '';
  return normalizeMarkerComparableText(
    element.getAttribute?.('aria-label')
    || element.getAttribute?.('name')
    || element.getAttribute?.('title')
    || element.getAttribute?.('placeholder')
    || element.innerText
    || element.textContent
    || ''
  );
}

function getElementComparableBox(element) {
  if (!element || typeof element.getBoundingClientRect !== 'function') return null;
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  return {
    x: Math.round(rect.x || 0),
    y: Math.round(rect.y || 0),
    width: Math.round(rect.width || 0),
    height: Math.round(rect.height || 0)
  };
}

function inferElementComparableRole(element) {
  if (!element) return '';

  const explicitRole = normalizeMarkerComparableText(element.getAttribute?.('role') || '');
  if (explicitRole) return explicitRole;

  const tagName = (element.tagName || '').toUpperCase();
  if (tagName === 'A') return 'link';
  if (tagName === 'BUTTON') return 'button';
  if (tagName === 'INPUT') return 'input';
  if (tagName === 'SELECT') return 'select';
  if (tagName === 'TEXTAREA') return 'textarea';
  if (hasInlineInteractionHandler(element) || hasActionLikeAttributes(element)) return 'button';

  const computed = window.getComputedStyle(element);
  if (computed.cursor === 'pointer') return 'button';
  return '';
}

function getRegionElementBoxScore(regionBox, elementBox) {
  if (!regionBox || !elementBox) return 0;

  const regionCenterX = regionBox.x + (regionBox.width / 2);
  const regionCenterY = regionBox.y + (regionBox.height / 2);
  const elementCenterX = elementBox.x + (elementBox.width / 2);
  const elementCenterY = elementBox.y + (elementBox.height / 2);
  const centerDistance = Math.hypot(regionCenterX - elementCenterX, regionCenterY - elementCenterY);
  const maxCenterDistance = Math.max(18, Math.min(regionBox.width, regionBox.height) * 0.8);
  const widthDiffRatio = Math.abs(elementBox.width - regionBox.width) / Math.max(regionBox.width, 1);
  const heightDiffRatio = Math.abs(elementBox.height - regionBox.height) / Math.max(regionBox.height, 1);

  let score = 0;
  if (centerDistance <= maxCenterDistance) {
    score += 4;
  }
  if (widthDiffRatio <= 0.35 && heightDiffRatio <= 0.35) {
    score += 4;
  } else if (widthDiffRatio <= 0.8 && heightDiffRatio <= 0.8) {
    score += 2;
  }
  if (isSameMarkerBox(regionBox, elementBox)) {
    score += 2;
  }

  return score;
}

function getRegionElementDomScore(element, region) {
  if (!(element instanceof Element)) return 0;

  const domSignature = region?.anchor?.dom_signature || {};
  const regionTagName = normalizeMarkerComparableText(domSignature.tag_name || '');
  const regionClassTokens = Array.isArray(domSignature.class_tokens)
    ? domSignature.class_tokens.map((token) => normalizeMarkerComparableText(token)).filter(Boolean)
    : [];
  const regionSiblingIndex = Number.isFinite(Number(domSignature.sibling_index))
    ? Number(domSignature.sibling_index)
    : null;
  const regionParentChain = Array.isArray(domSignature.parent_chain) ? domSignature.parent_chain : [];

  let score = 0;
  if (regionTagName && regionTagName === normalizeMarkerComparableText((element.tagName || '').toLowerCase())) {
    score += 3;
  }

  const elementClassTokens = getElementClassTokens(element, 6).map((token) => normalizeMarkerComparableText(token));
  const classOverlap = regionClassTokens.filter((token) => elementClassTokens.includes(token)).length;
  score += Math.min(classOverlap * 2, 6);

  if (regionSiblingIndex && getElementSiblingIndex(element) === regionSiblingIndex) {
    score += 1;
  }

  const elementParentChain = buildElementParentChain(element, regionParentChain.length || 2);
  regionParentChain.forEach((parentHint, index) => {
    const elementParent = elementParentChain[index];
    if (!elementParent) return;

    if (
      normalizeMarkerComparableText(parentHint?.tag_name || '')
      && normalizeMarkerComparableText(parentHint.tag_name) === normalizeMarkerComparableText(elementParent.tag_name || '')
    ) {
      score += 1;
    }

    if (
      normalizeMarkerComparableText(parentHint?.role || '')
      && normalizeMarkerComparableText(parentHint.role) === normalizeMarkerComparableText(elementParent.role || '')
    ) {
      score += 1;
    }

    const parentClassTokens = Array.isArray(parentHint?.class_tokens)
      ? parentHint.class_tokens.map((token) => normalizeMarkerComparableText(token)).filter(Boolean)
      : [];
    const elementParentClassTokens = Array.isArray(elementParent.class_tokens)
      ? elementParent.class_tokens.map((token) => normalizeMarkerComparableText(token)).filter(Boolean)
      : [];
    const parentClassOverlap = parentClassTokens.filter((token) => elementParentClassTokens.includes(token)).length;
    score += Math.min(parentClassOverlap, 2);

    const parentAriaLabel = normalizeMarkerComparableText(parentHint?.aria_label || '');
    const elementParentAriaLabel = normalizeMarkerComparableText(elementParent.aria_label || '');
    if (parentAriaLabel && elementParentAriaLabel && isRelatedMarkerText(parentAriaLabel, elementParentAriaLabel)) {
      score += 1;
    }

    const parentTextHint = normalizeMarkerComparableText(parentHint?.text_hint || '');
    const elementParentText = normalizeMarkerComparableText(elementParent.text_hint || '');
    if (parentTextHint && elementParentText && isRelatedMarkerText(parentTextHint, elementParentText)) {
      score += 1;
    }
  });

  return score;
}

function collectRegionDomSignatureCandidates(region, root) {
  const domSignature = region?.anchor?.dom_signature || {};
  const searchRoot = root || document;
  const candidates = [];
  const candidateSelectors = new Set();
  const tagName = String(domSignature.tag_name || '').toLowerCase();
  const classTokens = Array.isArray(domSignature.class_tokens) ? domSignature.class_tokens.filter(Boolean) : [];

  if (tagName) {
    candidateSelectors.add(tagName);
  }

  if (tagName && classTokens.length > 0) {
    candidateSelectors.add(
      `${tagName}${classTokens.slice(0, 3).map((token) => `.${escapeMarkerSelectorIdentifier(token)}`).join('')}`
    );
    candidateSelectors.add(
      `${tagName}${classTokens.slice(0, 1).map((token) => `.${escapeMarkerSelectorIdentifier(token)}`).join('')}`
    );
  }

  classTokens.slice(0, 2).forEach((token) => {
    candidateSelectors.add(`.${escapeMarkerSelectorIdentifier(token)}`);
  });

  candidateSelectors.forEach((selector) => {
    try {
      searchRoot.querySelectorAll(selector).forEach((element) => candidates.push(element));
    } catch (error) {
      console.warn('忽略非法 dom signature selector:', selector, error);
    }
  });

  return candidates;
}

function findRegionElementByPreviewNodeId(region, root) {
  const previewNodeId = String(region?.preview_node_id || '').trim();
  if (!previewNodeId) return null;

  const searchRoots = [];
  if (root && root !== document.body) {
    searchRoots.push(root);
  }
  searchRoots.push(document);

  const selector = `[data-cdp-extracted-id="${escapeMarkerSelectorAttributeValue(previewNodeId)}"]`;
  for (const searchRoot of searchRoots) {
    const target = searchRoot.querySelector?.(selector) || null;
    if (target instanceof Element) {
      return target;
    }
  }

  return null;
}

function getRegionElementCandidateScore(element, region) {
  if (!(element instanceof Element)) return Number.NEGATIVE_INFINITY;
  if (element.matches?.(MARKER_UI_SELECTOR) || element.closest?.(MARKER_UI_SELECTOR)) {
    return Number.NEGATIVE_INFINITY;
  }

  const stable = region?.anchor?.stable_attributes || {};
  const regionTextHints = collectComparableRegionTextHints(region);
  const elementText = getElementComparableText(element);
  const elementBox = getElementComparableBox(element);
  const regionBoxes = getRegionComparableBoxes(region);
  const regionRole = normalizeMarkerComparableText(stable.role || region?.control_type || '');
  const elementRole = inferElementComparableRole(element);
  const stableAriaLabel = normalizeMarkerComparableText(stable['aria-label'] || '');
  const elementAriaLabel = normalizeMarkerComparableText(element.getAttribute?.('aria-label') || '');
  const stableDataAiId = stable['data-ai-id'] || '';
  const stableDataTestId = stable['data-testid'] || '';

  let score = 0;

  if (stableDataAiId && element.getAttribute?.('data-ai-id') === stableDataAiId) {
    score += 120;
  }
  if (stable.id && element.id === stable.id) {
    score += 100;
  }
  if (stableDataTestId && element.getAttribute?.('data-testid') === stableDataTestId) {
    score += 80;
  }

  if (stableAriaLabel && elementAriaLabel) {
    if (stableAriaLabel === elementAriaLabel) {
      score += 20;
    } else if (isRelatedMarkerText(stableAriaLabel, elementAriaLabel)) {
      score += 12;
    }
  }

  if (regionRole && elementRole && regionRole === elementRole) {
    score += 4;
  }

  let textScore = 0;
  regionTextHints.forEach((hint) => {
    if (!hint || !elementText) return;
    if (hint === elementText) {
      textScore = Math.max(textScore, 14);
      return;
    }
    if (isRelatedMarkerText(hint, elementText)) {
      textScore = Math.max(textScore, 10);
    }
  });
  score += textScore;
  score += getRegionElementDomScore(element, region);

  let bestRegionBox = null;
  let boxScore = 0;
  regionBoxes.forEach((regionBox) => {
    const currentScore = getRegionElementBoxScore(regionBox, elementBox);
    if (currentScore >= boxScore) {
      boxScore = currentScore;
      bestRegionBox = regionBox;
    }
  });
  score += boxScore;

  if (bestRegionBox && elementBox) {
    const regionArea = Math.max(1, bestRegionBox.width * bestRegionBox.height);
    const elementArea = Math.max(1, elementBox.width * elementBox.height);
    const areaRatio = elementArea / regionArea;
    if (areaRatio > 12 && boxScore < 6 && textScore < 10) {
      score -= 8;
    }
  }

  return score;
}

function pickBestRegionElementCandidate(elements, region, minScore = 8) {
  const seen = new Set();
  let bestCandidate = null;

  elements.forEach((element) => {
    if (!(element instanceof Element) || seen.has(element)) return;
    seen.add(element);

    const score = getRegionElementCandidateScore(element, region);
    if (!bestCandidate || score > bestCandidate.score) {
      bestCandidate = { element, score };
    }
  });

  if (!bestCandidate || bestCandidate.score < minScore) {
    return null;
  }
  return bestCandidate;
}

function collectElementChainWithinRoot(element, root) {
  const chain = [];
  let current = element instanceof Element ? element : null;
  const scopedRoot = root instanceof Element ? root : null;

  while (current) {
    if (!current.closest?.(MARKER_UI_SELECTOR)) {
      chain.push(current);
    }
    if (scopedRoot && current === scopedRoot) break;
    if (!scopedRoot && current === document.body) break;
    current = current.parentElement;
  }

  return chain;
}

function findMatchingRegionForNode(node, regions = []) {
  const nodeId = String(node?.id || '');
  const nodeSelectorCandidates = uniqueMarkerValues([
    node?.selector,
    ...(Array.isArray(node?.anchor?.selector_candidates) ? node.anchor.selector_candidates : [])
  ]);
  const nodeElementId = String(node?.elementId || node?.anchor?.stable_attributes?.id || '');
  const nodeName = normalizeMarkerComparableText(
    node?.name
    || node?.anchor?.text_signature?.accessible_name
    || node?.anchor?.text_signature?.visible_text
    || node?.role
    || ''
  );
  const nodeBox = node?.box || null;

  return regions.find((region) => {
    if (region?.status === 'deleted') return false;

    const regionNumber = String(region?.region_number || '');
    if (nodeId && regionNumber && nodeId === regionNumber) return true;

    const regionPreviewNodeId = String(region?.preview_node_id || '');
    if (nodeId && regionPreviewNodeId && nodeId === regionPreviewNodeId) return true;

    const stableId = String(region?.anchor?.stable_attributes?.id || region?.element_dom_id || '');
    if (nodeElementId && stableId && nodeElementId === stableId) return true;

    const selectorCandidates = Array.isArray(region?.anchor?.selector_candidates)
      ? region.anchor.selector_candidates.filter(Boolean).map(String)
      : [];
    if (nodeSelectorCandidates.some((candidate) => selectorCandidates.includes(candidate))) return true;

    const regionText = normalizeMarkerComparableText(
      region?.anchor?.text_signature?.normalized
      || region?.element_name
      || region?.element_code
      || ''
    );
    if (nodeName && regionText && isRelatedMarkerText(nodeName, regionText)) {
      if (!nodeBox) return true;
      const regionBoxes = getRegionComparableBoxes(region);
      if (regionBoxes.some((regionBox) => isSameMarkerBox(nodeBox, regionBox))) return true;
    }

    if (nodeBox) {
      const regionBoxes = getRegionComparableBoxes(region);
      if (regionBoxes.some((regionBox) => isSameMarkerBox(nodeBox, regionBox))) return true;
    }

    return false;
  }) || null;
}

function findRegionElementByAnchor(region, root) {
  const anchor = region.anchor || {};
  const stable = anchor.stable_attributes || {};
  const searchRoot = root || document;

  const byPreviewNodeId = findRegionElementByPreviewNodeId(region, root);
  if (byPreviewNodeId) {
    return byPreviewNodeId;
  }

  if (stable.id) {
    const byId = document.getElementById(stable.id);
    if (byId && (!root || root === document.body || root.contains(byId))) {
      return byId;
    }
  }

  if (stable['data-ai-id']) {
    const byAiIds = Array.from(searchRoot.querySelectorAll(`[data-ai-id="${escapeMarkerSelectorAttributeValue(stable['data-ai-id'])}"]`));
    const matchedByAiId = pickBestRegionElementCandidate(byAiIds, region, 12);
    if (matchedByAiId?.element) return matchedByAiId.element;
  }

  if (stable['data-testid']) {
    const byTestIds = Array.from(searchRoot.querySelectorAll(`[data-testid="${stable['data-testid']}"]`));
    const matchedByTestId = pickBestRegionElementCandidate(byTestIds, region, 10);
    if (matchedByTestId?.element) return matchedByTestId.element;
  }

  if (stable['aria-label']) {
    const roleSelector = stable.role ? `[role="${stable.role}"]` : '';
    const ariaSelector = `${roleSelector}[aria-label="${stable['aria-label']}"], [aria-label="${stable['aria-label']}"]`;
    const byAriaCandidates = Array.from(searchRoot.querySelectorAll(ariaSelector));
    const matchedByAria = pickBestRegionElementCandidate(byAriaCandidates, region, 10);
    if (matchedByAria?.element) return matchedByAria.element;
  }

  const selectorCandidates = Array.isArray(anchor.selector_candidates) ? anchor.selector_candidates : [];
  const selectorMatchedElements = [];
  for (const candidate of selectorCandidates) {
    if (!candidate) continue;
    try {
      searchRoot.querySelectorAll(candidate).forEach((element) => selectorMatchedElements.push(element));
    } catch (error) {
      console.warn('忽略非法 selector candidate:', candidate, error);
    }
  }
  const matchedBySelector = pickBestRegionElementCandidate(selectorMatchedElements, region, 8);
  if (matchedBySelector?.element) return matchedBySelector.element;

  const domSignatureCandidates = collectRegionDomSignatureCandidates(region, searchRoot);
  const matchedByDomSignature = pickBestRegionElementCandidate(domSignatureCandidates, region, 9);
  if (matchedByDomSignature?.element) return matchedByDomSignature.element;

  const textHints = collectComparableRegionTextHints(region);
  if (textHints.length > 0) {
    const textCandidates = Array.from(searchRoot.querySelectorAll('button, a, input, select, textarea, [role], [aria-label], [data-ai-id], [data-testid], [tabindex]:not([tabindex="-1"]), [onclick], [data-action], [data-click], [data-toggle], [aria-haspopup="true"], .button, .dropdown-toggle'))
      .filter((element) => {
        const text = getElementComparableText(element);
        return textHints.some((hint) => hint && text && isRelatedMarkerText(hint, text));
      });
    const matchedByText = pickBestRegionElementCandidate(textCandidates, region, 12);
    if (matchedByText?.element) return matchedByText.element;
  }

  const regionBoxes = getRegionComparableBoxes(region);
  for (const regionBox of regionBoxes) {
    const centerX = regionBox.x + (regionBox.width / 2);
    const centerY = regionBox.y + (regionBox.height / 2);
    if (!Number.isFinite(centerX) || !Number.isFinite(centerY) || centerX < 0 || centerY < 0) {
      continue;
    }

    const byPoint = document.elementFromPoint(centerX, centerY);
    if (!byPoint || (root && root !== document.body && !root.contains(byPoint))) {
      continue;
    }

    const pointCandidates = collectElementChainWithinRoot(byPoint, root);
    const matchedByPoint = pickBestRegionElementCandidate(pointCandidates, region, 8);
    if (matchedByPoint?.element) {
      logMarkerDebug('findRegionElementByAnchor.point_fallback_matched', {
        region: summarizeRegionForMarkerDebug(region),
        regionBox,
        score: matchedByPoint.score,
        element: summarizeElementForMarkerDebug(matchedByPoint.element)
      });
      return matchedByPoint.element;
    }

    logMarkerDebug('findRegionElementByAnchor.point_fallback_rejected', {
      region: summarizeRegionForMarkerDebug(region),
      regionBox,
      pointElement: summarizeElementForMarkerDebug(byPoint),
      pointCandidates: pointCandidates.slice(0, 4).map((element) => ({
        score: getRegionElementCandidateScore(element, region),
        element: summarizeElementForMarkerDebug(element)
      }))
    });
  }

  return null;
}

let trackedRegionMarkers = [];
let trackedRegionAnimFrameId = null;
let trackedDocumentPayload = null;
let trackedSurfaceObserver = null;
let trackedRenderMuted = false;
let trackedRenderTimer = null;
const MARKER_UI_SELECTOR = '[data-ui-marker], #ui-selection-overlay, #selection-box';
const EXISTING_ANNOTATION_SELECTOR = '[data-tracking-region-id], [data-tracking-region-number], [data-cdp-extracted-id]';

function hasExistingAnnotation(element) {
  return !!element?.closest?.(EXISTING_ANNOTATION_SELECTOR);
}

function getMaxAnnotatedMarkerNumber() {
  const annotatedElements = document.querySelectorAll('[data-tracking-region-number], [data-cdp-extracted-id]');
  let maxNumber = 0;

  annotatedElements.forEach((element) => {
    const trackingNumber = Number(element.getAttribute('data-tracking-region-number'));
    const extractedNumber = Number(element.getAttribute('data-cdp-extracted-id'));
    const currentNumber = Math.max(
      Number.isFinite(trackingNumber) ? trackingNumber : 0,
      Number.isFinite(extractedNumber) ? extractedNumber : 0
    );

    if (currentNumber > maxNumber) {
      maxNumber = currentNumber;
    }
  });

  return maxNumber;
}

function getSampledViewportPoints(rect) {
  if (!rect || rect.width <= 0 || rect.height <= 0) return [];

  const insetX = Math.min(12, rect.width / 4);
  const insetY = Math.min(12, rect.height / 4);
  const rawPoints = [
    { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
    { x: rect.left + insetX, y: rect.top + insetY },
    { x: rect.right - insetX, y: rect.top + insetY },
    { x: rect.left + insetX, y: rect.bottom - insetY },
    { x: rect.right - insetX, y: rect.bottom - insetY }
  ];

  return rawPoints.filter(({ x, y }) => (
    Number.isFinite(x)
    && Number.isFinite(y)
    && x >= 0
    && y >= 0
    && x <= window.innerWidth
    && y <= window.innerHeight
  ));
}

function getTopRenderableElementAtPoint(x, y) {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;

  const candidates = typeof document.elementsFromPoint === 'function'
    ? document.elementsFromPoint(x, y)
    : [document.elementFromPoint(x, y)].filter(Boolean);

  return candidates.find((element) => (
    element instanceof Element
    && !element.closest(MARKER_UI_SELECTOR)
  )) || null;
}

function isElementVisuallyReachable(targetEl) {
  if (!targetEl?.isConnected) return false;
  const rect = targetEl.getBoundingClientRect();
  const points = getSampledViewportPoints(rect);
  if (points.length === 0) return false;

  return points.some(({ x, y }) => {
    const topElement = getTopRenderableElementAtPoint(x, y);
    if (!topElement) return false;
    return topElement === targetEl
      || targetEl.contains(topElement)
      || topElement.contains(targetEl);
  });
}

function setTrackedMarkerVisibility(marker, shouldShow) {
  if (!marker) return;

  if (marker.targetEl) {
    marker.targetEl.style.outline = shouldShow ? (marker.markerOutline || '') : (marker.previousOutline || '');
    marker.targetEl.style.outlineOffset = shouldShow ? (marker.markerOutlineOffset || '') : (marker.previousOutlineOffset || '');
  }

  if (marker.labelEl) {
    marker.labelEl.style.display = shouldShow ? 'flex' : 'none';
  }

  marker.isVisible = shouldShow;
}

function clearTrackedRegionMarkers() {
  if (trackedRegionAnimFrameId) {
    cancelAnimationFrame(trackedRegionAnimFrameId);
    trackedRegionAnimFrameId = null;
  }

  trackedRegionMarkers.forEach((marker) => {
    if (marker.labelEl?.isConnected) marker.labelEl.remove();
    if (marker.targetEl?.isConnected) {
      marker.targetEl.style.outline = marker.previousOutline || '';
      marker.targetEl.style.outlineOffset = marker.previousOutlineOffset || '';
      marker.targetEl.removeAttribute('data-tracking-region-id');
      marker.targetEl.removeAttribute('data-tracking-region-number');
      marker.targetEl.removeAttribute('data-tracking-region-status');
    }
  });

  trackedRegionMarkers = [];
  lastTrackedMarkerSummarySignature = '';
}

function syncTrackedRegionMarkerPositions() {
  if (trackedRegionAnimFrameId) {
    cancelAnimationFrame(trackedRegionAnimFrameId);
  }

  function updateMarkerPositions() {
    const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const runtimeSurfaces = detectRuntimeSurfaces();
    const activeRuntimeSurface = getActiveRuntimeSurface(runtimeSurfaces);

    trackedRegionMarkers.forEach((marker) => {
      if (!marker.targetEl?.isConnected || !marker.labelEl?.isConnected) return;
      const visibilityState = getTrackedMarkerVisibilityState(marker, activeRuntimeSurface);
      if (marker.isVisible !== visibilityState.shouldShow) {
        logMarkerDebug('renderTrackingDocument.visibility_changed', {
          regionId: marker.regionId,
          regionNumber: marker.regionNumber,
          shouldShow: visibilityState.shouldShow,
          reason: visibilityState.reason,
          element: summarizeElementForMarkerDebug(marker.targetEl)
        });
        setTrackedMarkerVisibility(marker, visibilityState.shouldShow);
        marker.lastVisibilityReason = visibilityState.reason;
      }
      if (!visibilityState.shouldShow) return;
      const rect = marker.targetEl.getBoundingClientRect();
      marker.labelEl.style.left = `${rect.left + scrollLeft}px`;
      marker.labelEl.style.top = `${rect.top + scrollTop - 18}px`;
    });

    logTrackedMarkerSummary('renderTrackingDocument.visibility_summary', activeRuntimeSurface);

    trackedRegionAnimFrameId = requestAnimationFrame(updateMarkerPositions);
  }

  trackedRegionAnimFrameId = requestAnimationFrame(updateMarkerPositions);
}

function isTrackedMarkerManagedElement(node) {
  if (!(node instanceof Element)) return false;
  return Boolean(
    node.closest(MARKER_UI_SELECTOR)
    || node.hasAttribute('data-tracking-region-id')
    || node.hasAttribute('data-tracking-region-number')
  );
}

function isRelevantTrackedDocumentMutation(mutation) {
  if (!mutation) return false;

  if (mutation.type === 'attributes') {
    const target = mutation.target;
    if (!(target instanceof Element)) return false;

    if (target.closest(MARKER_UI_SELECTOR)) {
      return false;
    }

    if (
      mutation.attributeName === 'style'
      && (target.hasAttribute('data-tracking-region-id') || target.hasAttribute('data-tracking-region-number'))
    ) {
      return false;
    }

    return true;
  }

  if (mutation.type === 'childList') {
    const changedNodes = [
      ...Array.from(mutation.addedNodes || []),
      ...Array.from(mutation.removedNodes || [])
    ];

    if (changedNodes.length === 0) return false;

    return changedNodes.some((node) => {
      if (!(node instanceof Element)) return true;
      return !isTrackedMarkerManagedElement(node);
    });
  }

  return true;
}

function setupTrackedDocumentObserver() {
  if (trackedSurfaceObserver) {
    trackedSurfaceObserver.disconnect();
  }

  trackedSurfaceObserver = new MutationObserver((mutations) => {
    if (!trackedDocumentPayload || trackedRenderMuted) return;
    const hasRelevantMutation = Array.isArray(mutations)
      && mutations.some((mutation) => isRelevantTrackedDocumentMutation(mutation));
    if (!hasRelevantMutation) return;
    if (trackedRenderTimer) {
      clearTimeout(trackedRenderTimer);
    }
    trackedRenderTimer = setTimeout(() => {
      renderTrackingDocument(trackedDocumentPayload);
    }, 120);
  });

  trackedSurfaceObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style', 'role', 'aria-modal', 'aria-hidden']
  });
}

function renderTrackingDocument(documentPayload) {
  trackedDocumentPayload = documentPayload;
  trackedRenderMuted = true;
  clearTrackedRegionMarkers();
  lastTrackedMarkerSummarySignature = '';

  logMarkerDebug('renderTrackingDocument.start', {
    regionCount: Array.isArray(documentPayload?.regions) ? documentPayload.regions.length : 0,
    surfaceCount: Array.isArray(documentPayload?.surfaces) ? documentPayload.surfaces.length : 0
  });

  const runtimeSurfaces = detectRuntimeSurfaces();
  const activeRuntimeSurface = getActiveRuntimeSurface(runtimeSurfaces);
  const runtimeSurfaceMap = new Map();
  const documentSurfaces = Array.isArray(documentPayload?.surfaces) && documentPayload.surfaces.length > 0
    ? documentPayload.surfaces
    : [{ surface_id: 'sf_main', surface_key: 'main', type: 'main', activation_hints: { role: 'main' } }];

  documentSurfaces.forEach((surface) => {
    const runtimeSurface = matchSurfaceToRuntime(surface, runtimeSurfaces);
    if (runtimeSurface) {
      runtimeSurfaceMap.set(surface.surface_id, runtimeSurface);
    }
  });

  const unmatchedRegionIds = [];
  const unmatchedRegionDetails = [];
  (documentPayload?.regions || []).forEach((region) => {
    if (region.status === 'deleted') return;
    const runtimeSurface = runtimeSurfaceMap.get(region.surface_id) || runtimeSurfaceMap.get('sf_main') || runtimeSurfaces[0];
    if (!runtimeSurface?.root) {
      unmatchedRegionIds.push(region.region_id);
      unmatchedRegionDetails.push({
        region: summarizeRegionForMarkerDebug(region),
        reason: 'runtime_surface_not_found'
      });
      return;
    }
    if (!shouldRenderForActiveSurface(runtimeSurface, activeRuntimeSurface)) {
      unmatchedRegionDetails.push({
        region: summarizeRegionForMarkerDebug(region),
        reason: 'inactive_surface'
      });
      return;
    }

    const targetEl = findRegionElementByAnchor(region, runtimeSurface.root);
    if (!targetEl) {
      unmatchedRegionIds.push(region.region_id);
      unmatchedRegionDetails.push({
        region: summarizeRegionForMarkerDebug(region),
        reason: 'target_not_found'
      });
      return;
    }

    if (targetEl.hasAttribute('data-ui-marker')) {
      unmatchedRegionIds.push(region.region_id);
      unmatchedRegionDetails.push({
        region: summarizeRegionForMarkerDebug(region),
        reason: 'target_already_marked',
        element: summarizeElementForMarkerDebug(targetEl)
      });
      return;
    }

    const color = getRegionStatusColor(region.status);
    const previousOutline = targetEl.style.outline || '';
    const previousOutlineOffset = targetEl.style.outlineOffset || '';
    targetEl.style.outline = `3px solid ${color}`;
    targetEl.style.outlineOffset = '2px';
    targetEl.setAttribute('data-tracking-region-id', region.region_id);
    targetEl.setAttribute('data-tracking-region-number', region.region_number);
    targetEl.setAttribute('data-tracking-region-status', region.status);

    const labelEl = document.createElement('div');
    labelEl.id = `ui-region-container-${region.region_id}`;
    labelEl.style.position = 'absolute';
    labelEl.style.zIndex = '9999999';
    labelEl.style.display = 'flex';
    labelEl.style.alignItems = 'center';
    labelEl.style.gap = '2px';
    labelEl.setAttribute('data-ui-marker', 'tracking-region');

    const badgeEl = document.createElement('div');
    badgeEl.textContent = `[${region.region_number}]`;
    badgeEl.style.backgroundColor = color;
    badgeEl.style.color = 'white';
    badgeEl.style.fontSize = '12px';
    badgeEl.style.fontWeight = 'bold';
    badgeEl.style.padding = '2px 4px';
    badgeEl.style.borderRadius = '3px';
    labelEl.appendChild(badgeEl);

    const deleteBtn = document.createElement('div');
    deleteBtn.innerHTML = '✕';
    deleteBtn.style.cssText = `
      width: 16px;
      height: 16px;
      background: #111827;
      color: white;
      border-radius: 50%;
      font-size: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-weight: bold;
    `;
    deleteBtn.title = '删除该埋点';
    deleteBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      chrome.runtime.sendMessage({
        action: 'deleteRegion',
        regionId: region.region_id,
        regionNumber: region.region_number
      });
    });
    labelEl.appendChild(deleteBtn);

    document.body.appendChild(labelEl);

    trackedRegionMarkers.push({
      regionId: region.region_id,
      regionNumber: region.region_number,
      labelEl,
      targetEl,
      previousOutline,
      previousOutlineOffset,
      markerOutline: `3px solid ${color}`,
      markerOutlineOffset: '2px',
      isVisible: true,
      lastVisibilityReason: 'visible'
    });
  });

  trackedRenderMuted = false;
  trackedRegionMarkers.forEach((marker) => {
    const visibilityState = getTrackedMarkerVisibilityState(marker, activeRuntimeSurface);
    marker.lastVisibilityReason = visibilityState.reason;
    if (marker.isVisible !== visibilityState.shouldShow) {
      setTrackedMarkerVisibility(marker, visibilityState.shouldShow);
    }
  });
  logTrackedMarkerSummary('renderTrackingDocument.initial_visibility_summary', activeRuntimeSurface);
  if (trackedRegionMarkers.length > 0) {
    syncTrackedRegionMarkerPositions();
  }
  setupTrackedDocumentObserver();

  const result = {
    success: true,
    renderedCount: trackedRegionMarkers.length,
    unmatchedRegionIds
  };
  logMarkerDebug('renderTrackingDocument.result', {
    renderedCount: result.renderedCount,
    unmatchedRegionIds,
    unmatchedRegionDetails
  });
  return result;
}

function cloneTrackingDocumentPayload(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function enrichTrackingDocumentAnchors(documentPayload) {
  const clonedDocument = cloneTrackingDocumentPayload(documentPayload) || {};
  const runtimeSurfaces = detectRuntimeSurfaces();
  const runtimeSurfaceMap = new Map();
  const documentSurfaces = Array.isArray(clonedDocument?.surfaces) && clonedDocument.surfaces.length > 0
    ? clonedDocument.surfaces
    : [{ surface_id: 'sf_main', surface_key: 'main', type: 'main', activation_hints: { role: 'main' } }];

  documentSurfaces.forEach((surface) => {
    const runtimeSurface = matchSurfaceToRuntime(surface, runtimeSurfaces);
    if (runtimeSurface) {
      runtimeSurfaceMap.set(surface.surface_id, runtimeSurface);
    }
  });

  const enrichedRegionIds = [];
  const unchangedRegionIds = [];
  const unmatchedRegionIds = [];
  const diagnostics = [];

  (clonedDocument?.regions || []).forEach((region) => {
    if (region?.status === 'deleted') return;

    const runtimeSurface = runtimeSurfaceMap.get(region.surface_id) || runtimeSurfaceMap.get('sf_main') || runtimeSurfaces[0] || null;
    const primaryRoot = runtimeSurface?.root || document.body;
    let targetEl = findRegionElementByAnchor(region, primaryRoot);
    let resolvedIn = runtimeSurface?.surfaceKey || runtimeSurface?.type || 'document';

    if (!targetEl && primaryRoot !== document.body) {
      targetEl = findRegionElementByAnchor(region, document.body);
      resolvedIn = 'document';
    }

    if (!targetEl) {
      unmatchedRegionIds.push(region.region_id);
      diagnostics.push({
        region: summarizeRegionForMarkerDebug(region),
        reason: 'target_not_found'
      });
      return;
    }

    const previousAnchorSignature = JSON.stringify(region.anchor || null);
    region.anchor = buildElementAnchorSnapshot(targetEl, {
      role: inferElementComparableRole(targetEl) || region.control_type || ''
    });

    if (targetEl.id) {
      region.element_dom_id = targetEl.id;
    }

    const nextAnchorSignature = JSON.stringify(region.anchor || null);
    if (previousAnchorSignature !== nextAnchorSignature) {
      enrichedRegionIds.push(region.region_id);
    } else {
      unchangedRegionIds.push(region.region_id);
    }

    diagnostics.push({
      region: summarizeRegionForMarkerDebug(region),
      reason: previousAnchorSignature !== nextAnchorSignature ? 'anchor_upgraded' : 'anchor_unchanged',
      resolvedIn,
      element: summarizeElementForMarkerDebug(targetEl)
    });
  });

  const result = {
    success: true,
    document: clonedDocument,
    enrichedRegionIds,
    unchangedRegionIds,
    unmatchedRegionIds
  };

  logMarkerDebug('enrichTrackingDocumentAnchors.result', {
    enrichedRegionIds,
    unchangedRegionIds,
    unmatchedRegionIds,
    diagnostics
  });

  return result;
}

function isPointInsideSelectionBox(x, y, box) {
  return Number.isFinite(x)
    && Number.isFinite(y)
    && x >= box.x
    && x <= box.x + box.width
    && y >= box.y
    && y <= box.y + box.height;
}

function getRectIntersectionArea(boxA, boxB) {
  const left = Math.max(boxA.x, boxB.x);
  const top = Math.max(boxA.y, boxB.y);
  const right = Math.min(boxA.x + boxA.width, boxB.x + boxB.width);
  const bottom = Math.min(boxA.y + boxA.height, boxB.y + boxB.height);
  const width = Math.max(0, right - left);
  const height = Math.max(0, bottom - top);
  return width * height;
}

function isBoxSelected(selectionBox, targetBox) {
  if (!selectionBox || !targetBox || targetBox.width <= 0 || targetBox.height <= 0) return false;

  const centerX = targetBox.x + targetBox.width / 2;
  const centerY = targetBox.y + targetBox.height / 2;
  if (isPointInsideSelectionBox(centerX, centerY, selectionBox)) {
    return true;
  }

  const overlapArea = getRectIntersectionArea(selectionBox, targetBox);
  if (overlapArea <= 0) return false;

  const targetArea = Math.max(1, targetBox.width * targetBox.height);
  return (overlapArea / targetArea) >= 0.5;
}

function getBoxArea(box) {
  if (!box) return 0;
  return Math.max(0, box.width || 0) * Math.max(0, box.height || 0);
}

function hasInlineInteractionHandler(element) {
  return ['onclick', 'onmousedown', 'onmouseup', 'ondblclick'].some((attr) => (
    element.hasAttribute(attr) || typeof element[attr] === 'function'
  ));
}

function hasActionLikeAttributes(element) {
  return element.getAttribute('data-action')
    || element.getAttribute('data-click')
    || element.getAttribute('data-toggle') === 'dropdown'
    || element.getAttribute('aria-haspopup') === 'true'
    || (element.hasAttribute('tabindex') && element.getAttribute('tabindex') !== '-1');
}

function isStrictlyActionableSelectionNode(node) {
  const element = node?.element;
  if (!element) return false;

  const tagName = element.tagName.toLowerCase();
  const role = (node.role || element.getAttribute('role') || '').toLowerCase();
  const actionableTags = new Set(['button', 'a', 'input', 'select', 'textarea', 'summary', 'details']);
  const actionableRoles = new Set([
    'button', 'link', 'checkbox', 'radio', 'menuitem', 'menuitemcheckbox',
    'menuitemradio', 'tab', 'switch', 'combobox', 'textbox', 'searchbox',
    'option', 'slider', 'spinbutton'
  ]);

  if (role === 'scrollable') return false;
  if (actionableTags.has(tagName)) return true;
  if (actionableRoles.has(role)) return true;
  if (tagName === 'label' && (element.control || element.htmlFor)) return true;
  if (hasInlineInteractionHandler(element) || hasActionLikeAttributes(element)) return true;

  const computed = window.getComputedStyle(element);
  const readableName = (node.name
    || element.getAttribute('aria-label')
    || element.getAttribute('title')
    || element.getAttribute('placeholder')
    || element.innerText
    || '')
    .replace(/\s+/g, ' ')
    .trim();

  if (computed.cursor === 'pointer') {
    if (readableName) return true;
    if (tagName === 'img' || tagName === 'svg') return true;
    if (element.querySelector('svg, img, [class*="icon"], [aria-hidden="true"]')) return true;
  }

  return false;
}

function collectInteractiveElementsInSelection(selectionBox) {
  if (!window.DomUtils || typeof window.DomUtils.extractInteractiveElements !== 'function') {
    return [];
  }

  const rawNodes = window.DomUtils.extractInteractiveElements();
  const matchedNodes = rawNodes.filter((node) => {
    if (!node?.element || !node.box) return false;
    if (node.element.closest(MARKER_UI_SELECTOR)) return false;
    if (hasExistingAnnotation(node.element)) return false;
    if (!isStrictlyActionableSelectionNode(node)) return false;
    return isBoxSelected(selectionBox, node.box);
  });

  const innermostNodes = matchedNodes.filter((node) => (
    !matchedNodes.some((candidate) => (
      candidate !== node
      && candidate.element
      && node.element
      && node.element.contains(candidate.element)
    ))
  ));

  if (innermostNodes.length === 0) {
    return [];
  }

  const selectionArea = Math.max(1, getBoxArea(selectionBox));
  innermostNodes.sort((a, b) => {
    const areaA = Math.max(1, getBoxArea(a.box));
    const areaB = Math.max(1, getBoxArea(b.box));
    const overlapA = getRectIntersectionArea(selectionBox, a.box);
    const overlapB = getRectIntersectionArea(selectionBox, b.box);
    const fitScoreA = overlapA / Math.max(1, Math.min(selectionArea, areaA));
    const fitScoreB = overlapB / Math.max(1, Math.min(selectionArea, areaB));
    if (fitScoreA !== fitScoreB) {
      return fitScoreB - fitScoreA;
    }

    const areaDeltaA = Math.abs(areaA - selectionArea);
    const areaDeltaB = Math.abs(areaB - selectionArea);
    if (areaDeltaA !== areaDeltaB) {
      return areaDeltaA - areaDeltaB;
    }

    return areaA - areaB;
  });

  const bestNode = innermostNodes[0];
  if (!bestNode) {
    return [];
  }

  const selectionSeed = Date.now();
  const tempId = `temp-${selectionSeed}-1`;
  bestNode.element.setAttribute('data-temp-selection-id', tempId);

  return [{
    element: getElementInfo(bestNode.element),
    tempId,
    box: bestNode.box,
    semanticContext: bestNode.semanticContext || null
  }];
}

function collectFallbackSelectionItem(left, top, width, height, absBox) {
  const points = [
    { x: left + width / 2, y: top + height / 2 },
    { x: left + width / 4, y: top + height / 4 },
    { x: left + 3 * width / 4, y: top + height / 4 },
    { x: left + width / 4, y: 3 * height / 4 },
    { x: left + 3 * width / 4, y: 3 * height / 4 }
  ];

  let bestElement = null;
  for (const point of points) {
    const el = document.elementFromPoint(point.x, point.y);
    if (!el) continue;

    let current = el;
    while (current && current !== document.body) {
      if (isInteractive(current) && !current.closest(MARKER_UI_SELECTOR) && !hasExistingAnnotation(current)) {
        bestElement = current;
        break;
      }
      current = current.parentElement;
    }

    if (bestElement) break;
    if (!bestElement && !hasExistingAnnotation(el)) bestElement = el;
  }

  if (!bestElement || bestElement.closest(MARKER_UI_SELECTOR) || hasExistingAnnotation(bestElement)) {
    return [];
  }

  const tempId = `temp-${Date.now()}-1`;
  bestElement.setAttribute('data-temp-selection-id', tempId);

  return [{
    element: getElementInfo(bestElement),
    tempId,
    box: absBox
  }];
}

// ============ 框选模式功能 ============
function startSelectionMode() {
  // 如果已经在框选模式，先退出
  if (isSelecting) {
    endSelectionMode();
  }

  isSelecting = true;
  document.body.style.cursor = 'crosshair';

  // 1. 创建全屏透明遮罩 (防止点中页面元素)
  selectionOverlay = document.createElement('div');
  selectionOverlay.id = 'ui-selection-overlay';
  selectionOverlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(0, 0, 0, 0.05);
    z-index: 99999998;
    cursor: crosshair;
  `;
  document.body.appendChild(selectionOverlay);

  // 2. 创建选框元素 (作为遮罩的子元素)
  selectionBox = document.createElement('div');
  selectionBox.id = 'selection-box';
  selectionBox.style.cssText = `
    position: absolute;
    border: 2px dashed #8b5cf6;
    background: rgba(139, 92, 246, 0.1);
    z-index: 99999999;
    pointer-events: none;
    display: none;
  `;
  selectionOverlay.appendChild(selectionBox);

  // 3. 添加事件监听 (监听在遮罩上)
  selectionOverlay.addEventListener('mousedown', onSelectionStart);
  selectionOverlay.addEventListener('mousemove', onSelectionMove);
  selectionOverlay.addEventListener('mouseup', onSelectionEnd);

  return { success: true, message: '框选模式已启动，请在页面上拖拽框选要添加的元素' };
}

function onSelectionStart(e) {
  isSelecting = true;
  // 使用 clientX/Y，因为遮罩是 fixed
  selectionStartX = e.clientX;
  selectionStartY = e.clientY;

  selectionBox.style.left = selectionStartX + 'px';
  selectionBox.style.top = selectionStartY + 'px';
  selectionBox.style.width = '0px';
  selectionBox.style.height = '0px';
  selectionBox.style.display = 'block';
}

function onSelectionMove(e) {
  if (!isSelecting || !selectionBox) return;

  const currentX = e.clientX;
  const currentY = e.clientY;

  const left = Math.min(selectionStartX, currentX);
  const top = Math.min(selectionStartY, currentY);
  const width = Math.abs(currentX - selectionStartX);
  const height = Math.abs(currentY - selectionStartY);

  selectionBox.style.left = left + 'px';
  selectionBox.style.top = top + 'px';
  selectionBox.style.width = width + 'px';
  selectionBox.style.height = height + 'px';
}

function onSelectionEnd(e) {
  if (!isSelecting || !selectionBox) return;

  const currentX = e.clientX;
  const currentY = e.clientY;

  const left = Math.min(selectionStartX, currentX);
  const top = Math.min(selectionStartY, currentY);
  const width = Math.abs(currentX - selectionStartX);
  const height = Math.abs(currentY - selectionStartY);

  // 1. 获取绝对坐标 (用于后续逻辑)
  const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
  const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
  const absBox = {
    x: left + scrollLeft,
    y: top + scrollTop,
    width: width,
    height: height
  };

  // 如果选框太小，忽略
  if (width < 5 || height < 5) {
    endSelectionMode();
    return;
  }

  // 2. 暂时隐藏遮罩和选框，以便识别下方的真实元素
  selectionOverlay.style.display = 'none';
  selectionBox.style.display = 'none';

  // [修改] 立即发送消息前，增加一个简单的防抖或标记，防止某些情况下触发两次
  if (!isSelecting) return;
  isSelecting = false; // 立即标记为非选择状态，防止重复触发

  const selectedItems = collectInteractiveElementsInSelection(absBox);
  const finalItems = selectedItems.length > 0
    ? selectedItems
    : collectFallbackSelectionItem(left, top, width, height, absBox);

  console.log('🎯 框选识别完成:', {
    count: finalItems.length,
    elements: finalItems.map((item) => ({
      name: item.element?.name,
      selector: item.element?.selector,
      tempId: item.tempId
    }))
  });

  chrome.runtime.sendMessage({
    action: 'elementSelected',
    elements: finalItems,
    box: absBox
  });

  endSelectionMode();
}

function getElementInfo(element) {
  // 获取元素的基本信息
  const tagName = element.tagName.toLowerCase();
  const id = element.id || '';
  const className = typeof element.className === 'string' ? element.className : '';
  const role = element.getAttribute('role') || '';
  const text = limitMarkerComparableText(element.textContent || '', 50);
  const href = element.getAttribute('href') || '';
  const src = element.getAttribute('src') || '';
  const anchor = buildElementAnchorSnapshot(element, { role });
  const selector = anchor?.selector_candidates?.[0] || tagName;

  // 获取无障碍信息
  const name = anchor?.text_signature?.accessible_name ||
               element.getAttribute('aria-name') ||
               element.getAttribute('name') ||
               text;

  // 获取元素类型
  let elementType = 'other';
  if (tagName === 'button' || role === 'button') {
    elementType = 'button';
  } else if (tagName === 'a' || role === 'link') {
    elementType = 'link';
  } else if (tagName === 'input') {
    elementType = 'input';
  } else if (tagName === 'img' || role === 'img' || tagName === 'image') {
    elementType = 'image';
  } else if (tagName === 'div' || tagName === 'span') {
    if (className.toLowerCase().includes('card')) {
      elementType = 'card';
    } else if (className.toLowerCase().includes('modal') || className.toLowerCase().includes('dialog')) {
      elementType = 'modal';
    }
  }

  return {
    tagName,
    id,
    className,
    role,
    text,
    name,
    href,
    src,
    selector,
    type: elementType,
    anchor
  };
}

function endSelectionMode() {
  isSelecting = false;
  document.body.style.cursor = '';

  if (selectionOverlay) {
    selectionOverlay.remove();
    selectionOverlay = null;
    selectionBox = null;
  }

  // 确保也移除可能遗留的 global 监听 (如果有的话)
  document.removeEventListener('mousedown', onSelectionStart);
  document.removeEventListener('mousemove', onSelectionMove);
  document.removeEventListener('mouseup', onSelectionEnd);
}

function highlightByNumberWithInfo(regionNumber, selector, elementId, box) {
  // 1. 尝试通过 data-region-number 属性找到元素 (手动框选的元素)
  let targetEl = document.querySelector(`[data-region-number="${regionNumber}"]`);

  // 2. 尝试通过 data-cdp-extracted-id 属性找到元素 (自动提取的元素)
  if (!targetEl) {
    targetEl = document.querySelector(`[data-cdp-extracted-id="${regionNumber}"]`);
  }

  if (!targetEl) {
    targetEl = document.querySelector(`[data-tracking-region-number="${regionNumber}"]`);
  }

  if (!targetEl && elementId) {
    // 尝试通过 id 查找
    targetEl = document.querySelector(`#${elementId}`);
  }

  if (!targetEl && selector) {
    // 尝试通过选择器查找
    targetEl = document.querySelector(selector);
  }

  if (!targetEl && box) {
    // 如果都找不到，使用坐标查找
    const centerX = box.x + box.width / 2;
    const centerY = box.y + box.height / 2;
    targetEl = document.elementFromPoint(centerX, centerY);
  }

  if (!targetEl && regionNumber <= 100) {
    // 尝试通过标注标签查找
    const label = document.querySelector(`[id^="ui-marker-label-"], [id^="ui-marker-container-"]`);
    if (label) {
      // 滚动到该元素位置
      const markerContainer = document.querySelector(`[id*="${regionNumber}"]`);
      if (markerContainer) {
        markerContainer.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }

  if (targetEl) {
    // 强制滚动到元素位置
    targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // 添加强力高亮类
    targetEl.classList.add('region-highlight-active');
    targetEl.setAttribute('data-region-highlight', 'true');

    // 3秒后移除强力高亮
    setTimeout(() => {
      targetEl.classList.remove('region-highlight-active');
      targetEl.removeAttribute('data-region-highlight');
    }, 3000);

    return { success: true, message: '已高亮元素' };
  }

  return { success: false, message: '无法找到对应元素' };
}


// 根据 CDP 传来的坐标，在页面上进行混合标注 (原生边框 + 悬浮数字标签)
let activeMarkers = [];
let animFrameId = null;

function setPreviewMarkerVisibility(marker, shouldShow) {
  if (!marker) return;

  if (marker.targetEl) {
    marker.targetEl.style.border = shouldShow ? (marker.previewBorder || '') : (marker.previousBorder || '');
    marker.targetEl.style.boxShadow = shouldShow ? (marker.previewShadow || '') : (marker.previousShadow || '');
    marker.targetEl.style.zIndex = shouldShow ? (marker.previewZIndex || '') : (marker.previousZIndex || '');
  }

  if (marker.boxEl) {
    marker.boxEl.style.display = shouldShow ? 'block' : 'none';
  }

  if (marker.labelEl) {
    marker.labelEl.style.display = shouldShow ? 'flex' : 'none';
  }

  marker.isVisible = shouldShow;
}

function getTrackedMarkerVisibilityState(marker, activeRuntimeSurface) {
  if (!marker?.targetEl?.isConnected) {
    return { shouldShow: false, reason: 'target_disconnected' };
  }
  if (!isElementInsideSurface(marker.targetEl, activeRuntimeSurface)) {
    return { shouldShow: false, reason: 'outside_active_surface' };
  }
  if (!isElementVisuallyReachable(marker.targetEl)) {
    return { shouldShow: false, reason: 'not_top_renderable' };
  }
  return { shouldShow: true, reason: 'visible' };
}

function getDomMarkerVisibilityState(marker, activeRuntimeSurface) {
  if (!marker?.targetEl?.isConnected) {
    return { shouldShow: false, reason: 'target_disconnected' };
  }
  if (!isElementInsideSurface(marker.targetEl, activeRuntimeSurface)) {
    return { shouldShow: false, reason: 'outside_active_surface' };
  }
  if (!isElementVisuallyReachable(marker.targetEl)) {
    return { shouldShow: false, reason: 'not_top_renderable' };
  }
  return { shouldShow: true, reason: 'visible' };
}

function getFallbackMarkerVisibilityState(marker, activeRuntimeSurface) {
  if (activeRuntimeSurface && activeRuntimeSurface.type !== 'main') {
    return { shouldShow: false, reason: 'non_main_surface_active' };
  }

  const boxWidth = marker.boxEl?.offsetWidth || 0;
  const boxHeight = marker.boxEl?.offsetHeight || 0;
  const centerX = marker.x + (boxWidth / 2);
  const centerY = marker.y + (boxHeight / 2);
  const topElement = getTopRenderableElementAtPoint(centerX, centerY);
  if (!topElement) {
    return { shouldShow: false, reason: 'no_top_renderable_element' };
  }

  return {
    shouldShow: true,
    reason: 'visible',
    topElement: summarizeElementForMarkerDebug(topElement)
  };
}

function buildPreviewMarkerSummary(activeRuntimeSurface) {
  const summary = {
    totalCount: activeMarkers.length,
    domBoundCount: 0,
    fallbackCount: 0,
    visibleCount: 0,
    hiddenCount: 0,
    hiddenReasonCounts: {},
    activeSurface: summarizeSurfaceForMarkerDebug(activeRuntimeSurface)
  };

  activeMarkers.forEach((marker) => {
    const visibilityState = marker.targetEl
      ? getDomMarkerVisibilityState(marker, activeRuntimeSurface)
      : getFallbackMarkerVisibilityState(marker, activeRuntimeSurface);

    if (marker.targetEl) {
      summary.domBoundCount += 1;
    } else {
      summary.fallbackCount += 1;
    }

    if (visibilityState.shouldShow) {
      summary.visibleCount += 1;
    } else {
      summary.hiddenCount += 1;
      incrementMarkerDebugCount(summary.hiddenReasonCounts, visibilityState.reason);
    }
  });

  return summary;
}

function logPreviewMarkerSummary(stage, activeRuntimeSurface) {
  const summary = buildPreviewMarkerSummary(activeRuntimeSurface);
  const signature = JSON.stringify(summary);
  if (signature === lastPreviewMarkerSummarySignature) return;
  lastPreviewMarkerSummarySignature = signature;
  logMarkerDebug(stage, summary);
}

function buildTrackedMarkerSummary(activeRuntimeSurface) {
  const summary = {
    totalCount: trackedRegionMarkers.length,
    visibleCount: 0,
    hiddenCount: 0,
    hiddenReasonCounts: {},
    activeSurface: summarizeSurfaceForMarkerDebug(activeRuntimeSurface)
  };

  trackedRegionMarkers.forEach((marker) => {
    const visibilityState = getTrackedMarkerVisibilityState(marker, activeRuntimeSurface);
    if (visibilityState.shouldShow) {
      summary.visibleCount += 1;
    } else {
      summary.hiddenCount += 1;
      incrementMarkerDebugCount(summary.hiddenReasonCounts, visibilityState.reason);
    }
  });

  return summary;
}

function logTrackedMarkerSummary(stage, activeRuntimeSurface) {
  const summary = buildTrackedMarkerSummary(activeRuntimeSurface);
  const signature = JSON.stringify(summary);
  if (signature === lastTrackedMarkerSummarySignature) return;
  lastTrackedMarkerSummarySignature = signature;
  logMarkerDebug(stage, summary);
}

function drawCDPMarkers(nodes, regions = [], options = {}) {
  logMarkerDebug('drawCDPMarkers.start', {
    nodeCount: nodes.length,
    regionCount: Array.isArray(regions) ? regions.length : 0,
    options
  });
  lastPreviewMarkerSummarySignature = '';

  const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
  const scrollTop = window.pageYOffset || document.documentElement.scrollTop;

  activeMarkers.forEach((marker) => {
    if (marker.labelEl?.isConnected) marker.labelEl.remove();
    if (marker.boxEl?.isConnected) marker.boxEl.remove();
    if (marker.targetEl?.isConnected) {
      marker.targetEl.style.border = marker.previousBorder || '';
      marker.targetEl.style.boxShadow = marker.previousShadow || '';
      marker.targetEl.style.zIndex = marker.previousZIndex || '';
      marker.targetEl.removeAttribute('data-ui-marker');
      if (marker.appliedRelative && marker.targetEl.getAttribute('data-ui-was-relative') === 'true') {
        marker.targetEl.style.position = '';
        marker.targetEl.removeAttribute('data-ui-was-relative');
      }
    }
  });

  // 清除旧的标记记录
  activeMarkers = [];
  if (animFrameId) {
    cancelAnimationFrame(animFrameId);
    animFrameId = null;
  }

  const skipMatchedRegions = options?.skipMatchedRegions !== false;

  nodes.forEach(node => {
    const region = findMatchingRegionForNode(node, regions);
    if (skipMatchedRegions && region) {
      logMarkerDebug('drawCDPMarkers.skip_matched_region', {
        node: summarizeNodeForMarkerDebug(node),
        region: summarizeRegionForMarkerDebug(region)
      });
      return;
    }

    // 寻找后台注入的真实元素
    const domBindingId = node?.domBindingId || node.id;
    const realEl = document.querySelector(`[data-cdp-extracted-id="${domBindingId}"]`);

    if (realEl) {
      // 🎯 DOM 原生着色逻辑（原生内联样式，防错位）
      const type = classifyElement(realEl);
      const color = colors[type] || colors.other;
      const previousBorder = realEl.style.border || '';
      const previousShadow = realEl.style.boxShadow || '';
      const previousZIndex = realEl.style.zIndex || '';
      const previewBorder = `3px solid ${color}`;
      const previewShadow = `${color} 0px 0px 8px`;
      const previewZIndex = '999999';

      realEl.style.border = previewBorder;
      realEl.style.boxShadow = previewShadow;
      realEl.style.zIndex = previewZIndex;

      const computed = window.getComputedStyle(realEl);
      let appliedRelative = false;
      if (computed.position === 'static') {
        realEl.style.position = 'relative';
        realEl.setAttribute('data-ui-was-relative', 'true');
        appliedRelative = true;
      }

      realEl.setAttribute('data-ui-marker', type);

      const displayNumber = region ? region.region_number : node.id;

      // 绘制标签容器
      // ⚠️ 用 displayNumber 作为 id，确保 removeRegionMarker 能通过 regionNumber 找到它
      const labelContainer = document.createElement('div');
      labelContainer.id = `ui-marker-container-${displayNumber}`;
      labelContainer.setAttribute('data-node-id', node.id); // 保留原始 node.id 备查
      labelContainer.style.position = 'absolute';
      labelContainer.style.zIndex = '9999999';
      labelContainer.style.display = 'flex';
      labelContainer.style.alignItems = 'center';
      labelContainer.style.gap = '2px';

      // 绘制数字标签
      const label = document.createElement('div');
      label.id = `ui-marker-label-${node.id}`;
      label.innerText = `[${displayNumber}]`;
      label.style.backgroundColor = 'red';
      label.style.color = 'white';
      label.style.fontSize = '12px';
      label.style.fontWeight = 'bold';
      label.style.padding = '2px 4px';
      label.style.borderRadius = '3px';
      label.style.cursor = 'pointer';
      labelContainer.appendChild(label);

      // 为所有 CDP 标记添加删除按钮 (无论是否有关联 region)
      const deleteBtn = document.createElement('div');
      deleteBtn.innerHTML = '✕';
      deleteBtn.style.cssText = `
        width: 16px;
        height: 16px;
        background: #dc3545;
        color: white;
        border-radius: 50%;
        font-size: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        font-weight: bold;
      `;
      deleteBtn.setAttribute('data-delete-cdp', node.id);
      deleteBtn.title = '删除该标注';
      deleteBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        // ⚠️ 从 data 属性读取当前编号（避免闭包捕获旧 id，重排后会失效）
        const currentId = deleteBtn.getAttribute('data-delete-cdp');
        chrome.runtime.sendMessage({
          action: 'deleteRegion',
          regionNumber: currentId,
          isCDP: true
        });
      });
      labelContainer.appendChild(deleteBtn);

      const rect = realEl.getBoundingClientRect();
      labelContainer.style.left = (rect.x + scrollLeft) + 'px';
      labelContainer.style.top = (rect.y + scrollTop - 15) + 'px';

      labelContainer.setAttribute('data-ui-marker', 'true');
      document.body.appendChild(labelContainer);

      // 记录追踪关系（用 displayNumber 作 key，与 labelContainer.id 对齐）
      activeMarkers.push({
        labelEl: labelContainer,
        targetEl: realEl,
        nodeId: node.id,
        nodeSummary: summarizeNodeForMarkerDebug(node),
        displayNumber: displayNumber,
        previousBorder,
        previousShadow,
        previousZIndex,
        previewBorder,
        previewShadow,
        previewZIndex,
        appliedRelative,
        isVisible: true,
        lastVisibilityReason: 'visible'
      });

      logMarkerDebug('drawCDPMarkers.bound_to_dom', {
        node: summarizeNodeForMarkerDebug(node),
        region: summarizeRegionForMarkerDebug(region),
        element: summarizeElementForMarkerDebug(realEl)
      });

    } else if (node.box) {
      // [降级方案]：如果在前台 DOM 中没找到被后台注过 ID 的元素，但是后台传来了备选的纯坐标框 (BoxModel)
      const absoluteX = node.box.x + scrollLeft;
      const absoluteY = node.box.y + scrollTop;

      // 绘制游离的品红色边框
      const box = document.createElement('div');
      box.id = `ui-marker-box-${node.id}`;
      box.style.position = 'absolute';
      box.style.border = '3px solid magenta';
      box.style.boxShadow = 'magenta 0px 0px 8px';
      box.style.pointerEvents = 'none'; // 确保不遮挡鼠标点击
      box.style.zIndex = '9999998';
      box.style.left = absoluteX + 'px';
      box.style.top = absoluteY + 'px';
      box.style.width = node.box.width + 'px';
      box.style.height = node.box.height + 'px';
      box.setAttribute('data-ui-marker', 'true');
      document.body.appendChild(box);

      // 查找对应的 region
      const displayNumber = region ? region.region_number : node.id;
      const hasRegion = !!region;

      // 绘制标签容器
      const labelContainer = document.createElement('div');
      labelContainer.id = `ui-marker-container-${node.id}`;
      labelContainer.style.position = 'absolute';
      labelContainer.style.zIndex = '9999999';
      labelContainer.style.display = 'flex';
      labelContainer.style.alignItems = 'center';
      labelContainer.style.gap = '2px';

      // 绘制数字标签
      const label = document.createElement('div');
      label.id = `ui-marker-label-${node.id}`;
      label.innerText = `[${displayNumber}]`;
      label.style.backgroundColor = 'red';
      label.style.color = 'white';
      label.style.fontSize = '12px';
      label.style.fontWeight = 'bold';
      label.style.padding = '2px 4px';
      label.style.borderRadius = '3px';
      label.style.cursor = 'pointer';
      labelContainer.appendChild(label);

      // 如果有关联 region，添加删除按钮
      if (hasRegion) {
        const deleteBtn = document.createElement('div');
        deleteBtn.innerHTML = '✕';
        deleteBtn.style.cssText = `
          width: 16px;
          height: 16px;
          background: #dc3545;
          color: white;
          border-radius: 50%;
          font-size: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          font-weight: bold;
        `;
        deleteBtn.setAttribute('data-delete-region', displayNumber);
        deleteBtn.title = '删除该埋点';
        deleteBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          // 直接发送删除请求，由 popup 处理确认逻辑
          chrome.runtime.sendMessage({
            action: 'deleteRegion',
            regionNumber: displayNumber
          });
        });
        labelContainer.appendChild(deleteBtn);
      }

      labelContainer.style.left = absoluteX + 'px';
      labelContainer.style.top = (absoluteY - 15) + 'px';
      labelContainer.setAttribute('data-ui-marker', 'true');
      document.body.appendChild(labelContainer);

      // 纯坐标降级框，仅靠 scroll 很难完美防错位，但也可加入更新队列
      activeMarkers.push({
        boxEl: box,
        labelEl: labelContainer,
        nodeId: node.id,
        nodeSummary: summarizeNodeForMarkerDebug(node),
        displayNumber,
        x: node.box.x,
        y: node.box.y,
        isVisible: true,
        lastVisibilityReason: 'visible'
      });

      logMarkerDebug('drawCDPMarkers.fallback_box_used', {
        node: summarizeNodeForMarkerDebug(node),
        region: summarizeRegionForMarkerDebug(region),
        reason: 'dom_target_not_found_but_box_available'
      });
    } else {
      logMarkerDebug('drawCDPMarkers.no_target_and_no_box', {
        node: summarizeNodeForMarkerDebug(node),
        region: summarizeRegionForMarkerDebug(region)
      });
    }
  });

  const initialRuntimeSurfaces = detectRuntimeSurfaces();
  const initialActiveRuntimeSurface = getActiveRuntimeSurface(initialRuntimeSurfaces);
  activeMarkers.forEach((marker) => {
    const visibilityState = marker.targetEl
      ? getDomMarkerVisibilityState(marker, initialActiveRuntimeSurface)
      : getFallbackMarkerVisibilityState(marker, initialActiveRuntimeSurface);
    marker.lastVisibilityReason = visibilityState.reason;
    if (marker.isVisible !== visibilityState.shouldShow) {
      setPreviewMarkerVisibility(marker, visibilityState.shouldShow);
    }
  });
  logPreviewMarkerSummary('drawCDPMarkers.initial_visibility_summary', initialActiveRuntimeSurface);

  // 启动高频位置同步循环 (为了让浮动的数字标签紧贴着原生 DOM 滑动)
  if (activeMarkers.length > 0) {
    function updatePositions() {
      const sl = window.pageXOffset || document.documentElement.scrollLeft;
      const st = window.pageYOffset || document.documentElement.scrollTop;
      const runtimeSurfaces = detectRuntimeSurfaces();
      const activeRuntimeSurface = getActiveRuntimeSurface(runtimeSurfaces);

      for (let i = 0; i < activeMarkers.length; i++) {
        const item = activeMarkers[i];

        // 如果绑定了真实的 DOM Node (原生模式)
        if (item.targetEl) {
          const visibilityState = getDomMarkerVisibilityState(item, activeRuntimeSurface);
          if (item.isVisible !== visibilityState.shouldShow) {
            logMarkerDebug('drawCDPMarkers.visibility_changed', {
              nodeId: item.nodeId,
              displayNumber: item.displayNumber,
              shouldShow: visibilityState.shouldShow,
              reason: visibilityState.reason,
              node: item.nodeSummary,
              element: summarizeElementForMarkerDebug(item.targetEl)
            });
            setPreviewMarkerVisibility(item, visibilityState.shouldShow);
            item.lastVisibilityReason = visibilityState.reason;
          }

          if (visibilityState.shouldShow) {
            const rect = item.targetEl.getBoundingClientRect();
            item.labelEl.style.left = (rect.x + sl) + 'px';
            item.labelEl.style.top = (rect.y + st - 15) + 'px';
          }
        }
        // 如果是游离坐标框 (降级模式)
        else {
          const visibilityState = getFallbackMarkerVisibilityState(item, activeRuntimeSurface);
          if (item.isVisible !== visibilityState.shouldShow) {
            logMarkerDebug('drawCDPMarkers.fallback_visibility_changed', {
              nodeId: item.nodeId,
              displayNumber: item.displayNumber,
              shouldShow: visibilityState.shouldShow,
              reason: visibilityState.reason,
              node: item.nodeSummary,
              topElement: visibilityState.topElement || null
            });
            setPreviewMarkerVisibility(item, visibilityState.shouldShow);
            item.lastVisibilityReason = visibilityState.reason;
          }

          if (visibilityState.shouldShow) {
            const absoluteX = item.x + sl;
            const absoluteY = item.y + st;
            if (item.boxEl) {
              item.boxEl.style.left = absoluteX + 'px';
              item.boxEl.style.top = absoluteY + 'px';
            }
            if (item.labelEl) {
              item.labelEl.style.left = absoluteX + 'px';
              item.labelEl.style.top = (absoluteY - 15) + 'px';
            }
          }
        }
      }
      logPreviewMarkerSummary('drawCDPMarkers.visibility_summary', activeRuntimeSurface);
      animFrameId = requestAnimationFrame(updatePositions);
    }
    animFrameId = requestAnimationFrame(updatePositions);
  }

  const result = {
    success: true,
    message: `成功应用 ${activeMarkers.length} 个原生着色标记`,
    renderedCount: activeMarkers.length
  };
  logMarkerDebug('drawCDPMarkers.result', result);
  return result;
}

// 监听来自popup的消息
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'hideMarkers') {
    // 临时隐藏所有标注（截图前调用，获取干净截图）
    const markerEls = document.querySelectorAll('[data-ui-marker], [id^="ui-marker-"]');
    markerEls.forEach(el => {
      el.setAttribute('data-was-visible', el.style.visibility || '');
      el.style.visibility = 'hidden';
    });
    // 同时隐藏被标注元素的 border/boxShadow
    const annotatedEls = document.querySelectorAll('[data-cdp-extracted-id]');
    annotatedEls.forEach(el => {
      el.setAttribute('data-was-border', el.style.border || '');
      el.setAttribute('data-was-shadow', el.style.boxShadow || '');
      el.style.border = '';
      el.style.boxShadow = '';
    });
    sendResponse({ success: true });
  } else if (request.action === 'showMarkers') {
    // 恢复所有标注
    const markerEls = document.querySelectorAll('[data-was-visible]');
    markerEls.forEach(el => {
      el.style.visibility = el.getAttribute('data-was-visible') || '';
      el.removeAttribute('data-was-visible');
    });
    const annotatedEls = document.querySelectorAll('[data-cdp-extracted-id]');
    annotatedEls.forEach(el => {
      el.style.border = el.getAttribute('data-was-border') || '';
      el.style.boxShadow = el.getAttribute('data-was-shadow') || '';
      el.removeAttribute('data-was-border');
      el.removeAttribute('data-was-shadow');
    });
    sendResponse({ success: true });
  } else if (request.action === 'drawCDPMarkers') {
    const result = drawCDPMarkers(request.nodes, request.regions || [], request.options || {});
    sendResponse(result);
  } else if (request.action === 'renderTrackingDocument') {
    const result = renderTrackingDocument(request.document || {});
    sendResponse(result);
  } else if (request.action === 'enrichTrackingDocumentAnchors') {
    const result = enrichTrackingDocumentAnchors(request.document || {});
    sendResponse(result);
  } else if (request.action === 'clearTrackingDocument') {
    trackedDocumentPayload = null;
    if (trackedSurfaceObserver) {
      trackedSurfaceObserver.disconnect();
      trackedSurfaceObserver = null;
    }
    clearTrackedRegionMarkers();
    sendResponse({ success: true });
  } else if (request.action === 'startSelection') {
    const result = startSelectionMode();
    sendResponse(result);
  } else if (request.action === 'getPageIdentity') {
    const pageIdentity = buildPageIdentity();
    console.log('[ResolveDebug][content] getPageIdentity.request', {
      url: window.location.href,
      title: document.title
    });
    console.log('[ResolveDebug][content] getPageIdentity.response', pageIdentity);
    sendResponse(pageIdentity);
  } else if (request.action === 'extractDOMInteractiveElements') {
    // ── 使用 dom-utils/interactive-detector.js 扫描页面可交互元素 ──
    try {
      if (!window.DomUtils || typeof window.DomUtils.extractInteractiveElements !== 'function') {
        sendResponse({ success: false, message: 'DomUtils 未加载，请刷新页面后重试' });
        return true;
      }

      const rawNodes = window.DomUtils.extractInteractiveElements();

      // 序列化（剔除 element 引用，同时打上 data-cdp-extracted-id 属性）
      const nodes = [];
      const startNumber = Number(request.startNumber) || 0;
      let counter = Math.max(startNumber, getMaxAnnotatedMarkerNumber()) + 1;
      for (const node of rawNodes) {
        // 在页面 DOM 上直接打标记（防套娃：若父/子已有标记则跳过）
        if (node.element) {
          if (node.element.closest(MARKER_UI_SELECTOR)) {
            continue;
          }

          const hasMarkedParent = hasExistingAnnotation(node.element);
          const hasMarkedChild = node.element.querySelector(EXISTING_ANNOTATION_SELECTOR) !== null;

          if (!hasMarkedParent && !hasMarkedChild) {
            const refId = String(counter++);
            node.element.setAttribute('data-cdp-extracted-id', refId);
            node.element.setAttribute('data-cdp-role', node.role);

            nodes.push({
              id: refId,
              role: node.role,
              name: node.name,
              selector: node.selector,
              elementId: node.element?.id || '',
              box: node.box,
              semanticContext: node.semanticContext,
              className: typeof node.element?.className === 'string' ? node.element.className : '',
              anchor: buildElementAnchorSnapshot(node.element, {
                role: node.role,
                name: node.name
              })
            });
          } else {
            continue;
          }
        }
      }

      console.log(`[DomUtils] extractInteractiveElements: 原始 ${rawNodes.length} 个 → 去重后 ${nodes.length} 个`);

      sendResponse({ success: true, nodes });
    } catch (err) {
      console.error('[DomUtils] extractInteractiveElements 失败:', err);
      sendResponse({ success: false, message: err.message });
    }
    return true;
  } else if (request.action === 'highlightByRegionNumber') {
    // 根据 regionNumber 高亮对应的元素
    const regionNumber = request.regionNumber;
    const selector = request.selector;
    const elementId = request.elementId;
    const box = request.box;
    const result = highlightByNumberWithInfo(regionNumber, selector, elementId, box);
    sendResponse(result);
  } else if (request.action === 'removeRegionMarker') {
    // 移除页面上的标签
    const regionNumber = request.regionNumber;
    const regionId = request.regionId;

    if (regionId) {
      const trackedMarker = trackedRegionMarkers.find((marker) => marker.regionId === regionId);
      if (trackedMarker) {
        if (trackedMarker.labelEl?.isConnected) trackedMarker.labelEl.remove();
        if (trackedMarker.targetEl?.isConnected) {
          trackedMarker.targetEl.style.outline = trackedMarker.previousOutline || '';
          trackedMarker.targetEl.style.outlineOffset = trackedMarker.previousOutlineOffset || '';
          trackedMarker.targetEl.removeAttribute('data-tracking-region-id');
          trackedMarker.targetEl.removeAttribute('data-tracking-region-number');
          trackedMarker.targetEl.removeAttribute('data-tracking-region-status');
        }
        trackedRegionMarkers = trackedRegionMarkers.filter((marker) => marker.regionId !== regionId);
        sendResponse({ success: true });
        return true;
      }
    }
    
    // 1. 查找对应的标签容器并移除
    const container = document.getElementById(`ui-marker-container-${regionNumber}`);
    if (container) {
      container.remove();
    }

    // 2. 尝试通过 id 移除降级模式的 box
    const box = document.getElementById(`ui-marker-box-${regionNumber}`);
    if (box) {
      box.remove();
    }

    // 3. 恢复原生元素的样式
    const highlightedEl = document.querySelector(`[data-region-number="${regionNumber}"], [data-cdp-extracted-id="${regionNumber}"], [data-tracking-region-number="${regionNumber}"]`);
    if (highlightedEl) {
      if (highlightedEl.hasAttribute('data-tracking-region-number')) {
        highlightedEl.style.outline = '';
        highlightedEl.style.outlineOffset = '';
        highlightedEl.removeAttribute('data-tracking-region-id');
        highlightedEl.removeAttribute('data-tracking-region-number');
        highlightedEl.removeAttribute('data-tracking-region-status');
      } else {
        highlightedEl.style.border = '';
        highlightedEl.style.boxShadow = '';
        highlightedEl.removeAttribute('data-region-number');
        highlightedEl.removeAttribute('data-cdp-extracted-id');
        highlightedEl.removeAttribute('data-region-highlight');
        highlightedEl.removeAttribute('data-manual-highlight');
        highlightedEl.removeAttribute('data-ui-marker');
        if (highlightedEl.getAttribute('data-ui-was-relative') === 'true') {
          highlightedEl.style.position = '';
          highlightedEl.removeAttribute('data-ui-was-relative');
        }
      }
    }

    // 4. 从 activeMarkers 中移除以便停止同步循环
    activeMarkers = activeMarkers.filter(m => {
        if (m.labelEl && m.labelEl.id === `ui-marker-container-${regionNumber}`) {
            return false;
        }
        return true;
    });

    trackedRegionMarkers = trackedRegionMarkers.filter((marker) => {
      if (marker.labelEl && marker.labelEl.id === `ui-region-container-${regionId}`) {
        return false;
      }
      return true;
    });

    sendResponse({ success: true });

  } else if (request.action === 'renumberMarkers') {
    // 根据 idMapping { oldId -> newId } 批量重排页面标签编号
    const { idMapping } = request;
    if (!idMapping) { sendResponse({ success: false }); return true; }

    for (const [oldId, newId] of Object.entries(idMapping)) {
      // 1. 更新标签容器 id 及内部文字
      const container = document.getElementById(`ui-marker-container-${oldId}`);
      if (container) {
        container.id = `ui-marker-container-${newId}`;
        container.setAttribute('data-node-id', newId);
        // 找到第一个 div 子元素（数字标签）更新文字
        const label = container.querySelector('div');
        if (label && /^\[\d+\]$/.test(label.textContent.trim())) {
          label.textContent = `[${newId}]`;
          label.id = `ui-marker-label-${newId}`;
        }
        // ⚠️ 同步更新删除按钮的 data-delete-cdp，修复闭包过期问题
        const delBtn = container.querySelector('[data-delete-cdp]');
        if (delBtn) {
          delBtn.setAttribute('data-delete-cdp', newId);
        }
      }

      // 2. 更新真实 DOM 元素上的 data-cdp-extracted-id
      const el = document.querySelector(`[data-cdp-extracted-id="${oldId}"]`);
      if (el) {
        el.setAttribute('data-cdp-extracted-id', newId);
      }

      // 3. 同步降级 box id
      const box = document.getElementById(`ui-marker-box-${oldId}`);
      if (box) {
        box.id = `ui-marker-box-${newId}`;
      }

      // 4. 同步 activeMarkers 记录，保持 rAF 位置循环正常
      for (const marker of activeMarkers) {
        if (String(marker.displayNumber) === String(oldId)) {
          marker.displayNumber = newId;
        }
      }
    }

    sendResponse({ success: true });

  } else if (request.action === 'applyNodeMetadata') {
    // 为通过框选添加的元素打上 data-cdp-extracted-id 标签，以便 drawCDPMarkers 能识别它
    const { id, selector, elementId, tempId } = request;
    let targetEl = null;
    let resolvedBy = null;
    const lookupDiagnostics = {
      id,
      tempId: tempId || '',
      elementId: elementId || '',
      selector: selector || ''
    };

    // 1. 优先通过临时 ID 找回确切的元素 (最准)
    if (tempId) {
      targetEl = document.querySelector(`[data-temp-selection-id="${tempId}"]`);
      if (targetEl) {
          resolvedBy = 'tempId';
          targetEl.removeAttribute('data-temp-selection-id'); // 用完即删
      }
    }

    // 2. 兜底方案 (如果 tempId 失效或未传)
    if (!targetEl && elementId) {
      targetEl = document.getElementById(elementId);
      if (targetEl) {
        resolvedBy = 'elementId';
      }
    }
    if (!targetEl && selector) {
      try {
        targetEl = document.querySelector(selector);
        if (targetEl) {
          resolvedBy = 'selector';
        }
      } catch (error) {
        lookupDiagnostics.selectorError = error.message;
      }
    }

    if (targetEl) {
      targetEl.setAttribute('data-cdp-extracted-id', id);
      logMarkerDebug('applyNodeMetadata.success', {
        resolvedBy,
        lookupDiagnostics,
        element: summarizeElementForMarkerDebug(targetEl)
      });
      sendResponse({ success: true });
    } else {
      logMarkerDebug('applyNodeMetadata.miss', {
        lookupDiagnostics,
        url: window.location.href
      });
      sendResponse({ success: false, message: '未找到元素', lookupDiagnostics });
    }
  } else if (request.action === 'getViewportInfo') {
    // 获取页面视口信息，用于坐标归一化
    const viewportInfo = {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      scrollWidth: document.body.scrollWidth,
      scrollHeight: document.body.scrollHeight
    };
    console.log('[ResolveDebug][content] getViewportInfo.response', viewportInfo);
    sendResponse(viewportInfo);
  }
  return true; // 保持消息通道开启
  });
