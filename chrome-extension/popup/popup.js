// 获取DOM元素
const closeBtn = document.getElementById('closeBtn');
const loading = document.getElementById('loading');
const status = document.getElementById('status');
const pageName = document.getElementById('pageName');
const pageUrl = document.getElementById('pageUrl');
const trackingSelectBtn = document.getElementById('trackingSelectBtn');
const selectionGenerateBtn = document.getElementById('selectionGenerateBtn');
const homeView = document.getElementById('homeView');
const trackingView = document.getElementById('trackingView');
const selectionView = document.getElementById('selectionView');
const viewTabButtons = Array.from(document.querySelectorAll('[data-view-tab]'));

// Chat UI elements
const chatView = document.getElementById('chatView');
const chatMessages = document.getElementById('chatMessages');
const chatEmptyState = document.getElementById('chatEmptyState');
const chatInput = document.getElementById('chatInput');
const chatSendBtn = document.getElementById('chatSendBtn');

// Claudable API Configuration
const trackingGateway = window.createTrackingGateway(window.API_CONFIG || {});
const MARKER_DEBUG_PREFIX = '[MarkerDebug][popup]';
const VIEW_NAMES = {
  home: 'home',
  tracking: 'tracking',
  selection: 'selection',
  chat: 'chat'
};
const SPECULATION_COORDINATE_WIDTH = 720;

let currentProjectId = null;
let currentStreamConnection = null;
let currentPageIdentity = null;
let currentViewportInfo = null;
let activeResolveRefreshTimer = null;
let activeStreamToken = 0;
let activeStreamProjectId = null;
let pendingLocalStreamClose = null;
let activeStreamState = null;
let streamReconnectTimer = null;
let activeViewName = VIEW_NAMES.home;

function deepClone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function logResolveDebug(stage, payload) {
  if (payload === undefined) {
    console.log(`[ResolveDebug][popup] ${stage}`);
    return;
  }
  console.log(`[ResolveDebug][popup] ${stage}`, payload);
}

function logMarkerDebug(stage, payload) {
  if (payload === undefined) {
    console.log(`${MARKER_DEBUG_PREFIX} ${stage}`);
    return;
  }
  console.log(`${MARKER_DEBUG_PREFIX} ${stage}`, payload);
}

function summarizeMarkerNode(node) {
  if (!node) return null;
  return {
    id: node.id ?? null,
    name: node.name || node.role || '',
    selector: node.selector || '',
    elementId: node.elementId || '',
    dataAiId: getNodeDataAiId(node),
    tempId: node.tempId || '',
    hasBox: Boolean(node.box),
    box: node.box ? {
      x: Math.round(node.box.x || 0),
      y: Math.round(node.box.y || 0),
      width: Math.round(node.box.width || 0),
      height: Math.round(node.box.height || 0)
    } : null
  };
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    };
    return map[char] || char;
  });
}

function createEmptyChangeSet() {
  if (typeof window.createEmptyTrackingChangeSet === 'function') {
    return window.createEmptyTrackingChangeSet();
  }
  return {
    added_regions: [],
    updated_regions: [],
    deleted_region_ids: [],
    rebound_regions: []
  };
}

function renumberRegions(regions) {
  regions.forEach((region, index) => {
    region.region_number = index + 1;
  });
}

function normalizeComparableText(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function isRelatedText(textA, textB) {
  if (!textA || !textB) return false;
  return textA === textB || textA.includes(textB) || textB.includes(textA);
}

function compactComparableText(value) {
  return normalizeComparableText(value).replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '');
}

function buildComparableBigrams(value) {
  const compact = compactComparableText(value);
  if (!compact) return [];
  if (compact.length < 2) return [compact];

  const bigrams = [];
  for (let index = 0; index < compact.length - 1; index += 1) {
    bigrams.push(compact.slice(index, index + 2));
  }
  return Array.from(new Set(bigrams));
}

function getTextSimilarityScore(textA, textB) {
  const normalizedA = normalizeComparableText(textA);
  const normalizedB = normalizeComparableText(textB);
  if (!normalizedA || !normalizedB) return 0;
  if (normalizedA === normalizedB) return 1;
  if (isRelatedText(normalizedA, normalizedB)) return 0.9;

  const compactA = compactComparableText(normalizedA);
  const compactB = compactComparableText(normalizedB);
  if (!compactA || !compactB) return 0;
  if (compactA === compactB) return 1;
  if (compactA.includes(compactB) || compactB.includes(compactA)) return 0.85;

  const bigramsA = buildComparableBigrams(compactA);
  const bigramsB = buildComparableBigrams(compactB);
  if (!bigramsA.length || !bigramsB.length) return 0;

  const bigramSetB = new Set(bigramsB);
  const overlap = bigramsA.filter((gram) => bigramSetB.has(gram)).length;
  return overlap / Math.max(1, Math.min(bigramsA.length, bigramsB.length));
}

function normalizeOptionalId(value) {
  const text = String(value ?? '').trim();
  if (!text || text === 'null' || text === 'undefined') return '';
  return text;
}

function getAnchorDataAiId(anchor) {
  if (!anchor || typeof anchor !== 'object') return '';
  const stable = anchor.stable_attributes || anchor.stableAttributes || {};
  return normalizeOptionalId(
    anchor['data-ai-id']
    || anchor.data_ai_id
    || anchor.dataAiId
    || stable['data-ai-id']
    || stable.data_ai_id
    || stable.dataAiId
  );
}

function getNodeDataAiId(item) {
  if (!item || typeof item !== 'object') return '';
  const element = item.element || {};
  return normalizeOptionalId(
    item['data-ai-id']
    || item.data_ai_id
    || item.dataAiId
    || element['data-ai-id']
    || element.data_ai_id
    || element.dataAiId
    || getAnchorDataAiId(item.anchor || element.anchor)
  );
}

function syncDraftRefs() {
  trackingState.regions = trackingState.draftDocument?.regions || [];
  trackingState.pageSpec = trackingState.draftDocument?.page_speculation || {};
}

function markRegionAsDraftAddition(region) {
  if (!region) return region;
  return {
    ...region,
    status: region.status === 'active' ? 'added' : (region.status || 'added')
  };
}

function getDraftDocumentForGenerationMerge() {
  if (trackingState.draftDocument) {
    return deepClone(trackingState.draftDocument);
  }
  if (trackingState.baselineDocument) {
    return deepClone(trackingState.baselineDocument);
  }
  return null;
}

function mergeGeneratedDocumentIntoDraft(generatedDocument) {
  const currentDraftDocument = getDraftDocumentForGenerationMerge();

  if (!currentDraftDocument) {
    const mergedDocument = deepClone(generatedDocument);
    mergedDocument.regions = mergedDocument.regions.map((region) => markRegionAsDraftAddition(region));
    renumberRegions(mergedDocument.regions);
    return {
      mergedDocument,
      appendedRegions: deepClone(mergedDocument.regions)
    };
  }

  const mergedRegions = deepClone(currentDraftDocument.regions || []);
  const existingRegionIds = new Set(mergedRegions.map((region) => region.region_id));
  const appendedRegionIds = new Set();

  generatedDocument.regions.forEach((region) => {
    if (!region || existingRegionIds.has(region.region_id)) return;
    const appendedRegion = markRegionAsDraftAddition(region);
    mergedRegions.push(appendedRegion);
    existingRegionIds.add(appendedRegion.region_id);
    appendedRegionIds.add(appendedRegion.region_id);
  });

  const shouldPreserveExistingPageSpeculation = Boolean(
    currentDraftDocument.page_binding_id
    || trackingState.pageBindingId
    || trackingState.baselineDocument?.page_binding_id
  );
  const shouldPreserveExistingRevision = Boolean(
    currentDraftDocument.page_binding_id
    || trackingState.pageBindingId
    || trackingState.baselineDocument?.page_binding_id
  );

  const mergedDocument = {
    ...deepClone(currentDraftDocument),
    ...deepClone(generatedDocument),
    page_speculation: deepClone(
      shouldPreserveExistingPageSpeculation
        ? (currentDraftDocument.page_speculation || {})
        : (generatedDocument.page_speculation || currentDraftDocument.page_speculation || {})
    ),
    page_binding_id: shouldPreserveExistingRevision
      ? (currentDraftDocument.page_binding_id || trackingState.pageBindingId || trackingState.baselineDocument?.page_binding_id || null)
      : (generatedDocument.page_binding_id || currentDraftDocument.page_binding_id || trackingState.pageBindingId || null),
    document_revision: shouldPreserveExistingRevision
      ? (currentDraftDocument.document_revision || trackingState.documentRevision || trackingState.baselineDocument?.document_revision || 0)
      : (generatedDocument.document_revision || currentDraftDocument.document_revision || trackingState.documentRevision || 0),
    project_id: generatedDocument.project_id || currentDraftDocument.project_id || trackingState.projectId || currentProjectId || null,
    page_identity: deepClone(generatedDocument.page_identity || currentDraftDocument.page_identity || currentPageIdentity || null),
    surfaces: Array.isArray(generatedDocument.surfaces) && generatedDocument.surfaces.length > 0
      ? deepClone(generatedDocument.surfaces)
      : deepClone(currentDraftDocument.surfaces || []),
    regions: mergedRegions
  };

  renumberRegions(mergedDocument.regions);

  return {
    mergedDocument,
    appendedRegions: mergedDocument.regions.filter((region) => appendedRegionIds.has(region.region_id))
  };
}

function isBaselineRegion(regionId) {
  return !!trackingState.baselineDocument?.regions?.some((region) => region.region_id === regionId);
}

function upsertChangeSetRegion(listName, region) {
  const targetList = trackingState.changeSet[listName];
  const existingIndex = targetList.findIndex((item) => item.region_id === region.region_id);
  if (existingIndex === -1) {
    targetList.push(deepClone(region));
  } else {
    targetList[existingIndex] = deepClone(region);
  }
}

function removeChangeSetRegion(listName, regionId) {
  trackingState.changeSet[listName] = trackingState.changeSet[listName].filter((item) => item.region_id !== regionId);
}

function getDeletedRegionPersistedId(region) {
  if (!region || region.id == null || region.id === '') return null;
  const numericId = Number(region.id);
  return Number.isFinite(numericId) ? numericId : region.id;
}

function markRegionUpdated(region) {
  if (!region) return;
  if (region.status === 'added' || !isBaselineRegion(region.region_id)) {
    region.status = 'added';
    upsertChangeSetRegion('added_regions', region);
    return;
  }
  region.status = 'modified';
  upsertChangeSetRegion('updated_regions', region);
}

function areAnchorsEquivalent(left, right) {
  return JSON.stringify(left || null) === JSON.stringify(right || null);
}

function enrichDocumentAnchorsFromSelectedNodes(documentInput, selectedNodes = currentExtractedNodes) {
  if (!documentInput) return null;

  const projectId = trackingState.projectId || currentProjectId || documentInput?.project_id || null;
  const normalizedDocument = createTrackingDocumentFromInput(documentInput, {
    pageBindingId: trackingState.pageBindingId,
    documentRevision: trackingState.documentRevision,
    projectId
  });

  if (!Array.isArray(selectedNodes) || selectedNodes.length === 0) {
    return normalizedDocument;
  }

  const remainingNodes = selectedNodes.map((node) => deepClone(node));
  const seededRegionIds = [];

  normalizedDocument.regions.forEach((region) => {
    const bestMatch = findBestMatchingTrackedNode(region, remainingNodes, 24);
    if (!bestMatch) return;

    const matchedNode = remainingNodes.splice(bestMatch.index, 1)[0];
    if (matchedNode?.anchor) {
      region.anchor = deepClone(matchedNode.anchor);
    }
    if (matchedNode?.elementId) {
      region.element_dom_id = matchedNode.elementId;
    }
    if (matchedNode?.id != null) {
      region.preview_node_id = String(matchedNode.id);
    }
    seededRegionIds.push(region.region_id);
    logMarkerDebug('enrichDocumentAnchorsFromSelectedNodes.seeded', {
      region_id: region.region_id,
      region_number: region.region_number,
      match_score: bestMatch.score,
      matched_node_id: matchedNode?.id ?? null,
      matched_node_name: matchedNode?.name || '',
      matched_node_element_id: matchedNode?.elementId || '',
      region_box: region.region || null,
      node_box: matchedNode?.box || null,
      node_speculation_box: getSpeculationComparableBoxFromNode(matchedNode)
    });
  });

  logMarkerDebug('enrichDocumentAnchorsFromSelectedNodes.result', {
    selectedNodeCount: selectedNodes.length,
    seededRegionIds
  });

  return normalizedDocument;
}

async function enrichDocumentAnchorsOnPage(tab, documentInput) {
  if (!tab?.id || !documentInput?.regions?.length) return null;

  const response = await sendTabMessage(tab.id, {
    action: 'enrichTrackingDocumentAnchors',
    document: deepClone(documentInput)
  });

  logMarkerDebug('enrichDocumentAnchorsOnPage.response', {
    regionCount: documentInput?.regions?.length || 0,
    response
  });

  return response?.success && response.document ? response : null;
}

function mergeEnrichedAnchorsIntoDraftDocument(documentInput) {
  if (!documentInput) return null;

  const projectId = trackingState.projectId || currentProjectId || trackingState.draftDocument?.project_id || null;
  const normalizedDocument = createTrackingDocumentFromInput(documentInput, {
    pageBindingId: trackingState.pageBindingId,
    documentRevision: trackingState.documentRevision,
    projectId
  });

  const previousRegionsById = new Map(
    (trackingState.draftDocument?.regions || []).map((region) => [region.region_id, deepClone(region)])
  );

  normalizedDocument.regions.forEach((region) => {
    const previousRegion = previousRegionsById.get(region.region_id);
    if (!previousRegion) return;

    region.status = previousRegion.status || region.status;
    region.id = previousRegion.id ?? region.id;
    region.element_id = previousRegion.element_id ?? region.element_id;
    region.section_id = previousRegion.section_id ?? region.section_id;
    region.element_code = previousRegion.element_code || region.element_code;
    region.element_name = previousRegion.element_name || region.element_name;
    region.section_code = previousRegion.section_code || region.section_code;
    region.section_name = previousRegion.section_name || region.section_name;

    if (!areAnchorsEquivalent(previousRegion.anchor, region.anchor)) {
      markRegionUpdated(region);
    }
  });

  trackingState.draftDocument = deepClone(normalizedDocument);
  syncDraftRefs();
  return normalizedDocument;
}

