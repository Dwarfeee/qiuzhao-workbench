/* 秋招工作台助手 · 弹出页 */
const API = 'http://127.0.0.1:8787';

// 跨事件保存：当前捕捉到的「来源平台」（如 BOSS直聘，仅追溯用）与「原页面 URL」
let currentPlatform = '';
let currentPageUrl = '';

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
    const res = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage, args: [SITE_EXTRACT_RULES] });
    const info = res && res[0] ? res[0].result : null;
    if (res && res[0] && res[0].error) { console.error('extractPage error:', res[0].error); }
    if (!info) { setStatus('无法读取页面' + (res && res[0] && res[0].error ? '：' + res[0].error : ''), true); return; }
    if (!info.text || !info.text.trim()) { setStatus('页面无可读内容，请确认在岗位详情页', true); return; }

    // 优先用 DeepSeek 智能解析（已做站点感知提取，只含右侧详情面板）
    setStatus('正在用 DeepSeek 解析岗位信息…');
    let parsed = null;
    try {
      const r = await callApi('smart-capture', {
        raw_text: info.text, url: info.url, title: info.title, h1: info.h1
      });
      if (r.ok && r.data && r.data.ok && r.data.company && r.data.title) {
        parsed = r.data;
      } else if (r.ok && r.data && r.data.reason) {
        console.warn('smart-capture 不可用，回退 guessJob：', r.data.reason, r.data.message);
      }
    } catch (e) {
      console.warn('smart-capture 调用失败，回退 guessJob：', e);
    }

    let company, title, loc, source, jd;
    if (parsed) {
      company = parsed.company || '';
      title = parsed.title || '';
      loc = parsed.location || '';
      source = parsed.source || '';
      jd = parsed.jd_text || info.text;
      setStatus('✓ DeepSeek 已解析（公司/岗位/薪资等），请核对后加入岗位池');
    } else {
      // 回退：本地启发式（无需 LLM）
      const g = guessJob(info);
      company = g.company || '';
      title = g.title || '';
      loc = '';
      source = g.source || '';
      jd = info.text;
      setStatus('已提取（启发式），请确认后加入岗位池（公司/岗位名必填）');
    }

    // 「来源网站」存公司官网，绝不放平台名；平台名仅做追溯展示
    currentPlatform = source || '';
    currentPageUrl = tab.url || info.url || '';
    let official = '';
    // 公司名已知时，先联网检索其官方招聘站，再展示表单
    if (company && company.trim()) {
      setStatus('正在全网检索「' + company.trim() + '」的官方招聘网站…');
      try {
        const sr = await callApi('find-company-site', { company: company.trim() });
        if (sr.ok && sr.data && sr.data.ok && sr.data.url) {
          official = sr.data.url;
          setStatus('✓ 已定位官方招聘站：' + official + '（可手动修改后再保存）');
        } else {
          const why = (sr.ok && sr.data && sr.data.message) ? '（' + sr.data.message + '）' : '';
          setStatus('未能自动检索到官网' + why + '，请在「来源网站」手动粘贴官网地址后再保存', true);
        }
      } catch (e) { console.warn('find-company-site 失败：', e); }
    }

    document.getElementById('c-company').value = company;
    document.getElementById('c-title').value = title;
    document.getElementById('c-loc').value = loc;
    document.getElementById('c-source').value = official;   // 来源网站 = 公司官网（检索失败则为空，需手动填）
    const plat = document.getElementById('c-platform');
    if (plat) plat.textContent = currentPlatform ? '来源平台：' + currentPlatform + '（仅作追溯，不写入「来源网站」）' : '';
    document.getElementById('c-jd').value = (jd || '').slice(0, 3000);
    const show = document.getElementById('c-url-show');
    if (show) show.textContent = '📎 可追溯链接：' + currentPageUrl;
    document.getElementById('cap-form').style.display = 'block';
  } catch (e) { setStatus('读取失败：' + e.message, true); }
});

