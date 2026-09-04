/* 秋招 OS 前端 · 单页应用 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.detail || data.error || r.statusText), { data });
  return data;
}

function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.classList.add('show'), 10);
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 3200);
}

function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
// 从 URL 取干净的主机名（去掉 www.），用于「来源」链接文字
function hostOf(u) { const m = /https?:\/\/([^/?#]+)/i.exec(u || ''); return m ? m[1].replace(/^www\./i, '') : (u || ''); }

const STATUS_LABELS = {
  'New': '新加入', 'Shortlisted': '精投待处理', 'Tailoring': '定制中', 'Ready to Apply': '待投递',
  'Applied': '已投递', 'Online Assessment': '笔试/OA', 'Interview': '面试中', 'Offer': '已获 Offer',
  'Rejected': '已拒', 'Withdrawn': '已撤回', 'Closed': '已关闭'
};
const stCls = s => 'st st-' + String(s).replace(/ /g, '');

/* ---------------- 导航 ---------------- */
$('#nav').addEventListener('click', e => {
  const a = e.target.closest('a[data-page]');
  if (!a) return;
  $$('#nav a').forEach(x => x.classList.remove('active'));
  a.classList.add('active');
  $$('.page').forEach(p => p.classList.remove('active'));
  $('#page-' + a.dataset.page).classList.add('active');
  loadPage(a.dataset.page);
});

function loadPage(p) {
  ({ dashboard: renderDashboard, jobs: renderJobs, precision: renderPrecision,
     today: renderToday, applications: renderApplications, interviews: renderInterviews, sources: renderSources,
     review: renderReview, notify: renderNotify, settings: renderSettings }[p] || (() => {}))();
}

/* ---------------- Modal ---------------- */
function openModal(html) { $('#modal').innerHTML = html; $('#modal-mask').classList.add('open'); }
function closeModal() { $('#modal-mask').classList.remove('open'); }
$('#modal-mask').addEventListener('click', e => { if (e.target.id === 'modal-mask') closeModal(); });