async function enrichDraftDocumentAnchorsOnPage(tab) {
  if (!tab?.id || !trackingState.draftDocument?.regions?.length) return null;

  try {
    const response = await enrichDocumentAnchorsOnPage(tab, trackingState.draftDocument);
    if (!response) {
      return response || null;
    }

    mergeEnrichedAnchorsIntoDraftDocument(response.document);
    return response;
  } catch (error) {
    console.warn('保存前升级 anchor 失败:', error.message);
    logMarkerDebug('enrichDraftDocumentAnchorsOnPage.error', {
      message: error.message
    });
    return null;
  }
}

function isSupportedPageUrl(url) {
  const text = String(url || '');
  if (!text) return false;
  return !text.startsWith('chrome://')
    && !text.startsWith('chrome-extension://')
    && !text.startsWith('devtools://');
}

function updatePageMeta(tab) {
  if (pageName) {
    pageName.textContent = tab?.title || 'Unknown Page';
  }
  if (pageUrl) {
    pageUrl.textContent = tab?.url || 'Unknown URL';
  }
}

function resetPageRuntimeState() {
  currentPageIdentity = null;
  currentViewportInfo = null;
  currentProjectId = null;
  currentExtractedNodes = [];
  resetChatState();
  renderSnapshotResults([]);
}

function hasTrackingConfigLoaded(config) {
  if (!config || typeof config !== 'object') return false;
  return Boolean(
    config.app_info?.apps?.length
    || config.business_line_info?.business_lines?.length
    || config.page_info?.pages?.length
    || config.section_info?.sections?.length
    || config.element_info?.elements?.length
    || config.field_info?.fields?.length
  );
}

async function sendTabMessage(tabId, payload) {
  const debugAction = payload?.action === 'getViewportInfo' || payload?.action === 'getPageIdentity';
  if (debugAction) {
    logResolveDebug(`sendTabMessage.request:${payload.action}`, {
      tabId,
      payload
    });
  }

  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, payload, (response) => {
      if (chrome.runtime.lastError) {
        const error = new Error(chrome.runtime.lastError.message);
        if (debugAction) {
          logResolveDebug(`sendTabMessage.error:${payload.action}`, {
            tabId,
            message: error.message
          });
        }
        reject(error);
        return;
      }
      if (debugAction) {
        logResolveDebug(`sendTabMessage.response:${payload.action}`, {
          tabId,
          response
        });
      }
      resolve(response);
    });
  });
}

async function detectTabHasDataAiId(tabId) {
  if (!tabId || !chrome.scripting?.executeScript) return false;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => Boolean(document.querySelector('[data-ai-id]'))
    });
    return Boolean(results?.[0]?.result);
  } catch (error) {
    logResolveDebug('detectTabHasDataAiId.error', {
      tabId,
      message: error.message
    });
    return false;
  }
}

async function ensureCurrentPageIdentity(tab) {
  if (currentPageIdentity) {
    logResolveDebug('ensureCurrentPageIdentity.cache_hit', currentPageIdentity);
    return currentPageIdentity;
  }
  if (!tab?.id) {
    throw new Error('无法获取当前标签页');
  }

  logResolveDebug('ensureCurrentPageIdentity.start', {
    tabId: tab.id,
    url: tab.url,
    title: tab.title
  });
  const pageIdentity = await sendTabMessage(tab.id, { action: 'getPageIdentity' });
  if (!pageIdentity) {
    throw new Error('未获取到页面身份信息');
  }

  const hasDataAiId = await detectTabHasDataAiId(tab.id);
  currentPageIdentity = hasDataAiId
    ? { ...pageIdentity, origin: '127.0.0.1' }
    : pageIdentity;
  logResolveDebug('ensureCurrentPageIdentity.success', currentPageIdentity);
  return currentPageIdentity;
}

// 显示加载状态
function showLoading() {
  loading.classList.remove('hidden');
}

// 隐藏加载状态
function hideLoading() {
  loading.classList.add('hidden');
}

// 显示状态信息
function showStatus(message, type) {
  status.textContent = message;
  status.className = 'status ' + (type === 'success' ? 'status-success' : 'status-error');
  status.classList.remove('hidden');

  // 3秒后自动隐藏
  setTimeout(() => {
    status.classList.add('hidden');
  }, 3000);
}

function setActiveView(viewName) {
  if (!Object.values(VIEW_NAMES).includes(viewName)) return;

  activeViewName = viewName;
  homeView?.classList.toggle('hidden', viewName !== VIEW_NAMES.home);
  trackingView?.classList.toggle('hidden', viewName !== VIEW_NAMES.tracking);
  selectionView?.classList.toggle('hidden', viewName !== VIEW_NAMES.selection);
  chatView?.classList.toggle('hidden', viewName !== VIEW_NAMES.chat);

  viewTabButtons.forEach((button) => {
    button.classList.toggle('active', button.dataset.viewTab === viewName);
  });
}

function updateChatEmptyState() {
  if (!chatEmptyState) return;
  const hasMessages = chatMessages.querySelector('.msg-group');
  chatEmptyState.classList.toggle('hidden', Boolean(hasMessages));
}

function updateChatInputState() {
  const canChat = Boolean(currentProjectId);

  chatInput.disabled = !canChat;
  chatSendBtn.disabled = !canChat;
  chatInput.placeholder = canChat
    ? '直接提出你的问题，或描述你想要查看的数据...'
    : '先在“标注位置”页设计新增区域埋点，再继续对话';

  if (!canChat) {
    chatSendBtn.classList.remove('active');
  } else if (chatInput.value.trim()) {
    chatSendBtn.classList.add('active');
  }

  updateSelectionActionState();
}

function resetChatState() {
  chatMessages.querySelectorAll('.msg-group').forEach((node) => node.remove());
  chatInput.value = '';
  chatInput.style.height = 'auto';
  chatSendBtn.classList.remove('active');
  updateChatEmptyState();
  updateChatInputState();
}

viewTabButtons.forEach((button) => {
  button.addEventListener('click', () => {
    setActiveView(button.dataset.viewTab);
  });
});

// 关闭按钮点击事件
closeBtn.addEventListener('click', () => {
  window.close();
});

async function startSelectionMode() {
  showLoading();
  setActiveView(VIEW_NAMES.selection);

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    if (!tab) {
      showStatus('无法获取当前标签页', 'error');
      hideLoading();
      return;
    }

    // 发送消息到content script启动框选模式
    chrome.tabs.sendMessage(tab.id, { action: 'startSelection' }, (response) => {
      hideLoading();

      if (chrome.runtime.lastError) {
        showStatus('请刷新页面后再试', 'error');
        console.error('Runtime error:', chrome.runtime.lastError);
        return;
      }

      if (response && response.success) {
        showStatus(response.message || '框选模式已启动，请在页面上拖拽框选', 'success');
      } else {
        showStatus(response?.message || '启动框选失败', 'error');
      }
    });
  } catch (error) {
    hideLoading();
    showStatus('发生错误: ' + error.message, 'error');
    console.error('Error:', error);
  }
}

// 识别可交互埋点按钮点击事件
const snapshotBtn = document.getElementById('snapshotBtn');
if (snapshotBtn) {
  snapshotBtn.addEventListener('click', async () => {
    setActiveView(VIEW_NAMES.selection);
    showLoading();

    // 清除上一次的结果显示
    const oldSnapshotDiv = document.getElementById('snapshotResult');
    if (oldSnapshotDiv) {
      oldSnapshotDiv.classList.add('hidden');
    }

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

      if (!tab) {
        showStatus('无法获取当前标签页', 'error');
        hideLoading();
        return;
      }

      // 发送消息到 background script (使用 CDP 提取)
      chrome.runtime.sendMessage({
        action: 'extractCDPSnapshot',
        tabId: tab.id,
        startNumber: getMaxUsedAnnotationNumber()
      }, async (response) => {
        hideLoading();

        if (chrome.runtime.lastError) {
          showStatus('提取失败，请确保您允许了插件顶部的调试权限', 'error');
          console.error('Runtime error:', chrome.runtime.lastError);
          return;
        }

        if (response && response.success) {
          // 提取快照完成后，自动启动框选模式
          chrome.tabs.sendMessage(tab.id, { action: 'startSelection' }, (selResponse) => {
            if (chrome.runtime.lastError) {
              console.warn('启动框选模式失败:', chrome.runtime.lastError.message);
              return;
            }
            if (selResponse && selResponse.success) {
              showStatus(selResponse.message || '框选模式已启动，请在页面上拖拽框选', 'success');
            }
          });
          showStatus(response.message || '快照提取完成，请开始框选', 'success');

          // 注意：手动框选列表会在框选完成后（elementSelected）自动显示

          // =============== 添加详细的控制台打印 ===============
          console.log("\n\n==== 1. 🎯 [处理完毕] 提取的结构化纯文本 ====");
          console.log("这正是可以直接喂给大模型 (LLM) 的极其紧凑的纯文本格式：");
          console.log(response.textSnapshot);
          console.log("=========================================================\n\n");

          if (response.interactiveNodes && response.interactiveNodes.length > 0) {
            console.log('提取出的包含坐标的纯节点数组:', response.interactiveNodes);

            const freshNodes = response.interactiveNodes.filter((node) => !hasExistingExtractedNode(node));
            currentExtractedNodes = [...currentExtractedNodes, ...freshNodes];

            // 渲染提取到的节点到 UI
            renderSnapshotResults(currentExtractedNodes);

            if (freshNodes.length === 0) {
              showStatus('未发现新的可交互元素，已跳过已有标注', 'success');
            }

            // 通知 content script 在页面上按照拿到的坐标纯悬浮画框
            try {
              if (trackingState.markerRenderMode === 'resolved') {
                chrome.tabs.sendMessage(tab.id, {
                  action: 'drawCDPMarkers',
                  nodes: currentExtractedNodes,
                  regions: trackingState.regions,
                  options: { skipMatchedRegions: true }
                }, () => {
                  if (chrome.runtime.lastError) {
                    showStatus('请刷新页面后重试（扩展需要重新注入到页面）', 'error');
                    console.warn('页面上的标记可能未更新:', chrome.runtime.lastError.message);
                  }
                });
              } else {
                await drawPreviewMarkersOnPage(tab);
              }
            } catch (e) {
              showStatus('请刷新页面后重试', 'error');
              console.warn('发送消息到页面失败:', e.message);
            }
          }

        } else {
          showStatus('提取失败: ' + (response ? response.message : '未知'), 'error');
        }
      });
    } catch (error) {
      hideLoading();
      showStatus('发生错误: ' + error.message, 'error');
      console.error('Error:', error);
    }
  });
}

/**
 * 渲染快照提取出的结果列表
 */
function renderSnapshotResults(nodes) {
  const container = document.getElementById('snapshotResult');
  const contentContainer = document.getElementById('snapshotResultContent');
  const countSpan = document.getElementById('snapshotCount');
  const emptyState = document.getElementById('selectionEmptyState');

  const hasNodes = Array.isArray(nodes) && nodes.length > 0;

  if (!hasNodes) {
    container.classList.add('hidden');
    emptyState?.classList.remove('hidden');
    updateSelectionActionState();
    return;
  }

  emptyState?.classList.add('hidden');
  container.classList.remove('hidden');
  countSpan.textContent = nodes.length;
  contentContainer.innerHTML = '';

  nodes.forEach((node) => {
    const item = document.createElement('div');
    item.className = 'snapshot-item';
    item.innerHTML = `
      <div class="snapshot-item-info">
        <span class="snapshot-item-number">${node.id}</span>
        <span class="snapshot-item-name" title="${node.name || node.role}">${node.name || node.role}</span>
      </div>
      <div style="display: flex; gap: 8px; align-items: center;">
        <svg class="snapshot-highlight-btn" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0ea5e9" stroke-width="2" title="定位元素">
          <path d="M5 12h14M12 5l7 7-7 7"></path>
        </svg>
        <svg class="snapshot-delete-btn" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" title="删除标注">
          <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"></path>
        </svg>
      </div>
    `;

    // 点击整行或定位图标进行高亮
    item.addEventListener('click', async (e) => {
      // 如果点击的是删除按钮，不触发高亮
      if (e.target.closest('.snapshot-delete-btn')) return;

      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab) {
        chrome.tabs.sendMessage(tab.id, {
          action: 'highlightByRegionNumber',
          regionNumber: node.id,
          selector: node.selector,
          elementId: node.elementId,
          dataAiId: getNodeDataAiId(node),
          box: node.box
        });
      }
    });

    // 点击删除按钮
    const deleteBtn = item.querySelector('.snapshot-delete-btn');
    deleteBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const nodeId = node.id;

      // 1. 从全局变量中移除已删除节点
      currentExtractedNodes = currentExtractedNodes.filter(n => n.id !== nodeId);
      const idMapping = rebaseExtractedNodeIds();

      // 2. 重新渲染侧边栏列表
      renderSnapshotResults(currentExtractedNodes);

      // 3. 通知页面移除被删除的标记
      await syncExtractedNodeMarkers({
        removedId: nodeId,
        idMapping
      });

      showStatus(`已删除标注 [${nodeId}]`, 'success');
    });

    contentContainer.appendChild(item);
  });

  updateSelectionActionState();
}

function updateSelectionActionState() {
  if (!selectionGenerateBtn) return;

  const hasSelectionNodes = Array.isArray(currentExtractedNodes) && currentExtractedNodes.length > 0;
  const canGenerate = hasSelectionNodes && !currentProjectId;

  selectionGenerateBtn.disabled = !canGenerate;
  if (!hasSelectionNodes) {
    selectionGenerateBtn.title = '请先到“埋点列表”页点击“添加框选”';
    return;
  }

  selectionGenerateBtn.title = currentProjectId
    ? '当前已进入对话流程，请切到“对话”页继续'
    : '根据当前新增标注设计新增区域埋点';
}

// === Claudable Chat Logic ===

// Generate a random project ID
function generateProjectId() {
  return 'ext-' + Math.random().toString(36).substring(2, 10);
}