document.getElementById('cap-save').addEventListener('click', async () => {
  const company = document.getElementById('c-company').value.trim();
  const title = document.getElementById('c-title').value.trim();
  if (!company || !title) { setStatus('招聘公司和岗位名必填', true); return; }
  if (SITE_NAMES.includes(company)) { setStatus('「' + company + '」是网站名，请填招人的公司', true); return; }
  const srcVal = document.getElementById('c-source').value.trim();
  const isSiteUrl = /^https?:\/\//i.test(srcVal);  // 官网检索成功时为 URL，否则为空
  const payload = {
    company,
    title,
    location: document.getElementById('c-loc').value.trim(),
    source: currentPlatform || 'Edge捕捉',  // 来源平台名（追溯用），永不是官网 URL
    url: currentPageUrl,
    source_url: isSiteUrl ? srcVal : currentPageUrl,  // 官方站则来源链接指向官网，否则指向原岗位页
    jd_text: document.getElementById('c-jd').value.trim()
  };
  setStatus('正在加入岗位池…');
  const r = await callApi('capture', payload);
  // 真实结果判定：relay 现在会透传 HTTP 状态；只有服务端返回 job_id 才算成功
  if (r.ok && r.data && (r.data.job_id || r.data.ok)) {
    setStatus('✓ 已加入岗位池（#' + (r.data.job_id != null ? r.data.job_id : '') + '，来源：' + (payload.source || 'Edge捕捉') + '）\n回到工作台刷新页面即可看到');
    document.getElementById('cap-form').style.display = 'none';
  } else {
    const msg = (r.data && (r.data.detail || r.data.message || r.data.error || r.data.reason))
      || r.error || '未知错误（未收到服务端响应）';
    setStatus('保存失败：' + msg + '\n请确认本地服务已在 8787 运行，并刷新工作台页面', true);
  }
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

  const sendFill = (extra) => new Promise(resolve => {
    chrome.tabs.sendMessage(tab.id,
      Object.assign({ type: 'fill', fields, personal, company: sel.value }, extra),
      resp => {
        if (chrome.runtime.lastError) return resolve({ ok: false, error: chrome.runtime.lastError.message });
        resolve(resp || { ok: false });
      });
  });

  // 本地可用值（key→value），用于 LLM 映射后取值；真实信息不出本地
  const KEYS = ['name', 'email', 'phone', 'school', 'major', 'city', 'gender', 'birth',
    'political', 'english', 'grad', 'degree', 'gpa', 'address', 'idcard'];
  const localMap = {};
  const pmap = { name: 'name', email: 'email', phone: 'phone', city: 'city', school: 'school', major: 'major' };
  for (const k in pmap) if (personal[k]) localMap[k] = personal[k];
  fields.forEach(f => { const key = canonicalize(f.label); if (key && f.value) localMap[key] = f.value; });

  const r1 = await sendFill({});
  if (!r1.ok) { setStatus('填充失败：' + (r1.error || ''), true); return; }
  let total = r1.filled || 0;
  const unfilled = (r1.unfilled || []).filter(Boolean);
  setStatus('✓ 已填充 ' + total + ' 个字段，DeepSeek 正在核对剩余 ' + unfilled.length + ' 个字段…');

  // 第二轮：把规则未命中的字段名交给 DeepSeek 映射，再补填（只发 label，不发真实值）
  if (unfilled.length) {
    try {
      const mr = await callApi('smart-fill-map', { labels: unfilled, available_keys: KEYS });
      if (mr.ok && mr.data && mr.data.ok && mr.data.map) {
        const mapped = [];
        for (const lab of unfilled) {
          const key = mr.data.map[lab];
          if (key && localMap[key]) mapped.push({ key, value: localMap[key] });
        }
        if (mapped.length) {
          const r2 = await sendFill({ mapped });
          total += (r2.filled || 0);
        }
      }
    } catch (e) { console.warn('smart-fill-map 失败，跳过：', e); }
  }
  setStatus('✓ 已尝试填充 ' + total + ' 个字段（DeepSeek 辅助 ' + (unfilled.length ? '已启用' : '无需') + '），请核对并补全缺失项');
});

