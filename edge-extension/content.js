/* 秋招工作台助手 · 内容脚本（接收 fill 指令，按 label/name/placeholder 启发式填充表单） */
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'fill') {
    // 异步执行：匹配规划同步完成（毫秒级），DOM 写入异步批处理，绝不阻塞弹窗
    (async () => {
      try {
        const plan = planFill(msg);
        // 基础字段先写
        writeFill(plan.matches);
        // LLM 二次映射补全（支持多值拆分、自动添加新行）
        let aiFilled = 0;
        if (msg.mapped && msg.mapped.length) {
          aiFilled = await fillByMapped(msg.mapped);
        }
        sendResponse({
          ok: true,
          filled: plan.matches.length + aiFilled,
          unfilled: plan.unfilled,
          all: plan.all
        });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    })();
    return true;
  }
});

// 字段名 → 规范键 的映射（兼容中英文）。只放硬事实，不放叙述类，避免把工作经历误填进实习经历。
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
  // 包裹型 label
  const wrap = el.closest('label');
  if (wrap) return wrap.textContent;
  // 兄弟 / 容器内 label（最常见：<div class="form-item"><label>姓名</label><input></div>）
  const container = el.closest('[class*="form-item"], [class*="item"], [class*="field"], [class*="form"], [class*="row"], [class*="col"], [class*="control"], [class*="group"], [class*="wrap"], [class*="box"], .el-form-item, .ant-form-item, li, tr') || el.parentElement;
  if (container) {
    const lab = container.querySelector('label, [class*="label"]');
    if (lab && lab !== el && lab.textContent.trim()) return lab.textContent.trim().replace(/\s+/g, ' ');
  }
  return '';
}

// 获取字段所在板块/分组标题，用于区分「实习经历 > 工作经历」这类歧义标签
function sectionHeading(el) {
  let n = el.closest('section, fieldset, [class*="section"], [class*="group"], [class*="block"], [class*="panel"], [class*="card"]');
  while (n) {
    const h = n.querySelector('h1,h2,h3,h4,h5,h6,legend,.title,[class*="title"],[class*="header"],[class*="heading"]');
    if (h && h.textContent.trim()) {
      const t = h.textContent.trim().replace(/\s+/g, ' ');
      if (!/^(下一步|提交|保存|取消|返回|上一步|确认)$/i.test(t)) return t;
    }
    n = n.parentElement && n.parentElement.closest('section, fieldset, [class*="section"], [class*="group"], [class*="block"], [class*="panel"], [class*="card"]');
  }
  return '';
}

function richLabelOf(el) {
  const raw = labelOf(el).trim().replace(/\s+/g, ' ');
  const sec = sectionHeading(el);
  if (sec && sec !== raw && !raw.toLowerCase().startsWith(sec.toLowerCase())) {
    return `${sec} > ${raw || '输入框'}`;
  }
  return raw || '';
}

function sectionElement(el) {
  return el.closest('section, fieldset, [class*="section"], [class*="group"], [class*="block"], [class*="panel"], [class*="card"]');
}

function findSectionByHeading(text) {
  if (!text) return null;
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,legend,.title,[class*="title"],[class*="header"],[class*="heading"]')];
  const h = headings.find(x => x.textContent.trim().replace(/\s+/g, ' ') === text);
  return h ? sectionElement(h) : null;
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
  // LLM 二次映射补进来的显式键值对（key 已规范化或带 __richLabel）
  (msg.mapped || []).forEach(m => { if (m.key && m.value != null) lookup[m.key] = m.value; });
  return lookup;
}

function setElValue(el, val) {
  if (Array.isArray(val)) val = val.join('\n');
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
    const rich = richLabelOf(el);
    if (rich) allLabels.push(rich);
    const key = canonicalize(rich);
    const val = key ? lookup[key] : lookup['__' + rich];
    if (val != null && String(val).trim()) matches.push({ el, val, label: rich });
    else if (rich) unfilled.push(rich);
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

// 查找所有与 rich label 匹配的可填元素（包含 fallback：按字段名后半段匹配）
function findElsByRichLabel(rich) {
  let els = [...document.querySelectorAll('input, textarea, select')].filter(el => {
    const t = (el.type || '').toLowerCase();
    if (el.tagName === 'SELECT') return true;
    return !['hidden', 'submit', 'button', 'file', 'checkbox', 'radio'].includes(t);
  });
  els = els.filter(e => richLabelOf(e) === rich);
  if (els.length) return els;
  // fallback：按 "分组 > 字段" 的字段部分精确匹配
  const field = rich.includes('>') ? rich.split('>')[1].trim() : rich;
  if (!field || field === '输入框') return [];
  return [...document.querySelectorAll('input, textarea, select')].filter(el => {
    const t = (el.type || '').toLowerCase();
    if (el.tagName === 'SELECT') return true;
    return !['hidden', 'submit', 'button', 'file', 'checkbox', 'radio'].includes(t);
  }).filter(e => labelOf(e).trim().replace(/\s+/g, ' ') === field);
}

// 把 LLM 返回值规整为「多行条目」数组：兼容数组 / 换行文本 / 编号列表 / 分号分隔
function toItems(raw) {
  if (Array.isArray(raw)) return raw.map(v => String(v).trim()).filter(Boolean);
  const s = String(raw || '').trim();
  if (!s) return [];
  const lines = s.split(/\r?\n/).map(x => stripMarker(x.trim())).filter(Boolean);
  if (lines.length >= 2) return lines;
  const bySemi = s.split(/[；;]/).map(x => x.trim()).filter(Boolean);
  if (bySemi.length >= 2) return bySemi;
  return [s];
}
function stripMarker(s) {
  return s.replace(/^(\d+[\.、)）]|\(\d+\)|[一二三四五六七八九十]+[、．.]|[•\-*·]|[A-Za-z]\.?\)|\+)\s*/, '').trim();
}

