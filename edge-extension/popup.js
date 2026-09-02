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
    const show = document.getElementById('c-url-show');
    if (show) show.textContent = '📎 可追溯链接：' + (tab.url || info.url || '');
    document.getElementById('cap-form').style.display = 'block';
    setStatus('已提取，请确认后加入岗位池（招聘公司/岗位名必填，来源网站已自动识别）');
  } catch (e) { setStatus('读取失败：' + e.message, true); }
});

document.getElementById('cap-save').addEventListener('click', async () => {
  const company = document.getElementById('c-company').value.trim();
  const title = document.getElementById('c-title').value.trim();
  if (!company || !title) { setStatus('招聘公司和岗位名必填', true); return; }
  if (SITE_NAMES.includes(company)) { setStatus('「' + company + '」是网站名，请填招人的公司', true); return; }
  const pageUrl = await getActiveUrl();
  const payload = {
    company,
    title,
    location: document.getElementById('c-loc').value.trim(),
    source: document.getElementById('c-source').value.trim(),
    url: pageUrl,
    source_url: pageUrl,
    jd_text: document.getElementById('c-jd').value.trim()
  };
  setStatus('正在加入岗位池…');
  const r = await callApi('capture', payload);
  if (r.ok) { setStatus('✓ 已加入岗位池（来源：' + (payload.source || 'Edge捕捉') + '）'); document.getElementById('cap-form').style.display = 'none'; }
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
  // 优先抓常见 JD 容器（BOSS/牛客/官网多有专属结构），回退整页正文
  let jd = '';
  const cand = document.querySelector(
    '.job-detail, .job-description, #job-description, .description, .detail-content, ' +
    '[class*="job-desc" i], [class*="JD" i], [id*="jd" i], [class*="position-detail" i]');
  if (cand) jd = cand.innerText || '';
  const text = (jd || (document.body ? document.body.innerText : '')).replace(/\s+/g, ' ').slice(0, 3000);
  return { title, url, h1, text };
}

// 已知招聘站点名（绝不能被当成「招聘公司」）
const SITE_NAMES = ['BOSS直聘', 'Boss直聘', '牛客网', '牛客', '拉勾网', '拉勾', '猎聘', 'LinkedIn', '领英', '智联招聘', '前程无忧', '51job', '实习僧', '海投网'];

// 岗位名常见关键词（用于区分「职位段」与「公司段」）
// 注意：不含「招聘/校招/社招」，因为它们前面必带岗位名（如 产品经理招聘→产品/经理 已命中），
// 否则「腾讯招聘」会被误判为职位段。
const JOB_KW = /(设计|产品|运营|开发|工程|程序|算法|测试|前端|后端|数据|分析|经理|专员|助理|实习|主管|总监|架构|研发|hr|人事|财务|市场|销售|客服|编辑|记者|策划|研究|顾问)/i;

function guessJob(info) {
  let host = '';
  try { host = new URL(info.url).hostname; } catch (e) {}
  let source = '官网';
  if (/zhipin|boss/.test(host)) source = 'BOSS直聘';
  else if (/nowcoder/.test(host)) source = '牛客';
  else if (/lagou/.test(host)) source = '拉勾';
  else if (/liepin/.test(host)) source = '猎聘';
  else if (/linkedin/.test(host)) source = 'LinkedIn';

  let raw = (info.h1 || info.title || '').trim();
  // 去掉标题尾巴的已知站点名（如 "-BOSS直聘"），避免污染公司识别
  let cleaned = raw.replace(/(?:[-_｜|]\s*)?(BOSS直聘|Boss直聘|牛客网|牛客|拉勾网|拉勾|猎聘|LinkedIn|领英|智联招聘|前程无忧|51job|实习僧|海投网)\s*$/i, '').trim();
  if (!cleaned) cleaned = raw;

  const segs = cleaned.split(/[|\-－_＿]/).map(s => s.trim()).filter(Boolean);
  let company = '', title = cleaned;

  if (segs.length >= 2) {
    const seg0Job = JOB_KW.test(segs[0]);
    const seg1Job = JOB_KW.test(segs[1]);
    if (segs.length === 2) {
      // 「职位-公司」或「公司-职位」
      if (seg0Job && !seg1Job) { title = segs[0]; company = segs[1]; }
      else if (seg1Job && !seg0Job) { title = segs[1]; company = segs[0]; }
      else { title = segs[0]; company = segs[1]; } // 默认按 BOSS 约定：职位-公司
    } else {
      // 3 段及以上：「职位-公司-城市」或「公司-职位-城市」
      if (seg0Job) { title = segs[0]; company = segs[1]; }
      else if (seg1Job) { title = segs[1]; company = segs[0]; }
      else { title = segs[0]; company = segs[1]; }
    }
  }
  // 守卫：公司绝不能是站点名
  if (SITE_NAMES.includes(company)) company = '';
  // 收尾：剥掉公司/职位尾巴的「招聘」（如「腾讯招聘」→「腾讯」、「UI设计师招聘」→「UI设计师」）
  company = company.replace(/招聘$/, '');
  title = title.replace(/招聘$/, '');
  return { company: company.trim(), title: title.trim(), source };
}
