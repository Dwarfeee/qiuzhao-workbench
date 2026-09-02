/* 秋招工作台助手 · 后台 Service Worker（中转 API 调用，绕过扩展页 CORS） */
const API = 'http://127.0.0.1:8787';

/* 点击图标 → 打开常驻侧边栏（不会因切标签页而消失，适合"边查官网边填"） */
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  // 让「点击图标」直接打开侧边栏；旧版 Edge 不支持时退回 onClicked 兜底
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  chrome.action.onClicked.addListener(() => {
    chrome.windows.getCurrent(w => {
      if (w && w.id != null) chrome.sidePanel.open({ windowId: w.id }).catch(() => {});
    });
  });
}

function relay(type, path, payload) {
  return new Promise(resolve => {
    fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    })
      .then(r => r.json()
        .then(d => resolve({ ok: r.ok, status: r.status, data: d }))
        .catch(() => resolve({ ok: r.ok, status: r.status, data: {} })))
      .catch(e => resolve({ ok: false, error: String(e) }));
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'capture') {
    relay('capture', '/api/extension/capture', msg.payload).then(sendResponse);
    return true; // 保持消息通道异步响应
  }
  if (msg.type === 'smart-capture') {
    relay('smart-capture', '/api/extension/smart-capture', msg.payload).then(sendResponse);
    return true;
  }
  if (msg.type === 'smart-fill-map') {
    relay('smart-fill-map', '/api/extension/smart-fill-map', msg.payload).then(sendResponse);
    return true;
  }
  if (msg.type === 'find-company-site') {
    relay('find-company-site', '/api/extension/find-company-site', msg.payload).then(sendResponse);
    return true;
  }
  if (msg.type === 'form-data') {
    relay('form-data', '/api/extension/form-data').then(sendResponse);
    return true;
  }
});