// 添加消息到聊天界面
function appendMessage(role, content, isHtml = false) {
  const msgGroup = document.createElement('div');
  msgGroup.className = 'msg-group';

  const msgDiv = document.createElement('div');
  msgDiv.className = role === 'user' ? 'msg-user' : 'msg-ai';

  if (isHtml) {
    msgDiv.innerHTML = content;
  } else {
    // 简单的 Markdown 转换 (如果有必要可以用 marked.js 等库，这是最基本的处理)
    msgDiv.innerHTML = content.replace(/\n/g, '<br/>');
  }

  msgGroup.appendChild(msgDiv);
  chatMessages.appendChild(msgGroup);
  updateChatEmptyState();

  // 滚动到底部
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function clearProjectStreamReconnectTimer() {
  if (streamReconnectTimer) {
    clearTimeout(streamReconnectTimer);
    streamReconnectTimer = null;
  }
}

function createStreamState(projectId) {
  return {
    projectId,
    reconnectAttempt: 0,
    hasCompleted: false,
    hasEverConnected: false,
    seenEventKeys: new Set()
  };
}

function buildProjectStreamEventKey(data) {
  if (!data || typeof data !== 'object') return null;

  const payload = data.data || {};
  const parts = [
    data.type || 'unknown',
    payload.id || '',
    payload.message_type || '',
    payload.status || '',
    data.timestamp || payload.created_at || '',
    payload.role || ''
  ];

  if (!payload.id && payload.content) {
    parts.push(String(payload.content).slice(0, 120));
  }

  return parts.join('|');
}

function closeCurrentProjectStream(reason, extra = {}) {
  if (!currentStreamConnection?.close) return;

  clearProjectStreamReconnectTimer();

  pendingLocalStreamClose = {
    token: activeStreamToken,
    projectId: activeStreamProjectId || currentProjectId || null,
    reason,
    extra,
    at: new Date().toISOString()
  };

  currentStreamConnection.close();
}

function handleProjectStreamEvent(data, streamState = activeStreamState) {
  console.log('Project stream event received:', data);

  const eventKey = buildProjectStreamEventKey(data);
  if (eventKey && streamState?.seenEventKeys?.has(eventKey)) {
    console.log('Project stream duplicate event skipped', {
      projectId: streamState.projectId,
      eventKey
    });
    return;
  }
  if (eventKey && streamState?.seenEventKeys) {
    streamState.seenEventKeys.add(eventKey);
  }

  if (data.type === 'message' && data.data) {
    const msg = data.data;
    if (msg.role === 'assistant' && msg.message_type === 'chat') {
      appendMessage('ai', msg.content);
    } else if (msg.message_type === 'error') {
      appendMessage('ai', `[Error] ${msg.content}`);
    }
  } else if (data.type === 'act_complete') {
    const analyzingMsg = document.getElementById('analyzing-msg');
    if (analyzingMsg && data.data?.status === 'completed') {
      if (streamState) {
        streamState.hasCompleted = true;
      }
      analyzingMsg.style.display = 'none';
    }
    if (currentProjectId) {
      fetchAndRenderSpeculation(currentProjectId);
    }
  }
}

function scheduleProjectStreamReconnect(projectId, streamState, closeMeta = {}) {
  if (!streamState || activeStreamState !== streamState || currentProjectId !== projectId) {
    return;
  }

  clearProjectStreamReconnectTimer();

  streamState.reconnectAttempt = (streamState.reconnectAttempt || 0) + 1;
  const attempt = streamState.reconnectAttempt;
  const delayMs = Math.min(10000, 1000 * (2 ** Math.min(attempt - 1, 3)));

  console.warn('Project stream reconnect scheduled', {
    projectId,
    attempt,
    delayMs,
    closeMeta
  });
  showStatus(`对话流连接中断，${Math.round(delayMs / 1000)} 秒后进行第 ${attempt} 次重连`, 'error');

  streamReconnectTimer = setTimeout(() => {
    if (!activeStreamState || activeStreamState !== streamState || currentProjectId !== projectId) {
      return;
    }

    console.warn('Project stream reconnecting', {
      projectId,
      attempt
    });
    openProjectStreamConnection(projectId, {
      streamState
    });
  }, delayMs);
}

function openProjectStreamConnection(projectId, options = {}) {
  const streamState = options.streamState || activeStreamState || createStreamState(projectId);
  const resolveOnOpen = options.resolveOnOpen || null;
  const streamToken = Date.now() + Math.random();
  activeStreamToken = streamToken;
  activeStreamProjectId = projectId;
  activeStreamState = streamState;
  clearProjectStreamReconnectTimer();

  currentStreamConnection = trackingGateway.connectProjectStream(projectId, {
    onOpen() {
      const wasReconnect = streamState.hasEverConnected || (streamState.reconnectAttempt || 0) > 0;
      streamState.hasEverConnected = true;
      streamState.reconnectAttempt = 0;
      console.log(wasReconnect ? 'Project stream reconnected' : 'Project stream connected', {
        projectId,
        streamToken
      });
      if (wasReconnect) {
        showStatus('对话流已重连，继续等待后端消息', 'success');
      }
      if (typeof resolveOnOpen === 'function') {
        resolveOnOpen(currentStreamConnection);
      }
    },
    onMessage(data) {
      try {
        handleProjectStreamEvent(data, streamState);
      } catch (error) {
        console.error('Error handling stream event:', error);
      }
    },
    onClose(event) {
      const localCloseMeta = pendingLocalStreamClose?.token === streamToken
        ? pendingLocalStreamClose
        : null;
      if (localCloseMeta) {
        pendingLocalStreamClose = null;
      }

      const isCurrentStream = activeStreamToken === streamToken;
      const shouldReconnect = isCurrentStream
        && !localCloseMeta
        && !streamState.hasCompleted
        && currentProjectId === projectId;

      if (isCurrentStream) {
        currentStreamConnection = null;
        activeStreamProjectId = null;
      }

      console.log('Project stream disconnected', {
        projectId,
        streamToken,
        initiatedBy: localCloseMeta ? 'local' : 'remote_or_unknown',
        localCloseMeta,
        code: event?.code ?? null,
        reason: event?.reason ?? '',
        wasClean: event?.wasClean ?? null,
        isCurrentStream,
        hasCompleted: streamState.hasCompleted,
        shouldReconnect
      });

      if (shouldReconnect) {
        scheduleProjectStreamReconnect(projectId, streamState, {
          code: event?.code ?? null,
          reason: event?.reason ?? '',
          wasClean: event?.wasClean ?? null
        });
      }
    },
    onError(event) {
      console.warn('Project stream error', {
        projectId,
        streamToken,
        message: event?.message || null
      });
    }
  });
}

function connectProjectStream(projectId) {
  return new Promise((resolve) => {
    if (currentStreamConnection?.close) {
      closeCurrentProjectStream('connectProjectStream.replace_existing', {
        nextProjectId: projectId
      });
    }

    activeStreamState = createStreamState(projectId);
    openProjectStreamConnection(projectId, {
      streamState: activeStreamState,
      resolveOnOpen: resolve
    });
  });
}

async function handleGeneratePlan() {
  if (!currentExtractedNodes.length) {
    showStatus('请先添加至少一个标注位置', 'error');
    setActiveView(VIEW_NAMES.selection);
    return;
  }

  showLoading();

  try {
    // 获取当前活动标签页
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    if (!tab) {
      showStatus('无法获取当前标签页', 'error');
      hideLoading();
      return;
    }

    // 0. 获取页面视口信息，用于坐标归一化
    const viewportInfo = await sendTabMessage(tab.id, { action: 'getViewportInfo' })
      .catch(() => ({ viewportWidth: 1920, viewportHeight: 1080 }));
    currentViewportInfo = viewportInfo;
    await ensureCurrentPageIdentity(tab);

    const scale = SPECULATION_COORDINATE_WIDTH / viewportInfo.viewportWidth;

    console.log('[归一化坐标计算] 视口宽度:', viewportInfo.viewportWidth, '归一化宽度:', SPECULATION_COORDINATE_WIDTH, '缩放比例:', scale);

    // 1. 直接用用户当前保留的标注节点构建语义上下文（不重新扫描，尊重用户的删减操作）
    let semanticContextText = '';
    if (currentExtractedNodes && currentExtractedNodes.length > 0) {
      currentExtractedNodes.forEach(node => {
        const ctx = node.semanticContext || {};
        const page = ctx.page || 'Unknown Page';
        const block = ctx.block || 'Unknown Block';
        const dataAiId = getNodeDataAiId(node);
        semanticContextText += `标注 [${node.id}]:\n`;
        semanticContextText += `- 页面: ${page}\n`;
        semanticContextText += `- 区块: ${block}\n`;
        semanticContextText += `- 元素: ${node.role}: ${node.name || 'unnamed'}\n`;
        if (dataAiId) semanticContextText += `- data-ai-id: ${dataAiId}\n`;
        if (node.className) semanticContextText += `- Class: ${node.className.substring(0, 60)}\n`;
        // 添加归一化坐标（基于320宽度坐标系）
        // top: 元素上边框到视口顶部的距离
        // left: 元素左边框到视口左侧的距离
        // width: 元素宽度
        // height: 元素高度
        if (node.box) {
          const top = Math.round(node.box.y * scale);
          const left = Math.round(node.box.x * scale);
          const width = Math.round(node.box.width * scale);
          const height = Math.round(node.box.height * scale);
          console.log(`[归一化坐标计算] 标注[${node.id}] 原坐标: x=${node.box.x}, y=${node.box.y}, width=${node.box.width}, height=${node.box.height} → 归一化: top=${top}, left=${left}, width=${width}, height=${height}`);
          semanticContextText += `- 坐标: top: ${top}, left: ${left}, width: ${width}, height: ${height}\n`;
        }
        semanticContextText += `\n`;
      });
    } else {
      console.warn('[GeneratePlan] currentExtractedNodes 为空，请先点击“识别可交互埋点”');
    }

    console.log('[Popup] semanticContextText (from currentExtractedNodes):', semanticContextText);

    // 截图前：临时隐藏页面上所有标注元素，获取干净的原始截图
    const [tabForCapture] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabForCapture) {
      await sendTabMessage(tabForCapture.id, { action: 'hideMarkers' }).catch(() => null);
      // 给浏览器一帧时间完成重绘
      await new Promise(resolve => setTimeout(resolve, 80));
    }

    let dataUrl, captureError;
    try {
      const captureRes = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ action: 'captureScreen' }, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ error: chrome.runtime.lastError.message });
          } else if (!response) {
            resolve({ error: 'No response from background script' });
          } else {
            resolve(response);
          }
        });
      });
      dataUrl = captureRes.dataUrl;
      captureError = captureRes.error;
    } finally {
      // 截图后：无论成败都恢复标注
      if (tabForCapture) {
        chrome.tabs.sendMessage(tabForCapture.id, { action: 'showMarkers' });
      }
    }

    if (captureError || !dataUrl) {
      throw new Error(captureError || 'Failed to capture screen');
    }

    // Extract base64 part
    const base64Data = dataUrl.split(',')[1];

    // 生成新的 Project ID
    const projectId = generateProjectId();

    // 切换到聊天视图，显示加载态
    resetChatState();
    setActiveView(VIEW_NAMES.chat);
    appendMessage('user', '请帮我设计当前页面的埋点方案。');

    const analyzingDiv = document.createElement('div');
    analyzingDiv.id = 'analyzing-msg';
    analyzingDiv.className = 'msg-ai';
    analyzingDiv.innerHTML = '<div style="display:flex;align-items:center;gap:8px;"><span class="loader"></span> 正在分析页面...</div>';

    const msgGroup = document.createElement('div');
    msgGroup.className = 'msg-group';
    msgGroup.appendChild(analyzingDiv);
    chatMessages.appendChild(msgGroup);

    // 1. 创建 Project
    const resolvedPageSpec = trackingState.pageSpec || trackingState.draftDocument?.page_speculation || {};
    const resolvedPageInfoLines = [];
    if (trackingState.pageBindingId) {
      if (resolvedPageSpec.app_id != null && resolvedPageSpec.app_id !== '') {
        resolvedPageInfoLines.push(`- app_id: ${resolvedPageSpec.app_id}`);
      }
      if (resolvedPageSpec.app_name) {
        resolvedPageInfoLines.push(`- app_name: ${resolvedPageSpec.app_name}`);
      }
      if (resolvedPageSpec.business_line) {
        resolvedPageInfoLines.push(`- business_line: ${resolvedPageSpec.business_line}`);
      }
      if (resolvedPageSpec.page_id != null && resolvedPageSpec.page_id !== '') {
        resolvedPageInfoLines.push(`- page_id: ${resolvedPageSpec.page_id}`);
      }
      if (resolvedPageSpec.page_name) {
        resolvedPageInfoLines.push(`- page_name: ${resolvedPageSpec.page_name}`);
      }
    }

    const resolvedPageInfoText = resolvedPageInfoLines.length > 0
      ? `以下是当前页面已存在并应沿用的应用、业务线和页面信息：\n${resolvedPageInfoLines.join('\n')}\n\n`
      : '';

    const initialPrompt = `使用tracking-design-extension技能  现在我会为你提供页面中已标注元素的 **语义上下文 (Page-Block-Element)**。
${resolvedPageInfoText}请【完全根据以下提供的文本描述】进行埋点方案设计。
如果某个标注提供了 data-ai-id，请在输出 JSON 对应的 regions[].anchor["data-ai-id"] 和 regions[].anchor.stable_attributes["data-ai-id"] 中原样保留，用于后续页面回显定位。
${semanticContextText}`;

    await trackingGateway.createProject({
      need_data_verify: true,
      project_id: projectId,
      name: `Tracking Plan - ${tab.title || 'Page'}`,
      initial_prompt: initialPrompt,
      selected_model: 'minimax-m2.5-hithink',
      preferred_cli: 'tracking-design',
      selected_nodes: deepClone(currentExtractedNodes),
      page_identity: deepClone(currentPageIdentity),
      baseline_document: deepClone(trackingState.draftDocument),
      viewport_info: currentViewportInfo
    });

    // 2. 建立 WebSocket 连接
    currentProjectId = projectId;
    updateChatInputState();
    await connectProjectStream(projectId);

    // 3. 上传截图文件
    // 将 base64 转换为 Blob
    const byteCharacters = atob(base64Data);
    const byteNumbers = new Array(byteCharacters.length);
    for (let i = 0; i < byteCharacters.length; i++) {
      byteNumbers[i] = byteCharacters.charCodeAt(i);
    }
    const byteArray = new Uint8Array(byteNumbers);
    const blob = new Blob([byteArray], { type: 'image/jpeg' });

    // 构建 FormData 进行上传
    const formData = new FormData();
    formData.append('file', blob, 'screenshot.jpg');

    const uploadData = await trackingGateway.uploadAsset(projectId, formData);
    const imagePath = uploadData.absolute_path || uploadData.path;

    // 4. 调用 ACT 接口发起对话
    await trackingGateway.act(projectId, {
      instruction: initialPrompt,
      cli_preference: 'tracking-design',
      is_initial_prompt: true,
      selected_nodes: deepClone(currentExtractedNodes),
      page_identity: deepClone(currentPageIdentity),
      baseline_document: deepClone(trackingState.draftDocument),
      viewport_info: currentViewportInfo,
      images: [{
        name: 'screenshot.jpg',
        path: imagePath
      }]
    });

    hideLoading();
  } catch (error) {
    hideLoading();
    showStatus('发生错误: ' + error.message, 'error');
    console.error('Error generating plan:', error);

    appendMessage('ai', `[System Error] ${error.message}`);
    if (!currentStreamConnection) {
      currentProjectId = null;
      updateChatInputState();
    }
  }
}