// 找到「字段组」容器：包裹该输入框及其重复行的紧邻祖先（form-item / li / tr / fieldset 等）
function fieldGroupOf(el) {
  let n = el;
  for (let i = 0; i < 8 && n; i++) {
    const cls = (n.className || '').toString();
    if (/form-item|formItem|form_item|form-group|formGroup|el-form-item|ant-form-item|ivu-form-item|field-item|fieldItem|form-row|formRow|form_field|item-row|itemRow|form-control-wrap|widget|row-item|rowItem/i.test(cls)) return n;
    if (/^(LI|TR|FIELDSET)$/.test(n.tagName)) return n;
    n = n.parentElement;
  }
  return null; // 找不到清晰字段组 -> 由调用方降级为单框换行
}

// 在范围内查找「添加/新增」按钮（排除上传/附件类按钮，避免误点）
function clickAddButton(scope) {
  if (!scope) return false;
  const re = /(添加|新增|增加|再加|添加一条|新增一条|add)/i;
  const plus = /[＋+]/;
  const bad = /(附件|图片|图像|照片|文件|上传|简历|证件|头像|作品|photo|upload|file|img)/i;
  const cands = [...scope.querySelectorAll('button, a, span, i, div, svg, [role="button"]')];
  for (const b of cands) {
    const text = (b.textContent || '').trim();
    const title = (b.getAttribute('title') || '').trim();
    const aria = (b.getAttribute('aria-label') || '').trim();
    const cls = (b.className || '').toString();
    const isAdd = re.test(text) || re.test(title) || re.test(aria)
      || (plus.test(text) && text.length <= 4) || plus.test(cls);
    if (isAdd && !bad.test(text) && !bad.test(title) && !bad.test(cls)) {
      try { b.click(); return true; } catch (_) {}
    }
  }
  return false;
}

// 把多个条目填进同一字段：先按字段组填入，不够时自动点「添加」新建行；
// 每加一行通常是独立 Form.Item，故加行后在「板块」范围内按位置继续填（用空 label 的同类输入框识别重复行，避免误填兄弟字段）
async function fillRichLabel(rich, rawVals) {
  const values = toItems(rawVals);
  if (!values.length) return 0;
  const firstEls = findElsByRichLabel(rich);
  if (!firstEls.length) return 0;
  const anchor = firstEls[0];
  const group = fieldGroupOf(anchor);
  const section = sectionElement(anchor) || document;

  const sameKind = (e) => {
    const t = (e.type || '').toLowerCase();
    if (e.tagName === 'SELECT' || anchor.tagName === 'SELECT') return e.tagName === anchor.tagName;
    return !['hidden', 'submit', 'button', 'file', 'checkbox', 'radio'].includes(t);
  };
  // 目标输入框：标签精确匹配 rich，或「无标签 + 同类型」（重复行通常无标签）
  const isTarget = (e) => richLabelOf(e) === rich || (labelOf(e).trim() === '' && sameKind(e));

  let filled = 0;
  for (let attempt = 0; attempt < values.length + 8; attempt++) {
    // 候选：优先字段组；字段组已用尽或为空时，扩大到板块（只取 anchor 及其之后、且为目标输入框的）
    let cands = [];
    if (group) cands = [...group.querySelectorAll('input, textarea, select')].filter(sameKind).filter(isTarget);
    if (cands.length <= filled) {
      cands = [...section.querySelectorAll('input, textarea, select')].filter(sameKind).filter(isTarget);
    }
    const ai = cands.indexOf(anchor);
    if (ai >= 0) cands = cands.slice(ai);
    let placed = false;
    for (const inp of cands) {
      if (inp.__ff_done) continue;
      if (filled < values.length) {
        try { setElValue(inp, values[filled]); inp.__ff_done = true; filled++; placed = true; } catch (_) {}
      }
    }
    if (filled >= values.length) break;
    const clicked = clickAddButton(group) || clickAddButton(section) || clickAddButton(document);
    if (!clicked) break;
    await new Promise(r => setTimeout(r, 350));
  }
  // 兜底：仍有剩余条目 -> 拼到最后一个已填输入框（换行）
  if (filled < values.length) {
    let cands = [];
    if (group) cands = [...group.querySelectorAll('input, textarea, select')].filter(sameKind).filter(isTarget);
    if (cands.length <= filled) cands = [...section.querySelectorAll('input, textarea, select')].filter(sameKind).filter(isTarget);
    const done = cands.filter(i => i.__ff_done);
    if (done.length) {
      const last = done[done.length - 1];
      const extra = values.slice(filled).join('\n');
      const cur = last.value || '';
      try { setElValue(last, cur ? cur + '\n' + extra : extra); } catch (_) {}
    }
    filled = values.length;
  }
  return filled;
}

// 处理 LLM 返回的显式映射（支持数组 -> 多行/自动加行）
async function fillByMapped(mapped) {
  let total = 0;
  for (const m of mapped) {
    if (!m.key || m.value == null) continue;
    const rich = m.key.startsWith('__') ? m.key.slice(2) : m.key;
    total += await fillRichLabel(rich, m.value);
  }
  return total;
}
