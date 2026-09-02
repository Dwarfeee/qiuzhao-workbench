/* 秋招工作台助手 · 弹出页 */
const API = 'http://127.0.0.1:8787';

function setStatus(s, err) {
  const el = document.getElementById('status');
  if (el) { el.textContent = s; el.className = 'muted' + (err ? ' err' : ''); }
}
function esc(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function getActiveUrl() {
  return new Promise(resolve => {
    chrome.tabs.query({ active: true, currentWindow: true }, t => resolve(t[0] ? t[0].url : ''));
  });
}
function callApi(type, payload) {
  return new Promise(resolve => {
    chrome.runtime.sendMessage({ type, payload }, resp => {
      if (chrome.runtime.lastError) return resolve({ ok: false, error: chrome.runtime.lastError.message });
      resolve(resp || { ok: false });
    });
  });
}

/* ---------- 捕捉当前页 ---------- */
document.getElementById('capture').addEventListener('click', async () => {
  setStatus('正在读取当前页…');
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const res = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage });
    const info = res && res[0] ? res[0].result : null;
    if (!info) { setStatus('无法读取页面', true); return; }
    const g = guessJob(info);
    document.getElementById('c-company').value = g.company || '';
    document.getElementById('c-title').value = g.title || '';
    document.getElementById('c-loc').value = '';
    document.getElementById('c-source').value = g.source || '';
    document.getElementById('c-jd').value = (info.text || '').slice(0, 3000);
    document.getElementById('cap-form').style.display = 'block';
    setStatus('已提取，请确认后加入岗位池（公司/岗位名必填）');
  } catch (e) { setStatus('读取失败：' + e.message, true); }
});

document.getElementById('cap-save').addEventListener('click', async () => {
  const payload = {
    company: document.getElementById('c-company').value.trim(),
    title: document.getElementById('c-title').value.trim(),
    location: document.getElementById('c-loc').value.trim(),
    source: document.getElementById('c-source').value.trim(),
    url: await getActiveUrl(),
    jd_text: document.getElementById('c-jd').value.trim()
  };
  if (!payload.company || !payload.title) { setStatus('公司和岗位名必填', true); return; }
  setStatus('正在加入岗位池…');
  const r = await callApi('capture', payload);
  if (r.ok) { setStatus('✓ 已加入岗位池'); document.getElementById('cap-form').style.display = 'none'; }
  else setStatus('失败：' + (r.error || '未知错误'), true);
});

/* ---------- 填网申 ---------- */
document.getElementById('fill').addEventListener('click', async () => {
  setStatus('拉取网申信息…');
  const r = await callApi('form-data');
  if (!r.ok) { setStatus('拉取失败：' + (r.error || ''), true); return; }
  const data = r.data || {};
  const sel = document.getElementById('f-company');
  const comps = data.companies || [];
  sel.innerHTML = '<option value="">通用（不指定企业）</option>' +
    comps.map(c => `<option>${esc(c)}</option>`).join('');
  sel.dataset.fields = JSON.stringify(data.fields || []);
  sel.dataset.personal = JSON.stringify(data.personal || {});
  document.getElementById('fill-box').style.display = 'block';
  setStatus('选择企业后点「填充当前页表单」（填完请自行核对补漏）');
});

document.getElementById('fill-go').addEventListener('click', async () => {
  const sel = document.getElementById('f-company');
  const fields = JSON.parse(sel.dataset.fields || '[]');
  const personal = JSON.parse(sel.dataset.personal || '{}');
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  setStatus('正在填充…');
  chrome.tabs.sendMessage(tab.id, { type: 'fill', fields, personal, company: sel.value }, resp => {
    if (chrome.runtime.lastError) { setStatus('填充失败：' + chrome.runtime.lastError.message, true); return; }
    const n = (resp && resp.filled) || 0;
    setStatus('✓ 已尝试填充 ' + n + ' 个字段，请核对并补全缺失项');
  });
});

/* ---------- 注入页面的提取函数（在目标标签页上下文执行） ---------- */
function extractPage() {
  const title = document.title || '';
  const url = location.href;
  const h1 = (document.querySelector('h1') || {}).textContent || '';
  const text = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 3000);
  return { title, url, h1, text };
}

function guessJob(info) {
  let host = '';
  try { host = new URL(info.url).hostname; } catch (e) {}
  let source = '官网';
  if (/zhipin|boss/.test(host)) source = 'BOSS直聘';
  else if (/nowcoder/.test(host)) source = '牛客';
  else if (/lagou/.test(host)) source = '拉勾';
  else if (/liepin/.test(host)) source = '猎聘';
  else if (/linkedin/.test(host)) source = 'LinkedIn';
  let title = (info.h1 || info.title || '').trim();
  let company = '';
  const segs = title.split(/[|\-－]/).map(s => s.trim()).filter(Boolean);
  if (segs.length >= 2) {
    const tail = segs[segs.length - 1] || '';
    if (/(直聘|招聘|boss|牛客|校招|社招|实习|官网|careers?|job)$/i.test(tail) || segs.length > 2) {
      company = segs[segs.length - 2] || '';
      title = segs[0] || title;
    }
  }
  return { company, title, source };
}