if (trackingSelectBtn) {
  trackingSelectBtn.addEventListener('click', () => startSelectionMode());
}

if (selectionGenerateBtn) {
  selectionGenerateBtn.addEventListener('click', () => handleGeneratePlan());
}

// === Speculation 数据拉取与渲染 ===

async function fetchAndRenderSpeculation(projectId) {
  try {
    console.log(`[fetchAndRenderSpeculation] 收到 act_complete，开始请求数据，projectId=${projectId}`);
    const result = await trackingGateway.getProjectData(projectId);
    console.log('[fetchAndRenderSpeculation] ✅ 接口返回数据（完整）:', JSON.stringify(result, null, 2));

    // Switch to our interactive UI
    if (result.success && result.speculation) {
      const nodeAnchoredSpeculation = enrichDocumentAnchorsFromSelectedNodes(result.speculation, currentExtractedNodes);
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const enrichedResponse = tab
        ? await enrichDocumentAnchorsOnPage(tab, nodeAnchoredSpeculation).catch((error) => {
          console.warn('生成结果实时升级 anchor 失败:', error.message);
          logMarkerDebug('fetchAndRenderSpeculation.enrich.error', {
            message: error.message
          });
          return null;
        })
        : null;
      const speculationDocument = enrichedResponse?.document || nodeAnchoredSpeculation;

      renderSpeculationCards(speculationDocument, result.config || {}, {
        source: 'generated',
        projectId
      });
    }
  } catch (error) {
    console.error('[fetchAndRenderSpeculation] ❌ 请求失败:', error);
  }
}

// --- Tracking UI State ---
let trackingState = {
  config: null,
  projectId: null,
  pageBindingId: null,
  documentRevision: 0,
  baselineDocument: null,
  draftDocument: null,
  markerRenderMode: 'preview',
  changeSet: createEmptyChangeSet(),
  regions: [],
  pageSpec: {},
  appMap: new Map(),
  bizMap: new Map(),
  pageMap: new Map(),
  sectionMap: new Map(),
  elementMap: new Map(),
  fieldMap: new Map()
};

// 存储当前自动提取的语义节点
let currentExtractedNodes = [];
let lastProcessedSelectionTime = 0; // 用于防抖，防止框选消息被重复处理

function isSameNodeBox(boxA, boxB) {
  if (!boxA || !boxB) return false;
  return Math.abs((boxA.x || 0) - (boxB.x || 0)) <= 2
    && Math.abs((boxA.y || 0) - (boxB.y || 0)) <= 2
    && Math.abs((boxA.width || 0) - (boxB.width || 0)) <= 2
    && Math.abs((boxA.height || 0) - (boxB.height || 0)) <= 2;
}

function getTrackedRegionComparableBox(region) {
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

  const normalizedBox = region?.normalized_box || {};
  const viewportWidth = Number(currentViewportInfo?.viewportWidth) || 0;
  const viewportHeight = Number(currentViewportInfo?.viewportHeight) || 0;
  if (!viewportWidth || !viewportHeight) return null;

  const normalizedWidth = Math.round((Number(normalizedBox.width_ratio) || 0) * viewportWidth);
  const normalizedHeight = Math.round((Number(normalizedBox.height_ratio) || 0) * viewportHeight);
  if (normalizedWidth <= 0 || normalizedHeight <= 0) return null;

  return {
    x: Math.round((Number(normalizedBox.left_ratio) || 0) * viewportWidth),
    y: Math.round((Number(normalizedBox.top_ratio) || 0) * viewportHeight),
    width: normalizedWidth,
    height: normalizedHeight
  };
}

function getSpeculationComparableBoxFromNode(nodeOrBox) {
  const box = nodeOrBox?.box || nodeOrBox || null;
  const viewportWidth = Number(currentViewportInfo?.viewportWidth) || 0;
  if (!box || !viewportWidth) return null;

  const scale = SPECULATION_COORDINATE_WIDTH / viewportWidth;
  return {
    x: Math.round((Number(box.x) || 0) * scale),
    y: Math.round((Number(box.y) || 0) * scale),
    width: Math.round((Number(box.width) || 0) * scale),
    height: Math.round((Number(box.height) || 0) * scale)
  };
}

function buildComparableAnnotationCandidate(item) {
  const element = item?.element || {};
  const anchor = item?.anchor || element.anchor || {};
  return {
    selector: item?.selector || element.selector || anchor?.selector_candidates?.[0] || '',
    elementId: item?.elementId || element.id || anchor?.stable_attributes?.id || '',
    dataAiId: getNodeDataAiId(item),
    name: normalizeComparableText(
      item?.name
      || element.name
      || element.text
      || anchor?.text_signature?.accessible_name
      || anchor?.text_signature?.visible_text
      || element.tagName
      || ''
    ),
    box: item?.box || null
  };
}

function matchesExtractedNode(node, item) {
  const existing = buildComparableAnnotationCandidate(node);
  const candidate = buildComparableAnnotationCandidate(item);

  if (existing.dataAiId && candidate.dataAiId && existing.dataAiId === candidate.dataAiId) return true;
  if (existing.elementId && candidate.elementId && existing.elementId === candidate.elementId) return true;
  if (existing.box && candidate.box && isSameNodeBox(existing.box, candidate.box)) return true;
  if (existing.selector && candidate.selector && existing.selector === candidate.selector && isRelatedText(existing.name, candidate.name)) {
    return true;
  }
  return false;
}

function matchesTrackedRegion(region, item) {
  return getTrackedRegionMatchScore(region, item) >= 24;
}

function getTrackedRegionMatchScore(region, item) {
  const candidate = buildComparableAnnotationCandidate(item);
  const regionPreviewNodeId = String(region?.preview_node_id || '');
  const candidateNodeId = String(item?.id || '');
  const regionStableId = region?.anchor?.stable_attributes?.id || region?.element_dom_id || '';
  const regionDataAiId = getAnchorDataAiId(region?.anchor);
  const regionSelectors = Array.isArray(region?.anchor?.selector_candidates) ? region.anchor.selector_candidates.filter(Boolean) : [];
  const regionText = normalizeComparableText(
    region?.anchor?.text_signature?.normalized
    || region?.anchor?.text_signature?.accessible_name
    || region?.element_name
    || region?.element_code
    || ''
  );
  const regionBox = getTrackedRegionComparableBox(region);
  const candidateSpeculationBox = getSpeculationComparableBoxFromNode(candidate.box);
  const textSimilarity = getTextSimilarityScore(regionText, candidate.name);

  let score = 0;
  if (regionPreviewNodeId && candidateNodeId && regionPreviewNodeId === candidateNodeId) {
    score += 160;
  }
  if (candidate.dataAiId && regionDataAiId && candidate.dataAiId === regionDataAiId) {
    score += 220;
  }
  if (candidate.elementId && regionStableId && candidate.elementId === regionStableId) {
    score += 120;
  }
  if (candidate.selector && regionSelectors.includes(candidate.selector)) {
    score += textSimilarity >= 0.3 ? 28 : 10;
  }
  if (candidate.box && regionBox && isSameNodeBox(candidate.box, regionBox)) {
    score += textSimilarity >= 0.3 ? 36 : 18;
  }
  if (candidateSpeculationBox && regionBox && isSameNodeBox(candidateSpeculationBox, regionBox)) {
    score += textSimilarity >= 0.3 ? 32 : 16;
  }

  if (textSimilarity >= 0.9) {
    score += 30;
  } else if (textSimilarity >= 0.5) {
    score += 22;
  } else if (textSimilarity >= 0.3) {
    score += 14;
  } else if (regionText && candidate.name) {
    score -= 8;
  }

  return score;
}

function findBestMatchingTrackedNode(region, nodes = [], minScore = 24) {
  let bestMatch = null;
  nodes.forEach((node, index) => {
    const score = getTrackedRegionMatchScore(region, node);
    if (!bestMatch || score > bestMatch.score) {
      bestMatch = { index, node, score };
    }
  });
  if (!bestMatch || bestMatch.score < minScore) {
    return null;
  }
  return bestMatch;
}

function getMaxTrackedRegionNumber() {
  return trackingState.regions.reduce((maxNumber, region) => {
    const regionNumber = Number(region?.region_number);
    return Number.isFinite(regionNumber) ? Math.max(maxNumber, regionNumber) : maxNumber;
  }, 0);
}

function getMaxExtractedNodeNumber() {
  return currentExtractedNodes.reduce((maxNumber, node) => {
    const nodeId = Number(node?.id);
    return Number.isFinite(nodeId) ? Math.max(maxNumber, nodeId) : maxNumber;
  }, 0);
}

function getMaxUsedAnnotationNumber() {
  return Math.max(getMaxTrackedRegionNumber(), getMaxExtractedNodeNumber());
}

function rebaseExtractedNodeIds(startNumber = getMaxTrackedRegionNumber() + 1) {
  const idMapping = {};

  currentExtractedNodes = currentExtractedNodes.map((node, index) => {
    const oldId = String(node.id);
    const newId = String(startNumber + index);
    if (oldId !== newId) {
      idMapping[oldId] = newId;
    }
    return {
      ...node,
      id: newId
    };
  });

  return idMapping;
}

async function syncExtractedNodeMarkers({ removedId = null, idMapping = {} } = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  if (removedId != null) {
    await sendTabMessage(tab.id, {
      action: 'removeRegionMarker',
      regionNumber: String(removedId)
    }).catch((error) => {
      console.warn('removeRegionMarker 失败:', error.message, removedId);
    });
  }

  if (Object.keys(idMapping).length > 0) {
    await sendTabMessage(tab.id, {
      action: 'renumberMarkers',
      idMapping
    }).catch((error) => {
      console.warn('renumberMarkers 失败:', error.message, idMapping);
    });
  }
}

function hasExistingExtractedNode(selectionItem) {
  return currentExtractedNodes.some((node) => matchesExtractedNode(node, selectionItem))
    || trackingState.regions.some((region) => matchesTrackedRegion(region, selectionItem));
}

function getOrderedTrackingRegions(regions = trackingState.regions) {
  return [...(Array.isArray(regions) ? regions : [])]
    .filter((region) => region?.status !== 'deleted')
    .sort((left, right) => Number(left?.region_number || 0) - Number(right?.region_number || 0));
}

function getPreviewOverlayRegions(regions = trackingState.regions) {
  const orderedRegions = getOrderedTrackingRegions(regions);
  if (!trackingState.baselineDocument?.regions?.length) {
    return orderedRegions;
  }

  const baselineRegionIds = new Set(
    trackingState.baselineDocument.regions.map((region) => region.region_id)
  );

  return orderedRegions.filter((region) => !baselineRegionIds.has(region.region_id));
}

function hasPreviewDraftRegions() {
  return trackingState.markerRenderMode === 'preview' && getOrderedTrackingRegions().length > 0;
}

function buildPreviewNodeFromRegion(region, matchedNode = null) {
  const fallbackBox = getTrackedRegionComparableBox(region);
  return {
    id: String(region.region_number),
    domBindingId: matchedNode?.domBindingId || matchedNode?.id || region.preview_node_id || '',
    role: matchedNode?.role || region.control_type || 'element',
    name: matchedNode?.name || region.element_name || region.element_code || `region-${region.region_number}`,
    selector: matchedNode?.selector || region.anchor?.selector_candidates?.find(Boolean) || '',
    elementId: matchedNode?.elementId || region.anchor?.stable_attributes?.id || region.element_dom_id || '',
    dataAiId: getNodeDataAiId(matchedNode) || getAnchorDataAiId(region.anchor),
    box: matchedNode?.box || fallbackBox,
    semanticContext: matchedNode?.semanticContext || region.semantic_context || null,
    className: matchedNode?.className || '',
    isManual: matchedNode?.isManual || false
  };
}

function syncPreviewNodesWithDraftRegions(regions = trackingState.regions) {
  const orderedRegions = getPreviewOverlayRegions(regions);
  const remainingNodes = [...currentExtractedNodes];
  const nextNodes = [];

  orderedRegions.forEach((region) => {
    const bestMatch = findBestMatchingTrackedNode(region, remainingNodes, 24);
    const matchedNode = bestMatch ? remainingNodes.splice(bestMatch.index, 1)[0] : null;
    nextNodes.push(buildPreviewNodeFromRegion(region, matchedNode));
  });

  currentExtractedNodes = nextNodes;
}

function removePreviewNodeForRegion(region) {
  if (!region) return;

  const matchIndex = currentExtractedNodes.findIndex((node) => (
    String(node?.id) === String(region.region_number)
    || matchesTrackedRegion(region, node)
  ));

  if (matchIndex !== -1) {
    currentExtractedNodes.splice(matchIndex, 1);
  }
}

function syncPreviewStateAfterRegionRemoval(removedRegion) {
  removePreviewNodeForRegion(removedRegion);
  syncPreviewNodesWithDraftRegions(trackingState.regions);
}

function appendSelectedElements(selectionItems = []) {
  let maxId = getMaxUsedAnnotationNumber();

  const addedNodes = [];
  selectionItems.forEach((item) => {
    if (!item?.element || hasExistingExtractedNode(item)) return;

    const element = item.element;
    const newId = ++maxId;
    const newNode = {
      id: newId,
      role: element.type || element.tagName,
      name: element.name || element.text || element.tagName,
      selector: element.selector || element.anchor?.selector_candidates?.[0] || '',
      elementId: element.id || element.anchor?.stable_attributes?.id || '',
      dataAiId: getNodeDataAiId(item),
      box: item.box,
      semanticContext: item.semanticContext || null,
      anchor: deepClone(element.anchor || item.anchor || null),
      isManual: true
    };

    currentExtractedNodes.push(newNode);
    addedNodes.push({
      ...newNode,
      tempId: item.tempId
    });
  });

  return addedNodes;
}

