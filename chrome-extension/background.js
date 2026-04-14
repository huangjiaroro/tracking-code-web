// 确保点击图标时打开侧边栏
chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.error(error));

// 监听来自 content.js 的删除 region 请求
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'deleteRegion') {
        // 通过 runtime 发送给 popup (side panel)
        chrome.runtime.sendMessage({
            action: 'deleteRegionFromPage',
            regionNumber: request.regionNumber,
            regionId: request.regionId,
            isCDP: request.isCDP
        }).catch(() => {
            // popup 可能没有打开
        });
        sendResponse({ success: true });
        return true;
    } else if (request.action === 'elementSelected') {
        // 转发元素选中消息给 popup
        chrome.runtime.sendMessage({
            action: 'elementSelected',
            element: request.element,
            elements: request.elements,
            tempId: request.tempId,
            box: request.box
        }).catch(() => {
            // popup 可能没有打开，忽略错误
        });
        sendResponse({ success: true });
        return true;
    }
});

// 监听来自 popup 的截图请求
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'captureScreen') {
        // 由于 background service worker 可能没有当前标签页的上下文，
        // 先查询 active tab，然后再获取截图
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            if (!tabs || tabs.length === 0) {
                sendResponse({ error: 'No active tab found' });
                return;
            }

            const tab = tabs[0];
            chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: 80 }, (dataUrl) => {
                if (chrome.runtime.lastError) {
                    // 如果仍然遇到 activeTab 权限问题，可能需要进一步处理（例如使用 content script）
                    sendResponse({ error: chrome.runtime.lastError.message });
                } else {
                    sendResponse({ dataUrl: dataUrl, title: tab.title });
                }
            });
        });

        return true; // 保持通道开启以异步发送响应
    }
});

// 监听来自 popup 的语义提取请求
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'extractCDPSnapshot') {
        const tabId = request.tabId;
        const startNumber = Number(request.startNumber) || 0;
        console.log("🚀 开始提取可交互元素（DOM 检测 + CDP 属性注入）...");

        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        // Step A: 让 content.js 用 DomUtils 扫描页面，返回节点数组
        // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        chrome.tabs.sendMessage(tabId, {
            action: 'extractDOMInteractiveElements',
            startNumber: startNumber
        }, async (domResponse) => {
            if (chrome.runtime.lastError) {
                console.error("content.js 通信失败:", chrome.runtime.lastError.message);
                sendResponse({ success: false, message: "请刷新页面后重试（扩展需要重新注入到页面）" });
                return;
            }

            if (!domResponse || !domResponse.success) {
                console.error("DOM 提取失败:", domResponse?.message);
                sendResponse({ success: false, message: domResponse?.message || 'DOM 元素提取失败' });
                return;
            }

            const interactiveNodes = domResponse.nodes; // [{ id, role, name, selector, box, semanticContext, className }]
            console.log(`✅ DOM 检测完成，共 ${interactiveNodes.length} 个可交互节点`);

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // Step B: 构建 textSnapshot（供 LLM 使用的纯文本）
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            let textSnapshot = '';
            interactiveNodes.forEach(node => {
                const sanitizedName = (node.name || '').substring(0, 30).replace(/\n/g, ' ');
                textSnapshot += `- ${node.role} "${sanitizedName}" [ref=${node.id}]\n`;
            });

            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            // Step C: 构建 semanticContextText（直接使用 content.js 返回的语义上下文）
            // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            let semanticContextText = '';
            interactiveNodes.forEach(node => {
                const ctx = node.semanticContext || {};
                const page = ctx.page || 'Unknown Page';
                const block = ctx.block || 'Unknown Block';

                semanticContextText += `标注 [${node.id}]:\n`;
                semanticContextText += `- 页面: ${page}\n`;
                semanticContextText += `- 区块: ${block}\n`;
                semanticContextText += `- 元素: ${node.role}: ${node.name || 'unnamed'}\n`;
                if (node.className) semanticContextText += `- Class: ${node.className.substring(0, 60)}\n`;
                semanticContextText += `\n`;
            });

            console.log(`[CDP] semanticContextText 长度: ${semanticContextText.length}, interactiveNodes 数量: ${interactiveNodes.length}`);

            // Step D: 响应 popup
            sendResponse({
                success: true,
                message: `✅ 提取完毕，DOM 检测出 ${interactiveNodes.length} 个交互元素！`,
                interactiveNodes: interactiveNodes,
                textSnapshot: textSnapshot,
                semanticContextText: semanticContextText
            });
        });

        return true; // 异步响应
    }
});