/* ---------- 注入页面的提取函数（在目标标签页上下文执行） ---------- */
// 各站点规则：ignore=要剔除的侧边栏/列表；prefer=优先抓取的主内容容器。
// 目的是「只抓你点开的右侧详情面板」，绝不被左侧岗位列表污染。
const SITE_EXTRACT_RULES = {
  'zhipin':  { ignore: '.job-list,.job-card-wrapper,.rec-job-list,.job-menu,.container-left,.sidebar,.job-search-list,.search-job-list,.job-card-left,.left-list',
               prefer: '.job-detail,#main .job-primary,.job-sec,.job-sec-text,.job-description,.detail-content,.position-detail,[class*="job-detail"],[class*="position-detail"],[class*="job-desc"]' },
  'boss':    { ignore: '.job-list,.job-card-wrapper,.rec-job-list,.job-menu,.container-left,.sidebar,.job-search-list,.search-job-list,.job-card-left,.left-list',
               prefer: '.job-detail,#main .job-primary,.job-sec,.job-sec-text,.job-description,.detail-content,.position-detail,[class*="job-detail"],[class*="position-detail"],[class*="job-desc"]' },
  'nowcoder': { ignore: '.sidebar,.job-list,.rec-list,.company-job-list,.post-aside,.left-side,.right-aside',
                prefer: '.position-info,.job-detail-box,.post-detail,.job-detail,.job-description,.detail-content,[class*="job-detail"],[class*="position-detail"]' },
  'lagou':   { ignore: '.job-list,.sidebar,.company-job-list,.left-aside,.position-list',
               prefer: '.job-detail,.position-info,.job-intro,.job-description,.detail-content,[class*="job-detail"],[class*="position-detail"]' },
  'liepin':  { ignore: '.sidebar,.job-list,.job-menu,.left-side,.search-job-list',
               prefer: '.job-detail,.about-position,.job-item-main,.job-description,.detail-content,[class*="job-detail"],[class*="position-detail"]' },
  'linkedin': { ignore: '.scaffold-layout__aside,.jobs-search-results-list,.left-rail',
                prefer: '.jobs-details,.jobs-description,.description__content,.job-details,[class*="job-details"]' }
};