async function syncSelectedElementsToPage(addedNodes) {
  if (!addedNodes.length) return;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  logMarkerDebug('syncSelectedElementsToPage.start', {
    tabId: tab.id,
    markerRenderMode: trackingState.markerRenderMode,
    addedNodes: addedNodes.map((node) => summarizeMarkerNode(node))
  });

  for (const node of addedNodes) {
    try {
      const response = await sendTabMessage(tab.id, {
        action: 'applyNodeMetadata',
        id: node.id,
        selector: node.selector,
        elementId: node.elementId,
        tempId: node.tempId
      });
      logMarkerDebug('syncSelectedElementsToPage.applyNodeMetadata.response', {
        node: summarizeMarkerNode(node),
        response
      });
    } catch (error) {
      console.warn('applyNodeMetadata 失败:', error.message, node);
      logMarkerDebug('syncSelectedElementsToPage.applyNodeMetadata.error', {
        node: summarizeMarkerNode(node),
        message: error.message
      });
    }
  }

  try {
    if (trackingState.markerRenderMode === 'resolved') {
      const response = await sendTabMessage(tab.id, {
        action: 'drawCDPMarkers',
        nodes: currentExtractedNodes,
        regions: trackingState.regions || [],
        options: { skipMatchedRegions: true }
      });
      logMarkerDebug('syncSelectedElementsToPage.drawCDPMarkers.response', response);
    } else {
      await drawPreviewMarkersOnPage(tab, {
        source: 'syncSelectedElementsToPage'
      });
    }
  } catch (error) {
    console.warn('drawCDPMarkers 失败:', error.message);
    logMarkerDebug('syncSelectedElementsToPage.draw.error', {
      message: error.message
    });
  }

  const firstNode = addedNodes[0];
  if (!firstNode) return;

  try {
    await sendTabMessage(tab.id, {
      action: 'highlightByRegionNumber',
      regionNumber: firstNode.id,
      selector: firstNode.selector,
      elementId: firstNode.elementId,
      box: firstNode.box
    });
  } catch (error) {
    console.warn('highlightByRegionNumber 失败:', error.message);
  }
}

function createTrackingDocumentFromInput(documentInput, options = {}) {
  if (typeof window.normalizeTrackingDocument === 'function') {
    return window.normalizeTrackingDocument(documentInput, {
      pageIdentity: currentPageIdentity,
      pageBindingId: options.pageBindingId,
      documentRevision: options.documentRevision,
      projectId: options.projectId,
      viewportInfo: currentViewportInfo
    });
  }
  return deepClone(documentInput);
}

async function clearPageMarkers(tab) {
  if (!tab?.id) return;

  await sendTabMessage(tab.id, {
    action: 'drawCDPMarkers',
    nodes: [],
    regions: []
  }).catch(() => null);

  await sendTabMessage(tab.id, { action: 'clearTrackingDocument' }).catch(() => null);
}

async function clearRecoveredTrackingState(tab, statusMessage) {
  closeCurrentProjectStream('clearRecoveredTrackingState', {
    statusMessage
  });
  currentStreamConnection = null;
  activeStreamProjectId = null;
  activeStreamState = null;
  trackingState.projectId = null;
  trackingState.pageBindingId = null;
  trackingState.documentRevision = 0;
  trackingState.baselineDocument = null;
  trackingState.draftDocument = null;
  trackingState.markerRenderMode = 'preview';
  trackingState.changeSet = createEmptyChangeSet();
  currentProjectId = null;
  currentExtractedNodes = [];
  resetChatState();
  syncDraftRefs();
  updateTopLevelUI();
  renderTrackingList();
  renderSnapshotResults([]);
  setActiveView(VIEW_NAMES.home);
  await clearPageMarkers(tab);
  showStatus(statusMessage, 'success');
}

async function drawPreviewMarkersOnPage(tab) {
  if (!tab?.id) return;

  try {
    const previewOverlayRegions = getPreviewOverlayRegions(trackingState.regions);
    logMarkerDebug('drawPreviewMarkersOnPage.start', {
      tabId: tab.id,
      nodeCount: currentExtractedNodes.length,
      previewOverlayRegionCount: previewOverlayRegions.length,
      hasBaselineDocument: Boolean(trackingState.baselineDocument?.regions?.length)
    });
    if (!trackingState.baselineDocument?.regions?.length) {
      await sendTabMessage(tab.id, { action: 'clearTrackingDocument' });
    }
    const response = await sendTabMessage(tab.id, {
      action: 'drawCDPMarkers',
      nodes: currentExtractedNodes,
      regions: previewOverlayRegions,
      options: { skipMatchedRegions: false }
    });
    logMarkerDebug('drawPreviewMarkersOnPage.response', response);
    return response;
  } catch (error) {
    console.warn('渲染草稿预览标记失败:', error.message);
    logMarkerDebug('drawPreviewMarkersOnPage.error', {
      message: error.message
    });
  }
}

async function renderMarkerStateOnPage() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  try {
    if (trackingState.markerRenderMode === 'resolved' && trackingState.draftDocument) {
      await sendTabMessage(tab.id, {
        action: 'drawCDPMarkers',
        nodes: [],
        regions: []
      });
      const response = await sendTabMessage(tab.id, {
        action: 'renderTrackingDocument',
        document: trackingState.draftDocument
      });
      logMarkerDebug('renderMarkerStateOnPage.renderTrackingDocument.response', response);
      return;
    }

    await drawPreviewMarkersOnPage(tab);
  } catch (error) {
    console.warn('渲染页面标记失败:', error.message);
    logMarkerDebug('renderMarkerStateOnPage.error', {
      message: error.message
    });
  }
}

function refreshTrackingStateUI() {
  syncDraftRefs();
  buildMaps();
  updateTopLevelUI();
  renderTrackingList();
  renderSnapshotResults(currentExtractedNodes);
  updateChatInputState();
  renderMarkerStateOnPage();
}

function setRecoveredDocument(documentInput, config, options = {}) {
  const trackingDocument = createTrackingDocumentFromInput(documentInput, options);
  if (options.pageBindingId) {
    trackingDocument.page_binding_id = options.pageBindingId;
  }
  if (Number.isFinite(Number(options.documentRevision))) {
    trackingDocument.document_revision = Number(options.documentRevision);
  }
  if (options.projectId) {
    trackingDocument.project_id = options.projectId;
  }

  trackingState.projectId = trackingDocument.project_id || trackingState.projectId || null;
  delete trackingDocument.project_id;
  trackingState.config = config || trackingState.config || {};
  trackingState.pageBindingId = trackingDocument.page_binding_id || null;
  trackingState.documentRevision = trackingDocument.document_revision || 1;
  trackingState.baselineDocument = deepClone(trackingDocument);
  trackingState.draftDocument = deepClone(trackingDocument);
  trackingState.markerRenderMode = 'resolved';
  trackingState.changeSet = createEmptyChangeSet();
  closeCurrentProjectStream('setRecoveredDocument', {
    pageBindingId: trackingState.pageBindingId,
    documentRevision: trackingState.documentRevision
  });
  currentStreamConnection = null;
  activeStreamProjectId = null;
  activeStreamState = null;
  currentProjectId = null;
  currentExtractedNodes = [];
  resetChatState();
  renderSnapshotResults([]);
  setActiveView(VIEW_NAMES.tracking);

  refreshTrackingStateUI();
}

function setGeneratedDocument(documentInput, config, options = {}) {
  const generatedDocument = createTrackingDocumentFromInput(documentInput, options);
  const { mergedDocument, appendedRegions } = mergeGeneratedDocumentIntoDraft(generatedDocument);
  trackingState.config = config || trackingState.config || {};
  trackingState.projectId = options.projectId || mergedDocument.project_id || trackingState.projectId || null;
  currentProjectId = options.projectId || mergedDocument.project_id || currentProjectId;

  if (!trackingState.baselineDocument) {
    trackingState.changeSet = createEmptyChangeSet();
    trackingState.changeSet.added_regions = deepClone(mergedDocument.regions);
  } else {
    appendedRegions.forEach((region) => upsertChangeSetRegion('added_regions', region));
  }

  trackingState.pageBindingId = mergedDocument.page_binding_id || trackingState.pageBindingId;
  trackingState.documentRevision = mergedDocument.document_revision || trackingState.documentRevision || 0;
  trackingState.draftDocument = deepClone(mergedDocument);
  trackingState.markerRenderMode = 'preview';
  syncPreviewNodesWithDraftRegions(trackingState.draftDocument.regions);

  refreshTrackingStateUI();
  setActiveView(VIEW_NAMES.tracking);
}

function deleteTrackingRegion(regionId) {
  const regionIndex = trackingState.regions.findIndex((region) => region.region_id === regionId);
  if (regionIndex === -1) return null;

  const [removedRegion] = trackingState.regions.splice(regionIndex, 1);
  renumberRegions(trackingState.regions);

  if (isBaselineRegion(regionId)) {
    const deletedRegionPersistedId = getDeletedRegionPersistedId(removedRegion);
    if (
      deletedRegionPersistedId != null
      && !trackingState.changeSet.deleted_region_ids.includes(deletedRegionPersistedId)
    ) {
      trackingState.changeSet.deleted_region_ids.push(deletedRegionPersistedId);
    }
    removeChangeSetRegion('updated_regions', regionId);
    removeChangeSetRegion('added_regions', regionId);
  } else {
    removeChangeSetRegion('added_regions', regionId);
  }

  return removedRegion;
}

function buildMaps() {
  trackingState.appMap.clear();
  trackingState.bizMap.clear();
  trackingState.pageMap.clear();
  trackingState.sectionMap.clear();
  trackingState.elementMap.clear();
  trackingState.fieldMap.clear();

  if (trackingState.config.app_info?.apps) {
    trackingState.config.app_info.apps.forEach(a => trackingState.appMap.set(String(a.id), a));
  }
  if (trackingState.config.business_line_info?.business_lines) {
    trackingState.config.business_line_info.business_lines.forEach(b => trackingState.bizMap.set(b.name, b));
  }
  if (trackingState.config.page_info?.pages) {
    trackingState.config.page_info.pages.forEach(p => trackingState.pageMap.set(String(p.id), p));
  }
  if (trackingState.config.section_info?.sections) {
    trackingState.config.section_info.sections.forEach(s => trackingState.sectionMap.set(String(s.id), s));
  }
  if (trackingState.config.element_info?.elements) {
    trackingState.config.element_info.elements.forEach(e => trackingState.elementMap.set(String(e.id), e));
  }
  if (trackingState.config.field_info?.fields) {
    trackingState.config.field_info.fields.forEach((field) => {
      if (field?.field_code) {
        trackingState.fieldMap.set(String(field.field_code), field);
      }
    });
  }
}

// 供 Modal 使用的临时变量
let activeSearchType = ''; // 'app', 'biz', 'page', 'section', 'element'
let activeRegionIndex = -1;

function renderSpeculationCards(speculation, config, options = {}) {
  if (options.source === 'generated') {
    setGeneratedDocument(speculation, config, options);
    return;
  }

  setRecoveredDocument(speculation, config, options);
}

function updateTopLevelUI() {
  const p = trackingState.pageSpec;

  // App
  const appBtn = document.getElementById('appSelectText');
  appBtn.textContent = p.app_name && p.app_code ? `${p.app_name} (${p.app_code})` : '请选择应用';

  // Biz
  const bizBtn = document.getElementById('bizSelectText');
  const bizCode = p.business_line;
  const bizObj = trackingState.bizMap.get(bizCode);
  bizBtn.textContent = bizObj ? (bizObj.chinese_name || bizObj.name) : (bizCode || '请选择业务线');

  // Page
  const pageBtn = document.getElementById('pageSelectText');
  let pageText = p.page_name && p.page_code ? `${p.page_name} (${p.page_code})` : '请选择页面';
  if (p.page_id === '待创建') {
    pageText += `<span class="new-tag">待创建</span>`;
  }
  pageBtn.innerHTML = pageText;
}

function calculateTrackKey(region) {
  const p = trackingState.pageSpec;
  const appCode = p.app_code || 'app';
  const bizLine = p.business_line || 'biz';
  const pageCode = p.page_code || 'page';
  const secCode = region.section_code || 'section';
  const eleCode = region.element_code || 'element';
  return `${appCode}_${bizLine}_${pageCode}_${secCode}_${eleCode}`;
}

function getActionName(actionCode) {
  const map = {
    '0': 'click(点击)', '1': 'scroll(滑动)', '2': 'exposure(曝光)', '3': 'input(输入)', '4': 'stay(停留)',
    '5': 'bidding(竞价)', '6': 'end(结束)', '7': 'dblclick(双击)', '8': 'hover(悬浮)', '9': 'pull(拉动)'
  };
  return map[actionCode] || actionCode;
}

function parseActionCodes(actionValue) {
  return String(actionValue || '')
    .split(',')
    .map((code) => code.trim())
    .filter(Boolean);
}

function getActionDisplay(actionValue) {
  const actionNames = parseActionCodes(actionValue).map((code) => getActionName(code));
  return actionNames.join(', ');
}

function getFieldOptions() {
  return trackingState.config?.field_info?.fields || [];
}

function getFieldInfoByCode(fieldCode) {
  if (!fieldCode) return null;
  return trackingState.fieldMap.get(String(fieldCode))
    || getFieldOptions().find((field) => field?.field_code === fieldCode)
    || null;
}

function getFieldPersistedId(field = {}) {
  if (!field || typeof field !== 'object') return null;
  if (field.id != null && field.id !== '') return field.id;
  if (field.field_id != null && field.field_id !== '') return field.field_id;
  if (field.fieldId != null && field.fieldId !== '') return field.fieldId;
  return null;
}

function resolveActionField(field = {}) {
  const fieldInfo = getFieldInfoByCode(field.fieldCode);
  return {
    id: getFieldPersistedId(field) ?? getFieldPersistedId(fieldInfo),
    fieldCode: field.fieldCode || '',
    fieldName: fieldInfo?.field_name || field.fieldName || '',
    dataType: fieldInfo?.data_type || field.dataType || 'string'
  };
}

function buildFieldPickerTriggerContent(field = {}) {
  const resolvedField = resolveActionField(field);
  if (!resolvedField.fieldCode) {
    return {
      code: '请选择字段',
      name: '支持按字段 code 或名称搜索'
    };
  }

  return {
    code: resolvedField.fieldCode,
    name: resolvedField.fieldName || '未匹配字段名称'
  };
}

function buildFieldOptionsHtml(selectedCode) {
  const fieldOptions = getFieldOptions();
  const normalizedCode = String(selectedCode || '');
  const hasSelectedCode = fieldOptions.some((field) => String(field.field_code) === normalizedCode);
  const placeholderLabel = fieldOptions.length > 0 ? '请选择字段' : '暂无字段配置';
  const options = [`<option value="">${placeholderLabel}</option>`];

  if (normalizedCode && !hasSelectedCode) {
    options.push(`<option value="${escapeHtml(normalizedCode)}" selected>${escapeHtml(normalizedCode)} (未匹配)</option>`);
  }

  fieldOptions.forEach((field) => {
    const fieldCode = String(field.field_code || '');
    const selected = normalizedCode === fieldCode ? ' selected' : '';
    options.push(`<option value="${escapeHtml(fieldCode)}"${selected}>${escapeHtml(fieldCode)}</option>`);
  });

  return options.join('');
}

