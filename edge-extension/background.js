/* 秋招工作台助手 · 后台 Service Worker（中转 API 调用，绕过扩展页 CORS） */
const API = 'http://127.0.0.1:8787';

/* 点击图标 → 打开独立小窗（失焦不关、手动×关；兼容不支持侧边栏的 Edge） */
chrome.action.onClicked.addListener((tab) => {
  // 记录点击时激活的标签页，避免独立小窗窃取焦点后 popup 找不到原岗位页
  if (tab && tab.id) {
    chrome.storage.local.set({ lastActionTabId: tab.id, lastActionTabUrl: tab.url || '' });
  }
  const target = chrome.runtime.getURL('popup.html');
  chrome.windows.getAll(wins => {
    const ex = (wins || []).find(w => w.type === 'popup' && w.url && w.url.indexOf(target) !== -1);
    if (ex) { chrome.windows.update(ex.id, { focused: true }); return; }
    chrome.windows.create({ url: target, type: 'popup', width: 392, height: 680 });
  });
});

function relay(type, path, payload, method) {
  method = method || 'POST';
  return new Promise(resolve => {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' }
    };
    if (method !== 'GET' && method !== 'HEAD') {
      opts.body = JSON.stringify(payload || {});
    }
    fetch(API + path, opts)
      .then(r => r.json()
        .then(d => {
          const base = { ok: r.ok, status: r.status, data: d };
          if (!r.ok && !d.detail && !d.message && !d.error && !d.reason) {
            base.error = `HTTP ${r.status} ${r.statusText || ''}`.trim();
          }
          resolve(base);
        })
        .catch(() => resolve({ ok: r.ok, status: r.status, data: {}, error: r.ok ? undefined : `HTTP ${r.status} 非 JSON 响应` })))
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
    relay('form-data', '/api/extension/form-data', {}, 'GET').then(sendResponse);
    return true;
  }
  if (msg.type === 'fill-netapply') {
    relay('fill-netapply', '/api/extension/fill-netapply', msg.payload).then(sendResponse);
    return true;
  }
  if (msg.type === 'fill-ats') {
    relay('fill-ats', '/api/extension/fill-ats', msg.payload).then(sendResponse);
    return true;
  }
  if (msg.type === 'fill-enhance') {
    relay('fill-enhance', '/api/extension/fill-enhance', msg.payload).then(sendResponse);
    return true;
  }
  if (msg.type === 'recommend-resume') {
    relay('recommend-resume', '/api/extension/recommend-resume', msg.payload).then(sendResponse);
    return true;
  }
});
