(function (global) {
  'use strict';

  function deepClone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function logGatewayRequest(scope, payload) {
    console.log(`[TrackingGateway][${scope}]`, deepClone(payload));
  }

  function logGatewayResponse(scope, payload) {
    console.log(`[TrackingGateway][${scope}]`, deepClone(payload));
  }

  function slugify(value, fallback = 'item') {
    const normalized = String(value || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
      .replace(/^-+|-+$/g, '');
    return normalized || fallback;
  }

  function createEmptyChangeSet() {
    return {
      added_regions: [],
      updated_regions: [],
      deleted_region_ids: [],
      rebound_regions: []
    };
  }

  function unwrapApiData(payload) {
    if (payload && typeof payload === 'object' && 'data' in payload) {
      return payload.data;
    }
    return payload;
  }

  function readApiField(payload, snakeKey, camelKey) {
    if (!payload || typeof payload !== 'object') return undefined;
    if (Object.prototype.hasOwnProperty.call(payload, snakeKey)) {
      return payload[snakeKey];
    }
    if (camelKey && Object.prototype.hasOwnProperty.call(payload, camelKey)) {
      return payload[camelKey];
    }
    return undefined;
  }

  const LOCAL_TRACKING_TOKEN_PARAM = 'openclaw_tracking_token';
  const LOCAL_GATEWAY_PARAM = 'openclaw_tracking_gateway';

  function isLocalTrackingHost(hostname) {
    return ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(hostname);
  }

  function getLocalTrackingSession(pageIdentity) {
    const rawUrl = pageIdentity?.source_url || pageIdentity?.url || '';
    if (!rawUrl) return null;

    try {
      const url = new URL(rawUrl);
      if (!['http:', 'https:'].includes(url.protocol)) return null;

      const token = url.searchParams.get(LOCAL_TRACKING_TOKEN_PARAM);
      if (!token) return null;

      const gatewayParam = url.searchParams.get(LOCAL_GATEWAY_PARAM);
      const gatewayOrigin = gatewayParam
        ? new URL(gatewayParam).origin
        : (isLocalTrackingHost(url.hostname) ? url.origin : null);
      if (!gatewayOrigin) return null;

      return {
        origin: gatewayOrigin,
        token,
        pageDocumentBase: `${gatewayOrigin}/api/openclaw/page_document`,
        agentBase: `${gatewayOrigin}/api/openclaw/agent`
      };
    } catch (error) {
      return null;
    }
  }

  function withLocalToken(url, localSession) {
    const resolvedUrl = new URL(url);
    resolvedUrl.searchParams.set('token', localSession.token);
    return resolvedUrl.toString();
  }

  function getPublicPageIdentity(pageIdentity) {
    const normalized = deepClone(pageIdentity || null);
    if (normalized && typeof normalized === 'object') {
      delete normalized.source_url;
    }
    return normalized;
  }

  function toNumberOrNull(value) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
  }

  function buildDefaultSurface() {
    return {
      surface_id: 'sf_main',
      surface_key: 'main',
      type: 'main',
      activation_hints: {
        role: 'main',
        aria_label: null,
        data_testids: [],
        class_tokens: ['main'],
        text_signature: 'main'
      }
    };
  }

  function buildNormalizedBox(box, viewportInfo) {
    const viewportWidth = Math.max(1, viewportInfo?.viewportWidth || 1440);
    const viewportHeight = Math.max(1, viewportInfo?.viewportHeight || 900);
    const safeBox = box || {};
    return {
      top_ratio: Number(((safeBox.y || 0) / viewportHeight).toFixed(4)),
      left_ratio: Number(((safeBox.x || 0) / viewportWidth).toFixed(4)),
      width_ratio: Number(((safeBox.width || 0) / viewportWidth).toFixed(4)),
      height_ratio: Number(((safeBox.height || 0) / viewportHeight).toFixed(4))
    };
  }

  function buildSemanticContextFromRegion(region) {
    return {
      page: region.page_name || region.page_code || 'Unknown Page',
      block: region.section_name || region.section_code || 'Unknown Block',
      element: `${region.control_type || 'element'}: ${region.element_name || region.element_code || 'unnamed'}`
    };
  }

  function uniqueStringValues(values = []) {
    return Array.from(new Set(
      values
        .map((value) => String(value || '').trim())
        .filter(Boolean)
    ));
  }

  function escapeSelectorAttributeValue(value) {
    return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function normalizeOptionalId(value) {
    const text = String(value ?? '').trim();
    if (!text || text === 'null' || text === 'undefined') return '';
    return text;
  }

  function readAnchorDataAiId(anchor = {}, stable = {}, region = {}) {
    return normalizeOptionalId(
      anchor['data-ai-id']
      || anchor.data_ai_id
      || anchor.dataAiId
      || stable['data-ai-id']
      || stable.data_ai_id
      || stable.dataAiId
      || region['data-ai-id']
      || region.data_ai_id
      || region.dataAiId
    );
  }

  function normalizeParentHint(parentHint) {
    const normalized = deepClone(parentHint) || {};
    return {
      tag_name: normalized.tag_name || normalized.tagName || '',
      role: normalized.role || '',
      aria_label: normalized.aria_label || normalized.ariaLabel || '',
      class_tokens: Array.isArray(normalized.class_tokens || normalized.classTokens)
        ? uniqueStringValues(normalized.class_tokens || normalized.classTokens)
        : [],
      text_hint: normalized.text_hint || normalized.textHint || ''
    };
  }

  function normalizeAnchor(anchor, region = {}) {
    const normalized = deepClone(anchor) || {};
    const stable = normalized.stable_attributes || normalized.stableAttributes || {};
    const textSignature = normalized.text_signature || normalized.textSignature || {};
    const domSignature = normalized.dom_signature || normalized.domSignature || {};
    const dataAiId = readAnchorDataAiId(normalized, stable, region);

    normalized['data-ai-id'] = dataAiId || null;
    normalized.stable_attributes = {
      'data-ai-id': dataAiId || null,
      id: stable.id || region.element_dom_id || null,
      'data-testid': stable['data-testid'] || stable.dataTestId || null,
      'aria-label': stable['aria-label'] || stable.ariaLabel || null,
      role: stable.role || null,
      title: stable.title || null,
      placeholder: stable.placeholder || null,
      name: stable.name || null,
      type: stable.type || null
    };
    normalized.inferred_role = normalized.inferred_role || normalized.inferredRole || region.control_type || null;

    const selectorCandidates = Array.isArray(normalized.selector_candidates || normalized.selectorCandidates)
      ? uniqueStringValues(normalized.selector_candidates || normalized.selectorCandidates)
      : [];
    normalized.selector_candidates = dataAiId
      ? uniqueStringValues([`[data-ai-id="${escapeSelectorAttributeValue(dataAiId)}"]`, ...selectorCandidates])
      : selectorCandidates;

    normalized.text_signature = {
      exact: textSignature.exact || region.element_name || '',
      normalized: textSignature.normalized || textSignature.exact || region.element_name || '',
      accessible_name: textSignature.accessible_name || textSignature.accessibleName || textSignature.exact || region.element_name || '',
      visible_text: textSignature.visible_text || textSignature.visibleText || '',
      title: textSignature.title || '',
      placeholder: textSignature.placeholder || '',
      alt: textSignature.alt || ''
    };

    normalized.dom_signature = {
      tag_name: domSignature.tag_name || domSignature.tagName || '',
      class_tokens: Array.isArray(domSignature.class_tokens || domSignature.classTokens)
        ? uniqueStringValues(domSignature.class_tokens || domSignature.classTokens)
        : [],
      sibling_index: Number.isFinite(Number(domSignature.sibling_index || domSignature.siblingIndex))
        ? Number(domSignature.sibling_index || domSignature.siblingIndex)
        : null,
      parent_chain: Array.isArray(domSignature.parent_chain || domSignature.parentChain)
        ? (domSignature.parent_chain || domSignature.parentChain).map((hint) => normalizeParentHint(hint))
        : []
    };

    // The extension now treats every persisted anchor as the richer v2 shape.
    // Older anchors are normalized into the v2 schema on read instead of
    // carrying a mixed v1/v2 state through save and resolve flows.
    normalized.extractor_version = 'anchor_v2';
    return normalized;
  }

  function buildAnchorFromRegion(region) {
    if (region.anchor) return normalizeAnchor(region.anchor, region);
    const selectorFromId = region.element_dom_id ? `#${region.element_dom_id}` : null;
    const dataAiId = readAnchorDataAiId({}, {}, region);
    return normalizeAnchor({
      'data-ai-id': dataAiId || null,
      stable_attributes: {
        'data-ai-id': dataAiId || null,
        id: region.element_dom_id || null,
        'data-testid': null,
        'aria-label': null,
        role: null
      },
      inferred_role: region.control_type || null,
      selector_candidates: [selectorFromId].filter(Boolean),
      text_signature: {
        exact: region.element_name || '',
        normalized: region.element_name || ''
      },
      extractor_version: 'anchor_v2'
    }, region);
  }

  function normalizeRegion(region, index, viewportInfo) {
    const normalized = deepClone(region);
    const regionIdSeed = normalized.region_id
      || normalized.element_code
      || normalized.element_name
      || normalized.region_number
      || index + 1;

    normalized.region_id = normalized.region_id || `reg_${slugify(regionIdSeed, `region-${index + 1}`)}`;
    normalized.region_number = Number.isFinite(Number(normalized.region_number))
      ? Number(normalized.region_number)
      : index + 1;
    normalized.status = normalized.status || 'active';
    normalized.surface_id = normalized.surface_id || 'sf_main';
    normalized.region = normalized.region || { top: 0, left: 0, width: 0, height: 0 };
    normalized.normalized_box = normalized.normalized_box || buildNormalizedBox({
      x: normalized.region.left || 0,
      y: normalized.region.top || 0,
      width: normalized.region.width || 0,
      height: normalized.region.height || 0
    }, viewportInfo);
    normalized.semantic_context = normalized.semantic_context || buildSemanticContextFromRegion(normalized);
    normalized.anchor = buildAnchorFromRegion(normalized);
    normalized.action_fields = Array.isArray(normalized.action_fields) ? normalized.action_fields : [];
    normalized.allow_action = Array.isArray(normalized.allow_action) ? normalized.allow_action : [];
    return normalized;
  }

  function normalizeTrackingDocument(document, options = {}) {
    const normalized = deepClone(document) || {};
    const pageIdentity = deepClone(options.pageIdentity || normalized.page_identity || null);
    const viewportInfo = options.viewportInfo || normalized.viewport_info || null;

    normalized.schema_version = normalized.schema_version || 'tracking_design_v2';
    normalized.page_binding_id = normalized.page_binding_id || options.pageBindingId || null;
    normalized.document_revision = Number.isFinite(Number(normalized.document_revision))
      ? Number(normalized.document_revision)
      : (options.documentRevision || 1);
    normalized.project_id = normalized.project_id || options.projectId || null;
    normalized.page_identity = pageIdentity;
    normalized.page_speculation = normalized.page_speculation || normalized.pageSpeculation || {};
    normalized.surfaces = Array.isArray(normalized.surfaces) && normalized.surfaces.length > 0
      ? normalized.surfaces
      : [buildDefaultSurface()];

    const rawRegions = Array.isArray(normalized.regions) ? normalized.regions : [];
    normalized.regions = rawRegions.map((region, index) => normalizeRegion(region, index, viewportInfo));
    return normalized;
  }

  class HttpTrackingGateway {
    constructor(config) {
      this.config = config;
      this.cachedConfig = null;
      this.localSession = null;
      this.localStreamHandlersByProject = new Map();
    }

    rememberLocalSession(pageIdentity) {
      const localSession = getLocalTrackingSession(pageIdentity);
      if (localSession) {
        this.localSession = localSession;
      }
      return localSession || this.localSession;
    }

    getLocalSessionFromPayload(payload) {
      return this.rememberLocalSession(payload?.page_identity || payload?.draft_document?.page_identity || null);
    }

    getPageDocumentBaseUrl() {
      return this.config.PAGE_DOCUMENT_API_BASE_URL || 'https://phonestat.hexin.cn/maidian/server';
    }

    async getTrackingConfig() {
      if (this.cachedConfig) {
        return deepClone(this.cachedConfig);
      }

      const fixturePath = this.config.TRACKING_CONFIG_FIXTURE || null;
      if (!fixturePath) {
        this.cachedConfig = {};
        return {};
      }

      const response = await fetch(chrome.runtime.getURL(fixturePath));
      if (!response.ok) {
        throw new Error(`Failed to load tracking config fixture: ${fixturePath}`);
      }

      const json = await response.json();
      this.cachedConfig = deepClone(json.config || json || {});
      return deepClone(this.cachedConfig);
    }

    async resolvePageDocument(pageIdentity) {
      const localSession = this.rememberLocalSession(pageIdentity);
      const publicPageIdentity = getPublicPageIdentity(pageIdentity);
      if (localSession) {
        const requestBody = {
          page_identity: publicPageIdentity
        };
        const url = `${localSession.pageDocumentBase}/tracking/page_document/resolve`;

        logGatewayRequest('Local.resolvePageDocument.request', {
          url,
          method: 'POST',
          body: requestBody
        });

        const response = await fetch(withLocalToken(url, localSession), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-OpenClaw-Token': localSession.token
          },
          body: JSON.stringify(requestBody)
        });
        logGatewayResponse('Local.resolvePageDocument.http_status', {
          url,
          status: response.status,
          ok: response.ok
        });
        if (!response.ok) {
          throw new Error(`Failed to resolve page document through local gateway: ${response.status}`);
        }

        const json = await response.json();
        logGatewayResponse('Local.resolvePageDocument.raw_response', json);
        const data = unwrapApiData(json) || {};
        const matched = Boolean(readApiField(data, 'matched', 'matched'));
        const pageBindingId = readApiField(data, 'page_binding_id', 'pageBindingId') || null;
        const projectId = readApiField(data, 'project_id', 'projectId') || null;
        const documentRevision = toNumberOrNull(readApiField(data, 'document_revision', 'documentRevision'));
        const rawDocument = readApiField(data, 'document', 'document');
        const candidates = Array.isArray(readApiField(data, 'candidates', 'candidates'))
          ? readApiField(data, 'candidates', 'candidates').map((item) => ({
            page_binding_id: readApiField(item, 'page_binding_id', 'pageBindingId') || null,
            project_id: readApiField(item, 'project_id', 'projectId') || null,
            document_revision: toNumberOrNull(readApiField(item, 'document_revision', 'documentRevision')),
            similarity: readApiField(item, 'similarity', 'similarity') ?? null
          }))
          : null;

        const result = {
          match_status: readApiField(data, 'match_status', 'matchStatus') || (matched ? 'EXACT' : 'NO_MATCH'),
          matched,
          page_binding_id: pageBindingId,
          project_id: projectId,
          document_revision: documentRevision,
          document: rawDocument ? normalizeTrackingDocument(rawDocument, {
            pageIdentity: publicPageIdentity,
            pageBindingId,
            documentRevision,
            projectId
          }) : null,
          candidates,
          local_gateway_mode: true,
          local_server_origin: localSession.origin
        };
        logGatewayResponse('Local.resolvePageDocument.normalized_result', result);
        return result;
      }

      const requestBody = {
        page_identity: publicPageIdentity
      };
      const url = `${this.getPageDocumentBaseUrl()}/tracking/page_document/resolve`;

      logGatewayRequest('Http.resolvePageDocument.request', {
        url,
        method: 'POST',
        body: requestBody
      });

      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });
      logGatewayResponse('Http.resolvePageDocument.http_status', {
        url,
        status: response.status,
        ok: response.ok
      });
      if (!response.ok) {
        throw new Error(`Failed to resolve page document: ${response.status}`);
      }

      const json = await response.json();
      logGatewayResponse('Http.resolvePageDocument.raw_response', json);
      const data = unwrapApiData(json) || {};
      const matched = Boolean(readApiField(data, 'matched', 'matched'));
      const pageBindingId = readApiField(data, 'page_binding_id', 'pageBindingId') || null;
      const projectId = readApiField(data, 'project_id', 'projectId') || null;
      const documentRevision = toNumberOrNull(readApiField(data, 'document_revision', 'documentRevision'));
      const rawDocument = readApiField(data, 'document', 'document');
      const candidates = Array.isArray(readApiField(data, 'candidates', 'candidates'))
        ? readApiField(data, 'candidates', 'candidates').map((item) => ({
          page_binding_id: readApiField(item, 'page_binding_id', 'pageBindingId') || null,
          project_id: readApiField(item, 'project_id', 'projectId') || null,
          document_revision: toNumberOrNull(readApiField(item, 'document_revision', 'documentRevision')),
          similarity: readApiField(item, 'similarity', 'similarity') ?? null
        }))
        : null;

      const result = {
        match_status: readApiField(data, 'match_status', 'matchStatus') || (matched ? 'EXACT' : 'NO_MATCH'),
        matched,
        page_binding_id: pageBindingId,
        project_id: projectId,
        document_revision: documentRevision,
        document: rawDocument ? normalizeTrackingDocument(rawDocument, {
          pageIdentity: publicPageIdentity,
          pageBindingId,
          documentRevision,
          projectId
        }) : null,
        candidates
      };
      logGatewayResponse('Http.resolvePageDocument.normalized_result', result);
      return result;
    }

    async createProject(payload) {
      const localSession = this.getLocalSessionFromPayload(payload);
      const url = localSession
        ? `${localSession.agentBase}/api/projects/`
        : `${this.config.API_BASE_URL}/api/projects/`;
      const response = await fetch(localSession ? withLocalToken(url, localSession) : url, {
        method: 'POST',
        headers: localSession
          ? { 'Content-Type': 'application/json', 'X-OpenClaw-Token': localSession.token }
          : { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        throw new Error(`Failed to create project: ${response.status}`);
      }
      return response.json();
    }

    async uploadAsset(projectId, formData) {
      const localSession = this.localSession;
      const url = localSession
        ? `${localSession.agentBase}/api/assets/${projectId}/upload`
        : `${this.config.API_BASE_URL}/api/assets/${projectId}/upload`;
      const response = await fetch(localSession ? withLocalToken(url, localSession) : url, {
        method: 'POST',
        headers: localSession ? { 'X-OpenClaw-Token': localSession.token } : undefined,
        body: formData
      });
      if (!response.ok) {
        throw new Error(`Failed to upload asset: ${response.status}`);
      }
      return response.json();
    }

    connectProjectStream(projectId, handlers) {
      const localSession = this.localSession;
      console.log('[connectProjectStream] localSession:', localSession, 'projectId:', projectId);
      if (localSession) {
        // For local gateway, WebSocket should connect directly to /api/chat/{project_id}
        // NOT through /api/openclaw/agent/api/chat/
        const wsUrl = new URL(`${localSession.origin}/api/chat/${projectId}?token=${localSession.token}`);
        console.log('[connectProjectStream] Using local gateway WebSocket, URL:', wsUrl.toString());
        const socket = new WebSocket(wsUrl.toString());
        socket.onopen = () => handlers?.onOpen?.();
        socket.onclose = (event) => handlers?.onClose?.(event);
        socket.onerror = (event) => handlers?.onError?.(event);
        socket.onmessage = (event) => {
          try {
            handlers?.onMessage?.(JSON.parse(event.data));
          } catch (error) {
            console.error('Failed to parse stream event:', error);
          }
        };
        return {
          close: () => {
            this.localStreamHandlersByProject.delete(projectId);
            socket.close();
          }
        };
      }

      console.log('[connectProjectStream] Using remote WebSocket, URL:', `${this.config.API_BASE_URL}/api/chat/${projectId}`);
      const wsUrl = new URL(`${this.config.API_BASE_URL}/api/chat/${projectId}`);
      wsUrl.protocol = wsUrl.protocol.replace('http', 'ws');
      const socket = new WebSocket(wsUrl.toString());
      socket.onopen = () => handlers?.onOpen?.();
      socket.onclose = (event) => handlers?.onClose?.(event);
      socket.onerror = (event) => handlers?.onError?.(event);
      socket.onmessage = (event) => {
        try {
          handlers?.onMessage?.(JSON.parse(event.data));
        } catch (error) {
          console.error('Failed to parse stream event:', error);
        }
      };
      return {
        close: () => socket.close()
      };
    }

    async act(projectId, payload) {
      const localSession = this.getLocalSessionFromPayload(payload);
      const url = localSession
        ? `${localSession.agentBase}/api/chat/${projectId}/act`
        : `${this.config.API_BASE_URL}/api/chat/${projectId}/act`;
      const response = await fetch(localSession ? withLocalToken(url, localSession) : url, {
        method: 'POST',
        headers: localSession
          ? { 'Content-Type': 'application/json', 'X-OpenClaw-Token': localSession.token }
          : { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        throw new Error(`Failed to send act: ${response.status}`);
      }
      const result = await response.json();
      if (localSession) {
        const handlers = this.localStreamHandlersByProject.get(projectId);
        if (handlers?.onMessage) {
          setTimeout(() => {
            handlers.onMessage({
              type: 'act_complete',
              data: { status: 'completed', source: 'local_gateway' }
            });
          }, 800);
        }
      }
      return result;
    }

    async getProjectData(projectId) {
      const localSession = this.localSession;
      console.log('[getProjectData] localSession:', localSession, 'projectId:', projectId);
      const url = localSession
        ? `${localSession.agentBase}/api/tracking/project_data/${projectId}`
        : `${this.config.API_BASE_URL}/api/tracking/project_data/${projectId}`;
      console.log('[getProjectData] URL:', url);
      const response = await fetch(localSession ? withLocalToken(url, localSession) : url, {
        headers: localSession ? { 'X-OpenClaw-Token': localSession.token } : undefined
      });
      console.log('[getProjectData] Response status:', response.status, 'url:', response.url);
      if (!response.ok) {
        throw new Error(`Failed to get project data: ${response.status}`);
      }
      return response.json();
    }

    async savePageDocument(payload) {
      const pageIdentity = deepClone(payload.page_identity || payload.draft_document?.page_identity || null);
      const pageBindingId = payload.page_binding_id || payload.draft_document?.page_binding_id || null;
      const projectId = payload.project_id || payload.draft_document?.project_id || null;
      const baseRevision = Number.isFinite(Number(payload.base_revision)) ? Number(payload.base_revision) : 0;
      const changeSet = deepClone(payload.change_set || createEmptyChangeSet());
      const localSession = this.rememberLocalSession(pageIdentity);
      const publicPageIdentity = getPublicPageIdentity(pageIdentity);
      const normalizedDraftDocument = normalizeTrackingDocument(payload.draft_document, {
        pageIdentity: publicPageIdentity,
        pageBindingId: localSession ? (pageBindingId || 'local-file') : pageBindingId,
        documentRevision: payload.draft_document?.document_revision || baseRevision || 1,
        projectId: localSession ? (projectId || 'local-openclaw') : projectId
      });

      if (localSession) {
        const requestBody = {
          page_identity: publicPageIdentity,
          draft_document: normalizedDraftDocument,
          change_set: changeSet,
          source_url: pageIdentity?.source_url || pageIdentity?.url || null,
          saved_at: new Date().toISOString()
        };
        const url = `${localSession.pageDocumentBase}/tracking/page_document/save`;

        logGatewayRequest('Local.savePageDocument.request', {
          url,
          method: 'POST',
          body: requestBody
        });

        const response = await fetch(withLocalToken(url, localSession), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-OpenClaw-Token': localSession.token
          },
          body: JSON.stringify(requestBody)
        });
        if (!response.ok) {
          throw new Error(`Failed to save local tracking document: ${response.status}`);
        }

        const json = await response.json();
        const data = unwrapApiData(json) || {};
        const successValue = readApiField(data, 'success', 'success');
        const okValue = readApiField(data, 'ok', 'ok');
        return {
          success: successValue !== undefined ? Boolean(successValue) : Boolean(okValue),
          local_file_mode: true,
          page_binding_id: 'local-file',
          document_revision: baseRevision + 1,
          document: normalizedDraftDocument,
          code_injection_enabled: Boolean(readApiField(data, 'code_injection_enabled', 'codeInjectionEnabled')),
          code_injection_performed: Boolean(readApiField(data, 'code_injection_performed', 'codeInjectionPerformed')),
          code_reference: readApiField(data, 'code_reference', 'codeReference') || null,
          implementation_guide: readApiField(data, 'implementation_guide', 'implementationGuide') || null,
          modified_html: readApiField(data, 'modified_html', 'modifiedHtml') || null,
          tracking_schema: readApiField(data, 'tracking_schema', 'trackingSchema') || null,
          event_count: toNumberOrNull(readApiField(data, 'event_count', 'eventCount')),
          unresolved_count: toNumberOrNull(readApiField(data, 'unresolved_count', 'unresolvedCount')),
          error_message: readApiField(data, 'error', 'error') || null
        };
      }

      if (!projectId) {
        throw new Error('project_id is required to save page document.');
      }

      const requestBody = {
        page_binding_id: pageBindingId,
        project_id: projectId,
        base_revision: baseRevision,
        draft_document: normalizedDraftDocument,
        change_set: changeSet
      };

      const url = `${this.getPageDocumentBaseUrl()}/tracking/page_document/save`;

      logGatewayRequest('Http.savePageDocument.request', {
        url,
        method: 'POST',
        body: requestBody
      });

      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });
      if (!response.ok) {
        throw new Error(`Failed to save document: ${response.status}`);
      }
      const json = await response.json();
      const data = unwrapApiData(json) || {};
      return {
        success: Boolean(readApiField(data, 'success', 'success')),
        page_binding_id: readApiField(data, 'page_binding_id', 'pageBindingId') || pageBindingId,
        document_revision: toNumberOrNull(readApiField(data, 'document_revision', 'documentRevision')),
        error_message: readApiField(data, 'error_message', 'errorMessage') || null
      };
    }
  }

  function createTrackingGateway(config) {
    return new HttpTrackingGateway(config);
  }

  global.createTrackingGateway = createTrackingGateway;
  global.normalizeTrackingDocument = normalizeTrackingDocument;
  global.createEmptyTrackingChangeSet = createEmptyChangeSet;
})(window);
