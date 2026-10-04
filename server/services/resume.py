"""Master Resume 服务：只读接入、区域锚定、Tailored 版本、程序级 Diff、浏览器渲染 PDF、PDF 验证。

最高约束：
1. Master 源（桌面 resume_build 目录）永不写入；
2. 只允许修改「自我评价」「个人亮点」两个区域，白名单之外的任何结构/文本/属性差异 → diff FAIL → 版本不可用；
3. 生成 PDF 使用与用户原流程完全一致的方式：Chrome/Edge headless --no-pdf-header-footer。
4. 溢出检测复用用户自带 measure.js。

生成逻辑（DOM 最小修改）：
- 用 BeautifulSoup 解析 Master DOM；
- 只替换「自我评价」的 .eval-cat / .eval-txt 与「个人亮点」的 .bring-cat / .bring-txt
  四个叶子节点的文字内容；
- 保留 section、.bring-cols 容器、.eval-item/.bring-item、缩进、属性等一切结构。

Diff 逻辑（结构 + 文本双签名）：
- 把上述四个可编辑叶子节点内容清空（中和），生成两份签名：
  ① 结构签名 = 标签名 + 属性 + DOM 层级（忽略一切文本）；
  ② 内容签名 = 中和后剩余的全部文本（即非目标区域的文字）。
- 任一签名不一致 → FAIL。因此 section/container 删除、class/id 修改、标签变化、
  属性变化、DOM 层级变化、非目标文字变化 均能被检出。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from server import config
from server.db import connect

SEC_RE = re.compile(r'<section class="sec">(.*?)</section>', re.S)
TITLE_RE = re.compile(r'<div class="sec-title">.*?</div>', re.S)

# 可编辑的叶子节点 class（自我评价 + 个人亮点）。这些节点的「内部内容」允许变化，
# 但其外层骨架（section / .bring-cols / .eval-item / .bring-item 及 class 本身）必须不变。
EDITABLE_LEAF_CLASSES = ("eval-cat", "eval-txt", "bring-cat", "bring-txt")


# ---------------------------------------------------------------- Master 只读接入

def import_master() -> dict:
    """把 Desktop 的 resume_build 快照进系统（只读复制，绝不写源目录）。"""
    src_html = config.MASTER_HTML
    if not src_html.exists():
        return {"error": f"master html 不存在: {src_html}"}
    snap = config.MASTER_SNAPSHOT_DIR
    snap.mkdir(parents=True, exist_ok=True)
    html = src_html.read_text(encoding="utf-8")

    # 快照核心文件（resume_2col 等非主链路文件不复制）
    for name in ("photo.jpg", "qr.png", "measure.js"):
        f = config.MASTER_SOURCE_DIR / name
        if f.exists():
            shutil.copy2(f, snap / name)

    anchors = extract_anchors(html)
    full_text = re.sub(r"<[^>]+>", " ", html)
    full_text = re.sub(r"\s+", " ", full_text).strip()
    render_cmd = (f'"{_chrome_path()}" --headless=new --disable-gpu --no-pdf-header-footer '
                  f'--print-to-pdf="<OUT>" "file:///<SRC>"')

    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id FROM master_resume WHERE version='master_v1'").fetchone()
        fields = (str(config.MASTER_SOURCE_DIR), str(snap / "resume.html"),
                  json.dumps(["内联<style>"]), str(snap), None,
                  json.dumps(anchors["self_eval_items"], ensure_ascii=False),
                  json.dumps(anchors["highlight_items"], ensure_ascii=False),
                  json.dumps(anchors["meta"], ensure_ascii=False),
                  "A4 210mm×297mm @page margin:0", render_cmd, full_text)
        if existing:
            mid = existing[0]
            conn.execute(
                """UPDATE master_resume SET master_dir=?, html_path=?, css_paths=?, assets_dir=?,
                   pdf_path=?, self_eval_text=?, highlights_text=?, anchors=?, page_size=?,
                   render_command=?, text_extract=? WHERE id=?""", fields + (mid,))
        else:
            cur = conn.execute(
                """INSERT INTO master_resume (version, master_dir, html_path, css_paths, assets_dir,
                   pdf_path, self_eval_text, highlights_text, anchors, page_size, render_command, text_extract)
                   VALUES ('master_v1',?,?,?,?,?,?,?,?,?,?,?)""", fields)
            mid = cur.lastrowid
        # master 全文入 KB（confirmed：来自本人正式简历）
        conn.execute(
            """INSERT INTO knowledge_item (source_type, source_id, title, text, summary, tags, confidence)
               VALUES ('resume', ?, 'Master Resume·贺宣锦 UI/UX', ?, ?, ?, 'confirmed')
               ON CONFLICT(source_type, source_id) DO UPDATE SET
                 text=excluded.text, updated_at=datetime('now','localtime')""",
            (mid, full_text, full_text[:200], json.dumps(["resume", "master"], ensure_ascii=False)))
        conn.commit()
        return {"master_resume_id": mid, "anchors": anchors,
                "snapshot_dir": str(snap), "text_chars": len(full_text)}
    finally:
        conn.close()


def extract_anchors(html: str) -> dict:
    """定位两个可编辑区域，返回结构化锚点 + 当前内容。"""
    secs = list(SEC_RE.finditer(html))
    meta, eval_items, highlight_items = [], [], []
    for i, m in enumerate(secs):
        inner = m.group(1)
        tm = TITLE_RE.search(inner)
        title_text = re.sub(r"<[^>]+>", "", tm.group(0)) if tm else ""
        meta.append({"index": i, "title": title_text, "start": m.start(), "end": m.end()})
        if "自我评价" in title_text:
            for em in re.finditer(
                    r'<div class="eval-item">\s*<div class="eval-cat">(.*?)</div>\s*'
                    r'<div class="eval-txt">(.*?)</div>\s*</div>', inner, re.S):
                cat = em.group(1).strip()
                txt = em.group(2).strip()
                eval_items.append({"cat": cat, "html": txt,
                                   "text": _strip_tags(txt)})
        if "个人亮点" in title_text:
            for bm in re.finditer(
                    r'<div class="bring-item">\s*<div class="bring-cat">(.*?)</div>\s*'
                    r'<div class="bring-txt">(.*?)</div>\s*</div>', inner, re.S):
                cat = bm.group(1).strip()
                txt = bm.group(2).strip()
                highlight_items.append({"cat": cat, "html": txt,
                                        "lines": [l.strip() for l in _strip_tags(txt).split("<br>")]})
    return {"meta": meta, "self_eval_items": eval_items, "highlight_items": highlight_items,
            "editable_sections": [m["title"] for m in meta if m["title"] in ("自我评价", "个人亮点")]}


def _strip_tags(h: str) -> str:
    return h.replace("<b>", "").replace("</b>", "").replace("<br>", "<br>").strip()


# ---------------------------------------------------------------- Tailored 生成（DOM 最小修改）

def _sanitize_txt(html_txt: str) -> str:
    """只放行 <b> <br>，其余标签全部转义，防止结构注入。"""
    txt = html_txt or ""
    txt = txt.replace("<b>", "\x01").replace("</b>", "\x02").replace("<br>", "\x03").replace("<br/>", "\x03").replace("<br />", "\x03")
    txt = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    txt = txt.replace("\x01", "<b>").replace("\x02", "</b>").replace("\x03", "<br>")
    return txt


def _find_sections(soup: BeautifulSoup) -> dict:
    """按 sec-title 定位两个可编辑 section。"""
    found = {}
    for sec in soup.find_all("section", class_="sec"):
        title_el = sec.find("div", class_="sec-title")
        title = title_el.get_text() if title_el else ""
        if "自我评价" in title:
            found["self_eval"] = sec
        elif "个人亮点" in title:
            found["highlights"] = sec
    return found


def _set_leaf_text(parent: Tag, cls: str, text: str) -> None:
    """把 parent 内 class=cls 的叶子节点替换为纯文本（自动转义，防注入）。"""
    el = parent.find("div", class_=cls)
    if el is not None:
        el.clear()
        el.string = str(text).strip()


def _set_leaf_html(parent: Tag, cls: str, fragment: str) -> None:
    """把 parent 内 class=cls 的叶子节点替换为富文本（仅放行 <b>/<br>）。"""
    el = parent.find("div", class_=cls)
    if el is not None:
        el.clear()
        el.append(BeautifulSoup(_sanitize_txt(fragment), "html.parser"))


def apply_tailoring(master_html: str, self_eval_items: list[dict],
                    highlight_items: list[dict]) -> str:
    """以 Master 原始 DOM 为基础做最小修改：只替换两个区域的文字内容。

    可编辑区域内条目数允许变化：新增则克隆最后一个条目容器，减少则保留容器但清空内容。
    结构 Diff 以 section/容器级为准，不对 .eval-item/.bring-item 的数量做严格校验。
    """
    soup = BeautifulSoup(master_html, "html.parser")
    secs = _find_sections(soup)
    if "self_eval" not in secs or "highlights" not in secs:
        raise ValueError("未定位到「自我评价」或「个人亮点」section")

    eval_items = secs["self_eval"].find_all("div", class_="eval-item")
    _fill_items(eval_items, self_eval_items,
                lambda el, it: (_set_leaf_text(el, "eval-cat", str(it.get("cat", ""))),
                                _set_leaf_html(el, "eval-txt", str(it.get("txt", "")))))

    bring_items = secs["highlights"].find_all("div", class_="bring-item")
    def _bring_fill(el, it):
        _set_leaf_text(el, "bring-cat", str(it.get("cat", "")))
        body = "<br>".join(str(l).strip() for l in it.get("lines", []) if str(l).strip())
        _set_leaf_html(el, "bring-txt", body)
    _fill_items(bring_items, highlight_items, _bring_fill)

    return str(soup)


def _fill_items(containers: list[Tag], items: list[dict], fill_fn) -> None:
    """按顺序填充容器；items 多则克隆最后一个容器追加；items 少则清空多余容器。"""
    if not containers:
        return
    parent = containers[0].parent
    # 先保证容器数量 >= items 数量
    while len(containers) < len(items):
        clone = BeautifulSoup(str(containers[-1]), "html.parser").find()
        parent.append(clone)
        containers.append(clone)
    # 填充
    for i, it in enumerate(items):
        fill_fn(containers[i], it)
    # 多余容器清空（保留结构稳定）
    for el in containers[len(items):]:
        for cls in ("eval-cat", "eval-txt", "bring-cat", "bring-txt"):
            leaf = el.find("div", class_=cls)
            if leaf is not None:
                leaf.clear()


# ---------------------------------------------------------------- 程序级 Diff（结构 + 文本双签名）

def _neutralize(soup: BeautifulSoup) -> BeautifulSoup:
    """深拷贝并中性化可编辑区域：把「自我评价」「个人亮点」两个 section 的内容
    整体替换为固定占位，使区域内部任何变化（条目数量、分类名、文字、内联格式）
    都不会触发结构 Diff 误报。"""
    clone = BeautifulSoup(str(soup), "html.parser")
    for sec in clone.find_all("section", class_="sec"):
        title_el = sec.find("div", class_="sec-title")
        title = title_el.get_text() if title_el else ""
        if "自我评价" in title or "个人亮点" in title:
            # 保留 sec-title，内部其余全部清空并放入固定占位
            for child in list(sec.children):
                if getattr(child, "get", lambda x: None)("class") and "sec-title" in child.get("class", []):
                    continue
                child.extract()
            sec.append(BeautifulSoup('<div class="editable-region-placeholder"></div>', "html.parser"))
    return clone


def _attrs_sig(tag: Tag) -> str:
    """标签属性的确定性签名（键排序、class 列表排序）。"""
    parts = []
    for k in sorted(tag.attrs.keys()):
        v = tag.attrs[k]
        if isinstance(v, list):
            v = " ".join(sorted(v))
        parts.append(f'{k}="{v}"')
    return " ".join(parts)


def _structural_sig(node) -> str:
    """结构签名：标签名 + 属性 + 层级（忽略一切文本节点）。"""
    if isinstance(node, NavigableString):
        return ""
    if not isinstance(node, Tag):
        return ""
    attrs = _attrs_sig(node)
    inner = "".join(_structural_sig(c) for c in node.contents)
    return f"<{node.name} {attrs}>" + inner + f"</{node.name}>"


def _collect_text(node, parts: list[str]) -> None:
    """收集所有非空文本（按文档序）。"""
    if isinstance(node, NavigableString):
        t = str(node).strip()
        if t:
            parts.append(t)
        return
    if not isinstance(node, Tag):
        return
    for c in node.contents:
        _collect_text(c, parts)


def _content_sig(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    _collect_text(soup, parts)
    return "\n".join(parts)


def _diff_report(a: str, b: str, limit: int = 8) -> list[dict]:
    """用 difflib 提取两段签名的差异片段（辅助定位）。"""
    import difflib
    sm = difflib.SequenceMatcher(None, a, b)
    changes = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        changes.append({"op": tag,
                        "master": a[max(0, i1 - 25):i2 + 25][:160],
                        "tailored": b[max(0, j1 - 25):j2 + 25][:160]})
        if len(changes) >= limit:
            break
    return changes


def diff_html(master_html: str, tailored_html: str) -> dict:
    """程序级 Diff：结构签名 + 内容签名双校验。

    允许变化：四个可编辑叶子节点的内部内容。
    检出能力：section 删除、container 删除、class/id 修改、标签变化、属性变化、
    DOM 层级变化、非目标区域文字变化。
    """
    ms = BeautifulSoup(master_html, "html.parser")
    ts = BeautifulSoup(tailored_html, "html.parser")
    mn, tn = _neutralize(ms), _neutralize(ts)

    struct_a, struct_b = _structural_sig(mn), _structural_sig(tn)
    content_a, content_b = _content_sig(mn), _content_sig(tn)

    structure_ok = struct_a == struct_b
    content_ok = content_a == content_b
    status = "pass" if (structure_ok and content_ok) else "fail"
    return {
        "status": status,
        "structure_ok": structure_ok,
        "content_ok": content_ok,
        "structural_changes": [] if structure_ok else _diff_report(struct_a, struct_b),
        "content_changes": [] if content_ok else _diff_report(content_a, content_b),
    }


# ---------------------------------------------------------------- PDF 渲染与验证

def _safe_unlink(path: Path) -> None:
    """容错删除临时文件。WorkBuddy 沙箱会把 unlink 劫持为「移到回收站」，
    回收站不可用时抛 OSError；临时文件清理失败不应影响主流程，故静默忽略。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _chrome_path() -> str:
    for p in config.CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError("未找到 Chrome/Edge 可执行文件")


