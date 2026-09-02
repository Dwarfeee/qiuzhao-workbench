/* 秋招工作台助手 · 内容脚本（接收 fill 指令，按 label/name/placeholder 启发式填充表单） */
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'fill') {
    try {
      // 关键路径优化（对标市面一键填表插件）：匹配规划同步完成（毫秒级），
      // DOM 写入放到 requestAnimationFrame 异步批处理，弹窗立即拿到 filled/unfilled/all，
      // 绝不为等页面格式化脚本而阻塞。
      const plan = planFill(msg);
      writeFill(plan.matches);
      sendResponse({ ok: true, filled: plan.matches.length, unfilled: plan.unfilled, all: plan.all });
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
    return true;
  }
});

// 字段名 → 规范键 的映射（兼容中英文）
const CANON = [
  ['name', ['姓名', '名字', '真实姓名', 'name']],
  ['email', ['邮箱', '电子邮件', 'email', 'e-mail', 'mail']],
  ['phone', ['手机', '电话', '联系电话', '手机号', 'tel', 'phone', '手机号码']],
  ['school', ['学校', '院校', '毕业院校', '大学', 'school', 'university', '本科院校']],
  ['major', ['专业', '主修', 'major', '修读']],
  ['city', ['城市', '工作地', '所在地', '所在城市', 'city', '期望城市']],
  ['gender', ['性别', 'sex', 'gender']],
  ['birth', ['出生', '生日', 'birth', '出生年月']],
  ['political', ['政治面貌', '政治', 'party', '党派']],
  ['english', ['英语', 'cet', '四六级', '等级', 'english']],
  ['grad', ['毕业时间', '毕业年月', 'graduation', '预计毕业']],
  ['degree', ['学历', '学位', 'degree', '文化程度']],
  ['gpa', ['gpa', '绩点']],
  ['address', ['地址', '住址', 'address']],
  ['idcard', ['身份证', 'idcard', '证件号']]
];

function canonicalize(text) {
  text = (text || '').toLowerCase();
  for (const [key, kws] of CANON) {
    for (const kw of kws) {
      if (text.includes(kw.toLowerCase())) return key;
    }
  }
  return null;
}

function labelOf(el) {
  const id = el.id;
  if (id) {
    const l = document.querySelector('label[for="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
    if (l && l.textContent.trim()) return l.textContent;
  }
  const al = el.getAttribute('aria-label');
  if (al && al.trim()) return al;
  const ph = el.getAttribute('placeholder');
  if (ph && ph.trim()) return ph;
  if (id) return id;
  const wrap = el.closest('label');
  if (wrap) return wrap.textContent;
  return '';
}

function buildLookup(msg) {
  const lookup = {};
  const p = msg.personal || {};
  const map = { name: 'name', email: 'email', phone: 'phone', city: 'city', school: 'school', major: 'major' };
  for (const k in map) { if (p[k]) lookup[map[k]] = p[k]; }
  (msg.fields || []).forEach(f => {
    const key = canonicalize(f.label);
    if (key) lookup[key] = f.value;
    else lookup['__' + (f.label || '').trim()] = f.value;
  });
  // LLM 二次映射补进来的显式键值对（key 已规范化，直接入表）
  (msg.mapped || []).forEach(m => { if (m.key && m.value) lookup[m.key] = m.value; });
  return lookup;
}

function setElValue(el, val) {
  if (el.tagName === 'SELECT') {
    for (const opt of el.options) {
      if (opt.text.includes(val) || opt.value === val) { el.value = opt.value; break; }
    }
  } else {
    el.value = val;
  }
  // 把 input/change 事件异步派发，避免页面格式化函数同步递归卡死内容脚本
  setTimeout(() => {
    try {
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (_) {}
  }, 0);
}

// 同步规划：扫描所有可填元素，按 lookup 计算「可填」与「未填」列表，毫秒级返回
function planFill(msg) {
  const lookup = buildLookup(msg);
  const els = [...document.querySelectorAll('input, textarea, select')].filter(el => {
    const t = (el.type || '').toLowerCase();
    if (el.tagName === 'SELECT') return true;
    return !['hidden', 'submit', 'button', 'file', 'checkbox', 'radio'].includes(t);
  });
  const matches = [];
  const unfilled = [];
  const allLabels = [];
  for (const el of els) {
    const lab = labelOf(el);
    if (lab.trim()) allLabels.push(lab.trim());
    const key = canonicalize(lab);
    const val = key ? lookup[key] : lookup['__' + lab.trim()];
    if (val) matches.push({ el, val });
    else if (lab.trim()) unfilled.push(lab.trim());
  }
  return { matches, unfilled, all: allLabels };
}

// 异步批处理写入：每帧最多写 12 个字段，避免一次性阻塞主线程（弹窗早已拿到结果）
function writeFill(matches) {
  let i = 0;
  function step() {
    const end = Math.min(i + 12, matches.length);
    for (; i < end; i++) {
      try { setElValue(matches[i].el, matches[i].val); } catch (_) {}
    }
    if (i < matches.length) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