/* ================= Dashboard ================= */
async function renderDashboard() {
  const d = await api('/api/dashboard');
  const brief = await api('/api/daily/briefing').catch(() => null);
  $('#dash-date').textContent = new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' });

  // 今日秋招简报（工作日）或周末复盘
  if (brief) {
    if (brief.is_weekend) {
      renderWeekendReview();
    } else {
      $('#daily-brief').innerHTML = `<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px">
        <b style="font-size:16px">☀️ 今日秋招</b><span class="muted">${esc(brief.date)}</span>
        <span class="muted">今日目标：<b>${brief.daily_target}</b> 个精投机会（目标非 KPI，宁缺毋滥）</span>
      </div>
      <div class="stat-grid small" style="grid-template-columns:repeat(5,1fr)">
        <div class="stat"><div class="n">${brief.today_jobs}</div><div class="l">今日发现</div></div>
        <div class="stat hl"><div class="n">${brief.today_high}</div><div class="l">高匹配</div></div>
        <div class="stat hl"><div class="n">${brief.suggest_shortlist}</div><div class="l">建议精投</div></div>
        <div class="stat"><div class="n">${brief.today_applied}</div><div class="l">今日已投</div></div>
        <div class="stat"><div class="n">${brief.pending}</div><div class="l">待处理</div></div>
      </div>
      <div class="grid-2" style="gap:12px">
        <div><b>今日重点岗位</b>
          <div style="margin-top:6px">${(brief.top_jobs || []).length ? brief.top_jobs.slice(0, 10).map((j, i) =>
            `<div style="padding:5px 0;border-bottom:1px solid var(--soft)">${i + 1}. <b>${esc(j.company)}</b> / ${esc(j.title)} <span class="badge b-${j.grade}">${j.grade} ${j.fit_score}</span> ${j.deadline && j.deadline_days != null ? `<span class="muted">截止 ${esc(j.deadline_label)}</span>` : ''}
             <button class="btn sm" style="float:right" onclick="showJobDetail(${j.id})">查看 JD</button></div>`).join('') : '<div class="empty">暂无已分析岗位</div>'}</div>
        </div>
        <div>
          <b>今日待办</b>
          <div style="margin-top:6px">${(brief.todo || []).map(t => `<div class="${t.done ? 'todo-done' : ''}" style="padding:3px 0">${t.done ? '☑' : '☐'} ${esc(t.task)}</div>`).join('')}</div>
        </div>
      </div>
      <div style="margin-top:10px"><b>即将发生</b>
        <div style="margin-top:6px">
          ${(brief.deadlines || []).length ? brief.deadlines.map(x => `<div style="padding:2px 0">⏰ 截止：${esc(x.company)} · ${esc(x.title)} <span class="badge ${x.days <= 3 ? 'b-D' : 'b-B'}">${esc(x.label)}</span></div>`).join('') : '<div class="muted">暂无临近截止</div>'}
          ${(brief.upcoming_interviews || []).length ? brief.upcoming_interviews.map(x => `<div style="padding:2px 0">🎤 面试：${esc(x.j_company)} · ${esc(x.j_title)} <span class="badge b-A">${esc(x.round)}</span> ${esc(x.scheduled_at || '')}</div>`).join('') : ''}
        </div>
      </div>`;
    }
  }

  renderDailyDelivery();

  $('#stat-grid').innerHTML = [
    ['今日发现', d.today_jobs, ''], ['今日新增 S/A', d.today_new_s_a, 'hl'],
    ['待投 (Ready)', d.ready, 'hl'], ['今日已投', d.today_applied, ''],
    ['精投待处理', d.pending, ''], ['累计投递', d.total_applied, ''],
    ['面试中', d.upcoming_interviews.length, ''], ['Offer', d.offers, 'hl'],
  ].map(([l, n, hl]) => `<div class="stat ${hl}"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');

  $('#dash-top').innerHTML = d.top_jobs.length ? '<table><tbody>' + d.top_jobs.map(j =>
    `<tr><td><b>${esc(j.company)}</b> · ${esc(j.title)}</td><td><span class="badge b-${j.grade}">${j.grade} ${j.fit_score}</span></td></tr>`).join('') + '</tbody></table>'
    : '<div class="empty">今日暂无已分析岗位</div>';
  $('#dash-deadline').innerHTML = d.deadlines.length ? '<table><tbody>' + d.deadlines.map(j =>
    `<tr><td>${esc(j.company)} · ${esc(j.title)}</td><td class="muted">${esc(j.deadline)}</td></tr>`).join('') + '</tbody></table>'
    : '<div class="empty">无即将截止岗位</div>';
  $('#dash-interviews').innerHTML = d.upcoming_interviews.length ? d.upcoming_interviews.map(i =>
    `<div class="jc-meta" style="padding:6px 0;border-bottom:1px solid var(--soft)"><b>${esc(i.company)}</b> · ${esc(i.round)} · ${esc(i.scheduled_at || '')}</div>`).join('')
    : '<div class="empty">近期无面试安排</div>';
  $('#dash-recent').innerHTML = d.recent_apps.length ? d.recent_apps.map(a =>
    `<div style="padding:6px 0;border-bottom:1px solid var(--soft)">${esc(a.company)} · ${esc(a.title)} <span class="${stCls(a.status)}" style="float:right">${STATUS_LABELS[a.status]}</span></div>`).join('')
    : '<div class="empty">暂无投递记录</div>';

  const max = Math.max(1, ...d.trend.map(t => t.count));
  $('#dash-trend').innerHTML = d.trend.length ? d.trend.map(t =>
    `<div class="bar" style="height:${Math.max(4, t.count / max * 60)}px" title="${t.date}: ${t.count}"><span>${t.date.slice(5)}</span></div>`).join('')
    : '<div class="empty">暂无投递趋势数据</div>';

  // 秋招总览 + 面试轮次分布
  const ov = d.overview || {};
  let overviewEl = document.getElementById('dash-overview');
  if (!overviewEl) {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<h3>秋招总览</h3><div id="dash-overview"></div>';
    $('#page-dashboard').insertBefore(card, $('#page-dashboard').querySelector('.grid-2'));
    overviewEl = document.getElementById('dash-overview');
  }
  overviewEl.innerHTML = `<div class="stat-grid small" style="grid-template-columns:repeat(7,1fr)">
    ${[['总岗位', ov.total_jobs], ['精投', ov.shortlisted], ['已投', ov.applied], ['OA', ov.oa],
       ['面试', ov.interviewing], ['Offer', ov.offers], ['Reject', ov.rejected]]
       .map(([l, n]) => `<div class="stat"><div class="n">${n ?? 0}</div><div class="l">${l}</div></div>`).join('')}</div>`;
  const rd = d.round_dist || {};
  if (Object.keys(rd).length) {
    let rdEl = document.getElementById('dash-rounds');
    if (!rdEl) {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = '<h3>面试轮次分布</h3><div id="dash-rounds"></div>';
      $('#page-dashboard').insertBefore(card, $('#page-dashboard').querySelector('.grid-2').nextSibling);
      rdEl = document.getElementById('dash-rounds');
    }
    rdEl.innerHTML = Object.entries(rd).map(([r, n]) => `<span class="tag" style="margin-right:6px">${esc(r)}：<b>${n}</b></span>`).join('');
  }
}

/* ================= 今日投递清单（练手/面试准备策略）================= */
async function renderDailyDelivery() {
  const el = document.getElementById('daily-delivery');
  if (!el) return;
  const s = await api('/api/delivery/today').catch(() => null);
  if (!s) { el.innerHTML = ''; return; }
  const phaseCls = s.phase.includes('练手') ? 'b-B' : 'b-A';
  const jobsHtml = (s.jobs || []).length ? s.jobs.map(j =>
    `<div style="padding:6px 0;border-bottom:1px solid var(--soft)">
       <b>${esc(j.company)}</b> / ${esc(j.title)} <span class="badge b-${j.grade}">${j.grade} ${j.fit_score}</span>
       ${j.company_scale ? `<span class="badge b-${j.company_scale === '500+' ? 'S' : j.company_scale === '100-499' ? 'A' : 'B'}">${esc(j.company_scale)} 人</span>` : ''}
       <button class="btn sm" style="float:right" onclick="showJobDetail(${j.id})">查看 JD</button>
     </div>`).join('')
    : '<div class="empty">今日无符合策略的可投岗位（精投中心还空？先去岗位池 ★ 加入精投）</div>';
  el.innerHTML = `<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px">
      <b style="font-size:15px">🎯 今日投递清单</b>
      <span class="badge ${phaseCls}">${esc(s.phase)}</span>
      ${s.interview_ready
        ? '<span class="badge b-A">已准备好面试（跳过 0-99）</span>'
        : '<span class="badge b-B">练手期（含 0-99）</span>'}
    </div>
    <div class="stat-grid small" style="grid-template-columns:repeat(4,1fr);margin-bottom:8px">
      <div class="stat"><div class="n">${s.total_eligible}</div><div class="l">可投岗位（符合规模）</div></div>
      <div class="stat hl"><div class="n">${s.daily_quota}</div><div class="l">今日配额</div></div>
      <div class="stat"><div class="n">${s.week_added}/${s.weekly_target}</div><div class="l">本周已加 / 目标</div></div>
      <div class="stat"><div class="n">${s.sizes_eligible.join(' · ')}</div><div class="l">今日可投规模</div></div>
    </div>
    ${s.sizes_excluded.length ? `<div class="muted" style="margin-bottom:6px">今日跳过规模：<b>${s.sizes_excluded.join('、')}</b></div>` : ''}
    <div>${jobsHtml}</div>`;
}

/* ================= 周末复盘 ================= */
async function renderWeekendReview() {
  const wr = await api('/api/daily/weekend-review').catch(() => null);
  if (!wr) { $('#daily-brief').innerHTML = '<div class="empty">周末复盘数据加载失败</div>'; return; }
  const s = wr.stats || {};
  $('#daily-brief').innerHTML = `<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px">
    <b style="font-size:16px">📅 周末复盘</b><span class="muted">${esc(wr.week || '')} ${esc(wr.range || '')}</span>
    ${wr.sample_sufficient ? '' : '<span class="badge b-B">样本不足</span>'}
  </div>
  <div class="stat-grid small" style="grid-template-columns:repeat(7,1fr)">
    ${[['发现', s.discovered], ['高匹配', '—'], ['精投', s.shortlisted], ['投递', s.applied], ['OA', s.oa], ['面试', s.interviews], ['Offer', s.offers]]
      .map(([l, n]) => `<div class="stat"><div class="n">${n ?? 0}</div><div class="l">${l}</div></div>`).join('')}
  </div>
  <div class="grid-2" style="gap:12px">
    <div class="card"><h3>岗位方向表现</h3>${(wr.directions || []).length ? '<table><thead><tr><th>方向</th><th>投递</th><th>面试</th><th>Offer</th></tr></thead><tbody>' + wr.directions.map(d => `<tr><td><b>${esc(d.direction)}</b></td><td>${d.applied}</td><td>${d.interviewing}</td><td>${d.offer}</td></tr>`).join('') + '</tbody></table>' : '<div class="empty">无数据</div>'}</div>
    <div class="card"><h3>招聘来源表现</h3>${(wr.sources || []).length ? '<table><thead><tr><th>来源</th><th>投递</th><th>面试</th></tr></thead><tbody>' + wr.sources.map(x => `<tr><td><b>${esc(x.source)}</b></td><td>${x.applied}</td><td>${x.interviewing}</td></tr>`).join('') + '</tbody></table>' : '<div class="empty">无数据</div>'}</div>
  </div>
  <div class="grid-2" style="gap:12px">
    <div class="card"><h3>Fit Score 是否与结果相关</h3>${(wr.fit_score_conversion || []).length ? '<table><thead><tr><th>分档</th><th>投递</th><th>面试率</th></tr></thead><tbody>' + wr.fit_score_conversion.map(f => `<tr><td><b>${esc(f.bucket)}</b></td><td>${f.applied}</td><td>${f.interview_rate != null ? f.interview_rate + '%' : '—'} ${f.sufficient ? '' : '<span class="badge b-B">样本不足</span>'}</td></tr>`).join('') + '</tbody></table>' : '<div class="empty">无数据</div>'}</div>
    <div class="card"><h3>反复出现的面试问题</h3>${(wr.recurring_questions || []).length ? wr.recurring_questions.map(q => `<div style="padding:4px 0">• ${esc(q.question)} <span class="tag">出现 ${q.c} 次</span></div>`).join('') : '<div class="empty">暂无记录问题</div>'}</div>
  </div>
  <div class="muted" style="margin-top:8px">完整周报与下周策略见「周度复盘」页。${wr.sample_sufficient ? '' : '本周样本不足，暂不强行下结论。'}</div>`;
}

/* ================= 岗位池 ================= */
async function renderJobs() {
  const q = $('#job-search').value || '', dir = $('#job-dir-filter').value || '';
  // 岗位池只显示尚未加入精投中心的岗位（status=New）——「加入精投」视为把岗位迁移到精投中心
  const jobs = await api(`/api/jobs?q=${encodeURIComponent(q)}&direction=${encodeURIComponent(dir)}&status=New`);
  const dirs = [...new Set(jobs.map(j => j.direction).filter(Boolean))];
  const sel = $('#job-dir-filter');
  if (sel.options.length <= 1) sel.innerHTML = '<option value="">全部方向</option>' + dirs.map(d => `<option>${d}</option>`).join('');
  $('#jobs-list').innerHTML = jobs.length ? jobs.map(j => jobCard(j)).join('') : '<div class="empty">岗位池为空：点上方「添加岗位 / 粘贴 JD」</div>';
}

/* ---------- 截止时间与规模辅助 ---------- */
function deadlineHint(dl, scale) {
  if (!dl) return '';
  const d = new Date(dl.replace(/-/g, '/'));
  if (isNaN(d)) return '';
  const days = Math.ceil((d - new Date()) / 86400000);
  const big = scale === '500+';
  if (days < 0) return ' <span class="badge b-D">已截止</span>';
  if (days === 0) return ' <span class="badge b-D">🔴 TODAY</span>';
  if (days <= 3) return ` <span class="badge b-C">⚠️ ${days} 天后</span>`;
  if (days <= 14) return big ? ` <span class="badge b-C">🔥 大厂 ${days} 天紧急</span>` : ` <span class="muted">（${days} 天）</span>`;
  return ` <span class="muted">（${days} 天）</span>`;
}

function scaleTag(scale, jobId) {
  if (!scale) return '<span class="tag muted">规模未标记</span>';
  const cls = scale === '500+' ? 'b-S' : scale === '100-499' ? 'b-A' : scale === '0-99' ? 'b-B' : '';
  return `<span class="badge ${cls}">${esc(scale)} 人</span>`;
}

function fallRecruitTag(v) {
  if (!v) return '<span class="tag muted">秋招未标记</span>';
  if (v === '进行中') return '<span class="badge b-A">🍂 秋招进行中</span>';
  if (v === '未开始') return '<span class="badge b-D">秋招未开始</span>';
  return `<span class="tag">秋招：${esc(v)}</span>`;
}

// 投递策略标签：体现 planner 的「小厂练手 / 大厂主投」思路
function strategyTag(scale) {
  if (scale === '0-99') return '<span class="badge b-B">🛠 小厂练手</span>';
  if (scale === '100-499') return '<span class="badge b-A">🎯 主投</span>';
  if (scale === '500+') return '<span class="badge b-S">🏆 大厂冲刺</span>';
  return '';
}

window.setFallRecruit = (id, cur) => {
  const opts = [
    { v: '进行中', label: '🍂 秋招进行中', desc: '已开始，可投递', cls: 'b-A' },
    { v: '未开始', label: '秋招未开始', desc: '暂未启动', cls: 'b-D' },
  ];
  const btns = opts.map(o => `<button class="btn scale-opt ${o.v === cur ? 'primary' : ''}" onclick="applyFallRecruit(${id}, '${o.v}')">
      <span class="badge ${o.cls}">${o.label}</span></button>`).join('');
  openModal(`<h2>🍂 秋招状态</h2>
    <p class="muted">标记该公司秋招是否已开始，便于岗位池一眼区分。</p>
    <div class="scale-opts">${btns}</div>
    ${cur ? `<div class="m-actions" style="justify-content:flex-start"><button class="btn sm link-btn" onclick="clearFallRecruit(${id})">清除当前标记</button></div>` : ''}
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button></div>`);
};

window.applyFallRecruit = async (id, v) => {
  try {
    await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ fall_recruit: v }) });
    closeModal(); toast('秋招状态：' + v); renderJobs();
  } catch (e) { toast(e.data?.detail || e.message, true); }
};

window.clearFallRecruit = async (id) => {
  try {
    await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ fall_recruit: '' }) });
    closeModal(); toast('已清除秋招状态标记'); renderJobs();
  } catch (e) { toast(e.data?.detail || e.message, true); }
};

window.setScale = (id, cur) => {
  const opts = [
    { v: '0-99', label: '0-99 人', desc: '小厂 · 练手优先', cls: 'b-B' },
    { v: '100-499', label: '100-499 人', desc: '中型 · 正式投递', cls: 'b-A' },
    { v: '500+', label: '500 人以上', desc: '大厂', cls: 'b-S' },
  ];
  const btns = opts.map(o => `<button class="btn scale-opt ${o.v === cur ? 'primary' : ''}" onclick="applyScale(${id}, '${o.v}')">
      <span class="badge ${o.cls}">${o.label}</span> <span class="muted">${o.desc}</span></button>`).join('');
  openModal(`<h2>🏷 标记公司人数规模</h2>
    <p class="muted">用于「前 7 天练手（含 0-99）」与「准备好面试后跳过 0-99」策略。</p>
    <div class="scale-opts">${btns}</div>
    ${cur ? `<div class="m-actions" style="justify-content:flex-start"><button class="btn sm link-btn" onclick="clearScale(${id})">清除当前标记</button></div>` : ''}
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button></div>`);
};

window.applyScale = async (id, v) => {
  try {
    await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ company_scale: v }) });
    closeModal(); toast('规模已标记：' + v + ' 人'); renderJobs();
  } catch (e) { toast(e.data?.detail || e.message, true); }
};

window.clearScale = async (id) => {
  try {
    await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ company_scale: '' }) });
    closeModal(); toast('已清除规模标记'); renderJobs();
  } catch (e) { toast(e.data?.detail || e.message, true); }
};

window.setDeadline = async (id, cur) => {
  const v = prompt('填写招聘截止时间（YYYY-MM-DD）：\n\n留空表示未标注（系统不会猜测）', cur || '');
  if (v === null) return;
  const val = v.trim();
  if (val && !/^\d{4}-\d{2}-\d{2}$/.test(val)) { toast('格式应为 YYYY-MM-DD', true); return; }
  try {
    await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ deadline: val }) });
    toast(val ? '截止时间已记录：' + val : '已清空截止时间'); renderJobs();
  } catch (e) { toast(e.data?.detail || e.message, true); }
};

function jobCard(j) {
  const a = j.match_analysis;
  let matchHtml = '';
  if (a) {
    matchHtml = `<ul class="match-list">` +
      (a.matched || []).slice(0, 3).map(m =>
        `<li>✔ <b>${m.dimension}</b> <span class="muted">（JD: ${m.jd_keywords.map(esc).join('、')}）</span>
         <div class="ev">${m.evidence.map(e => `<b>${esc(e.title)}</b>`).join(' · ')}</div></li>`).join('') +
      (a.gaps || []).slice(0, 2).map(g => `<li>✘ <b>${g.dimension}</b> <span class="muted">缺口：资料库无对应证据</span></li>`).join('') +
      `</ul>`;
  }
  const bucket = j.fit_score != null ? (j.fit_score >= 80 ? 'A' : j.fit_score >= 70 ? 'B' : j.fit_score >= 60 ? 'C' : 'D') : '';
  return `<div class="job-card" id="job-${j.id}">
    <div class="row1"><div><span class="jc-company">${esc(j.company)}</span> <span class="jc-title">${esc(j.title)}</span></div>
    <div>${j.grade ? `<span class="badge b-${j.grade}">${j.grade} · ${j.fit_score}</span>` : '<span class="muted">未分析</span>'}
    ${bucket ? `<span class="badge ${bucket === 'A' ? 'b-S' : bucket === 'B' ? 'b-A' : bucket === 'C' ? 'b-B' : 'b-D'}" style="margin-left:4px">${bucket === 'A' ? '强匹配' : bucket === 'B' ? '较匹配' : bucket === 'C' ? '弱匹配' : '不匹配'}</span>` : ''}</div></div>
    <div class="jc-meta">
      <span title="抓取来源网站（可点击追溯）">🌐 来源：${ j.source_url ? '<a href="'+esc(j.source_url)+'" target="_blank" title="'+esc(j.source_url)+'">'+esc(hostOf(j.source_url))+'</a>' : '<b>'+esc(j.source || '手动添加')+'</b>' }${ j.source && j.source_url ? ' <span class="muted">· 来自 '+esc(j.source)+'</span>' : '' }</span>
      ${j.location ? ` · 📍 ${esc(j.location)}` : ''}
      ${j.job_type ? ` · ${esc(j.job_type)}` : ''}
      ${j.deadline ? ` · ⏰ 截止 <b>${esc(j.deadline)}</b>${deadlineHint(j.deadline, j.company_scale)}` : ' · ⏰ 截止 <span class=\"muted\">未标注</span>'}
      ${j.url ? ` · <a href="${esc(j.url)}" target="_blank">原岗位链接↗</a>` : ''}
    </div>
    <div class="jc-tags">
      <span class="tag">${esc(j.direction || '未分类')}</span>
      <span class="tag">${STATUS_LABELS[j.status] || j.status}</span>
      ${scaleTag(j.company_scale, j.id)}
      ${strategyTag(j.company_scale)}
      ${fallRecruitTag(j.fall_recruit)}
      ${j.jd_text ? '<span class="tag">有 JD</span>' : ''}
    </div>
    ${matchHtml}
    <div class="jc-actions">
      <button class="btn sm primary" onclick="analyzeJob(${j.id})">JD 分析 + 匹配度</button>
      <button class="btn sm" onclick="shortlistJob(${j.id})">★ 加入精投</button>
      <button class="btn sm" onclick="setScale(${j.id}, '${esc(j.company_scale || '')}')">🏷 标记规模</button>
      <button class="btn sm" onclick="setFallRecruit(${j.id}, '${esc(j.fall_recruit || '')}')">🍂 秋招状态</button>
      <button class="btn sm" onclick="setDeadline(${j.id}, '${esc(j.deadline || '')}')">⏰ 截止</button>
      <button class="btn sm" onclick="editJobJD(${j.id})">✎ 编辑 JD</button>
      <button class="btn sm" onclick="editJob(${j.id})">📝 编辑岗位</button>
      <button class="btn sm" onclick="showJobDetail(${j.id})">详情 / JD</button>
      <button class="btn sm danger" onclick="delJob(${j.id})">删除</button>
    </div></div>`;
}

window.analyzeJob = async id => {
  toast('分析中…（大模型理解 JD + 检索资料库证据）');
  try {
    const r = await api(`/api/jobs/${id}/analyze`, { method: 'POST' });
    toast(`匹配度 ${r.score}（${r.grade}）${r.llm ? ' · LLM' : ' · 规则版'}：命中 ${r.matched_count} 项 / 缺口 ${r.gap_count} 项`);
    renderJobs();
  }
  catch (e) { toast(e.message, true); }
};
window.shortlistJob = async id => {
  try { await api(`/api/jobs/${id}/shortlist`, { method: 'POST' }); toast('已加入精投中心'); renderJobs(); }
  catch (e) { toast(e.data?.msg || e.message, true); }
};
window.delJob = async id => {
  if (!confirm('确认删除该岗位？此操作会一并删除它的投递记录、定制简历版本与打招呼语。')) return;
  try {
    await api(`/api/jobs/${id}`, { method: 'DELETE' });
    toast('已删除岗位'); renderJobs();
    if (typeof renderPrecision === 'function') renderPrecision();
  } catch (e) { toast(e.data?.detail || e.message || '删除失败', true); }
};
window.showJobDetail = async id => {
  const j = await api(`/api/jobs/${id}`);
  openModal(`<h2>${esc(j.company)} · ${esc(j.title)}</h2>
    <div class="muted" style="margin-bottom:10px">${esc(j.location || '')} · ${esc(j.source)} · 发现于 ${esc(j.discovered_date || '')}</div>
    <div class="field"><label>JD 原文</label><textarea class="input" rows="14" readonly>${esc(j.jd_text || '（未录入 JD）')}</textarea></div>
    ${j.match_analysis ? `<div class="checklist">${(j.match_analysis.matched || []).map(m => `<div class="ck-done">✔ ${esc(m.dimension)}</div>`).join('')}${(j.match_analysis.gaps || []).map(g => `<div class="ck-todo">✘ ${esc(g.dimension)}（缺口）</div>`).join('')}</div>` : ''}
    <div class="m-actions"><button class="btn" onclick="closeModal()">关闭</button></div>`);
};
window.editJobJD = async id => {
  const j = await api(`/api/jobs/${id}`);
  openModal(`<h2>✎ 编辑 JD · ${esc(j.company)}</h2>
    <div class="field"><label>JD 原文</label><textarea class="input" id="ej-jd" rows="16">${esc(j.jd_text || '')}</textarea></div>
    <div class="m-actions">
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="ej-save">保存 JD</button>
      <button class="btn" id="ej-reanalyze">保存并重新分析（LLM）</button>
    </div>`);
  $('#ej-save').onclick = async () => {
    await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ jd_text: $('#ej-jd').value }) });
    closeModal(); toast('JD 已保存'); renderJobs();
  };
  $('#ej-reanalyze').onclick = async () => {
    await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ jd_text: $('#ej-jd').value }) });
    closeModal(); toast('JD 已保存，正在用 LLM 重新分析…');
    try { const r = await api(`/api/jobs/${id}/analyze`, { method: 'POST', body: JSON.stringify({ use_llm: true }) }); toast(`LLM 匹配度 ${r.score}（${r.grade}）${r.llm ? '' : '（已回退规则版）'}`); }
    catch (e) { toast('分析失败：' + e.message, true); }
    renderJobs();
  };
};

window.editJob = async id => {
  const j = await api(`/api/jobs/${id}`);
  openModal(`<h2>📝 编辑岗位 · ${esc(j.company)}</h2>
    <p class="muted">手动添加的所有字段均可二次修改，保存后即时生效。</p>
    <div class="grid-2">
      <div class="field"><label>公司 *</label><input class="input" id="ej-company" value="${esc(j.company || '')}"></div>
      <div class="field"><label>岗位名称 *</label><input class="input" id="ej-title" value="${esc(j.title || '')}"></div>
      <div class="field"><label>地点</label><input class="input" id="ej-location" value="${esc(j.location || '')}"></div>
      <div class="field"><label>岗位类型</label><select class="input" id="ej-job-type"><option value="">未填</option><option ${j.job_type === '校招' ? 'selected' : ''}>校招</option><option ${j.job_type === '社招' ? 'selected' : ''}>社招</option><option ${j.job_type === '实习' ? 'selected' : ''}>实习</option></select></div>
      <div class="field"><label>来源</label><input class="input" id="ej-source" value="${esc(j.source || '')}"></div>
      <div class="field"><label>来源站 URL</label><input class="input" id="ej-source-url" value="${esc(j.source_url || '')}"></div>
      <div class="field"><label>岗位 URL</label><input class="input" id="ej-url" value="${esc(j.url || '')}"></div>
      <div class="field"><label>截止日期</label><input class="input" id="ej-deadline" type="date" value="${esc(j.deadline || '')}"></div>
    </div>
    <div class="field"><label>备注</label><input class="input" id="ej-notes" value="${esc(j.notes || '')}"></div>
    <div class="field"><label>JD 原文</label><textarea class="input" id="ej-jd2" rows="12">${esc(j.jd_text || '')}</textarea></div>
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="ej2-save">保存修改</button></div>`);
  $('#ej2-save').onclick = async () => {
    const patch = {
      company: $('#ej-company').value.trim(), title: $('#ej-title').value.trim(),
      location: $('#ej-location').value.trim(), job_type: $('#ej-job-type').value,
      source: $('#ej-source').value.trim(), source_url: $('#ej-source-url').value.trim(),
      url: $('#ej-url').value.trim(), deadline: $('#ej-deadline').value,
      notes: $('#ej-notes').value.trim(), jd_text: $('#ej-jd2').value,
    };
    if (!patch.company || !patch.title) { toast('公司 / 岗位名称 必填', true); return; }
    try {
      await api(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(patch) });
      closeModal(); toast('岗位信息已更新'); renderJobs();
    } catch (e) { toast(e.data?.detail || e.message, true); }
  };
};

$('#btn-add-job').onclick = () => {
  openModal(`<h2>添加岗位 / 粘贴 JD</h2>
    <div class="grid-2">
      <div class="field"><label>公司 *</label><input class="input" id="nj-company"></div>
      <div class="field"><label>岗位名称 *</label><input class="input" id="nj-title"></div>
      <div class="field"><label>地点</label><input class="input" id="nj-location"></div>
      <div class="field"><label>来源</label><input class="input" id="nj-source" value="手动添加"></div>
      <div class="field"><label>来源站 URL（可追溯）</label><input class="input" id="nj-source-url" placeholder="如 https://www.gdrc.com/"></div>
      <div class="field"><label>岗位类型</label><select class="input" id="nj-job-type"><option value="">未填</option><option>校招</option><option>社招</option><option>实习</option></select></div>
      <div class="field"><label>岗位 URL</label><input class="input" id="nj-url"></div>
      <div class="field"><label>截止日期</label><input class="input" id="nj-deadline" type="date"></div>
    </div>
    <div class="field"><label>JD 原文（粘贴完整 JD，分析越准）</label><textarea class="input" id="nj-jd" rows="12"></textarea></div>
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button>
    <button class="btn primary" id="nj-save">保存（自动去重）</button></div>`);
  $('#nj-save').onclick = async () => {
    try {
      const r = await api('/api/jobs', { method: 'POST', body: JSON.stringify({
        company: $('#nj-company').value, title: $('#nj-title').value, location: $('#nj-location').value,
        url: $('#nj-url').value, source: $('#nj-source').value, source_url: $('#nj-source-url').value,
        job_type: $('#nj-job-type').value, jd_text: $('#nj-jd').value,
        deadline: $('#nj-deadline').value }) });
      if (r.error === 'duplicate') { toast('岗位已存在（自动去重拦截）：' + r.msg, true); return; }
      toast('岗位已入库，可运行 JD 分析'); closeModal(); renderJobs();
    } catch (e) { toast(e.data?.msg || e.message, true); }
  };
};
$('#job-search').oninput = debounce(renderJobs, 300);
$('#job-dir-filter').onchange = renderJobs;
// 「仅看 UI/UX」硬过滤：切到 UI/UX 方向并高亮；再次点击取消
$('#btn-uiux-only').onclick = () => {
  const sel = $('#job-dir-filter');
  if (![...sel.options].some(o => o.value === 'UI/UX')) {
    const o = document.createElement('option'); o.value = 'UI/UX'; o.textContent = 'UI/UX'; sel.appendChild(o);
  }
  if (sel.value === 'UI/UX') { sel.value = ''; $('#btn-uiux-only').classList.remove('primary'); }
  else { sel.value = 'UI/UX'; $('#btn-uiux-only').classList.add('primary'); }
  renderJobs();
};
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

/* ---------- 批量粘贴 JD（一次多条，用 --- 分隔）---------- */
$('#btn-bulk-job').onclick = () => {
  openModal(`<h2>📋 批量粘贴 JD（一次多条）</h2>
    <div class="muted" style="margin-bottom:8px">
      把多个岗位的 JD 一次性粘贴进来，<b>每条之间用单独一行的 <code>---</code> 分隔</b>。
      系统会自动拆分成多个岗位，并<b>逐条自动分析评分</b>。<br>
      建议每条格式（第一行写岗位和公司，方便识别）：<br>
      <code>UI 设计师-公司名-招聘网站</code><br>
      <code>薪资：10K-15K/月</code><br>
      <code>工作地点：深圳</code><br>
      <code>截止：2026-09-30</code><br>
      （然后是 JD 正文）
    </div>
    <div class="field"><textarea class="input" id="bulk-text" rows="16" placeholder="岗位1-公司A-来源&#10;薪资：8K-12K/月&#10;工作地点：深圳&#10;截止：2026-09-30&#10;岗位职责...&#10;&#10;---&#10;&#10;岗位2-公司B-来源&#10;薪资：...&#10;岗位职责..."></textarea></div>
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button>
    <button class="btn primary" id="bulk-save">批量入库并分析</button></div>`);
  $('#bulk-save').onclick = async () => {
    const text = $('#bulk-text').value;
    if (!text.trim()) { toast('请先粘贴内容', true); return; }
    $('#bulk-save').disabled = true;
    $('#bulk-save').textContent = '正在入库并分析…';
    try {
      const r = await api('/api/jobs/bulk', { method: 'POST', body: JSON.stringify({ text }) });
      closeModal();
      toast(`完成：新增 ${r.created} 个${r.filtered_non_uiux ? `，非 UI/UX 过滤 ${r.filtered_non_uiux} 个` : ''}${r.skipped_duplicate ? `，重复跳过 ${r.skipped_duplicate} 个` : ''}`);
      renderJobs();
    } catch (e) {
      toast(e.data?.detail || e.message, true);
      $('#bulk-save').disabled = false;
      $('#bulk-save').textContent = '批量入库并分析';
    }
  };
};

/* ---------- 生成投递排期 ---------- */
$('#btn-build-plan').onclick = async () => {
  if (!confirm('生成投递排期？\n\n会把未投递的 UI/UX 岗位分配到工作日（每天 8 个）：\n· 前 7 天练手期：优先投 0-99 / 100-499，跳过 500+\n· 之后：按截止紧急度 + 匹配度 + 薪资综合排序\n· 已标记「准备好面试」时自动排除 0-99 小厂\n\n已排期的岗位会重新分配。')) return;
  try {
    const r = await api('/api/plan/build', { method: 'POST' });
    toast(`排期完成：${r.total} 个岗位 / ${r.days} 天`);
    renderJobs();
  } catch (e) { toast(e.data?.detail || e.message, true); }
};

/* ================= 精投中心 ================= */
async function renderPrecision() {
  const apps = await api('/api/applications');
  const active = apps.filter(a => ['Shortlisted', 'Tailoring', 'Ready to Apply'].includes(a.status));
  window._apps = {};
  apps.forEach(a => { window._apps[a.id] = a; });
  $('#precision-list').innerHTML = active.length ? active.map(precisionCard).join('') : '<div class="empty">精投中心为空：在岗位池点「★ 加入精投」</div>';
}

function precisionCard(a) {
  const ma = a.match_analysis || {};
  const hasGreeting = !!a.greeting_id;
  const approved = !!a.resume_approved;
  const checklist = [
    [!!a.j_company, 'JD'], [!!ma.matched, '匹配分析'],
    [!!a.resume_version_id, '简历版本'], [a.diff_status === 'pass', 'Diff 通过'],
    [approved, '✓ 已满意'], [hasGreeting, '打招呼语(可选)'], [a.status === 'Ready to Apply', 'Ready'],
  ];
  const canReady = approved && a.status !== 'Ready to Apply';
  const satBtn = (a.resume_version_id && a.diff_status === 'pass')
    ? (approved
        ? `<button class="btn sm" onclick="approveResume(${a.id}, ${a.resume_version_id}, false)">✗ 不满意</button>`
        : `<button class="btn sm success" onclick="approveResume(${a.id}, ${a.resume_version_id}, true)">✓ 满意此简历</button>`)
    : '';
  return `<div class="job-card">
    <div class="row1"><div><span class="jc-company">${esc(a.j_company)}</span> <span class="jc-title">${esc(a.j_title)}</span>
      <span class="badge b-${a.grade}" style="margin-left:6px">${a.grade} ${a.fit_score}</span></div>
      <span class="${stCls(a.status)}">${STATUS_LABELS[a.status]}</span></div>
    <div class="jc-meta">
      ${a.j_location ? `📍 ${esc(a.j_location)}` : '<span class="muted">地点未标注</span>'}
      · ${scaleTag(a.company_scale, a.job_id)}
      ${a.j_source_url ? ` · 🌐 来源：<a href="${esc(a.j_source_url)}" target="_blank" title="${esc(a.j_source_url)}">${esc(hostOf(a.j_source_url))}</a>${a.j_source ? ' <span class="muted">· 来自 ' + esc(a.j_source) + '</span>' : ''}` : ''}
      ${a.j_url ? ` · <a href="${esc(a.j_url)}" target="_blank">原岗位链接↗</a>` : ''}
    </div>
    <div class="checklist">${checklist.map(([ok, l]) => `<div class="${ok ? 'ck-done' : 'ck-todo'}">${ok ? '✔' : '○'} ${l}</div>`).join('')}</div>
    ${ma.matched ? `<ul class="match-list">${ma.matched.slice(0, 3).map(m =>
      `<li>✔ <b>${m.dimension}</b><div class="ev">${m.evidence.map(e => `<b>${esc(e.title)}</b>`).join(' · ')}</div></li>`).join('')}</ul>` : ''}
    <div class="jc-actions">
      <button class="btn sm primary" onclick="openResumeVersion(${a.job_id}, ${a.id})">✎ 定制简历（自我评价+亮点）</button>
      <button class="btn sm" onclick="genGreetings(${a.job_id})">打招呼语</button>
      ${satBtn}
      <button class="btn sm ${approved ? 'primary' : ''}" onclick="setReady(${a.id})" ${canReady ? '' : 'disabled title="请先点「✓ 满意此简历」确认后再准备投递"'} ${a.status === 'Ready to Apply' ? 'disabled' : ''}>准备投递</button>
      ${a.status === 'Ready to Apply' ? `<button class="btn sm primary" onclick="markApplied(${a.id})">✓ 已人工投递</button>` : ''}
      ${a.rv_pdf && a.diff_status === 'pass' ? `<a class="btn sm" href="/api/resume/versions/${a.resume_version_id}/pdf" target="_blank">📄 简历 PDF</a>` : ''}
      <button class="btn sm danger" onclick="returnToPool(${a.id})">↩ 返回岗位池</button>
      <button class="btn sm danger" onclick="delJob(${a.job_id})">🗑 删除岗位</button>
    </div>
    ${a.resume_version_id && a.diff_status === 'pass' && !approved ? '<div class="muted" style="margin-top:6px">👉 简历已生成并校验通过，点「✓ 满意此简历」即可解锁「准备投递」。</div>' : ''}
    ${a.diff_status === 'fail' ? '<div class="bad" style="margin-top:6px">⚠ Diff 未通过：非白名单区域被改动，此版本已锁定，请重新生成。</div>' : ''}
  </div>`;
}

window.setReady = async id => {
  try {
    const r = await api(`/api/applications/${id}/status`, { method: 'POST', body: JSON.stringify({ status: 'Ready to Apply' }) });
    let msg = '✓ READY TO APPLY — 材料齐备，请人工投递';
    if (r.exported_to) {
      const d = new Date();
      const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      msg += `；简历已下载到 桌面/秋招简历/${day}`;
    }
    toast(msg);
    renderPrecision();
    renderToday();
  }
  catch (e) {
    const map = { jd: 'JD', fit_analysis: '匹配分析', resume: '简历版本', approved: '简历未确认满意' };
    const miss = (e.data?.missing || []).map(k => map[k] || k);
    toast('还不能投递：' + miss.join('、'), true);
  }
};
window.returnToPool = async id => {
  if (!confirm('退回岗位池？该岗位将从精投中心移除，回到岗位池（已生成的简历版本保留）。')) return;
  try {
    await api(`/api/applications/${id}/return-to-pool`, { method: 'POST' });
    toast('已退回岗位池'); renderPrecision(); renderJobs();
  } catch (e) { toast(e.data?.detail || e.message, true); }
};
window.markApplied = async id => {
  const d = prompt('投递日期（YYYY-MM-DD，留空=今天）', '') || '';
  await api(`/api/applications/${id}/status`, { method: 'POST', body: JSON.stringify({ status: 'Applied', applied_date: d || new Date().toISOString().slice(0, 10) }) });
  toast('已记录投递'); renderPrecision(); renderToday();
};
window.genGreetings = async jobId => {
  try {
    const r = await api(`/api/greetings/${jobId}/generate`, { method: 'POST' });
    if (r.error) { toast(r.error, true); return; }
    renderGreetingsModal(jobId, r);
    renderPrecision();
  } catch (e) { toast(e.message || (e.data && e.data.detail) || e, true); }
};

/* 复制单条打招呼语（带兜底 + 反馈） */
window.copyGreet = async (k) => {
  const el = document.getElementById('greet-' + k);
  const btn = document.getElementById('copy-' + k);
  if (!el) return;
  const text = el.innerText;
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text); ok = true;
    } else {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      ok = document.execCommand('copy'); document.body.removeChild(ta);
    }
  } catch (e) { ok = false; }
  if (btn) {
    const old = btn.innerHTML;
    btn.innerHTML = ok ? '✓ 已复制' : '⚠ 复制失败';
    btn.style.color = ok ? 'var(--ok, #2a9d4a)' : 'var(--bad, #d9534f)';
    setTimeout(() => { btn.innerHTML = old; btn.style.color = ''; }, 1400);
  }
  if (ok) toast('已复制到剪贴板'); else toast('复制失败，请手动选择文本复制', true);
};

window.renderGreetingsModal = (jobId, r) => {
  const titles = { platform_short: '招聘平台短消息', hr_chat: 'HR 私聊版', apply_note: '投递备注', portfolio_intro: '作品集版' };
  const greets = r.greetings || {};
  openModal(`<h2>打招呼语</h2>
    <div class="muted" style="margin-bottom:8px">已结合岗位 JD + 你的真实简历生成（大模型）。不满意点 🔄 换一版（每次都会存为新版本）。点 📋 可直接复制对应文案。</div>
    ${Object.entries(titles).map(([k, label]) => greets[k] ? `<div class="greet-box"><div class="greet-head"><h4>${label}</h4><button class="icon-btn" id="copy-${k}" onclick="copyGreet('${k}')" title="复制这段话">📋 复制</button></div><pre id="greet-${k}">${esc(greets[k])}</pre></div>` : '').join('')}
    <div class="muted">依据岗位 JD 与你的真实简历素材生成，未编造经历/数据。</div>
    <div class="m-actions">
      <button class="btn" onclick="closeModal()">关闭</button>
      <button class="btn primary" id="greet-refresh">🔄 重新生成</button>
    </div>`);
  const rf = document.getElementById('greet-refresh');
  if (rf) rf.onclick = async () => {
    rf.disabled = true; rf.textContent = '生成中…';
    try {
      const r2 = await api(`/api/greetings/${jobId}/generate`, { method: 'POST' });
      if (r2.error) { toast(r2.error, true); return; }
      renderGreetingsModal(jobId, r2);
      toast('已重新生成一版');
    } catch (e) { toast(e.message, true); }
    finally { rf.disabled = false; rf.textContent = '🔄 重新生成'; }
  };
};

/* ---------- 定制简历（核心：只改两个区域） ---------- */
window.openResumeVersion = async (jobId, appId) => {
  window._rvAppId = appId;
  const a = (window._apps || {})[appId] || {};
  const master = await api('/api/resume/master');
  if (!master.id) { toast('请先在简历中心导入 Master Resume', true); return; }
  let evalItems = (master.self_eval_text || []).map(it => ({ cat: it.cat, txt: it.text }));
  let hlItems = (master.highlights_text || []).map(it => ({ cat: it.cat, lines: it.lines }));
  if (a.resume_version_id) {
    try {
      const rv = await api(`/api/resume/versions/${a.resume_version_id}`);
      if (rv.new_self_eval) evalItems = rv.new_self_eval.map(it => ({ cat: it.cat, txt: it.txt }));
      if (rv.new_highlights) hlItems = rv.new_highlights.map(it => ({ cat: it.cat, lines: it.lines }));
    } catch (e) { /* 用 master 原文 */ }
  }
  openModal(`<h2>定制简历 · ${esc(a.j_company)} · ${esc(a.j_title)}</h2>
    <div class="muted" style="margin-bottom:12px">⚠ 只允许修改下方「自我评价」与「个人亮点」。保存后系统自动做程序级 Diff（白名单外任何字节差异 → FAIL 回滚）并渲染 PDF。修改文案请只写资料库中有的真实经历。<b>加粗</b>：用 &lt;b&gt;关键词&lt;/b&gt; 包裹，导出 PDF 即加粗显示（🤖 一键生成已自动为重点词加粗，不满意可手动改）。</div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
      <button class="btn sm" id="rv-gen">🤖 按 JD 一键生成（LLM 自动改写）</button>
      <button class="btn sm" id="rv-refresh" title="不满意？点此让大模型重新生成（可多次）">🔄 重新生成</button>
      <span id="rv-gen-status" class="muted" style="font-size:12px"></span>
    </div>
    <h3>① 自我评价</h3><div id="rv-evals"></div>
    <button class="btn sm" onclick="rvAddEval()">+ 添加一条</button>
    <h3 style="margin-top:14px">② 个人亮点</h3><div id="rv-brings"></div>
    <button class="btn sm" onclick="rvAddBring()">+ 添加一项</button>
    <div class="field" style="margin-top:14px"><label>修改原因（记录用）</label><input class="input" id="rv-reason" placeholder="如：强调 B 端数据产品经验以匹配 JD 第 2 条要求"></div>
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button>
    <button class="btn primary" id="rv-save">生成版本 + Diff + PDF</button></div>`);
  const renderEvals = () => $('#rv-evals').innerHTML = evalItems.map((it, i) => `
    <div class="field"><label>条目 ${i + 1} 小标题</label>
    <div class="form-row"><input class="input" value="${esc(it.cat)}" oninput="evalItems[${i}].cat=this.value">
    <button class="btn sm danger" onclick="evalItems.splice(${i},1);renderEvals()">删除</button></div>
    <textarea class="input" rows="3" style="margin-top:6px" oninput="evalItems[${i}].txt=this.value" placeholder="可使用 <b>加粗</b>">${esc(it.txt)}</textarea></div>`).join('');
  const renderBrings = () => $('#rv-brings').innerHTML = hlItems.map((it, i) => `
    <div class="field"><label>亮点 ${i + 1}</label>
    <div class="form-row"><input class="input" value="${esc(it.cat)}" oninput="hlItems[${i}].cat=this.value">
    <button class="btn sm danger" onclick="hlItems.splice(${i},1);renderBrings()">删除</button></div>
    <textarea class="input" rows="2" style="margin-top:6px" oninput="hlItems[${i}].lines=this.value.split('\\n')" placeholder="每行一个短句">${esc((it.lines || []).join('\n'))}</textarea></div>`).join('');
  window.evalItems = evalItems; window.hlItems = hlItems;
  window.renderEvals = renderEvals; window.renderBrings = renderBrings;
  window.rvAddEval = () => { evalItems.push({ cat: '', txt: '' }); renderEvals(); };
  window.rvAddBring = () => { hlItems.push({ cat: '', lines: [''] }); renderBrings(); };
  window.rvDoGenerate = async () => {
    const st = document.getElementById('rv-gen-status');
    if (st) { st.textContent = '大模型生成中…'; st.style.color = '#888'; }
    try {
      const d = await api(`/api/jobs/${jobId}/resume/generate-draft`, { method: 'POST' });
      if (d.error) { toast(d.error, true); if (st) st.textContent = ''; return; }
      evalItems = d.self_eval_items.map(it => ({ cat: it.cat, txt: it.txt }));
      hlItems = d.highlight_items.map(it => ({ cat: it.cat, lines: it.lines }));
      window.evalItems = evalItems; window.hlItems = hlItems;
      renderEvals(); renderBrings();
      let msg = '已生成，请核对后点「生成版本 + Diff + PDF」';
      if (d.length_warnings && d.length_warnings.length)
        msg += `（已自动将 ${d.length_warnings.length} 处压缩到与原文相近字数）`;
      toast(msg);
      if (st) { st.textContent = '✓ 已重新生成，可再次点 🔄 换一版'; st.style.color = 'var(--ok, #2a9d4a)'; }
    } catch (e) { toast(e.data?.detail || e.message, true); if (st) st.textContent = ''; }
  };
  $('#rv-gen').onclick = async () => {
    if (!confirm('按该岗位 JD 调用 LLM 自动生成定制文案？\n\n将基于你的真实简历，改写「自我评价 / 个人亮点」以贴合 JD；生成后请核对再点「生成版本 + Diff + PDF」。')) return;
    await window.rvDoGenerate();
  };
  $('#rv-refresh').onclick = async () => { await window.rvDoGenerate(); };
  renderEvals(); renderBrings();
  $('#rv-save').onclick = async () => {
    toast('生成中：复制 Master 环境 → 替换两个区域 → Diff → Chrome 渲染 PDF → 验证…');
    try {
      const r = await api(`/api/jobs/${jobId}/resume-version`, { method: 'POST', body: JSON.stringify({
        self_eval_items: evalItems.filter(x => x.cat && x.txt), highlight_items: hlItems.filter(x => x.cat && x.lines.some(l => l.trim())),
        reason: $('#rv-reason').value }) });
      if (r.error) { toast(r.error, true); return; }
      showVersionResult(r, appId);
    } catch (e) { toast(e.message, true); }
  };
};

window.showVersionResult = (r, appId) => {
  const pc = r.pdf_check || {};
  const diff = r.diff || {};
  const diffStatus = r.diff_status || 'fail';
  const ok = diffStatus === 'pass';
  const aid = appId != null ? appId : window._rvAppId;
  openModal(`<h2>${ok ? '✅ 版本生成成功' : '❌ 验证未通过，版本已锁定'} <span class="badge ${ok ? 'b-S' : 'b-D'}">${diffStatus.toUpperCase()}</span></h2>
    <div class="card" style="margin:0 0 12px">
      <div class="grid-2" style="gap:8px">
        <div>结构 Diff：<b class="${diff.structure_ok ? 'ok' : 'bad'}">${diff.structure_ok ? 'PASS（结构一致）' : 'FAIL'}</b></div>
        <div>内容 Diff：<b class="${diff.content_ok ? 'ok' : 'bad'}">${diff.content_ok ? 'PASS（文字一致）' : 'FAIL'}</b></div>
        <div>PDF 渲染：<b class="${pc.checks?.pdf_exists ? 'ok' : 'bad'}">${pc.checks?.pdf_exists ? 'OK' : '失败'}</b></div>
        <div>页数一致：<b>${pc.checks?.pages_equal ? 'OK' : (JSON.stringify(pc.checks?.pages || ''))}</b></div>
        <div>非目标区域文本一致：<b class="${pc.checks?.text_outside_regions_equal ? 'ok' : 'bad'}">${pc.checks?.text_outside_regions_equal ? 'OK' : 'FAIL'}</b></div>
        ${(() => {
          const lay = pc.layout || {}; const ov = lay.overflowPx;
          if (!lay.ok || ov == null) return '<div>溢出检测（measure.js）：<b>未捕获</b></div>';
          if (ov <= 0) return `<div>溢出检测（measure.js）：<b class="ok">未溢出（余 ${-ov}px）</b></div>`;
          if (ov <= 8) return `<div>溢出检测（measure.js）：<b style="color:#d98c00">满版边缘·溢出 ${ov}px（余量紧张，已放行）</b></div>`;
          return `<div>溢出检测（measure.js）：<b class="bad">溢出 ${ov}px（末行被裁切）</b></div>`;
        })()}
        <div>孤行检查：<b>${pc.layout?.orphans?.length ? pc.layout.orphans.length + ' 处提示' : '无'}</b></div>
      </div>
      ${(r.diff.structural_changes?.length || r.diff.content_changes?.length) ? `<pre class="mono" style="margin-top:8px;background:var(--red-soft);padding:8px;border-radius:6px;max-height:160px;overflow:auto">${esc(JSON.stringify({structural: r.diff.structural_changes, content: r.diff.content_changes}, null, 1))}</pre>` : ''}
    </div>
    ${ok ? `<div class="greet-box"><h4>说明</h4><pre>Master 与 Tailored 除「自我评价 / 个人亮点」外逐字节一致；PDF 页数、页边距、图片与二维码渲染流程与原版完全相同（Chrome headless --no-pdf-header-footer）。可点击下方 PDF 核对后，回到精投中心「准备投递」。</pre></div>` : ''}
    <div class="m-actions">
      ${ok && r.pdf_path ? `<a class="btn primary" href="/api/resume/versions/${r.resume_version_id}/pdf" target="_blank">📄 查看 PDF</a>` : ''}
      ${ok ? `<button class="btn success" onclick="approveResume(${aid}, ${r.resume_version_id}, true)">✓ 满意，采用并准备投递</button>` : ''}
      ${ok ? `<button class="btn" onclick="closeModal()">不满意，稍后调整</button>` : `<button class="btn" onclick="closeModal()">关闭</button>`}
    </div>`);
  renderPrecision();
};

window.approveResume = async (appId, rvId, approved) => {
  try {
    const r = await api(`/api/applications/${appId}/approve-resume`, {
      method: 'POST',
      body: JSON.stringify({ approved, resume_version_id: rvId || null }) });
    if (approved) toast('✓ 已满意：已加入「今日投递」，回到精投中心点「准备投递」可下载简历');
    else toast('已取消满意标记，并移出今日投递');
    closeModal();
    renderPrecision();
    renderToday();
  } catch (e) { toast(e.data?.detail || e.message || '操作失败', true); }
};

/* ================= 投递管理 ================= */
async function renderApplications() {
  const apps = await api('/api/applications');
  const opts = ['New','Shortlisted','Tailoring','Ready to Apply','Applied','Online Assessment','Interview','Offer','Rejected','Withdrawn','Closed'];
  $('#apps-list').innerHTML = apps.length ? `<div class="card"><table>
    <thead><tr><th>公司 · 岗位</th><th>匹配</th><th>状态</th><th>投递日</th><th>下一步</th></tr></thead><tbody>` +
    apps.map(a => `<tr>
      <td><b>${esc(a.j_company)}</b> · ${esc(a.j_title)}<div class="muted">${a.url ? `<a href="${esc(a.url)}" target="_blank">岗位链接↗</a> · ` : ''}方向 ${esc(a.direction || '—')}</div></td>
      <td>${a.grade ? `<span class="badge b-${a.grade}">${a.grade} ${a.fit_score}</span>` : '—'}</td>
      <td><span class="${stCls(a.status)}">${STATUS_LABELS[a.status]}</span>
        <select class="input sel" style="margin-top:4px;font-size:11px" onchange="updateAppStatus(${a.id}, this.value)">
          ${opts.map(o => `<option value="${o}" ${o === a.status ? 'selected' : ''}>${STATUS_LABELS[o] || o}</option>`).join('')}</select></td>
      <td>${esc(a.applied_date || '—')}</td>
      <td class="muted">${esc(a.next_step || '—')}</td></tr>`).join('') + '</tbody></table></div>'
    : '<div class="empty">暂无投递记录</div>';
}

/* ================= 今日投递 ================= */
async function renderToday() {
  const apps = await api('/api/applications/today');
  $('#today-list').innerHTML = apps.length ? apps.map(todayCard).join('')
    : '<div class="empty">今天还没有准备投递的岗位。在精投中心点「✓ 满意此简历」→「准备投递」，满意简历会自动下载到桌面，并归集到这里。</div>';
}
function todayCard(a) {
  return `<div class="job-card">
    <div class="row1"><div><span class="jc-company">${esc(a.j_company)}</span> <span class="jc-title">${esc(a.j_title)}</span>
      <span class="badge b-${a.grade}" style="margin-left:6px">${a.grade} ${a.fit_score}</span></div>
      <span class="${stCls(a.status)}">${STATUS_LABELS[a.status]}</span></div>
    <div class="jc-meta">
      ${a.j_location ? `📍 ${esc(a.j_location)}` : ''}
      ${a.j_url ? ` · <a href="${esc(a.j_url)}" target="_blank">原岗位链接↗</a>` : ''}
      ${a.ready_at ? ` · 🕒 准备于 ${esc(a.ready_at)}` : ''}
    </div>
    <div class="jc-actions">
      ${a.rv_pdf && a.diff_status === 'pass' ? `<a class="btn sm" href="/api/resume/versions/${a.resume_version_id}/pdf" target="_blank">📄 简历 PDF</a>` : ''}
      ${a.rv_pdf && a.diff_status === 'pass' ? `<button class="btn sm" onclick="exportDesktop(${a.resume_version_id})">⬇ 下载到桌面</button>` : ''}
      <button class="btn sm primary" onclick="markApplied(${a.id})">✓ 已人工投递</button>
    </div>
  </div>`;
}
window.updateAppStatus = async (id, status) => {
  try {
    const body = { status };
    if (status === 'Applied') body.applied_date = new Date().toISOString().slice(0, 10);
    await api(`/api/applications/${id}/status`, { method: 'POST', body: JSON.stringify(body) });
    toast('状态已更新'); renderApplications();
  } catch (e) { toast('更新失败：' + (e.data?.missing ? '材料不全 ' + e.data.missing.join('、') : e.message), true); renderApplications(); }
};

/* ================= 面试管理 ================= */
async function renderInterviews() {
  const list = await api('/api/interviews');
  $('#interviews-list').innerHTML = list.length ? `<div class="card"><table>
    <thead><tr><th>公司 · 岗位</th><th>轮次</th><th>时间</th><th>形式/面试官</th><th>表现</th><th>操作</th></tr></thead><tbody>` +
    list.map(i => `<tr>
      <td><b>${esc(i.company)}</b> · ${esc(i.position || i.j_title || '')}</td>
      <td><span class="badge b-B">${esc(i.round)}</span></td>
      <td>${esc(i.scheduled_at || '待定')}</td>
      <td class="muted">${esc(i.format || '—')} ${esc(i.interviewer || '')}</td>
      <td class="muted">${esc(i.performance || '—')}</td>
      <td><button class="btn sm" onclick="showInterviewDetail(${i.id})">详情</button>
      <button class="btn sm primary" onclick="openInterviewBrief(${i.id})">📋 面试准备</button></td></tr>`).join('') + '</tbody></table></div>'
    : '<div class="empty">暂无面试记录：投递后进入流程即可在此登记</div>';
}

window.showInterviewDetail = async id => {
  const list = await api('/api/interviews');
  const i = list.find(x => x.id === id);
  if (!i) return;
  const qs = await api(`/api/questions?company=${encodeURIComponent(i.company)}`).catch(() => []);
  openModal(`<h2>面试详情 · ${esc(i.company)} · ${esc(i.position || i.j_title || '')} <span class="badge b-B">${esc(i.round)}</span></h2>
    <div class="grid-2">
      <div class="field"><label>面试官</label><input class="input" id="ivd-who" value="${esc(i.interviewer || '')}"></div>
      <div class="field"><label>形式</label><input class="input" id="ivd-format" value="${esc(i.format || '')}"></div>
    </div>
    <div class="field"><label>面试表现</label><input class="input" id="ivd-perf" value="${esc(i.performance || '')}" placeholder="好 / 一般 / 待改进"></div>
    <div class="field"><label>面试官反馈</label><textarea class="input" id="ivd-feedback" rows="3">${esc(i.interviewer_feedback || '')}</textarea></div>
    <div class="field"><label>需要改进的地方</label><textarea class="input" id="ivd-improve" rows="3">${esc(i.improvements || '')}</textarea></div>
    <div class="field"><label>复盘</label><textarea class="input" id="ivd-review" rows="4">${esc(i.review || '')}</textarea></div>
    <div class="field"><label>下一步</label><input class="input" id="ivd-next" value="${esc(i.next_step || '')}"></div>
    <div class="card" style="margin:10px 0"><h3>历史问题库（${esc(i.company)}）</h3>
      ${qs.length ? qs.map(q => `<div style="padding:6px 0;border-bottom:1px solid var(--soft)"><b>${esc(q.question)}</b> <span class="tag">${esc(q.round)}</span> <span class="muted">${esc(q.result || '')}</span></div>`).join('') : '<div class="empty">暂无记录问题</div>'}
    </div>
    <div class="m-actions">
      <button class="btn" onclick="closeModal()">关闭</button>
      <button class="btn primary" onclick="saveInterviewDetail(${id})">保存</button></div>`);
};
window.saveInterviewDetail = async id => {
  try {
    await api(`/api/interviews/${id}`, { method: 'PATCH', body: JSON.stringify({
      interviewer: $('#ivd-who').value, format: $('#ivd-format').value,
      performance: $('#ivd-perf').value, interviewer_feedback: $('#ivd-feedback').value,
      improvements: $('#ivd-improve').value, review: $('#ivd-review').value,
      next_step: $('#ivd-next').value }) });
    toast('已保存'); closeModal(); renderInterviews();
  } catch (e) { toast(e.message, true); }
};

window.openInterviewBrief = async id => {
  toast('生成面试准备简报…');
  try {
    const b = await api(`/api/interviews/${id}/brief`);
    const job = b.job || {};
    openModal(`<h2>📋 面试准备简报 · ${esc(b.company)} · ${esc(b.position || job.title || '')}</h2>
      ${job.fit_score != null ? `<div class="muted" style="margin-bottom:8px">Fit Score <b>${job.fit_score}</b>（${job.grade}）· ${esc(job.title || '')}</div>` : ''}
      ${b.data_sufficiency === 'insufficient' ? '<div class="bad" style="margin-bottom:8px">⚠ 资料库证据较少，以下内容为可用部分，未编造。</div>' : ''}
      <div class="card" style="margin:8px 0"><h3>① 这个岗位最看重什么（JD 维度 + 你的真实证据）</h3>
        ${(b.strengths || []).length ? b.strengths.map(s => `<div style="padding:4px 0"><b>${esc(s.dimension)}</b>：${s.evidence.map(e => esc(e.title)).join(' · ') || '（无证据）'}</div>`).join('') : '<div class="empty">资料不足</div>'}
      </div>
      <div class="card" style="margin:8px 0"><h3>② 最应该讲的项目/经历（来自你的资料库）</h3>
        ${(b.related_materials || []).length ? b.related_materials.map(m => `<div style="padding:4px 0">▪ <b>${esc(m.title)}</b> <span class="tag">${esc(m.source_type)}</span></div>`).join('') : '<div class="empty">资料不足</div>'}
      </div>
      <div class="card" style="margin:8px 0"><h3>③ 可能被问的问题</h3>
        ${(b.possible_questions || []).length ? b.possible_questions.map(q => `<div style="padding:4px 0">• ${esc(q.question)} ${q.prev ? '<span class="tag">历史真实问题</span>' : `<span class="tag">${esc(q.dimension)}</span>`}</div>`).join('') : '<div class="empty">资料不足</div>'}
      </div>
      <div class="card" style="margin:8px 0"><h3>④ 薄弱点（JD 要求但资料库无证据）</h3>
        ${(b.gaps || []).length ? b.gaps.map(g => `<div style="padding:4px 0">✘ ${esc(g.dimension)}：${esc(g.note || '资料不足')}</div>`).join('') : '<div class="ok">无明显缺口</div>'}
      </div>
      <div class="card" style="margin:8px 0"><h3>⑤ 面试前准备</h3>
        ${(b.prep_checklist || []).map(p => `<div style="padding:3px 0">☐ ${esc(p)}</div>`).join('')}
      </div>
      <div class="m-actions"><button class="btn" onclick="closeModal()">关闭</button></div>`);
  } catch (e) { toast(e.message, true); }
};
$('#btn-add-interview').onclick = async () => {
  const apps = await api('/api/applications');
  openModal(`<h2>记录面试</h2>
    <div class="field"><label>关联投递</label><select class="input" id="iv-app">
      <option value="">（不关联，手填公司）</option>
      ${apps.map(a => `<option value="${a.id}">${esc(a.j_company)} · ${esc(a.j_title)}（${STATUS_LABELS[a.status]}）</option>`).join('')}</select></div>
    <div class="grid-2">
      <div class="field"><label>公司（不关联时填写）</label><input class="input" id="iv-company"></div>
      <div class="field"><label>轮次 *</label><select class="input" id="iv-round">${['OA','HR Screen','一面','二面','三面','Final','HR','Offer'].map(r => `<option>${r}</option>`).join('')}</select></div>
      <div class="field"><label>时间</label><input class="input" id="iv-time" type="datetime-local"></div>
      <div class="field"><label>形式</label><select class="input" id="iv-format"><option>视频</option><option>现场</option><option>电话</option><option>笔试</option></select></div>
    </div>
    <div class="field"><label>面试官</label><input class="input" id="iv-who"></div>
    <div class="field"><label>面试问题（每行一个）</label><textarea class="input" id="iv-questions" rows="5"></textarea></div>
    <div class="field"><label>面试表现</label><input class="input" id="iv-perf" placeholder="好 / 一般 / 待改进"></div>
    <div class="field"><label>面试官反馈</label><textarea class="input" id="iv-feedback" rows="2"></textarea></div>
    <div class="field"><label>需要改进的地方</label><textarea class="input" id="iv-improve" rows="2"></textarea></div>
    <div class="field"><label>复盘</label><textarea class="input" id="iv-review" rows="3"></textarea></div>
    <div class="field"><label>下一步</label><input class="input" id="iv-next"></div>
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button>
    <button class="btn primary" id="iv-save">保存</button></div>`);
  $('#iv-save').onclick = async () => {
    try {
      const r = await api('/api/interviews', { method: 'POST', body: JSON.stringify({
        application_id: $('#iv-app').value ? +$('#iv-app').value : null,
        company: $('#iv-company').value, round: $('#iv-round').value,
        scheduled_at: $('#iv-time').value ? $('#iv-time').value.replace('T', ' ') : '',
        format: $('#iv-format').value, interviewer: $('#iv-who').value,
        questions: $('#iv-questions').value.split('\n').filter(Boolean),
        performance: $('#iv-perf').value, interviewer_feedback: $('#iv-feedback').value,
        improvements: $('#iv-improve').value, review: $('#iv-review').value,
        next_step: $('#iv-next').value }) });
      // 把每个问题也存入问题库（供以后复用 + 相似提示）
      const qs = $('#iv-questions').value.split('\n').filter(Boolean);
      for (const q of qs) {
        await api('/api/questions', { method: 'POST', body: JSON.stringify({
          interview_id: r.interview_id, company: $('#iv-company').value || '',
          position: '', round: $('#iv-round').value, question: q }) }).catch(() => {});
      }
      toast('面试已记录（问题已入问题库）'); closeModal(); renderInterviews();
    } catch (e) { toast(e.message, true); }
  };
};

/* ================= 岗位来源 ================= */
async function renderSources() {
  const cand = await api('/api/radar/candidates');
  $('#candidate-buckets').innerHTML = `<div class="stat-grid small" style="grid-template-columns:repeat(4,1fr)">
    <div class="stat hl"><div class="n">${cand.A?.length ?? 0}</div><div class="l">A 强匹配（优先精投）</div></div>
    <div class="stat"><div class="n">${cand.B?.length ?? 0}</div><div class="l">B 较匹配（考虑）</div></div>
    <div class="stat"><div class="n">${cand.C?.length ?? 0}</div><div class="l">C 弱匹配（不建议）</div></div>
    <div class="stat"><div class="n">${cand.D_filtered ?? 0}</div><div class="l">D 自动过滤</div></div>
  </div>
  ${(cand.A || []).slice(0, 8).map(j => `<div style="padding:6px 0;border-bottom:1px solid var(--soft)"><b>${esc(j.company)}</b> · ${esc(j.title)} <span class="badge b-${j.grade}" style="float:right">${j.grade} ${j.fit_score}</span> <button class="btn sm primary" style="float:right;margin-right:8px" onclick="shortlistJob(${j.id})">★ 精投</button></div>`).join('') || '<div class="empty">暂无 A 级岗位</div>'}`;

  const srcs = await api('/api/sources');
  $('#sources-list').innerHTML = srcs.length ? '<table><thead><tr><th>来源</th><th>类型</th><th>优先级</th><th>状态</th><th>URL</th><th>操作</th></tr></thead><tbody>' +
    srcs.map(s => `<tr>
      <td><b>${esc(s.source_name)}</b></td>
      <td><span class="tag">${esc(s.source_type)}</span></td>
      <td><input type="number" min="1" max="10" value="${s.priority}" class="input" style="width:60px" onchange="setSourceState(${s.id}, {priority: +this.value})"></td>
      <td><button class="btn sm ${s.enabled ? '' : 'danger'}" onclick="setSourceState(${s.id}, {enabled: ${s.enabled ? 0 : 1}})">${s.enabled ? '启用中' : '已暂停'}</button></td>
      <td class="muted mono" style="max-width:260px">${esc(s.url || '')}</td>
      <td><button class="btn sm danger" onclick="delSource(${s.id})">删除</button></td></tr>`).join('') + '</tbody></table>'
    : '<div class="empty">来源池为空：点「导入 Edge 收藏夹」或手动添加</div>';
}
$('#btn-import-edge').onclick = async () => {
  toast('正在读取 Edge 收藏夹…');
  const r = await api('/api/sources/import-edge', { method: 'POST' });
  if (r.error) { toast(r.error, true); return; }
  toast(`已导入 ${r.imported} 个招聘来源（跳过重复 ${r.skipped} 个，共扫描 ${r.total_bookmarks} 条收藏）`);
  renderSources();
};
$('#btn-add-source').onclick = () => {
  openModal(`<h2>添加岗位来源</h2>
    <div class="grid-2">
      <div class="field"><label>来源名称 *</label><input class="input" id="ns-name" placeholder="如 公司招聘官网"></div>
      <div class="field"><label>类型</label><select class="input" id="ns-type"><option value="platform">招聘平台</option><option value="official">公司官网</option><option value="manual">手动添加</option></select></div>
      <div class="field"><label>URL *</label><input class="input" id="ns-url" placeholder="https://…"></div>
      <div class="field"><label>优先级（1-10）</label><input class="input" id="ns-pri" type="number" min="1" max="10" value="5"></div>
    </div>
    <div class="m-actions"><button class="btn" onclick="closeModal()">取消</button>
    <button class="btn primary" id="ns-save">保存</button></div>`);
  $('#ns-save').onclick = async () => {
    try {
      await api('/api/sources', { method: 'POST', body: JSON.stringify({
        source_name: $('#ns-name').value, source_type: $('#ns-type').value,
        url: $('#ns-url').value, priority: +$('#ns-pri').value }) });
      toast('来源已添加'); closeModal(); renderSources();
    } catch (e) { toast(e.message, true); }
  };
};
window.setSourceState = async (id, patch) => {
  await api(`/api/sources/${id}`, { method: 'PATCH', body: JSON.stringify(patch) });
  toast('已更新'); renderSources();
};
window.delSource = async id => {
  if (!confirm('删除该来源？')) return;
  await api(`/api/sources/${id}`, { method: 'DELETE' }); renderSources();
};

/* ================= 数据分析 ================= */
async function renderAnalytics() {
  const a = await api('/api/analytics');

  // Funnel
  const layers = a.funnel || [];
  const maxCount = Math.max(1, ...layers.map(l => l.count));
  $('#funnel-chart').innerHTML = `<div class="funnel">
    ${layers.map((l, i) => `<div class="funnel-row">
      <div class="funnel-label">${esc(l.label)}</div>
      <div class="funnel-bar-wrap"><div class="funnel-bar" style="width:${Math.max(4, l.count / maxCount * 100)}%">${l.count}</div></div>
      ${l.rate_vs_prev != null ? `<div class="funnel-rate ${l.sufficient ? '' : 'muted'}">${l.rate_vs_prev}%${l.sufficient ? '' : ' <span class="badge b-B">样本不足</span>'}</div>` : '<div class="funnel-rate muted">—</div>'}
    </div>`).join('')}
  </div>`;

  // Fit Score 转化
  $('#fit-conv').innerHTML = '<table><thead><tr><th>Fit Score</th><th>投递</th><th>OA</th><th>面试</th><th>Offer</th><th>OA率</th><th>面试率</th></tr></thead><tbody>' +
    (a.fit_score_conversion || []).map(r => `<tr>
      <td><b>${esc(r.bucket)}</b></td><td>${r.applied}</td><td>${r.oa}</td><td>${r.interview}</td><td>${r.offer}</td>
      <td>${r.oa_rate != null ? r.oa_rate + '%' : '—'}</td>
      <td>${r.interview_rate != null ? r.interview_rate + '%' : '—'} ${r.sufficient ? '' : '<span class="badge b-B">样本不足</span>'}</td>
    </tr>`).join('') + '</tbody></table>';

  // 投递质量
  const q = a.quality_today || {};
  $('#quality-today').innerHTML = `<div class="stat-grid small" style="grid-template-columns:repeat(4,1fr)">
    <div class="stat hl"><div class="n">${q.total ?? 0}</div><div class="l">今日投递</div></div>
    <div class="stat hl"><div class="n">${q.S_A ?? 0}</div><div class="l">S/A 级</div></div>
    <div class="stat"><div class="n">${q.B ?? 0}</div><div class="l">B 级</div></div>
    <div class="stat"><div class="n">${q.C_D ?? 0}</div><div class="l">C/D 级</div></div>
  </div>
  <div class="muted">${q.quality_rate != null ? `今日高质量投递占比 <b>${q.quality_rate}%</b>（S/A 级）` : '今日暂无投递'}</div>`;

  // 方向分析
  $('#direction-analysis').innerHTML = (a.directions || []).length ? '<table><thead><tr><th>方向</th><th>岗位</th><th>精投</th><th>投递</th><th>OA</th><th>面试</th><th>Offer</th><th>投递→OA</th><th>OA→面试</th></tr></thead><tbody>' +
    a.directions.map(d => `<tr>
      <td><b>${esc(d.direction)}</b></td><td>${d.total}</td><td>${d.shortlisted}</td><td>${d.applied}</td>
      <td>${d.oa}</td><td>${d.interviewing}</td><td>${d.offer}</td>
      <td>${d.apply_to_oa_rate != null ? d.apply_to_oa_rate + '%' : '—'}</td>
      <td>${d.oa_to_interview_rate != null ? d.oa_to_interview_rate + '%' : '—'} ${d.sufficient ? '' : '<span class="badge b-B">样本不足</span>'}</td>
    </tr>`).join('') + '</tbody></table>' : '<div class="empty">暂无岗位数据</div>';

  // 来源分析
  $('#source-analysis').innerHTML = (a.sources || []).length ? '<table><thead><tr><th>来源</th><th>发现</th><th>精投</th><th>投递</th><th>OA</th><th>面试</th><th>Offer</th><th>面试率</th></tr></thead><tbody>' +
    a.sources.map(s => `<tr>
      <td><b>${esc(s.source)}</b></td><td>${s.total}</td><td>${s.shortlisted}</td><td>${s.applied}</td>
      <td>${s.oa}</td><td>${s.interviewing}</td><td>${s.offer}</td>
      <td>${s.interview_rate != null ? s.interview_rate + '%' : '—'} ${s.sufficient ? '' : '<span class="badge b-B">样本不足</span>'}</td>
    </tr>`).join('') + '</tbody></table>' : '<div class="empty">暂无来源数据</div>';
}

/* ================= 资料库 ================= */
async function renderKb() {
  const s = await api('/api/kb/stats');
  const c = s.counts || {};
  $('#kb-stats').innerHTML = [
    ['资料条目', c.total], ['已确认', c.confirmed], ['文档', c.document], ['项目', c.project],
    ['经历', c.experience], ['奖项', c.award],
  ].map(([l, n]) => `<div class="stat"><div class="n">${n ?? 0}</div><div class="l">${l}</div></div>`).join('');

  const docs = await api('/api/kb/documents');
  $('#kb-docs').innerHTML = docs.length ? '<table><thead><tr><th>文件</th><th>分类</th><th>解析</th><th>摘要</th><th>操作</th></tr></thead><tbody>' +
    docs.map(d => `<tr><td>${esc(d.file_name)}<div class="muted mono">${esc(d.file_type)}</div></td>
      <td><span class="tag">${d.category}</span></td>
      <td>${d.parse_status === 'parsed' ? '<span class="ok">✔ 已解析</span>' : d.parse_status === 'failed' ? '<span class="bad">✘ 失败</span>' : '未解析'}</td>
      <td class="muted" style="max-width:280px">${esc((d.summary || '').slice(0, 60))}</td>
      <td><button class="btn sm" onclick="reparseDoc(${d.id})">重新解析</button>
      <button class="btn sm danger" onclick="delDoc(${d.id})">删除</button></td></tr>`).join('') + '</tbody></table>'
    : '<div class="empty">尚未上传资料</div>';

  const p = await api('/api/kb/personal');
  const fields = [['name', '姓名'], ['email', '邮箱'], ['phone', '电话'], ['city', '所在城市'],
    ['target_cities', '求职城市（逗号分隔）'], ['target_directions', '求职方向'], ['target_positions', '目标岗位'],
    ['target_industries', '目标行业'], ['salary_expectation', '薪资期望'], ['available_date', '到岗时间']];
  $('#personal-form').innerHTML = fields.map(([k, l]) => {
    const v = Array.isArray(p[k]) ? p[k].join(', ') : (p[k] || '');
    return `<div class="field"><label>${l}</label><input class="input" id="pi-${k}" value="${esc(v)}"></div>`;
  }).join('');
}

$('#btn-kb-upload').onclick = async () => {
  const f = $('#kb-file').files[0];
  if (!f) { toast('请选择文件', true); return; }
  const fd = new FormData();
  fd.append('file', f); fd.append('category', $('#kb-category').value);
  const r = await fetch('/api/kb/upload', { method: 'POST', body: fd }).then(x => x.json());
  if (r.error) { toast(r.error, true); return; }
  toast(r.parse_status === 'parsed' ? `已入库并解析${r.ocr_used ? '（OCR）' : ''}：${r.text_chars} 字` : '已保存，但文本解析失败（可人工转录）');
  $('#kb-file').value = ''; renderKb();
};
$('#btn-kb-url').onclick = async () => {
  const url = $('#kb-url').value.trim();
  if (!url) return;
  const fd = new FormData(); fd.append('url', url); fd.append('title', '');
  const r = await fetch('/api/kb/url', { method: 'POST', body: fd }).then(x => x.json());
  if (r.error) { toast(r.error, true); return; }
  toast(`网页作品集已入库（${r.text_chars} 字）`); $('#kb-url').value = ''; renderKb();
};
$('#btn-kb-search').onclick = async () => {
  const q = $('#kb-q').value.trim();
  const r = await api(`/api/kb/search?q=${encodeURIComponent(q)}`);
  $('#kb-search-result').innerHTML = r.items.length ? r.items.map(it =>
    `<div style="padding:8px 0;border-bottom:1px solid var(--soft)"><b>${esc(it.title)}</b> <span class="tag">${it.source_type}</span> <span class="tag">${it.confidence}</span>
     <div class="muted" style="margin-top:3px">${esc((it.summary || it.text || '').slice(0, 110))}…</div></div>`).join('')
    : `<div class="empty">资料库中没有检索到「${esc(q)}」的相关内容</div>`;
};
window.reparseDoc = async id => { await api(`/api/kb/documents/${id}/reparse`, { method: 'POST' }); renderKb(); };
window.delDoc = async id => { if (!confirm('删除该资料记录？文件保留在 candidate/ 目录。')) return; await api(`/api/kb/documents/${id}`, { method: 'DELETE' }); renderKb(); };
$('#btn-personal-save').onclick = async () => {
  const keys = ['name', 'email', 'phone', 'city', 'target_cities', 'target_directions', 'target_positions', 'target_industries', 'salary_expectation', 'available_date'];
  const data = {};
  keys.forEach(k => { const v = $(`#pi-${k}`).value.trim(); if (['target_cities', 'target_directions', 'target_positions', 'target_industries'].includes(k)) data[k] = v ? v.split(/[,，]/).map(s => s.trim()) : []; else data[k] = v; });
  await api('/api/kb/personal', { method: 'POST', body: JSON.stringify(data) });
  toast('个人信息已保存（入索引，供 JD 匹配调用）');
};

/* ================= 简历中心 ================= */
async function renderResume() {
  const m = await api('/api/resume/master');
  $('#master-info').innerHTML = m.id ? `<div class="card">
    <h3>Master Resume <span class="badge b-S" style="margin-left:6px">READ ONLY</span></h3>
    <table><tbody>
    <tr><td class="muted">源（只读）</td><td class="mono">${esc(m.master_dir)}</td></tr>
    <tr><td class="muted">页面</td><td>${esc(m.page_size)}</td></tr>
    <tr><td class="muted">渲染</td><td class="mono">Chrome/Edge headless --no-pdf-header-footer</td></tr>
    <tr><td class="muted">锁定区域</td><td>姓名 / 联系方式 / 教育 / 实习 / 项目 / 荣誉 / 技能 / 排版 / CSS / 图片 / 二维码 / 链接</td></tr>
    <tr><td class="muted">可编辑</td><td><b>自我评价</b>（${(m.self_eval_text || []).length} 条）、<b>个人亮点</b>（${(m.highlights_text || []).length} 项）</td></tr>
    </tbody></table></div>` :
    `<div class="card"><div class="empty">尚未导入 Master Resume <button class="btn primary" style="margin-top:10px" onclick="importMaster()">从 C:\\Users\\19600\\Desktop\\resume_build 导入（只读快照）</button></div></div>`;
  const versions = await api('/api/resume/versions');
  const approved = versions.filter(v => v.approved);
  $('#approved-list').innerHTML = approved.length ? `<div class="card approved-box">
    <h3>✓ 已满意 · 待投递（共 ${approved.length} 份）</h3>
    <div class="muted">这些简历已点「✓ 满意此简历」并准备投递；点「准备投递」时 PDF 已自动下载到 <span class="mono">C:\\Users\\19600\\Desktop\\秋招简历\\当天日期</span>（按 YYYY-MM-DD 每天新建一个文件夹，也可点下方按钮补下载）。</div>
    ${approved.map(v => `<div class="appr-row"><div><b>${esc(v.company)}</b> · ${esc(v.position)} ${v.app_status ? `<span class="tag">${esc(v.app_status)}</span>` : ''}</div>
      <div class="jc-actions">
        <a class="btn sm" href="/api/resume/versions/${v.id}/pdf" target="_blank">📄 PDF</a>
        <button class="btn sm" onclick="exportDesktop(${v.id})">⬇ 下载到桌面</button>
      </div></div>`).join('')}
  </div>` : '<div class="empty">暂无已满意简历：在精投中心点「✓ 满意此简历」即可归集到此处</div>';
  $('#versions-list').innerHTML = versions.length ? '<table><thead><tr><th>公司 · 岗位</th><th>Diff</th><th>状态</th><th>生成时间</th><th></th></tr></thead><tbody>' +
    versions.map(v => `<tr><td><b>${esc(v.company)}</b> · ${esc(v.position)}</td>
      <td><b class="${v.diff_status === 'pass' ? 'ok' : v.diff_status === 'fail' ? 'bad' : ''}">${v.diff_status?.toUpperCase()}</b></td>
      <td>${v.status}</td><td class="muted">${esc(v.created_at || '')}</td>
      <td>${v.diff_status === 'pass'
        ? `<a class="btn sm" href="/api/resume/versions/${v.id}/pdf" target="_blank">PDF</a> <button class="btn sm" onclick="showVersionDetail(${v.id})">Diff 详情</button> <button class="btn sm" onclick="exportDesktop(${v.id})">⬇ 下载</button>`
        : `<button class="btn sm" onclick="showVersionDetail(${v.id})">失败原因</button>`}</td></tr>`).join('') + '</tbody></table>'
    : '<div class="empty">暂无简历版本：在精投中心点「定制简历」生成</div>';
}
window.exportDesktop = async id => {
  try {
    const r = await api(`/api/resume/versions/${id}/export-desktop`, { method: 'POST' });
    if (r.ok) toast('✓ 已下载到：' + r.path);
    else toast('下载失败：' + (r.error || '未知'), true);
  } catch (e) { toast(e.data?.detail || e.message, true); }
};
window.importMaster = async () => { const r = await api('/api/resume/import-master', { method: 'POST' }); if (r.error) { toast(r.error, true); return; } toast('Master Resume 已导入（只读）'); renderResume(); };
window.showVersionDetail = async id => {
  const v = await api(`/api/resume/versions/${id}`);
  const diff = v.diff_json || {};
  openModal(`<h2>版本详情 · ${esc(v.company)} · ${esc(v.position)} <span class="badge ${v.diff_status === 'pass' ? 'b-S' : 'b-D'}">${v.diff_status?.toUpperCase()}</span></h2>
    ${(diff.structural_changes?.length || diff.content_changes?.length) ? `<div class="bad" style="margin-bottom:8px">白名单外发现差异（结构 ${diff.structural_changes?.length || 0} 处 / 内容 ${diff.content_changes?.length || 0} 处）：</div><pre class="mono" style="background:var(--red-soft);padding:10px;border-radius:8px;max-height:200px;overflow:auto">${esc(JSON.stringify({structural: diff.structural_changes, content: diff.content_changes}, null, 1))}</pre>` : '<div class="ok" style="margin-bottom:8px">结构 + 内容 Diff 均一致 ✔</div>'}
    <div class="diff-box">
      <div class="diff-col diff-old"><h4>原 · 自我评价</h4>${(v.orig_self_eval || []).map(it => `<div><b>${esc(it.cat)}</b><div>${esc(it.text)}</div></div>`).join('')}</div>
      <div class="diff-col diff-new"><h4>新 · 自我评价</h4>${(v.new_self_eval || []).map(it => `<div><b>${esc(it.cat)}</b><div>${esc(it.txt)}</div></div>`).join('')}</div>
      <div class="diff-col diff-old"><h4>原 · 个人亮点</h4>${(v.orig_highlights || []).map(it => `<div><b>${esc(it.cat)}</b><div>${esc((it.lines || []).join(' / '))}</div></div>`).join('')}</div>
      <div class="diff-col diff-new"><h4>新 · 个人亮点</h4>${(v.new_highlights || []).map(it => `<div><b>${esc(it.cat)}</b><div>${esc((it.lines || []).join(' / '))}</div></div>`).join('')}</div>
    </div>
    ${v.pdf_check_json ? `<div class="muted mono" style="margin-top:10px;max-height:150px;overflow:auto;background:var(--soft);padding:8px;border-radius:6px">${esc(JSON.stringify(v.pdf_check_json, null, 1))}</div>` : ''}
    <div class="m-actions">${v.diff_status === 'pass' ? `<a class="btn primary" href="/api/resume/versions/${v.id}/pdf" target="_blank">📄 PDF</a>` : ''}
    <button class="btn" onclick="closeModal()">关闭</button></div>`);
};

/* ================= 周度复盘 ================= */
async function renderReview() {
  const list = await api('/api/reviews');
  $('#reviews-list').innerHTML = list.length ? list.map(r => {
    const s = r.stats || {};
    return `<div class="card review-block"><h3>📅 ${esc(r.week)} <span class="h-sub">${esc(s.range || '')}</span></h3>
      <div class="stat-grid small">${[['发现', s.discovered], ['精投', s.shortlisted], ['投递', s.applied], ['OA', s.oa], ['面试', s.interviews], ['Offer', s.offers]]
        .map(([l, n]) => `<div class="stat"><div class="n">${n ?? 0}</div><div class="l">${l}</div></div>`).join('')}</div>
      <pre>${esc(r.strategy_next_week || '')}</pre>
      <div class="muted">方向分析：${Object.entries(r.insights || {}).map(([d, v]) => `${d}（发现${v.discovered}·均分${v.avg_score ?? '—'}）`).join(' ｜ ')}</div></div>`;
  }).join('') : '<div class="empty">暂无周报（每周六 Automation 自动生成，或点上方手动生成）</div>';
}
$('#btn-review-gen').onclick = async () => {
  const r = await api('/api/reviews/generate', { method: 'POST' });
  toast(`周报已生成：${r.week}`); renderReview();
};

/* ================= 通知 ================= */
async function renderNotify() {
  const list = await api('/api/notifications');
  $('#notify-list').innerHTML = list.length ? list.map(n =>
    `<div class="card" style="padding:12px 16px"><b>${esc(n.title)}</b> <span class="tag">${esc(n.kind)}</span> <span class="tag">${esc(n.channel)}</span>
     ${n.status === 'sent' ? '<span class="ok">✔ 已发送</span>' : n.status === 'failed' ? '<span class="bad">✘ 失败</span>' : '<span class="muted">待发送</span>'}
     ${n.error_message ? `<div class="bad" style="margin-top:4px">失败原因：${esc(n.error_message)}</div>` : ''}
     <div style="margin-top:5px">${esc(n.content)}</div><div class="muted" style="margin-top:4px">${esc(n.created_at || '')}</div></div>`).join('')
    : '<div class="empty">暂无通知：S/A 级岗位、截止提醒、面试提醒、周报等会自动出现在这里</div>';
}

/* ================= 设置 ================= */
async function renderSettings() {
  const s = await api('/api/system/status');
  const as = await api('/api/automation/settings').catch(() => ({}));
  const runs = await api('/api/automation/runs').catch(() => []);

  const fields = [
    ['weekday_radar_enabled', '工作日岗位雷达', 'checkbox'],
    ['radar_run_time', '岗位雷达运行时间', 'time'],
    ['daily_target', '每日岗位目标数量', 'number'],
    ['deadline_reminder', '截止日期提醒', 'checkbox'],
    ['interview_reminder', '面试提醒', 'checkbox'],
    ['weekend_review', '周末 Review Mode', 'checkbox'],
    ['review_run_time', '周报运行时间', 'time'],
  ];
  $('#automation-settings').innerHTML = `<table><tbody>` +
    fields.map(([k, l, t]) => `<tr><td class="muted">${l}</td><td>
      ${t === 'checkbox'
        ? `<input type="checkbox" id="as-${k}" ${as[k] === '1' ? 'checked' : ''} onchange="saveAutoSetting('${k}', this.checked ? '1' : '0')">`
        : `<input type="${t}" class="input" id="as-${k}" value="${esc(as[k] || '')}" onchange="saveAutoSetting('${k}', this.value)">`}
      </td></tr>`).join('') + '</tbody></table>' +
    '<div class="muted" style="margin-top:8px">岗位雷达：周一至周五运行；周末自动切换 Review Mode。20 是目标不是 KPI，宁缺毋滥。</div>';

  // 通知设置
  const np = await api('/api/notify/prefs').catch(() => ({}));
  const ws = await api('/api/notify/webhook-status').catch(() => ({}));
  const nf = [
    ['notify_enabled', '微信/Webhook 通知总开关', 'checkbox'],
    ['notify_daily_brief', '每日早报', 'checkbox'],
    ['notify_high_match', '高匹配岗位提醒', 'checkbox'],
    ['notify_deadline', '截止提醒', 'checkbox'],
    ['notify_interview', '面试提醒', 'checkbox'],
    ['notify_status_change', '状态变化提醒', 'checkbox'],
    ['notify_weekly_review', '周报', 'checkbox'],
  ];
  $('#notify-settings').innerHTML = `<div class="muted" style="margin-bottom:10px">当前 Webhook：<b>${esc(ws.type || '未配置')}</b> ${ws.configured ? '<span class="ok">✔ 已配置</span>' : '<span class="bad">未配置（需在 .env 设置）</span>'}</div>
    <table><tbody>` + nf.map(([k, l]) => `<tr><td class="muted">${l}</td><td>
      <input type="checkbox" id="np-${k}" ${np[k] === '1' ? 'checked' : ''} onchange="saveNotifyPref('${k}', this.checked ? '1' : '0')"></td></tr>`).join('') + '</tbody></table>' +
    `<div style="margin-top:10px"><button class="btn primary" onclick="testNotify()">📤 发送测试通知</button>
    <button class="btn" onclick="dispatchNotify()">立即分发待发通知</button></div>
    <div class="muted" style="margin-top:8px">支持 Server酱 / PushPlus / 企业微信机器人 webhook（.env 配置 WEBHOOK_TYPE 与对应 Secret）。Token 不落库、不进日志。</div>`;

  await renderLLMSettings();
  await renderDeliverySettings();

  $('#automation-runs').innerHTML = runs.length ? '<table><thead><tr><th>任务</th><th>时间</th><th>状态</th><th>结果</th></tr></thead><tbody>' +
    runs.map(r => `<tr><td>${esc(r.automation_name)}</td><td class="muted">${esc(r.run_at || '')}</td><td><span class="${r.status === 'ok' ? 'ok' : 'bad'}">${esc(r.status)}</span></td><td class="muted" style="max-width:320px">${esc(r.output_summary || '')}</td></tr>`).join('') + '</tbody></table>'
    : '<div class="empty">暂无运行记录</div>';

  $('#settings-info').innerHTML = `<table><tbody>
    <tr><td class="muted">数据库</td><td class="mono">jobs.db（SQLite · WAL · FTS5）· 唯一数据源</td></tr>
    <tr><td class="muted">Master 源</td><td class="mono">${esc(s.master_source)} ${s.master_source_exists ? '<span class="ok">✔ 存在</span>' : '<span class="bad">✘ 不存在</span>'}（只读，永不写入）</td></tr>
    <tr><td class="muted">PDF 渲染</td><td class="mono">${esc(s.chrome)} · headless --no-pdf-header-footer</td></tr>
    <tr><td class="muted">OCR</td><td>${esc(s.ocr)}</td></tr>
    <tr><td class="muted">数据量</td><td>${Object.entries(s.counts).map(([k, v]) => `${k}:${v}`).join(' · ')}</td></tr>
    <tr><td class="muted">微信</td><td>V1 未接入（当前环境无微信消息通道）。通知层已预留 channel 字段与 wechat_command 表，未来接入零重构。</td></tr>
    <tr><td class="muted">Automation</td><td>工作日岗位雷达 / 每日管家 / 截止提醒 / 面试提醒 / 周六周报（WorkBuddy 侧配置）</td></tr>
    </tbody></table>`;
}

window.saveNotifyPref = async (k, v) => {
  await api('/api/notify/prefs', { method: 'POST', body: JSON.stringify({ [k]: v }) });
  toast('通知设置已保存');
};
window.testNotify = async () => {
  const r = await api('/api/notify/test', { method: 'POST' });
  toast(r.message || (r.ok ? '发送成功' : '发送失败'), !r.ok);
};
window.dispatchNotify = async () => {
  const r = await api('/api/notify/dispatch', { method: 'POST' });
  toast(`已分发：成功 ${r.sent} / 失败 ${r.failed}`);
};

window.saveAutoSetting = async (k, v) => {
  await api('/api/automation/settings', { method: 'POST', body: JSON.stringify({ [k]: v }) });
  toast('设置已保存');
};

/* ================= LLM 密钥管理（cc switch 式切换/续费） ================= */
window._llmProviders = [];
async function renderDeliverySettings() {
  const el = document.getElementById('delivery-settings');
  if (!el) return;
  const s = await api('/api/delivery/settings').catch(() => ({}));
  el.innerHTML = `<table><tbody>
    <tr><td class="muted">练手期起点（前 7 天只投 0-99 / 100-499）</td><td>
      <input type="date" class="input" id="ds-practice" value="${esc(s.practice_start_date || '')}"></td></tr>
    <tr><td class="muted">每日投递配额</td><td>
      <input type="number" class="input" id="ds-quota" value="${esc(s.daily_quota ?? 8)}" min="1" max="20"></td></tr>
    <tr><td class="muted">每周新增岗位目标（周六日补 50 个）</td><td>
      <input type="number" class="input" id="ds-weekly" value="${esc(s.weekly_target ?? 50)}" min="1" max="200"></td></tr>
    <tr><td class="muted">岗位 JD 分析默认用 LLM（关闭则规则版）</td><td>
      <input type="checkbox" id="ds-llm" ${s.default_llm_analysis ? 'checked' : ''}></td></tr>
    <tr><td class="muted">面试准备状态</td><td>
      ${s.interview_ready ? '<span class="badge b-A">已准备好（跳过 0-99）</span> <button class="btn sm" onclick="resetInterviewReady()">重置</button>' : '<span class="badge b-B">练手期（含 0-99）</span>'}</td></tr>
  </tbody></table>
  <div class="m-actions"><button class="btn primary" id="ds-save">保存策略</button></div>`;
  const save = document.getElementById('ds-save');
  if (save) save.onclick = async () => {
    await api('/api/delivery/settings', { method: 'POST', body: JSON.stringify({
      practice_start_date: document.getElementById('ds-practice').value,
      daily_quota: parseInt(document.getElementById('ds-quota').value) || 8,
      weekly_target: parseInt(document.getElementById('ds-weekly').value) || 50,
      default_llm_analysis: document.getElementById('ds-llm').checked,
    }) });
    toast('投递策略已保存'); renderDeliverySettings(); renderDashboard();
  };
}
window.resetInterviewReady = async () => {
  await api('/api/delivery/interview-ready/reset', { method: 'POST' });
  toast('已重置面试准备：恢复可投 0-99'); renderDeliverySettings(); renderDashboard();
};

async function renderLLMSettings() {
  const d = await api('/api/settings/llm').catch(() => ({ providers: [], active_provider: 'custom', configured: false }));
  window._llmProviders = d.providers || [];
  const active = d.active_provider;
  const opts = window._llmProviders.map(p => `<option value="${p.id}" ${p.id === active ? 'selected' : ''}>${esc(p.label)}</option>`).join('');
  const listHtml = window._llmProviders.map(p => {
    const masked = p.key_masked ? `🔑 ${esc(p.key_masked)}` : '🔒 未配置';
    const tag = p.active ? '<span class="ok">● 当前</span>' : `<button class="btn sm" onclick="switchLLM('${p.id}')">切到</button>`;
    return `<tr><td>${esc(p.label)}</td><td class="mono">${masked}</td><td>${tag}</td></tr>`;
  }).join('');
  $('#llm-settings').innerHTML = `
    <div class="muted" style="margin-bottom:10px">选择 Provider，填 Key 即可「切换 / 续费」。所有 Key 只存 .env，不落库、不进日志。当前生效：<b>${esc(active)}</b></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:10px">
      <div><label class="muted">Provider</label><br><select id="llm-provider" class="input" onchange="onLLMProviderChange()">${opts}</select></div>
      <div style="flex:1;min-width:240px"><label class="muted">API Key（留空=不修改；续费请填新 Key）</label><br><input id="llm-key" class="input" placeholder="sk-..." style="width:100%"></div>
    </div>
    <div id="llm-custom-fields" style="display:none;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <div><label class="muted">Base URL</label><br><input id="llm-base" class="input" placeholder="https://.../v1" style="width:300px"></div>
      <div><label class="muted">Model</label><br><input id="llm-model" class="input" placeholder="model-name" style="width:200px"></div>
    </div>
    <div style="margin-top:6px">
      <button class="btn primary" onclick="saveLLM()">💾 保存并切换</button>
      <button class="btn" onclick="testLLM()">🔌 测试连接</button>
    </div>
    <div id="llm-msg" class="muted" style="margin-top:8px"></div>
    <h4 style="margin:14px 0 6px">已存密钥</h4>
    <table><thead><tr><th>Provider</th><th>Key</th><th>操作</th></tr></thead><tbody>${listHtml}</tbody></table>
    <div class="muted" style="margin-top:8px">切换 = 一键换用已存 Key（无需重填）；续费 = 同一 Provider 填新 Key 覆盖旧值。保存后「定制简历」的 🤖 一键生成即用当前 Provider。</div>`;
  onLLMProviderChange();
}

window.onLLMProviderChange = () => {
  const provider = document.getElementById('llm-provider')?.value;
  const isCustom = provider === 'custom';
  const cf = document.getElementById('llm-custom-fields');
  if (cf) cf.style.display = isCustom ? 'flex' : 'none';
  // 把当前 Provider 的 masked key 显示为 placeholder，提示用户
  const p = window._llmProviders.find(x => x.id === provider);
  const keyInput = document.getElementById('llm-key');
  if (keyInput) keyInput.placeholder = p && p.key_masked ? `当前：${p.key_masked}（续费请填新 Key）` : 'sk-...（新增/续费时填写）';
};

window.saveLLM = async () => {
  const provider = document.getElementById('llm-provider').value;
  const api_key = document.getElementById('llm-key').value.trim();
  const body = { provider, api_key };
  if (provider === 'custom') {
    body.base_url = document.getElementById('llm-base').value.trim();
    body.model = document.getElementById('llm-model').value.trim();
  }
  const r = await api('/api/settings/llm', { method: 'POST', body: JSON.stringify(body) });
  document.getElementById('llm-msg').textContent = r.message || (r.ok ? '已保存' : '失败');
  if (r.ok) { document.getElementById('llm-key').value = ''; await renderLLMSettings(); toast('LLM 密钥已更新'); }
  else toast(r.message, true);
};

window.switchLLM = async (provider) => {
  const r = await api('/api/settings/llm', { method: 'POST', body: JSON.stringify({ provider }) });
  document.getElementById('llm-msg').textContent = r.message || '';
  if (r.ok) { await renderLLMSettings(); toast(`已切换到 ${provider}`); }
  else toast(r.message, true);
};

window.testLLM = async () => {
  const msg = document.getElementById('llm-msg');
  msg.textContent = '连接测试中…';
  const r = await api('/api/settings/llm/test', { method: 'POST' });
  msg.textContent = r.ok ? `✅ ${r.message}（model=${r.model || '?'}）` : `❌ ${r.message}`;
};

/* ---------------- 启动 ---------------- */
(async function init() {
  try { const m = await api('/api/resume/master'); $('#sys-mini').textContent = m.id ? '● Master Resume 已接入（只读）' : '● Master Resume 未导入'; } catch (e) { $('#sys-mini').textContent = '● 服务异常'; }
  loadPage('dashboard');
  maybeShowReadinessPopup();
})();

/* 扩展一键捕捉 / 其他端改动后，切回工作台或窗口获得焦点时自动刷新，
   避免岗位池/看板停留在旧列表（无需手动 F5）。 */
function autoRefreshOnFocus() {
  try { renderJobs(); } catch (e) {}
  try { renderDashboard(); } catch (e) {}
  try { renderPrecision(); } catch (e) {}
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) autoRefreshOnFocus(); });
window.addEventListener('focus', autoRefreshOnFocus);

/* 每日打开工作台弹窗：问是否准备好面试。选「做好了」→ 之后跳过 0-99 小厂。 */
async function maybeShowReadinessPopup() {
  try {
    const s = await api('/api/delivery/today').catch(() => null);
    if (!s || s.interview_ready) return;   // 已准备好则不再打扰
    openModal(`<h2>🎯 面试准备检查</h2>
      <p>你今天做好面试的准备了么？</p>
      <p class="muted">选「做好了」后，系统将不再推送 <b>0-99 人</b> 小厂，只投 <b>100-499</b> 与 <b>500+</b> 规模岗位；选「还没」则继续练手（含 0-99）。</p>
      <div class="m-actions">
        <button class="btn" onclick="closeModal()">还没（继续练手）</button>
        <button class="btn primary" id="ready-yes">✓ 做好了</button>
      </div>`);
    const btn = document.getElementById('ready-yes');
    if (btn) btn.onclick = async () => {
      await api('/api/delivery/interview-ready', { method: 'POST' });
      closeModal();
      toast('✓ 已标记为准备好面试：后续只投 100-499 与 500+');
      renderDashboard();
    };
  } catch (e) { /* 弹窗失败不影响主流程 */ }
}