function buildFieldPickerOptionsHtml(selectedCode, regionIdx, fieldIdx) {
  const fieldOptions = getFieldOptions();
  const normalizedCode = String(selectedCode || '');

  if (fieldOptions.length === 0) {
    return '<div class="field-picker-empty">暂无字段配置</div>';
  }

  const options = [];
  options.push(`
    <button
      type="button"
      class="field-picker-option${normalizedCode ? '' : ' is-selected'}"
      data-action="selectFieldOption"
      data-region="${regionIdx}"
      data-field="${fieldIdx}"
      data-field-code=""
      data-search-text="clear 清空 不选择字段"
    >
      <span class="field-picker-option-main">
        <span class="field-picker-option-code">不选择字段</span>
        <span class="field-picker-option-name">清空当前字段选择</span>
      </span>
    </button>
  `);

  fieldOptions.forEach((field) => {
    const fieldCode = String(field.field_code || '');
    const fieldName = String(field.field_name || '');
    const selectedClass = normalizedCode === fieldCode ? ' is-selected' : '';
    const searchText = normalizeComparableText(`${fieldCode} ${fieldName}`);

    options.push(`
      <button
        type="button"
        class="field-picker-option${selectedClass}"
        data-action="selectFieldOption"
        data-region="${regionIdx}"
        data-field="${fieldIdx}"
        data-field-code="${escapeHtml(fieldCode)}"
        data-search-text="${escapeHtml(searchText)}"
      >
        <span class="field-picker-option-main">
          <span class="field-picker-option-code">${escapeHtml(fieldCode)}</span>
          <span class="field-picker-option-name">${escapeHtml(fieldName || '未命名字段')}</span>
        </span>
      </button>
    `);
  });

  options.push('<div class="field-picker-empty field-picker-empty-search hidden">未找到匹配字段</div>');
  return options.join('');
}

function getFieldHelperText(field = {}) {
  const resolvedField = resolveActionField(field);
  if (!resolvedField.fieldCode) {
    return getFieldOptions().length > 0
      ? '字段选项来自返回结果的 field_info.fields'
      : '未获取到 field_info 字段列表';
  }

  const helperParts = [resolvedField.fieldName, resolvedField.dataType].filter(Boolean);
  return helperParts.join(' · ') || resolvedField.fieldCode;
}

function getSelectableFieldActions(region) {
  const actions = Array.isArray(region?.allow_action) ? region.allow_action : [];
  const normalized = [...new Set(actions.map((action) => String(action)).filter(Boolean))];
  return normalized.length > 0 ? normalized : ['0', '1', '2', '3', '4'];
}

function getPrimaryActionCode(actionValue) {
  return parseActionCodes(actionValue)[0] || '';
}

function buildActionOptionsHtml(region, selectedAction) {
  const actionOptions = getSelectableFieldActions(region);
  const normalizedAction = getPrimaryActionCode(selectedAction);
  const options = ['<option value="">请选择动作</option>'];

  if (normalizedAction && !actionOptions.includes(normalizedAction)) {
    options.push(`<option value="${escapeHtml(normalizedAction)}" selected>${escapeHtml(getActionName(normalizedAction))}</option>`);
  }

  actionOptions.forEach((actionCode) => {
    const selected = normalizedAction === actionCode ? ' selected' : '';
    options.push(`<option value="${escapeHtml(actionCode)}"${selected}>${escapeHtml(getActionName(actionCode))}</option>`);
  });

  return options.join('');
}

function closeAllFieldPickers(exceptPicker = null) {
  document.querySelectorAll('.field-picker.is-open').forEach((picker) => {
    if (exceptPicker && picker === exceptPicker) return;
    picker.classList.remove('is-open');
    const trigger = picker.querySelector('.field-picker-trigger');
    const searchInput = picker.querySelector('.field-picker-search');
    if (trigger) {
      trigger.setAttribute('aria-expanded', 'false');
    }
    if (searchInput) {
      searchInput.value = '';
    }
    filterFieldPickerOptions(picker, '');
  });
}

function filterFieldPickerOptions(picker, query = '') {
  if (!picker) return;
  const normalizedQuery = normalizeComparableText(query);
  let visibleCount = 0;

  picker.querySelectorAll('.field-picker-option').forEach((option) => {
    const searchText = option.dataset.searchText || '';
    const shouldShow = !normalizedQuery || searchText.includes(normalizedQuery);
    option.classList.toggle('hidden', !shouldShow);
    if (shouldShow) {
      visibleCount += 1;
    }
  });

  const emptyState = picker.querySelector('.field-picker-empty-search');
  if (emptyState) {
    emptyState.classList.toggle('hidden', visibleCount > 0);
  }
}

let fieldPickerGlobalListenerBound = false;

function ensureFieldPickerGlobalListener() {
  if (fieldPickerGlobalListenerBound) return;

  document.addEventListener('click', (event) => {
    if (!event.target.closest('.field-picker')) {
      closeAllFieldPickers();
    }
  });

  fieldPickerGlobalListenerBound = true;
}