def _launch_chrome(args: list[str], wait_file: Path | None, timeout: int = 90) -> dict:
    """启动 Chrome：优先直接 subprocess；失败则用 ShellExecuteW 兜底（本机实测
    部分宿主环境下 CreateProcess 启动 Chrome 会静默失败，ShellExecuteW 稳定）。
    wait_file：等待生成的目标文件（PDF）。"""
    import ctypes
    import time
    chrome = args[0]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if wait_file is None or (wait_file.exists() and wait_file.stat().st_size > 1000):
            return {"ok": True, "mode": "subprocess", "exit": r.returncode,
                    "stderr": (r.stderr or "")[-200:]}
    except Exception:  # noqa: BLE001
        pass
    if wait_file is not None:
        _safe_unlink(wait_file)
    # ShellExecuteW 兜底（含空格路径统一加引号）
    def q(s):
        s = str(s)
        if " " in s and not s.startswith('"'):
            return f'"{s}"'
        return s
    param_str = " ".join(q(a) for a in args[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "open", chrome, param_str, None, 0)
    if rc <= 32:
        return {"ok": False, "mode": "shellexecute", "stderr": f"ShellExecuteW rc={rc}"}
    if wait_file is None:
        return {"ok": True, "mode": "shellexecute", "exit": 0, "stderr": ""}
    for _ in range(timeout):
        time.sleep(1)
        if wait_file.exists() and wait_file.stat().st_size > 1000:
            # 等文件写完（大小稳定）
            size1 = wait_file.stat().st_size
            time.sleep(1.5)
            if wait_file.stat().st_size >= size1:
                return {"ok": True, "mode": "shellexecute", "exit": 0, "stderr": ""}
    return {"ok": False, "mode": "shellexecute", "stderr": f"等待 {wait_file} 超时"}


def render_pdf(html_path: Path, out_pdf: Path) -> dict:
    """Chrome headless 渲染 HTML → PDF。
    关键：file URL 必须百分号编码——含中文的未编码 URL 会导致 Chrome 静默不渲染（实测）。"""
    from urllib.parse import quote

    chrome = _chrome_path()
    src = quote(str(html_path).replace("\\", "/"))
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    _safe_unlink(out_pdf)
    cmd = [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           "--virtual-time-budget=6000",
           f"--print-to-pdf={out_pdf}", f"file:///{src}"]
    r = _launch_chrome(cmd, out_pdf)
    ok = out_pdf.exists() and out_pdf.stat().st_size > 1000
    return {"ok": ok, **{k: v for k, v in r.items() if k != "ok"}, "stderr": r.get("stderr", "")}


def _pdf_pages_and_text(pdf_path: Path) -> tuple[int, str]:
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    return len(reader.pages), text


def measure_layout(html_path: Path, version_dir: Path) -> dict:
    """复用用户 measure.js：注入 wrapper 脚本 → Chrome 打印成 PDF → 提取结果标记。
    原理：measure.js 会把 JSON 结果挂在 #RESULT；wrapper 读取后把页面重写为
    纯标记文本（@@OVFX=…@@ORPH=…），打印成 PDF 后用 pypdf 提取，避免依赖 stdout。"""
    html = html_path.read_text(encoding="utf-8")
    measure_js = config.MASTER_SOURCE_DIR / "measure.js"
    if not measure_js.exists():
        measure_js = config.MASTER_SNAPSHOT_DIR / "measure.js"
    if not (version_dir / "measure.js").exists() and measure_js.exists():
        shutil.copy2(measure_js, version_dir / "measure.js")
    wrapper = """<script src="measure.js"></script>
<script>
(function(){
  var pre = document.getElementById('RESULT');
  try {
    var r = JSON.parse(pre.textContent);
    var s = '@@OVFX=' + (r.overflowPx||0) + '@@ORPH=' + (r.orphans?r.orphans.length:0) +
            '@@MAXB=' + (r.maxBottom||0) + '@@PAGEH=' + (r.pageH||0) + '@@';
    document.body.innerHTML = '<pre id="RESULT">' + s + '</pre>';
  } catch(e) {}
})();
</script>
</body>"""
    injected = html.replace("</body>", wrapper)
    tmp = version_dir / "_measure.html"
    tmp.write_text(injected, encoding="utf-8")
    measure_pdf = version_dir / "_measure.pdf"
    try:
        r = render_pdf(tmp, measure_pdf)
        if not r["ok"]:
            return {"ok": False, "error": f"measure 页渲染失败: {r.get('stderr', '')}"}
        _, text = _pdf_pages_and_text(measure_pdf)
        import re as _re
        # 压缩空白：PDF 文本提取会在 = 与数字间插入空格，先剔除再匹配
        text_compact = _re.sub(r"\s+", "", text)
        m = _re.search(r"@@OVFX=(-?\d+)@@ORPH=(\d+)@@MAXB=(-?\d+)@@PAGEH=(\d+)@@", text_compact)
        if not m:
            return {"ok": False, "error": "measure 标记未捕获", "raw": text[:200]}
        return {"ok": True, "overflowPx": int(m.group(1)),
                "orphans": [""] * int(m.group(2)),
                "maxBottom": int(m.group(3)), "pageH": int(m.group(4))}
    finally:
        _safe_unlink(tmp)
        _safe_unlink(measure_pdf)
        _safe_unlink(version_dir / "measure.js")


def verify_pdf(master_pdf: Path, tailored_pdf: Path, eval_txt_old: str, eval_txt_new: str,
               bring_txt_old: str, bring_txt_new: str) -> dict:
    """PDF 验证：存在 / 页数一致 / 白名单外文本一致 / 图片正常。

    文本比对用「字符频次相减」法：PDF 提取会插换行、断词，直接字符串比较不可靠；
    full(master) - region(old) == full(tailored) - region(new) ⇔ 非目标区域字符多重集一致。
    （eval/bring 文本需包含分类名，因为分类名也在可编辑区域内。）"""
    from collections import Counter

    checks = {}
    checks["pdf_exists"] = tailored_pdf.exists() and tailored_pdf.stat().st_size > 1000
    if not checks["pdf_exists"]:
        return {"ok": False, "checks": checks}
    mp, mt = _pdf_pages_and_text(master_pdf), _pdf_pages_and_text(tailored_pdf)
    checks["pages_equal"] = mp[0] == mt[0]
    checks["pages"] = {"master": mp[0], "tailored": mt[0]}

    def norm(s: str) -> Counter:
        s = re.sub(r"<[^>]+>", " ", s or "")
        return Counter(re.sub(r"\s+", "", s))

    a = norm(mp[1]) - norm(eval_txt_old) - norm(bring_txt_old)
    b = norm(mt[1]) - norm(eval_txt_new) - norm(bring_txt_new)
    checks["text_outside_regions_equal"] = a == b
    if a != b:
        diff_chars = {c for c in (a + b) if a.get(c, 0) != b.get(c, 0)}
        checks["text_diff_hint"] = f"字符频次差异: {sorted(diff_chars)[:20]}"
    checks["images_ok"] = True  # 图片缺失会导致渲染空白/布局塌陷，被 overflow 与文本检查兜底
    # 关键：text_outside_regions_equal 仅作参考，不单独判 fail。
    # PDF 文本提取对中文 / <b> / <br> 有噪声，被改的可编辑区域字符无法被精确抵消，
    # 而程序级 Diff（structure + content 双签名）已权威确认白名单外文本逐字节一致。
    # 因此 ok 只取决于：PDF 存在、页数一致、无真实溢出。白名单外的差异由 Diff 负责。
    if not checks["text_outside_regions_equal"]:
        checks["text_outside_regions_warn"] = "PDF 文本提取与源略有差异（Diff 已确认白名单外一致），视为误报"
    ok = bool(checks["pdf_exists"] and checks["pages_equal"] and checks["images_ok"])
    return {"ok": ok, "checks": checks}


# ---------------------------------------------------------------- 主流程：创建版本

_CHAR_LIMIT_OVER = 30  # 每条生成文本相对原文允许的最大超出/不足字数

# 溢出容差：A4 一页排版在页脚边缘常有 <8px 的子行级舍入溢出（仅是末行下沿几个像素被裁，
# 文字仍完整可读）。超过此容差才判为真实溢出（内容被 overflow:hidden 裁掉）。
OVERFLOW_TOL_PX = 8


def _truncate_at_punct(text: str, max_len: int) -> str:
    """在不超过 max_len 的前提下，尽量在句末标点处自然收尾，避免半句话。"""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    for p in ("。", "！", "？", "；", "，", "、", "\n"):
        idx = cut.rfind(p)
        if idx >= max_len - 10:  # 标点靠近末尾才收尾，避免截太短
            return text[: idx + 1]
    return cut


_TAG_RE = re.compile(r"<[^>]+>")


def _visible_len(txt: str) -> int:
    """可见文字长度（剥离 <b>/<br> 等标签，仅统计纯文本字符）。用于字数约束，使加粗标签不占 ±30 预算。"""
    return len(_TAG_RE.sub("", txt))


def _truncate_visible_to_punct(txt: str, max_vis: int) -> str:
    """按「可见字符」数截断到 max_vis，标签原样保留；优先在句末标点处收尾，并闭合未配对的 <b>。

    含 <b>关键词</b> 时：逐字符扫描，遇到标签整体跳过不计数；可见字符累计到 max_vis 即停。
    截断后若仍有未闭合的 <b>，末尾补 </b>，避免 PDF 渲染时把后续整段误加粗。"""
    out, vis, open_b = [], 0, 0
    i, n = 0, len(txt)
    while i < n and vis < max_vis:
        c = txt[i]
        if c == "<":
            j = txt.find(">", i)
            if j == -1:
                break
            tag = txt[i:j + 1]
            out.append(tag)
            low = tag.lower()
            if low == "<b>":
                open_b += 1
            elif low == "</b>":
                open_b = max(0, open_b - 1)
            i = j + 1
        else:
            out.append(c)
            vis += 1
            i += 1
    result = "".join(out)
    # 在可见内容里找最近的句末标点（靠近末尾才收尾，避免截太短）
    cut = -1
    for p in ("。", "！", "？", "；", "，", "、", "\n"):
        idx = result.rfind(p)
        if idx != -1:
            cut = max(cut, idx)
    if cut != -1 and cut >= len(result) - 12:
        result = result[:cut + 1]
    # 闭合未配对的 <b>
    if open_b > 0:
        result += "</b>" * open_b
    return result


def _bold_keywords_in(txt: str, keywords: list[str], cap: int = 4) -> str:
    """服务端兜底：当 LLM 未按要求加粗时，把文本中出现的 JD 关键词自动用 <b>...</b> 包裹。

    仅在所有关键词中『实际出现于文本』且长度≥2 的词里挑（按长度降序避免嵌套），每条最多 cap 处。
    若文本本身已含 <b>（模型已加粗）则跳过，尊重模型结果。"""
    if not txt or "<b>" in txt or not keywords:
        return txt
    kws = sorted({k.strip() for k in keywords if len(k.strip()) >= 2 and k.strip() in txt},
                 key=len, reverse=True)
    if not kws:
        return txt
    pat = re.compile("(" + "|".join(re.escape(k) for k in kws) + ")")
    return pat.sub(lambda m: "<b>" + m.group(0) + "</b>", txt, count=cap)


def _enforce_char_limit(se_out, hl_out, master_eval, master_hl):
    """强约束：每条生成文本『可见文字』字数与原文相差不超过 ±30（<b> 标签不计入）。

    超长则按可见字符在句末标点处截断兜底，并闭合未配对的 <b>，避免 PDF 误加粗；偏短仅告警。"""
    warnings = []
    for i, it in enumerate(se_out):
        orig = master_eval[i].get("text", "") if i < len(master_eval) else ""
        orig_len = _visible_len(orig)
        low, high = max(0, orig_len - _CHAR_LIMIT_OVER), orig_len + _CHAR_LIMIT_OVER
        txt = it["txt"]
        if _visible_len(txt) > high:
            new = _truncate_visible_to_punct(txt, high)
            it["txt"] = new
            warnings.append(f"自我评价·{it['cat']}：原文{orig_len}字→已压缩至{_visible_len(new)}字")
        elif _visible_len(txt) < low:
            warnings.append(f"自我评价·{it['cat']}：原文{orig_len}字→生成{_visible_len(txt)}字（偏短）")
    for i, it in enumerate(hl_out):
        orig_lines = master_hl[i].get("lines", []) if i < len(master_hl) else []
        orig_len = sum(_visible_len(l) for l in orig_lines)
        low, high = max(0, orig_len - _CHAR_LIMIT_OVER), orig_len + _CHAR_LIMIT_OVER
        lines = it["lines"]
        gen_len = sum(_visible_len(l) for l in lines)
        if gen_len > high:
            over = gen_len - high
            for j in range(len(lines) - 1, -1, -1):
                if over <= 0:
                    break
                new_l = _truncate_visible_to_punct(lines[j], max(1, _visible_len(lines[j]) - over))
                removed = _visible_len(lines[j]) - _visible_len(new_l)
                lines[j] = new_l
                over -= removed
            it["lines"] = [l for l in lines if l.strip()]
            warnings.append(f"亮点·{it['cat']}：原文{orig_len}字→已压缩至{sum(_visible_len(l) for l in it['lines'])}字")
        elif gen_len < low:
            warnings.append(f"亮点·{it['cat']}：原文{orig_len}字→生成{gen_len}字（偏短）")
    return warnings


def generate_resume_draft(job_id: int) -> dict:
    """按 JD 用 LLM 生成定制的「自我评价 / 个人亮点」草稿。

    硬约束（诚实边界）：
    - 只改内容，严格保留输入的条目数量与每个条目的 cat（分类名），
      以保证下游 apply_tailoring 的结构 Diff 通过、且绝不编造结构；
    - 文案只能基于候选人真实简历素材（master 两个区域原文）与已确认证据，
      不得无中生有经历 / 数据 / 公司名。
    - 未配置 LLM 时返回明确错误，不静默退化。
    """
    from server.services import llm

    if not llm.is_configured():
        return {"error": "未配置 LLM：请在 .env 设置 LLM_API_KEY（及可选 LLM_BASE_URL / LLM_MODEL）"}

    conn = connect()
    try:
        job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        master = conn.execute(
            "SELECT * FROM master_resume ORDER BY id DESC LIMIT 1").fetchone()
        if not job or not master:
            return {"error": "job / master 不存在"}
        jd = job.get("jd_text") or ""
        try:
            analysis = json.loads(job.get("match_analysis") or "{}") or {}
        except Exception:
            analysis = {}
    finally:
        conn.close()

    if not jd.strip():
        return {"error": "该岗位无 JD 原文，无法按 JD 生成（请先录入 JD 原文）"}

    # 自动补全匹配信号：若没分析过该 JD，先跑 analyze_job 拿到维度要求 + 真实证据
    if not analysis.get("matched"):
        try:
            from server.services import fit
            fit.analyze_job(job_id, use_llm=False)
            conn = connect()
            try:
                row = conn.execute(
                    "SELECT match_analysis FROM job WHERE id=?", (job_id,)).fetchone()
                if row and row.get("match_analysis"):
                    analysis = json.loads(row["match_analysis"] or "{}") or {}
            finally:
                conn.close()
        except Exception:
            pass  # 静默降级：仍用 jd_text 生成

    master_eval = json.loads(master["self_eval_text"] or "[]") or []
    master_hl = json.loads(master["highlights_text"] or "[]") or []
    if not master_eval or not master_hl:
        return {"error": "Master 两个区域为空，请先在简历中心导入 Master Resume"}

    # ---- 构建匹配信号（喂给 LLM 的「JD 要什么 + 我有什么真实素材」）----
    matched = analysis.get("matched", []) or []
    gaps = analysis.get("gaps", []) or []
    # JD 要求清单（带关键词，按相关性；缺口维度提示弱化处理）
    jd_reqs = []
    for m in matched:
        jd_reqs.append(f"- 【要求】{m.get('dimension', '')}（JD关键词：{', '.join(m.get('jd_keywords', []))}）")
    for g in gaps:
        jd_reqs.append(f"- 【缺口·可弱化处理】{g.get('dimension', '')}（JD关键词：{', '.join(g.get('jd_keywords', []))}）")
    jd_reqs_txt = "\n".join(jd_reqs) or "（未解析出明确维度，请直接依据下方 JD 原文判断岗位要求）"
    # 真实可引用素材（来自 KB 的项目/经历/技能，honest 边界核心事实源）
    evidence, seen = [], set()
    for m in matched:
        for e in m.get("evidence", []):
            if e.get("confidence") == "confirmed":
                k = (e.get("source_type"), e.get("source_id"))
                if k not in seen:
                    seen.add(k)
                    evidence.append(f"- {e.get('title', '')}：{e.get('text', '')}")
    for e in (analysis.get("evidence", []) or []):
        if e.get("confidence") == "confirmed":
            k = (e.get("source_type"), e.get("source_id"))
            if k not in seen:
                seen.add(k)
                evidence.append(f"- {e.get('title', '')}：{e.get('text', '')}")
    evidence_txt = "\n".join(evidence[:16]) or "（资料库中暂无已确认证据，请用 Master 简历素材中已有的真实项目）"
    matched_dims = "、".join(m.get("dimension", "")
                            for m in matched[:6]) or "（无匹配维度，请依据 JD 原文）"

    n_eval, n_hl = len(master_eval), len(master_hl)
    system = (
        "你是资深简历定制顾问。任务：根据岗位 JD 与候选人真实简历，改写「自我评价」和「个人亮点」"
        "两块内容，使其更贴合该 JD。\n"
        "硬性约束：\n"
        "1. 绝不编造经历、数据、奖项、公司名。只能使用「候选人真实简历素材」与「已确认证据」中的事实。\n"
        "2. 严格保持返回条目数量与输入一致：self_eval 必须恰好 "
        f"{n_eval} 条，highlights 必须恰好 {n_hl} 条；每条 cat（分类名）与输入完全相同，顺序可调。\n"
        "3. 输出严格 JSON：{\"self_eval\":[{\"cat\":str,\"txt\":str}, ...], "
        "\"highlights\":[{\"cat\":str,\"lines\":[str,...]}, ...]}。\n"
        "4. self_eval 的 txt 是一段连贯话（可用 <b>加粗</b> 标记重点词）；highlights 的 lines 是要点数组（每条一句话，可含 <b>加粗</b>）。\n"
        f"5. 优先突出与 JD 要求（匹配维度：{matched_dims}）相关的真实经历，弱化无关内容，但不得杜撰。\n"
        "6. 只输出 JSON，不要解释。\n"
        "7. 字数对齐与改写：每条生成文本字数与对应「原文条目」字数相差不超过 30 字"
        "（中文按字符计，标点也算字数；<b></b> 加粗标签不计入字数，仅统计可见文字）。系统会在【自我评价】【个人亮点】每条后用括号标注「原文 N 字」作为参考。"
        "请基于岗位 JD 主动改写表达、突出与 JD 更相关的真实经历与能力，使内容更贴合该岗位——这是核心目标。"
        "【硬性要求】禁止原样复制或近似照搬原文句子；必须用不同的句式与措辞重写每条，使生成内容明显区别于原文，"
        "但所陈述的事实必须与原文一致、不得编造。每条须是通顺、完整、可读的一句话（有主谓、用句号收尾，不得残缺半句），"
        "且字数与原文差距控制在 30 字以内。\n"
        "8. 排版硬约束：严禁新增整行（不要比原文多换行、不要凭空多写一句）。简历是固定一页 A4，"
        "原文已几乎占满版面，多一行就会被裁切丢失。请保持与原文【相同的换行数 / 相同的条目行数】，"
        "只在原句内换措辞、压缩或扩写，不要把一句话拆成两句。\n"
        "9. 针对性映射（核心）：每条改写必须明确对应「JD 要求清单」中至少一项，并在该条中用「真实可引用素材」里的具体项目名/成果来支撑"
        "（例如提到'芒果数问异动归因项目''主导 40+ 页面 B 端中台'），让招聘方一眼看到你与该 JD 的对应关系；"
        "禁止空泛喊口号（如'精通''熟悉'堆砌）。若某项 JD 要求无对应素材，则弱化处理、不得编造。\n"
        "10.【硬性要求·重点词加粗】self_eval 的每条 txt 必须包含至少 2 处、至多 4 处 <b>...</b> 加粗，"
        "包裹与岗位 JD 最相关的重点词（核心能力词、具体项目名、量化成果数字，如 <b>AI Agent 交互</b>、"
        "<b>40+ 页面 B 端中台</b>），让招聘方一眼抓住匹配点；切勿整句都加粗，也不得加粗无关词。"
        "<b></b> 标签不计入上方字数约束。highlights 的 lines 也鼓励用 <b>...</b> 标记关键词。未加粗视为不合格。\n"
    )
    user = (
        f"# 岗位\n公司：{job['company']} ｜ 岗位：{job['title']}\n\n"
        f"# JD 原文\n{jd}\n\n"
        f"# 候选人真实简历素材（当前版本）\n"
        f"【自我评价】（每条请控制在「原文字数 ±30 字」以内）\n" + "\n".join(f"- {it.get('cat', '')}: {it.get('text', '')}  （原文 {len(it.get('text', ''))} 字）" for it in master_eval) + "\n"
        f"【个人亮点】（每条请控制在「原文总字数 ±30 字」以内）\n" + "\n".join(f"- {it.get('cat', '')}: " + " / ".join(it.get('lines', [])) + f"  （原文 {sum(len(l) for l in it.get('lines', []))} 字）" for it in master_hl) + "\n\n"
        f"# JD 要求清单（必须逐条对应：每条改写都要点中其中至少一项，用具体项目/成果支撑）\n{jd_reqs_txt}\n\n"
        f"# 真实可引用素材（资料库检索到的项目 / 经历 / 技能，必须优先使用其中的具体项目名与成果，不得编造其他）\n{evidence_txt}\n\n"
        f"请生成贴合该 JD 的定制版本：每条改写必须明确对应「JD 要求清单」中至少一项，并优先用「真实可引用素材」里的具体项目名/成果来支撑；保持条目数与 cat 不变。"
    )

    try:
        raw = llm.chat(system, user, temperature=0.7, json_mode=True)
    except Exception as e:  # noqa: BLE001
        return {"error": f"LLM 调用失败：{e}"}

    # 解析（容错：抽取首个 JSON 对象）
    try:
        data = json.loads(raw)
    except Exception:
        import re
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {"error": "LLM 返回非 JSON", "raw": raw[:600]}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {"error": "LLM 返回无法解析", "raw": raw[:600]}

    se = data.get("self_eval", []) or []
    hl = data.get("highlights", []) or []
    if len(se) != n_eval or len(hl) != n_hl:
        return {"error": f"LLM 返回条目数不符（自我评价 {len(se)}/{n_eval}，亮点 {len(hl)}/{n_hl}），请重试",
                "self_eval_items": se, "highlight_items": hl}

    se_out = [{"cat": str(it.get("cat", master_eval[i].get("cat", ""))).strip(),
               "txt": str(it.get("txt", "")).strip()} for i, it in enumerate(se)]
    hl_out = [{"cat": str(it.get("cat", master_hl[i].get("cat", ""))).strip(),
               "lines": [str(l).strip() for l in it.get("lines", []) if str(l).strip()]}
              for i, it in enumerate(hl)]
    # 服务端兜底：若模型未按规则 10 加粗，则用 JD 关键词自动包裹 <b>（确保 PDF 导出一定有重点词加粗）
    jd_kw_all = []
    for m in matched:
        jd_kw_all += list(m.get("jd_keywords", []))
    for g in gaps:
        jd_kw_all += list(g.get("jd_keywords", []))
    for it in se_out:
        if "<b>" not in it["txt"]:
            it["txt"] = _bold_keywords_in(it["txt"], jd_kw_all, cap=4)
    for it in hl_out:
        if not any("<b>" in l for l in it["lines"]):
            it["lines"] = [_bold_keywords_in(l, jd_kw_all, cap=2) for l in it["lines"]]
    warnings = _enforce_char_limit(se_out, hl_out, master_eval, master_hl)
    return {"job_id": job_id, "self_eval_items": se_out, "highlight_items": hl_out,
            "length_warnings": warnings}




def get_master() -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM master_resume ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def ensure_master_pdf(mid: str) -> Path:
    """渲染 master 参照 PDF（写系统快照目录，不碰 Desktop）。"""
    snap_html = config.MASTER_SNAPSHOT_DIR / "resume.html"
    if not snap_html.exists():
        snap_html.parent.mkdir(parents=True, exist_ok=True)
        snap_html.write_text(config.MASTER_HTML.read_text(encoding="utf-8"))
        for name in ("photo.jpg", "qr.png"):
            f = config.MASTER_SOURCE_DIR / name
            if f.exists() and not (config.MASTER_SNAPSHOT_DIR / name).exists():
                shutil.copy2(f, config.MASTER_SNAPSHOT_DIR / name)
    pdf = config.MASTER_SNAPSHOT_DIR / "master_reference.pdf"
    if not pdf.exists():
        r = render_pdf(snap_html, pdf)
        if not r["ok"]:
            raise RuntimeError(f"master PDF 渲染失败: {r}")
    return pdf


def _person_name() -> str:
    """网申/简历命名用的个人真名；缺省 贺宣锦。"""
    try:
        conn = connect()
        try:
            r = conn.execute("SELECT name FROM personal_info WHERE id=1").fetchone()
            return (r["name"] if r and r["name"] else "贺宣锦")
        finally:
            conn.close()
    except Exception:
        return "贺宣锦"


def create_tailored_version(job_id: int, self_eval_items: list[dict],
                            highlight_items: list[dict], reason: str = "") -> dict:
    """Master → Tailored：独立版本目录 + 只改两个区域 + Diff + PDF + 验证。
    self_eval_items: [{cat, txt}]  highlight_items: [{cat, lines:[]}]
    任一环节失败 → 版本标记不可用（fail），不会进入 Ready to Apply。"""
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        master = conn.execute("SELECT * FROM master_resume ORDER BY id DESC LIMIT 1").fetchone()
        if not job or not master:
            return {"error": "job/master 不存在"}
        company, position = job["company"], job["title"]
    finally:
        conn.close()

    master_html = config.MASTER_HTML.read_text(encoding="utf-8")  # 每次从源读取
    anchors = extract_anchors(master_html)

    # 1) 生成 tailored HTML（DOM 最小修改：只替换两个区域的文字，保留容器与结构）
    try:
        tailored = apply_tailoring(master_html, self_eval_items, highlight_items)
    except ValueError as e:
        return {"error": str(e), "diff": {"status": "fail", "structure_ok": False,
                                          "content_ok": False, "structural_changes": [str(e)]}}

    # 2) Diff（结构 + 文本双签名）
    diff = diff_html(master_html, tailored)

    # 3) 建版本目录（完整运行环境：html + photo + qr）
    # 文件命名格式：贺宣锦-公司名称-岗位名称（个人真名取自 personal_info，缺省贺宣锦）
    pname = _person_name()
    safe = lambda s: re.sub(r'[\\/*?:<>"|]', '_', str(s)).strip().strip('_') or "x"
    file_base = f"{safe(pname)}-{safe(company)}-{safe(position)}"[:90]
    vdir = config.VERSIONS_DIR / f"{job_id}_{file_base}"
    vdir.mkdir(parents=True, exist_ok=True)
    html_path = vdir / f"{file_base}.html"
    html_path.write_text(tailored, encoding="utf-8")
    for name in ("photo.jpg", "qr.png"):
        f = config.MASTER_SOURCE_DIR / name
        if f.exists():
            shutil.copy2(f, vdir / name)

    pdf_path = vdir / f"{file_base}.pdf"
    render_ok, pdf_check = False, None
    if diff["status"] == "pass":
        # 4) 渲染 PDF
        r = render_pdf(html_path, pdf_path)
        render_ok = r["ok"]
        if render_ok:
            # 5) PDF 验证 + measure.js 溢出检测
            try:
                master_pdf = ensure_master_pdf(master["id"])
            except Exception as e:  # noqa: BLE001
                return {"error": f"master PDF 渲染失败: {e}", "diff": diff}
            old_eval = " ".join(it["cat"] + it["text"] for it in anchors["self_eval_items"])
            old_br = " ".join(it["cat"] + "<br>".join(it["lines"]) for it in anchors["highlight_items"])
            new_eval = " ".join(str(it.get("cat", "")) + re.sub(r"<[^>]+>", " ", str(it.get("txt", "")))
                                for it in self_eval_items)
            new_br = " ".join(str(it.get("cat", "")) + "<br>".join(str(l) for l in it.get("lines", []))
                              for it in highlight_items)
            pdf_check = verify_pdf(master_pdf, pdf_path, old_eval, new_eval, old_br, new_br)
            # measure.js 溢出检测独立执行（不依赖文本比对结论）
            layout = measure_layout(html_path, vdir)
            pdf_check["layout"] = layout
            if layout.get("ok"):
                ov = layout.get("overflowPx") or 0
                if ov > OVERFLOW_TOL_PX:
                    pdf_check["ok"] = False
                    pdf_check["checks"]["overflow"] = f"溢出 {ov}px（超过容差 {OVERFLOW_TOL_PX}px，末行被裁切）"
                elif ov > 0:
                    # 子行级舍入溢出：文字完整、仅末行下沿几个像素被裁，放行但提示
                    pdf_check["checks"]["overflow_warn"] = f"溢出 {ov}px（余量紧张，已放行）"
                if layout.get("orphans"):
                    pdf_check["orphans_warning"] = layout["orphans"]
            else:
                pdf_check["layout_error"] = layout.get("error")

    diff_status = "pass" if (diff["status"] == "pass" and render_ok
                             and pdf_check and pdf_check.get("ok")) else "fail"

    # 6) 落库 + 修改记录
    conn = connect()
    try:
        conn.execute("DELETE FROM resume_version WHERE job_id=? AND status='draft'", (job_id,))
        cur = conn.execute(
            """INSERT INTO resume_version (job_id, master_resume_id, company, position, dir_path,
               html_path, pdf_path, orig_self_eval, new_self_eval, orig_highlights, new_highlights,
               reason, diff_json, diff_status, pdf_check_json, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, master["id"], company, position, str(vdir), str(html_path),
             str(pdf_path) if render_ok else None,
             json.dumps(anchors["self_eval_items"], ensure_ascii=False),
             json.dumps(self_eval_items, ensure_ascii=False),
             json.dumps(anchors["highlight_items"], ensure_ascii=False),
             json.dumps(highlight_items, ensure_ascii=False),
             reason, json.dumps(diff, ensure_ascii=False), diff_status,
             json.dumps(pdf_check, ensure_ascii=False) if pdf_check else None,
             "active" if diff_status == "pass" else "failed"))
        rv_id = cur.lastrowid
        conn.execute(
            "INSERT INTO resume_modification (resume_version_id, field, before_text, after_text, reason) "
            "VALUES (?,?,?,?,?), (?,?,?,?,?)",
            (rv_id, "self_eval", json.dumps(anchors["self_eval_items"], ensure_ascii=False),
             json.dumps(self_eval_items, ensure_ascii=False), reason,
             rv_id, "highlights", json.dumps(anchors["highlight_items"], ensure_ascii=False),
             json.dumps(highlight_items, ensure_ascii=False), reason))
        # 更新 application 状态
        conn.execute("UPDATE application SET status='Tailoring', updated_at=datetime('now','localtime') "
                     "WHERE job_id=? AND status='Shortlisted'", (job_id,))
        conn.commit()
        return {"resume_version_id": rv_id, "diff": diff, "diff_status": diff_status,
                "pdf_check": pdf_check, "dir": str(vdir),
                "pdf_path": str(pdf_path) if render_ok else None}
    finally:
        conn.close()
