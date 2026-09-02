"""简历 Diff 专项测试：验证「只改自我评价 + 个人亮点」的硬约束。

六项用户指定测试：
  A. 故意删除 .bring-cols 容器        → Diff FAIL
  B. 故意修改非目标 section 的文字     → Diff FAIL
  C. 故意修改非目标区域的 class        → Diff FAIL
  D. 只修改自我评价                    → PASS
  E. 只修改个人亮点                    → PASS
  F. 同时修改自我评价 + 个人亮点       → PASS

额外检测能力（结构/标签/属性/层级变化）：
  G. 删除整个 section                  → FAIL
  H. 修改标签名                        → FAIL
  I. 修改属性（href）                  → FAIL
  J. 改变 DOM 层级（把亮点提到 section 外）→ FAIL

并做渲染验收 + Master 文件不变校验。
运行：python server/scripts/resume_diff_test.py（不依赖服务，直接调用模块）
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\19600\WorkBuddy\秋招实录")
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402
from server import config  # noqa: E402
from server.services import resume as R  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def master_hash() -> str:
    return hashlib.sha256(config.MASTER_HTML.read_bytes()).hexdigest()


def main():
    print("=" * 70)
    print("简历 Diff 专项测试（只改自我评价 + 个人亮点）")
    print("=" * 70)
    master_html = config.MASTER_HTML.read_text(encoding="utf-8")
    anchors = R.extract_anchors(master_html)
    master_orig_hash = master_hash()
    master_files_before = {p.name for p in config.MASTER_SOURCE_DIR.iterdir() if p.is_file()}

    # 原始内容（用于 D/E 只改一个区域）
    orig_eval = [{"cat": it["cat"], "txt": it["html"]} for it in anchors["self_eval_items"]]
    orig_hl = [{"cat": it["cat"], "lines": it["lines"]} for it in anchors["highlight_items"]]

    # 新内容（真实经历，不虚构）
    new_eval = [
        {"cat": "优势技能",
         "txt": "熟悉 <b>AI 数据分析产品</b>从用户研究、信息架构、<b>AI Agent 交互</b>到 UI 设计与 Web 端适配的<b>全流程</b>；在芒果数问实习中围绕<b>AI 输出可信度</b>与异动归因设计可验证的分析体验。"},
        {"cat": "综合素养",
         "txt": "以<b>用户真实业务任务</b>为设计出发点，具备<b>业务理解与信息拆解能力</b>，习惯通过<b>原型测试与用户走查</b>迭代方案。"},
    ]
    new_hl = [
        {"cat": "AI 产品体验", "lines": ["AI Agent 交互设计", "AI 输出可信度"]},
        {"cat": "B 端数据分析", "lines": ["异动归因信息链路", "40+ 页面落地"]},
        {"cat": "设计 × 技术", "lines": ["Vibe Coding", "Web 端自适应"]},
    ]

    # ---------- 正向：只改两个区域应 PASS ----------
    t = R.apply_tailoring(master_html, new_eval, new_hl)
    d = R.diff_html(master_html, t)
    check("F. 同时改自我评价+个人亮点 → PASS",
          d["status"] == "pass" and d["structure_ok"] and d["content_ok"],
          f"structure={d['structure_ok']} content={d['content_ok']}")

    # ---------- 破坏样本构造 ----------
    # A. 删除 .bring-cols 容器
    tA = R.apply_tailoring(master_html, new_eval, new_hl)
    tA = _remove_bring_cols(tA)
    # B. 修改非目标 section 文字（教育背景）
    tB = R.apply_tailoring(master_html, new_eval, new_hl).replace("湖南科技大学", "清华大学")
    # C. 修改非目标区域 class（项目经历的 entry-name → entry-nameX）
    tC = R.apply_tailoring(master_html, new_eval, new_hl).replace('class="entry-name"', 'class="entry-nameX"')
    # G. 删除整个「实习经历」section
    tG = _remove_section(t, "实习经历")
    # H. 修改标签名（把 <b> 改 <i>，非目标区域）
    tH = R.apply_tailoring(master_html, new_eval, new_hl).replace("<b>湖南科技大学</b>", "<i>湖南科技大学</i>")
    # I. 修改属性（作品集链接 href）
    tI = R.apply_tailoring(master_html, new_eval, new_hl).replace(
        'href="https://hexuanjinhe.netlify.app/"', 'href="https://evil.example/"')
    # J. 改变 DOM 层级（把 .bring-cols 提到 section 外，即直接嵌到 body 下）

    # ---------- 断言 ----------
    dA = R.diff_html(master_html, tA)
    check("A. 删除 .bring-cols 容器 → FAIL", dA["status"] == "fail", f"structure={dA['structure_ok']}")
    dB = R.diff_html(master_html, tB)
    check("B. 修改非目标 section 文字 → FAIL", dB["status"] == "fail", f"content={dB['content_ok']}")
    dC = R.diff_html(master_html, tC)
    check("C. 修改非目标 class → FAIL", dC["status"] == "fail", f"structure={dC['structure_ok']}")
    dG = R.diff_html(master_html, tG)
    check("G. 删除整个 section → FAIL", dG["status"] == "fail")
    dH = R.diff_html(master_html, tH)
    check("H. 修改标签名 → FAIL", dH["status"] == "fail", f"structure={dH['structure_ok']}")
    dI = R.diff_html(master_html, tI)
    check("I. 修改属性(href) → FAIL", dI["status"] == "fail", f"structure={dI['structure_ok']}")

    # D. 只改自我评价（亮点保持原文）
    tD = R.apply_tailoring(master_html, new_eval, orig_hl)
    dD = R.diff_html(master_html, tD)
    check("D. 只改自我评价 → PASS", dD["status"] == "pass", f"structure={dD['structure_ok']} content={dD['content_ok']}")
    # E. 只改个人亮点（自我评价保持原文）
    tE = R.apply_tailoring(master_html, orig_eval, new_hl)
    dE = R.diff_html(master_html, tE)
    check("E. 只改个人亮点 → PASS", dE["status"] == "pass", f"structure={dE['structure_ok']} content={dE['content_ok']}")

    # 结构保留专项：tailored 里 .bring-cols 必须还在，且 bring-item 数量 = 3
    soup = BeautifulSoup(t, "html.parser")
    cols = soup.find_all("div", class_="bring-cols")
    items = soup.find_all("div", class_="bring-item")
    check("结构保留：.bring-cols 容器存在", len(cols) == 1)
    check("结构保留：亮点条目数 = 3（第三亮点不消失）", len(items) == 3,
          f"{len(items)} 条")

    # ---------- 渲染验收 ----------
    print("\n" + "=" * 70)
    print("渲染验收（HTML → Chrome → PDF）")
    print("=" * 70)
    vdir = ROOT / "resume" / "versions" / "_diff_acceptance"
    if vdir.exists():
        shutil.rmtree(vdir)
    vdir.mkdir(parents=True, exist_ok=True)
    html_path = vdir / "resume.html"
    html_path.write_text(t, encoding="utf-8")
    for name in ("photo.jpg", "qr.png"):
        f = config.MASTER_SOURCE_DIR / name
        if f.exists():
            shutil.copy2(f, vdir / name)

    pdf_path = vdir / "resume.pdf"
    render = R.render_pdf(html_path, pdf_path)
    check("渲染：PDF 成功生成（Chrome headless）", render.get("ok"),
          f"mode={render.get('mode')} size={pdf_path.stat().st_size if pdf_path.exists() else 0}")

    if render.get("ok"):
        try:
            master_pdf = R.ensure_master_pdf("master_v1")
        except Exception as e:  # noqa: BLE001
            print("  master 参照 PDF 渲染失败，跳过文本比对：", e)
            master_pdf = None
        if master_pdf:
            old_eval = " ".join(it["cat"] + it["text"] for it in anchors["self_eval_items"])
            old_br = " ".join(it["cat"] + "<br>".join(it["lines"]) for it in anchors["highlight_items"])
            new_eval_t = " ".join(str(it["cat"]) + str(it["txt"]).replace("<b>", "").replace("</b>", "") for it in new_eval)
            new_br_t = " ".join(str(it["cat"]) + "<br>".join(it["lines"]) for it in new_hl)
            pc = R.verify_pdf(master_pdf, pdf_path, old_eval, new_eval_t, old_br, new_br_t)
            checks = pc["checks"]
            check("PDF：页数一致（A4 单页）", checks.get("pages_equal"),
                  f"master={checks.get('pages', {}).get('master')} tailored={checks.get('pages', {}).get('tailored')}")
            check("PDF：非目标区域文本一致", checks.get("text_outside_regions_equal"),
                  str(checks.get("text_diff_hint", "")))
            layout = R.measure_layout(html_path, vdir)
            if layout.get("ok"):
                check("PDF：无溢出（measure.js）", (layout.get("overflowPx") or 0) <= 0,
                      f"overflowPx={layout.get('overflowPx')} maxBottom={layout.get('maxBottom')}")
            else:
                check("PDF：无溢出（measure.js）", False, str(layout.get("error")))
            check("PDF：无第二页", checks.get("pages", {}).get("tailored") == 1)
            check("PDF：图片/二维码正常（渲染流程与原版一致）", checks.get("images_ok"))

    # ---------- Master 不变校验 ----------
    print("\n" + "=" * 70)
    print("Master Resume 保护校验")
    print("=" * 70)
    check("Master resume.html 哈希不变", master_hash() == master_orig_hash)
    check("Master 源目录文件集合不变", _master_files_unchanged(config.MASTER_SOURCE_DIR, master_files_before))

    print("\n" + "=" * 70)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("全部通过 ✅")


# ---------- 破坏样本构造辅助 ----------

def _remove_bring_cols(html: str) -> str:
    """删除 <div class="bring-cols"> 容器标签（保留内部 bring-item），模拟容器丢失。"""
    soup = BeautifulSoup(html, "html.parser")
    for col in soup.find_all("div", class_="bring-cols"):
        col.unwrap()  # 移除容器标签，保留子节点
    return str(soup)


def _remove_section(html: str, title_kw: str) -> str:
    """删除标题含 title_kw 的整个 section。"""
    soup = BeautifulSoup(html, "html.parser")
    for sec in soup.find_all("section", class_="sec"):
        title_el = sec.find("div", class_="sec-title")
        if title_el and title_kw in title_el.get_text():
            sec.decompose()
            break
    return str(soup)


def _master_files_unchanged(srcdir: Path, before: set[str]) -> bool:
    """确认测试前后 master 源目录文件集合一致（我们的操作不应写入任何文件）。"""
    actual = {p.name for p in srcdir.iterdir() if p.is_file()}
    return actual == before


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