function extractPage(SITE_EXTRACT_RULES) {
  const title = document.title || '';
  const url = location.href;
  let host = '';
  try { host = new URL(url).hostname; } catch (e) {}

  let ignoreSel = '', preferSel = '';
  for (const k in SITE_EXTRACT_RULES) {
    if (host.includes(k)) { ignoreSel = SITE_EXTRACT_RULES[k].ignore; preferSel = SITE_EXTRACT_RULES[k].prefer; break; }
  }

  const JD_HEADINGS = /职位描述|岗位职责|工作职责|岗位要求|任职要求|job description|responsibilities|requirements/i;
  const GENERIC_HEADING = /^(职位描述|岗位职责|工作职责|岗位要求|任职要求|公司介绍|关于我们|职位信息|job description|responsibilities|requirements|公司福利|工作地址)$/i;

  // 清理节点：去掉 script/style/noscript 等不会展示给用户的标签后再取文本
  function cleanText(el) {
    if (!el) return '';
    const clone = el.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,svg,canvas,template,iframe').forEach(n => n.remove());
    return (clone.innerText || '').replace(/\s+/g, ' ').trim();
  }
  function stripTags(el) {
    if (!el) return el;
    const clone = el.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,svg,canvas,template,iframe').forEach(n => n.remove());
    return clone;
  }

  // 1) 优先主内容容器
  let root = null;
  if (preferSel) {
    for (const sel of preferSel.split(',')) {
      const el = document.querySelector(sel.trim());
      if (el) { root = el; break; }
    }
  }

  // 2) 通用：找含 JD 关键词的最大容器（适配没写专用规则的官网）
  if (!root) {
    const all = Array.from(document.querySelectorAll('div, section, article, main'));
    let best = null, bestLen = 0;
    for (const el of all) {
      const t = cleanText(el);
      if (JD_HEADINGS.test(t) && t.length > bestLen && t.length < 20000) { best = el; bestLen = t.length; }
    }
    root = best;
  }

  // 3) 从 root 里剔除侧边栏/列表（避免左侧岗位列表混入）
  let container = root;
  if (root && ignoreSel) {
    const clone = stripTags(root);
    clone.querySelectorAll(ignoreSel).forEach(n => n.remove());
    container = clone;
  } else if (root) {
    container = stripTags(root);
  }

  // 4) 标题：先站点专用选择器，再非通用 heading，最后 document.title
  let h1 = '';
  const titleSelectors = (host.includes('zhipin') || host.includes('boss'))
    ? '.job-name .name,.job-name>.name,.job-name,.job-title,.position-name,[class*="job-name"],[class*="job-title"],[class*="position-name"]'
    : host.includes('nowcoder')
    ? '.post-title,.job-name,.position-name,[class*="job-name"],[class*="position-name"]'
    : host.includes('lagou')
    ? '.job-name,.position-name,[class*="job-name"],[class*="position-name"]'
    : host.includes('liepin')
    ? '.job-title,.position-title,[class*="job-title"],[class*="position-name"]'
    : host.includes('linkedin')
    ? '.jobs-details-top-card__job-title,[class*="job-title"],[class*="position-title"]'
    : '';

  function pickTitle(scope) {
    if (!scope) return '';
    if (titleSelectors) {
      for (const sel of titleSelectors.split(',')) {
        const el = scope.querySelector(sel.trim());
        if (el && el.textContent.trim()) {
          const txt = el.textContent.trim();
          if (!GENERIC_HEADING.test(txt)) return txt;
        }
      }
    }
    for (const h of scope.querySelectorAll('h1,h2,h3')) {
      const txt = h.textContent.trim();
      if (txt && !GENERIC_HEADING.test(txt)) return txt;
    }
    return '';
  }

  h1 = pickTitle(container) || pickTitle(root) || (() => {
    const h = document.querySelector('h1');
    if (h) { const txt = h.textContent.trim(); if (txt && !GENERIC_HEADING.test(txt)) return txt; }
    return '';
  })();
  if (!h1) h1 = title;

  // 5) 正文
  let text = cleanText(container).slice(0, 4000);
  // 若主容器为空，回退整页（仍剔除 ignore + script）
  if (!text && document.body) {
    const b = stripTags(document.body);
    if (ignoreSel) b.querySelectorAll(ignoreSel).forEach(n => n.remove());
    text = (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 4000);
  }

  return { title, url, h1, text, host };
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

  const raw = (info.h1 || info.title || '').trim();
  // 去掉整串尾巴的已知站点名（如 "-BOSS直聘"），避免污染公司识别
  let cleaned = raw.replace(/(?:[-_｜|]\s*)?(BOSS直聘|Boss直聘|牛客网|牛客|拉勾网|拉勾|猎聘|LinkedIn|领英|智联招聘|前程无忧|51job|实习僧|海投网)\s*$/i, '').trim();
  if (!cleaned) cleaned = raw;

  // 切分：支持「-」「_」「｜」与空格（牛客/官网 h1 多为空格分隔）
  const SITE_SET = new Set(SITE_NAMES.map(s => s.toLowerCase()));
  const RECRUIT = /^(招聘|校招|社招|实习|内推)$/i; // 独立词段需剔除（如「校招」「招聘」）
  let segs = cleaned.split(/[|\-－_＿\s]+/).map(s => s.trim()).filter(Boolean);
  segs = segs.filter(s => !SITE_SET.has(s.toLowerCase()) && !RECRUIT.test(s));
  segs = segs.map(s => s.replace(/(招聘|校招|社招)$/, '').trim()).filter(Boolean);

  let company = '', title = cleaned;
  if (segs.length >= 2) {
    const s0 = JOB_KW.test(segs[0]);
    const s1 = JOB_KW.test(segs[1]);
    if (segs.length === 2) {
      // 「职位-公司」或「公司-职位」
      if (s0 && !s1) { title = segs[0]; company = segs[1]; }
      else if (s1 && !s0) { title = segs[1]; company = segs[0]; }
      else { title = segs[0]; company = segs[1]; } // 默认按 BOSS 约定：职位-公司
    } else {
      // 3 段及以上：「职位-公司-城市」或「公司-职位-城市」
      if (s0) { title = segs[0]; company = segs[1]; }
      else if (s1) { title = segs[1]; company = segs[0]; }
      else { title = segs[0]; company = segs[1]; }
    }
  }
  // 守卫：公司绝不能是站点名（兜底，独立词段已在上面过滤）
  if (SITE_SET.has(company.toLowerCase())) company = '';
  // 收尾：剥掉公司/职位尾巴的「招聘」（如「腾讯招聘」→「腾讯」、「UI设计师招聘」→「UI设计师」）
  company = company.replace(/招聘$/, '');
  title = title.replace(/招聘$/, '');
  return { company: company.trim(), title: title.trim(), source };
}