function renderTrackingList() {
  const container = document.getElementById('trackingList');
  container.innerHTML = '';

  if (trackingState.regions.length === 0) {
    container.innerHTML = '<div class="tracking-empty-state">暂无埋点数据。<br>先点击上方“添加框选”开始标注。</div>';
    return;
  }

  trackingState.regions.forEach((region, index) => {
    const key = calculateTrackKey(region);
    const p = trackingState.pageSpec;

    // Combined Title: app-section-element
    const appName = p.app_name || 'app';
    const sectionName = region.section_name || '未选区块';
    const elementName = region.element_name || '未选元素';
    const combinedTitle = `${appName}-${sectionName}-${elementName}`;

    // Actions
    const actionsHtml = (region.allow_action || []).map(a =>
      `<span class="action-badge">${getActionName(a)}</span>`
    ).join('');

    // Detail View Dropdowns
    const sectionDisplay = `${region.section_name || '未选'} (${region.section_code || '-'})`;
    const elementDisplay = `${region.element_name || '未选'} (${region.element_code || '-'})`;

    const isExpanded = !!region._isExpanded;
    const card = document.createElement('div');
    card.className = `tracking-card ${isExpanded ? 'expanded' : ''}`;
    card.id = `tracking-card-${index}`;
    card.innerHTML = `
      <!-- Header (Always visible) -->
      <div class="tracking-card-header" data-index="${index}">
        <div class="card-header-top">
          <div class="icon-box chevron-icon" data-action="toggleExpand" data-index="${index}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <polyline points="9 18 15 12 9 6"></polyline>
            </svg>
          </div>
          <span class="region-index">${region.region_number}</span>
          <span class="tracking-card-title">${combinedTitle}</span>
          <div class="delete-card-btn" data-action="removeCard" data-index="${index}">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"></path>
            </svg>
          </div>
        </div>

        <div class="card-header-bottom">
          <div class="info-row">
            <span class="info-label">埋点Key</span>
            <span class="info-value">${key}</span>
          </div>
          <div class="info-row">
            <span class="info-label">支持动作</span>
            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
              ${actionsHtml || '<span class="info-value">无</span>'}
            </div>
          </div>

          <!-- Extra Fields Summary (Compact View) -->
          ${region.action_fields && region.action_fields.length > 0 ? `
            <div class="compact-fields-container">
              <div class="compact-fields-title">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                  <polyline points="14 2 14 8 20 8"></polyline>
                  <line x1="16" y1="13" x2="8" y2="13"></line>
                  <line x1="16" y1="17" x2="8" y2="17"></line>
                </svg>
                额外信息字段
              </div>
              <table class="compact-fields-table">
                <thead>
                  <tr>
                    <th>字段代码</th>
                    <th>字段名称</th>
                    <th>动作</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  ${region.action_fields.map((f) => {
                    const resolvedField = resolveActionField(f);
                    const actionDisplay = getActionDisplay(f.action) || '-';
                    const remark = f.remark || '-';
                    return `
                      <tr>
                        <td style="font-family: monospace;">${escapeHtml(resolvedField.fieldCode || '-')}</td>
                        <td>${escapeHtml(resolvedField.fieldName || resolvedField.fieldCode || '-')}</td>
                        <td>${escapeHtml(actionDisplay)}</td>
                        <td style="color: #64748b; font-style: italic;">${escapeHtml(remark)}</td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          ` : ''}
        </div>
      </div>

      <!-- Detail View (Expanded) -->
      <div class="tracking-card-detail">
        <div class="detail-input-group">
            <!-- 埋点Key Box -->
            <div class="field-row">
                <span class="info-label">埋点Key</span>
                <div class="styled-input-box">
                    <span class="info-value">${key}</span>
                </div>
            </div>

            <!-- 所属区块 Dropdown -->
            <div class="field-row">
                <span class="info-label">所属区块</span>
                <div class="styled-dropdown" data-action="openSearch" data-type="section" data-index="${index}">
                    <div class="dropdown-left">
                        <div class="icon-box">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="3" y="3" width="7" height="7"></rect>
                                <rect x="14" y="3" width="7" height="7"></rect>
                                <rect x="14" y="14" width="7" height="7"></rect>
                                <rect x="3" y="14" width="7" height="7"></rect>
                            </svg>
                        </div>
                        <span>${sectionDisplay}</span>
                    </div>
                    <div class="icon-box">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </div>
                </div>
            </div>

            <!-- 交互元素 Dropdown -->
            <div class="field-row">
                <span class="info-label">交互元素</span>
                <div class="styled-dropdown" data-action="openSearch" data-type="element" data-index="${index}">
                    <div class="dropdown-left">
                        <div class="icon-box">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M18 11V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"></path>
                                <path d="M14 10V4a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"></path>
                                <path d="M10 10.5V6a2 2 0 0 0-2-2v0a2 2 0 0 0-2 2v0"></path>
                                <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"></path>
                            </svg>
                        </div>
                        <span>${elementDisplay}</span>
                    </div>
                    <div class="icon-box">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </div>
                </div>
            </div>

            <!-- 功能说明 -->
            <div class="field-row">
                <span class="info-label">功能说明</span>
                <span class="info-value">${region.function_desc || '在此处展示功能说明'}</span>
            </div>

            <!-- 支持动作 -->
            <div class="field-row">
                <span class="info-label">支持动作</span>
                <div class="styled-dropdown">
                    <div class="dropdown-left tags-container">
                        ${(region.allow_action || []).map(a => `
                            <span class="tag-item">
                                ${getActionName(a)}
                                <span class="tag-close" data-action="removeAction" data-index="${index}" data-action-code="${a}">×</span>
                            </span>
                        `).join('')}
                        ${(!region.allow_action || region.allow_action.length === 0) ? '<span style="color: #94a3b8; font-size: 13px;">请选择动作...</span>' : ''}
                    </div>
                </div>
            </div>
        </div>

        <!-- Extra Fields Config Box -->
        <div class="extra-config-container">
            <div class="extra-config-title">
                <span class="extra-config-symbol">{ }</span>
                额外信息字段配置
            </div>
            <div class="extra-config-subtitle">字段列表取自返回数据的 field_info.fields</div>

            <div id="extraFieldsList-${index}">
                ${(region.action_fields || []).map((f, fIdx) => {
                  const resolvedField = resolveActionField(f);
                  const fieldTriggerContent = buildFieldPickerTriggerContent(f);
                  const selectedAction = getPrimaryActionCode(f.action);
                  return `
                    <div class="extra-field-card">
                        <div class="delete-field-btn" title="删除字段" data-action="removeField" data-region="${index}" data-field="${fIdx}">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M18 6L6 18M6 6l12 12"></path>
                            </svg>
                        </div>

                        <div class="extra-field-group">
                            <span class="extra-field-label">字段</span>
                            <div class="field-picker" data-region="${index}" data-field="${fIdx}">
                                <button
                                  type="button"
                                  class="extra-field-control field-picker-trigger${resolvedField.fieldCode ? ' has-value' : ''}"
                                  data-action="toggleFieldPicker"
                                  data-region="${index}"
                                  data-field="${fIdx}"
                                  aria-expanded="false"
                                >
                                    <span class="field-picker-trigger-copy">
                                        <span class="field-picker-trigger-code">${escapeHtml(fieldTriggerContent.code)}</span>
                                        <span class="field-picker-trigger-name">${escapeHtml(fieldTriggerContent.name)}</span>
                                    </span>
                                    <span class="field-picker-trigger-icon">
                                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <polyline points="6 9 12 15 18 9"></polyline>
                                      </svg>
                                    </span>
                                </button>
                                <div class="field-picker-panel">
                                    <div class="field-picker-search-shell">
                                        <svg class="field-picker-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                          <circle cx="11" cy="11" r="7"></circle>
                                          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                                        </svg>
                                        <input
                                          type="text"
                                          class="field-picker-search"
                                          placeholder="搜索字段 code / 名称"
                                          data-region="${index}"
                                          data-field="${fIdx}"
                                        >
                                    </div>
                                    <div class="field-picker-options">
                                        ${buildFieldPickerOptionsHtml(resolvedField.fieldCode, index, fIdx)}
                                    </div>
                                </div>
                            </div>
                            <span class="extra-field-helper">${escapeHtml(getFieldHelperText(f))}</span>
                        </div>

                        <div class="extra-field-group">
                            <span class="extra-field-label">触发动作</span>
                            <select class="extra-field-control extra-field-select" data-region="${index}" data-field="${fIdx}" data-key="action">
                                ${buildActionOptionsHtml(region, selectedAction)}
                            </select>
                        </div>

                        <div class="extra-field-group">
                            <span class="extra-field-label">备注说明</span>
                            <textarea class="extra-field-control extra-field-textarea" placeholder="填写备注..." data-region="${index}" data-field="${fIdx}" data-key="remark">${escapeHtml(f.remark || '')}</textarea>
                        </div>
                    </div>
                  `;
                }).join('')}
                ${(!region.action_fields || region.action_fields.length === 0) ? '<div class="empty-extra-fields">暂无字段</div>' : ''}
            </div>

            <div class="add-field-circle-btn" data-action="addField" data-index="${index}" title="添加额外字段">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                    <line x1="12" y1="5" x2="12" y2="19"></line>
                    <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
            </div>
        </div>
      </div>
    `;
    container.appendChild(card);
  });

  // Attach event listeners for dynamically created elements
  attachCardEventListeners();
}

// Attach event listeners for card elements
function attachCardEventListeners() {
  ensureFieldPickerGlobalListener();

  // Toggle expand buttons
  document.querySelectorAll('[data-action="toggleExpand"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const index = parseInt(btn.dataset.index);
      toggleCardExpand(index);
    });
  });

  // Delete card buttons
  document.querySelectorAll('[data-action="removeCard"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const index = parseInt(btn.dataset.index);
      const region = trackingState.regions[index];
      if (!region) return;
      const usePreviewDraft = hasPreviewDraftRegions();
      const removedRegion = deleteTrackingRegion(region.region_id);
      let idMapping = {};
      if (usePreviewDraft) {
        syncPreviewStateAfterRegionRemoval(removedRegion);
      } else {
        idMapping = rebaseExtractedNodeIds();
      }
      refreshTrackingStateUI();
      renderSnapshotResults(currentExtractedNodes);

      if (!usePreviewDraft) {
        chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
          if (tabs[0]) {
            chrome.tabs.sendMessage(tabs[0].id, {
              action: 'removeRegionMarker',
              regionId: removedRegion?.region_id,
              regionNumber: removedRegion?.region_number
            });
            await syncExtractedNodeMarkers({ idMapping });
          }
        });
      }
    });
  });

  // Card headers
  document.querySelectorAll('.tracking-card-header').forEach(header => {
    header.addEventListener('click', () => {
      const btn = header.querySelector('[data-action="toggleExpand"]');
      if (btn) {
        const index = parseInt(btn.dataset.index);
        toggleCardExpand(index);
      }
    });
  });

  // Open search modal buttons
  document.querySelectorAll('[data-action="openSearch"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const type = btn.dataset.type;
      const index = parseInt(btn.dataset.index);
      openSearchModal(type, index);
    });
  });

  // Add extra field buttons
  document.querySelectorAll('[data-action="addField"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const index = parseInt(btn.dataset.index);
      addExtraField(index);
    });
  });

  // Remove extra field buttons
  document.querySelectorAll('[data-action="removeField"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const regionIdx = parseInt(btn.dataset.region);
      const fieldIdx = parseInt(btn.dataset.field);
      removeExtraField(regionIdx, fieldIdx);
    });
  });

  // Extra field selects
  document.querySelectorAll('.extra-field-select').forEach(select => {
    select.addEventListener('change', () => {
      const regionIdx = parseInt(select.dataset.region);
      const fieldIdx = parseInt(select.dataset.field);
      const key = select.dataset.key;
      updateExtraField(regionIdx, fieldIdx, key, select.value, { rerender: true });
    });
  });

  document.querySelectorAll('[data-action="toggleFieldPicker"]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      const picker = button.closest('.field-picker');
      if (!picker) return;

      const isOpen = picker.classList.contains('is-open');
      if (isOpen) {
        closeAllFieldPickers();
        return;
      }

      closeAllFieldPickers(picker);
      picker.classList.add('is-open');
      button.setAttribute('aria-expanded', 'true');

      const searchInput = picker.querySelector('.field-picker-search');
      if (searchInput) {
        searchInput.value = '';
        filterFieldPickerOptions(picker, '');
        searchInput.focus();
      }
    });
  });

  document.querySelectorAll('.field-picker-panel').forEach((panel) => {
    panel.addEventListener('click', (event) => {
      event.stopPropagation();
    });
  });

  document.querySelectorAll('.field-picker-search').forEach((input) => {
    input.addEventListener('input', () => {
      filterFieldPickerOptions(input.closest('.field-picker'), input.value);
    });

    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeAllFieldPickers();
      }
    });
  });

  document.querySelectorAll('[data-action="selectFieldOption"]').forEach((option) => {
    option.addEventListener('click', () => {
      const regionIdx = parseInt(option.dataset.region);
      const fieldIdx = parseInt(option.dataset.field);
      const fieldCode = option.dataset.fieldCode || '';
      updateExtraField(regionIdx, fieldIdx, 'fieldCode', fieldCode, { rerender: true });
      closeAllFieldPickers();
    });
  });

  // Extra field remark textarea
  document.querySelectorAll('.extra-field-textarea').forEach(textarea => {
    textarea.addEventListener('input', () => {
      const regionIdx = parseInt(textarea.dataset.region);
      const fieldIdx = parseInt(textarea.dataset.field);
      const key = textarea.dataset.key;
      updateExtraField(regionIdx, fieldIdx, key, textarea.value);
    });

    textarea.addEventListener('change', () => {
      const regionIdx = parseInt(textarea.dataset.region);
      const fieldIdx = parseInt(textarea.dataset.field);
      const key = textarea.dataset.key;
      updateExtraField(regionIdx, fieldIdx, key, textarea.value, { rerender: true });
    });
  });
}

// Toggle card expand/collapse
function toggleCardExpand(index) {
  const region = trackingState.regions[index];
  if (region) {
    region._isExpanded = !region._isExpanded;
  }
  const card = document.getElementById(`tracking-card-${index}`);
  if (card) {
    card.classList.toggle('expanded', Boolean(region && region._isExpanded));
  }
}

function updateExtraField(regionIdx, fieldIdx, key, val, options = {}) {
  const region = trackingState.regions[regionIdx];
  const extraField = region?.action_fields?.[fieldIdx];
  if (extraField) {
    if (key === 'fieldCode') {
      const fieldInfo = getFieldInfoByCode(val);
      extraField.fieldCode = val;
      extraField.id = getFieldPersistedId(fieldInfo);
      extraField.fieldName = fieldInfo?.field_name || '';
      extraField.dataType = fieldInfo?.data_type || 'string';
    } else {
      extraField[key] = val;
    }

    markRegionUpdated(region);

    if (options.rerender) {
      renderTrackingList();
    }
  }
}

function removeExtraField(regionIdx, fieldIdx) {
  if (trackingState.regions[regionIdx] && trackingState.regions[regionIdx].action_fields) {
    trackingState.regions[regionIdx].action_fields.splice(fieldIdx, 1);
    markRegionUpdated(trackingState.regions[regionIdx]);
    renderTrackingList();
  }
}

function addExtraField(regionIdx) {
  if (!trackingState.regions[regionIdx].action_fields) {
    trackingState.regions[regionIdx].action_fields = [];
  }
  const defaultAction = getSelectableFieldActions(trackingState.regions[regionIdx])[0] || '0';
  trackingState.regions[regionIdx].action_fields.push({
    id: null, fieldCode: '', fieldName: '', dataType: 'string', action: defaultAction, remark: ''
  });
  markRegionUpdated(trackingState.regions[regionIdx]);
  renderTrackingList();
}

// --- Modal Logic ---
const searchModal = document.getElementById('searchModal');
const searchInput = document.getElementById('searchInput');
const searchResults = document.getElementById('searchResults');
const searchModalFooter = document.getElementById('searchModalFooter');

const addModal = document.getElementById('addModal');
const addNameInput = document.getElementById('addNameInput');
const addCodeInput = document.getElementById('addCodeInput');

document.getElementById('appSelectBtn').addEventListener('click', () => openSearchModal('app'));
document.getElementById('bizSelectBtn').addEventListener('click', () => openSearchModal('biz'));
document.getElementById('pageSelectBtn').addEventListener('click', () => openSearchModal('page'));

function openSearchModal(type, regionIndex = -1) {
  activeSearchType = type;
  activeRegionIndex = regionIndex;

  const map = {
    'app': '搜索应用',
    'biz': '搜索业务线',
    'page': '搜索页面',
    'section': '搜索区块',
    'element': '搜索元素'
  };

  document.getElementById('searchModalTitle').textContent = map[type];
  searchInput.value = '';

  // Show Add New button for everything except app and biz by default based on config
  if (['page', 'section', 'element'].includes(type)) {
    searchModalFooter.style.display = 'flex';
  } else {
    searchModalFooter.style.display = 'none';
  }

  searchModal.classList.add('active');
  searchInput.focus();
  renderSearchResults();
};

document.getElementById('closeSearchModal').addEventListener('click', () => {
  searchModal.classList.remove('active');
});

searchInput.addEventListener('input', renderSearchResults);

function renderSearchResults() {
  const query = searchInput.value.toLowerCase().trim();
  searchResults.innerHTML = '';

  let dataList = [];
  if (activeSearchType === 'app') dataList = trackingState.config?.app_info?.apps || [];
  else if (activeSearchType === 'biz') dataList = trackingState.config?.business_line_info?.business_lines || [];
  else if (activeSearchType === 'page') {
    const apps = trackingState.config?.page_info?.pages || [];
    // 过滤同 app
    dataList = apps.filter(p => !trackingState.pageSpec.app_id || String(p.app_id) === String(trackingState.pageSpec.app_id));
  }
  else if (activeSearchType === 'section') dataList = trackingState.config?.section_info?.sections || [];
  else if (activeSearchType === 'element') dataList = trackingState.config?.element_info?.elements || [];

  const filtered = dataList.filter(item => {
    let name = '';
    let code = '';
    
    if (activeSearchType === 'app') {
      name = item.app_name || '';
      code = item.app_sign || '';
    } else if (activeSearchType === 'biz') {
      name = item.chinese_name || '';
      code = item.name || '';
    } else if (activeSearchType === 'page') {
      name = item.page_name || '';
      code = item.page_short || '';
    } else if (activeSearchType === 'section') {
      name = item.section_name || '';
      code = item.section_code || '';
    } else if (activeSearchType === 'element') {
      name = item.element_name || '';
      code = item.element_code || '';
    }

    return name.toLowerCase().includes(query) || code.toLowerCase().includes(query);
  });

  if (filtered.length === 0) {
    searchResults.innerHTML = '<div style="padding: 16px; text-align: center; color: #94a3b8; font-size: 12px;">暂无数据</div>';
    return;
  }

  filtered.forEach(item => {
    let name = '';
    let code = '';
    
    if (activeSearchType === 'app') {
      name = item.app_name;
      code = item.app_sign;
    } else if (activeSearchType === 'biz') {
      name = item.chinese_name;
      code = item.name;
    } else if (activeSearchType === 'page') {
      name = item.page_name;
      code = item.page_short;
    } else if (activeSearchType === 'section') {
      name = item.section_name;
      code = item.section_code;
    } else if (activeSearchType === 'element') {
      name = item.element_name;
      code = item.element_code;
    }

    const div = document.createElement('div');
    div.className = 'search-result-item';
    div.innerHTML = `
      <div class="search-result-name">${name}</div>
      <div class="search-result-code">${code}</div>
    `;
    div.onclick = () => selectSearchResult(item);
    searchResults.appendChild(div);
  });
}

function selectSearchResult(item) {
  const p = trackingState.pageSpec;
  if (activeSearchType === 'app') {
    p.app_id = item.id; p.app_name = item.app_name; p.app_code = item.app_sign;
    // When app changes, clear only page selection, keep section and element
    p.page_id = null; p.page_name = null; p.page_code = null;
  } else if (activeSearchType === 'biz') {
    p.business_line = item.name;
    // When biz changes, keep section and element selections
  } else if (activeSearchType === 'page') {
    p.page_id = item.id; p.page_name = item.page_name; p.page_code = item.page_short;
    // When page changes, keep section and element selections
  } else if (activeSearchType === 'section') {
    const r = trackingState.regions[activeRegionIndex];
    r.section_id = item.id; r.section_name = item.section_name; r.section_code = item.section_code;
    markRegionUpdated(r);
  } else if (activeSearchType === 'element') {
    const r = trackingState.regions[activeRegionIndex];
    r.element_id = item.id; r.element_name = item.element_name; r.element_code = item.element_code;
    markRegionUpdated(r);
  }

  searchModal.classList.remove('active');
  updateTopLevelUI();
  renderTrackingList();
}

// Add New Modal Interactions
document.getElementById('addNewItemBtn').addEventListener('click', () => {
  searchModal.classList.remove('active');
  const map = {
    'page': '新建页面',
    'section': '新建区块',
    'element': '新建元素'
  };
  document.getElementById('addModalTitle').textContent = map[activeSearchType] || '新建';
  addNameInput.value = '';
  addCodeInput.value = '';
  addModal.classList.add('active');
  addNameInput.focus();
});

document.getElementById('closeAddModal').addEventListener('click', () => addModal.classList.remove('active'));
document.getElementById('cancelAddBtn').addEventListener('click', () => addModal.classList.remove('active'));

document.getElementById('confirmAddBtn').addEventListener('click', () => {
  const name = addNameInput.value.trim();
  const code = addCodeInput.value.trim();
  if (!name || !code) return alert('请输入名称和代码');

  const p = trackingState.pageSpec;
  if (activeSearchType === 'page') {
    p.page_id = '待创建'; p.page_name = name; p.page_code = code;
  } else if (activeSearchType === 'section') {
    const r = trackingState.regions[activeRegionIndex];
    r.section_id = '待创建'; r.section_name = name; r.section_code = code;
    markRegionUpdated(r);
  } else if (activeSearchType === 'element') {
    const r = trackingState.regions[activeRegionIndex];
    r.element_id = '待创建'; r.element_name = name; r.element_code = code;
    markRegionUpdated(r);
  }

  addModal.classList.remove('active');
  updateTopLevelUI();
  renderTrackingList();
});

document.getElementById('saveTrackingBtn').addEventListener('click', async () => {
  const btn = document.getElementById('saveTrackingBtn');
  btn.textContent = '保存中...';
  btn.disabled = true;

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    await ensureCurrentPageIdentity(tab);
    await enrichDraftDocumentAnchorsOnPage(tab);

    console.log('[Popup][savePageDocument.request]', {
      project_id: trackingState.projectId || currentProjectId || trackingState.draftDocument?.project_id || null,
      page_binding_id: trackingState.pageBindingId,
      base_revision: trackingState.pageBindingId ? trackingState.documentRevision : 0,
      page_identity: deepClone(currentPageIdentity),
      draft_document: deepClone(trackingState.draftDocument),
      change_set: deepClone(trackingState.changeSet)
    });

    const saveResult = await trackingGateway.savePageDocument({
      project_id: trackingState.projectId || currentProjectId || trackingState.draftDocument?.project_id || null,
      page_binding_id: trackingState.pageBindingId,
      base_revision: trackingState.pageBindingId ? trackingState.documentRevision : 0,
      page_identity: currentPageIdentity,
      draft_document: deepClone(trackingState.draftDocument),
      change_set: deepClone(trackingState.changeSet)
    });

    if (saveResult.success) {
      if (saveResult.local_file_mode) {
        console.log('[Popup][savePageDocument.local_result]', saveResult);
        if (saveResult.code_injection_performed === false && saveResult.implementation_guide) {
          showStatus(`已生成 OpenClaw 改写说明：${saveResult.implementation_guide}`, 'success');
        } else {
          const modifiedPath = saveResult.modified_html ? `：${saveResult.modified_html}` : '';
          showStatus(`已写入本地 HTML${modifiedPath}`, 'success');
        }
        setRecoveredDocument(saveResult.document || trackingState.draftDocument, trackingState.config, {
          projectId: trackingState.projectId || currentProjectId || trackingState.draftDocument?.project_id || 'local-openclaw',
          pageBindingId: saveResult.page_binding_id || 'local-file',
          documentRevision: saveResult.document_revision || ((trackingState.documentRevision || 0) + 1)
        });
        return;
      }

      try {
        const resolved = await refetchResolvedDocument(currentPageIdentity, {
          maxAttempts: 3,
          delayMs: 400,
          minDocumentRevision: saveResult.document_revision
        });

        if (resolved?.matched && resolved.document) {
          showStatus('已保存并刷新最新埋点配置', 'success');
          setRecoveredDocument(resolved.document, trackingState.config, {
            projectId: resolved.project_id || trackingState.projectId || currentProjectId || trackingState.draftDocument?.project_id || null,
            pageBindingId: resolved.page_binding_id || saveResult.page_binding_id,
            documentRevision: resolved.document_revision || saveResult.document_revision
          });
        } else {
          console.warn('保存成功，但未重新获取到最新文档或拿到的仍是旧版本，回退到本地草稿展示。', {
            saveResult,
            resolved
          });
          showStatus('已保存，但最新文档尚未生效，暂展示本地结果', 'success');
          setRecoveredDocument(saveResult.document || trackingState.draftDocument, trackingState.config, {
            projectId: trackingState.projectId || currentProjectId || trackingState.draftDocument?.project_id || null,
            pageBindingId: saveResult.page_binding_id,
            documentRevision: saveResult.document_revision
          });
        }
      } catch (refreshError) {
        console.warn('保存成功，但重新获取最新文档失败，回退到本地草稿展示。', refreshError);
        showStatus('已保存，但刷新最新文档失败，暂展示本地结果', 'success');
        setRecoveredDocument(saveResult.document || trackingState.draftDocument, trackingState.config, {
          projectId: trackingState.projectId || currentProjectId || trackingState.draftDocument?.project_id || null,
          pageBindingId: saveResult.page_binding_id,
          documentRevision: saveResult.document_revision
        });
      }
    } else {
      showStatus(saveResult.error_message || '保存失败', 'error');
    }
  } catch (err) {
    console.error(err);
    showStatus(err.message || '保存请求错误', 'error');
  } finally {
    btn.textContent = '确认保存';
    btn.disabled = false;
  }
});

// 处理文本框输入事件 (调整高度和发送按钮状态)
chatInput.addEventListener('input', async function () {
  this.style.height = 'auto';
  this.style.height = (this.scrollHeight < 120 ? this.scrollHeight : 120) + 'px';

  if (!this.disabled && this.value.trim().length > 0) {
    chatSendBtn.classList.add('active');
  } else {
    chatSendBtn.classList.remove('active');
  }
});

// 处理文本框 Enter 发送 (Shift+Enter 换行)
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSendChatMessage();
  }
});

chatSendBtn.addEventListener('click', () => {
  handleSendChatMessage();
});

// 发送聊天消息
async function handleSendChatMessage() {
  const text = chatInput.value.trim();
  if (!text) return;
  if (!currentProjectId) {
    showStatus('请先在“标注位置”页设计新增区域埋点', 'error');
    return;
  }

  // 清空输入框
  chatInput.value = '';
  chatInput.style.height = 'auto';
  chatSendBtn.classList.remove('active');

  // 显示用户消息
  appendMessage('user', text);

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    await ensureCurrentPageIdentity(tab);

    await trackingGateway.act(currentProjectId, {
      instruction: text,
      cli_preference: 'tracking-design',
      is_continue_session: true,
      page_identity: deepClone(currentPageIdentity),
      baseline_document: deepClone(trackingState.draftDocument),
      selected_nodes: deepClone(currentExtractedNodes),
      viewport_info: currentViewportInfo,
      images: []
    });
  } catch (error) {
    console.error('Error sending message:', error);
    appendMessage('ai', `[Error sending message] ${error.message}`);
  }
}

async function initializeRecoveredDocument(tab) {
  logResolveDebug('initializeRecoveredDocument.start', {
    tabId: tab?.id,
    url: tab?.url,
    title: tab?.title
  });
  if (!hasTrackingConfigLoaded(trackingState.config)) {
    try {
      trackingState.config = await trackingGateway.getTrackingConfig();
      buildMaps();
      logResolveDebug('initializeRecoveredDocument.config_loaded', {
        appCount: trackingState.config?.app_info?.apps?.length || 0,
        pageCount: trackingState.config?.page_info?.pages?.length || 0
      });
    } catch (error) {
      console.warn('加载 tracking 配置失败:', error);
      logResolveDebug('initializeRecoveredDocument.config_error', {
        message: error.message,
        stack: error.stack
      });
    }
  } else {
    buildMaps();
    logResolveDebug('initializeRecoveredDocument.config_reused', {
      appCount: trackingState.config?.app_info?.apps?.length || 0,
      pageCount: trackingState.config?.page_info?.pages?.length || 0
    });
  }

  try {
    currentViewportInfo = await sendTabMessage(tab.id, { action: 'getViewportInfo' })
      .catch(() => currentViewportInfo);
    logResolveDebug('initializeRecoveredDocument.viewport', currentViewportInfo);
    await ensureCurrentPageIdentity(tab);
    logResolveDebug('initializeRecoveredDocument.before_resolve', {
      page_identity: deepClone(currentPageIdentity)
    });
    const resolved = await trackingGateway.resolvePageDocument(currentPageIdentity);
    logResolveDebug('initializeRecoveredDocument.resolve_result', resolved);

    if (resolved.matched && resolved.document) {
      renderSpeculationCards(resolved.document, trackingState.config || {}, {
        source: 'recovered',
        projectId: resolved.project_id,
        pageBindingId: resolved.page_binding_id,
        documentRevision: resolved.document_revision
      });
      showStatus('已加载历史埋点文档', 'success');
      return;
    }

    logResolveDebug('initializeRecoveredDocument.no_match', {
      matched: resolved?.matched,
      matchStatus: resolved?.match_status,
      candidates: resolved?.candidates || null
    });
    await clearRecoveredTrackingState(tab, '未找到历史埋点，可开始新建设计');
  } catch (error) {
    console.warn('初始化历史文档失败:', error);
    logResolveDebug('initializeRecoveredDocument.error', {
      message: error.message,
      stack: error.stack
    });
  }
}

async function refreshResolvedDocumentForTab(tab, source = 'tabs.onUpdated') {
  if (!tab?.id) return;

  updatePageMeta(tab);
  if (!isSupportedPageUrl(tab.url)) {
    showStatus('请在普通网页上使用', 'error');
    logResolveDebug('refreshResolvedDocumentForTab.skip_internal_page', {
      source,
      tabId: tab.id,
      url: tab.url
    });
    return;
  }

  resetPageRuntimeState();
  logResolveDebug('refreshResolvedDocumentForTab.start', {
    source,
    tabId: tab.id,
    url: tab.url,
    title: tab.title
  });
  await initializeRecoveredDocument(tab);
}

async function refetchResolvedDocument(pageIdentity, options = {}) {
  const maxAttempts = Number.isFinite(Number(options.maxAttempts)) ? Number(options.maxAttempts) : 3;
  const delayMs = Number.isFinite(Number(options.delayMs)) ? Number(options.delayMs) : 400;
  const minDocumentRevision = Number.isFinite(Number(options.minDocumentRevision))
    ? Number(options.minDocumentRevision)
    : null;
  let lastResolved = null;
  let lastError = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      logResolveDebug('refetchResolvedDocument.attempt', {
        attempt,
        maxAttempts,
        min_document_revision: minDocumentRevision,
        page_identity: deepClone(pageIdentity)
      });
      const resolved = await trackingGateway.resolvePageDocument(pageIdentity);
      lastResolved = resolved;
      const resolvedRevision = Number.isFinite(Number(resolved?.document_revision))
        ? Number(resolved.document_revision)
        : null;
      const revisionSatisfied = minDocumentRevision == null
        || (resolvedRevision != null && resolvedRevision >= minDocumentRevision);
      logResolveDebug('refetchResolvedDocument.result', {
        attempt,
        resolved,
        resolved_revision: resolvedRevision,
        revision_satisfied: revisionSatisfied
      });

      if (resolved?.matched && resolved.document && revisionSatisfied) {
        return resolved;
      }
    } catch (error) {
      lastError = error;
      logResolveDebug('refetchResolvedDocument.error', {
        attempt,
        message: error.message,
        stack: error.stack
      });
    }

    if (attempt < maxAttempts) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  if (lastResolved && minDocumentRevision == null) return lastResolved;
  if (lastError) throw lastError;
  return null;
}

// 侧边栏加载完成时初始化
document.addEventListener('DOMContentLoaded', async () => {
  logResolveDebug('DOMContentLoaded');
  setActiveView(VIEW_NAMES.home);
  updateChatEmptyState();
  updateChatInputState();
  updateSelectionActionState();

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status !== 'complete') return;
    if (!tab?.active || !tab?.id) return;

    if (currentStreamConnection && currentProjectId) {
      logResolveDebug('tabs.onUpdated.skip_active_stream', {
        tabId,
        projectId: currentProjectId,
        status: changeInfo.status,
        url: tab.url
      });
      return;
    }

    if (activeResolveRefreshTimer) {
      clearTimeout(activeResolveRefreshTimer);
    }

    activeResolveRefreshTimer = setTimeout(async () => {
      activeResolveRefreshTimer = null;
      try {
        const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!activeTab?.id || activeTab.id !== tabId) return;
        await refreshResolvedDocumentForTab(activeTab, 'tabs.onUpdated');
      } catch (error) {
        console.error('tab 刷新后重新加载历史文档失败:', error);
        logResolveDebug('tabs.onUpdated.refresh_error', {
          tabId,
          message: error.message,
          stack: error.stack
        });
      }
    }, 250);
  });

  // 监听来自 background.js 的删除 region 请求
  chrome.runtime.onMessage.addListener((request) => {
    if (request.action === 'deleteRegionFromPage') {
      if (request.regionId && !request.isCDP) {
        const usePreviewDraft = hasPreviewDraftRegions();
        const removedRegion = deleteTrackingRegion(request.regionId);
        if (removedRegion) {
          let idMapping = {};
          if (usePreviewDraft) {
            syncPreviewStateAfterRegionRemoval(removedRegion);
          } else {
            idMapping = rebaseExtractedNodeIds();
          }
          refreshTrackingStateUI();
          renderSnapshotResults(currentExtractedNodes);
          showStatus(`已删除埋点 #${removedRegion.region_number}`, 'success');
          if (!usePreviewDraft) {
            chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
              if (tabs[0]) {
                chrome.tabs.sendMessage(tabs[0].id, {
                  action: 'removeRegionMarker',
                  regionId: removedRegion.region_id,
                  regionNumber: removedRegion.region_number
                });
                await syncExtractedNodeMarkers({ idMapping });
              }
            });
          }
        }
        return true;
      }

      const regionNumber = String(request.regionNumber);
      const usePreviewDraft = hasPreviewDraftRegions();
      
      // 1. 检查是否是手动框选的 region
      const index = (!request.isCDP || usePreviewDraft)
        ? trackingState.regions.findIndex(r => String(r.region_number) === regionNumber)
        : -1;
      if (index !== -1) {
        const removedRegion = deleteTrackingRegion(trackingState.regions[index].region_id);
        let idMapping = {};
        if (usePreviewDraft) {
          syncPreviewStateAfterRegionRemoval(removedRegion);
        } else {
          idMapping = rebaseExtractedNodeIds();
        }
        refreshTrackingStateUI();
        renderSnapshotResults(currentExtractedNodes);
        showStatus(`已删除埋点 #${regionNumber}`, 'success');

        if (!usePreviewDraft) {
          chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
            if (tabs[0]) {
              chrome.tabs.sendMessage(tabs[0].id, {
                action: 'removeRegionMarker',
                regionId: removedRegion?.region_id,
                regionNumber: regionNumber
              });
              await syncExtractedNodeMarkers({ idMapping });
            }
          });
        }
      } 
      // 2. 检查是否是自动提取的节点 (CDP)
      else {
        const cdpIndex = currentExtractedNodes.findIndex(n => String(n.id) === regionNumber);
        if (cdpIndex !== -1) {
          // 移除节点
          currentExtractedNodes.splice(cdpIndex, 1);
          let idMapping = {};
          if (usePreviewDraft) {
            syncPreviewNodesWithDraftRegions(trackingState.regions);
          } else {
            idMapping = rebaseExtractedNodeIds();
          }

          renderSnapshotResults(currentExtractedNodes);
          showStatus(`已删除语义标注 [${regionNumber}]`, 'success');

          chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
            if (tabs[0]) {
              if (usePreviewDraft) {
                await drawPreviewMarkersOnPage(tabs[0]);
              } else {
                await syncExtractedNodeMarkers({
                  removedId: regionNumber,
                  idMapping
                });
              }
            }
          });
        }
      }
    } else if (request.action === 'elementSelected') {
      // 处理从页面框选的元素
      console.log('[Debug] elementSelected received:', request);
      
      // [防抖] 500ms 内不处理重复的框选
      const now = Date.now();
      if (now - lastProcessedSelectionTime < 500) {
        console.warn('[Debug] Duplicate elementSelected ignored');
        return;
      }
      lastProcessedSelectionTime = now;

      const selectionItems = Array.isArray(request.elements)
        ? request.elements
        : (request.element ? [{
          element: request.element,
          tempId: request.tempId,
          box: request.box
        }] : []);

      if (selectionItems.length === 0) {
        showStatus('框选范围内未找到可添加的可操作元素', 'error');
        return;
      }

      (async () => {
        const addedNodes = appendSelectedElements(selectionItems);

        if (addedNodes.length === 0) {
          showStatus('框选范围内的元素都已存在，无需重复添加', 'error');
          return;
        }

        setActiveView(VIEW_NAMES.selection);
        renderSnapshotResults(currentExtractedNodes);
        await syncSelectedElementsToPage(addedNodes);

        if (addedNodes.length === 1) {
          showStatus(`已手动提取语义元素: ${addedNodes[0].name}`, 'success');
        } else {
          showStatus(`已批量添加 ${addedNodes.length} 个可操作元素`, 'success');
        }
      })().catch((error) => {
        console.error('批量处理框选元素失败:', error);
        showStatus(`批量添加失败: ${error.message}`, 'error');
      });
    }
    return true;
  });

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    logResolveDebug('DOMContentLoaded.active_tab', tab ? {
      id: tab.id,
      url: tab.url,
      title: tab.title
    } : null);

    if (tab) {
      updatePageMeta(tab);

      if (!isSupportedPageUrl(tab.url)) {
        showStatus('请在普通网页上使用', 'error');
        logResolveDebug('DOMContentLoaded.skip_internal_page', {
          url: tab.url
        });
      } else {
        await initializeRecoveredDocument(tab);
      }
    }
  } catch (error) {
    console.error('Error checking tab:', error);
    logResolveDebug('DOMContentLoaded.error', {
      message: error.message,
      stack: error.stack
    });
  }
});
