/* 秋招工作台助手 · 后台 Service Worker（中转 API 调用，绕过扩展页 CORS） */
const API = 'http://127.0.0.1:8787';

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'capture') {
    fetch(API + '/api/extension/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg.payload)
    })
      .then(r => r.json())
      .then(d => sendResponse({ ok: true, data: d }))
      .catch(e => sendResponse({ ok: false, error: String(e) }));
    return true; // 保持消息通道异步响应
  }
  if (msg.type === 'form-data') {
    fetch(API + '/api/extension/form-data')
      .then(r => r.json())
      .then(d => sendResponse({ ok: true, data: d }))
      .catch(e => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
});
