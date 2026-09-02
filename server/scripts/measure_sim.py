"""模拟：把 Master 的自我评价/亮点文本各加 ~+25 字（典型 LLM 改写），看是否溢出。
用于验证「改写后溢出」是否因 master 本身余量太小（仅 27px）。
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from server import config
from server.services.resume import extract_anchors, apply_tailoring, measure_layout

def extend(s, n=25):
    # 在句末补一个不换行、自然的中文短语，模拟 LLM 扩写
    pad = "（并在项目中持续打磨细节与用户体验）"
    return (s + pad)[:len(s)+n] if len(s) < 200 else s + pad

def main():
    html = config.MASTER_HTML.read_text(encoding="utf-8")
    anchors = extract_anchors(html)
    # 场景1：原文不动（master 本身）
    se0 = [{"cat": it["cat"], "txt": it["text"]} for it in anchors["self_eval_items"]]
    hl0 = [{"cat": it["cat"], "lines": it["lines"]} for it in anchors["highlight_items"]]
    # 场景2：每条 +25 字
    se1 = [{"cat": it["cat"], "txt": extend(it["text"])} for it in anchors["self_eval_items"]]
    hl1 = [{"cat": it["cat"], "lines": [extend(l) for l in it["lines"]]} for it in anchors["highlight_items"]]

    vdir = ROOT / "resume" / "_diag"
    for name, se, hl in [("master原文", se0, hl0), ("每条+25字", se1, hl1)]:
        t = apply_tailoring(html, se, hl)
        tmp = vdir / f"_sim_{name}.html"
        tmp.write_text(t, encoding="utf-8")
        r = measure_layout(tmp, vdir)
        print(f"[{name}] overflowPx={r.get('overflowPx')} pageH={r.get('pageH')} maxBottom={r.get('maxBottom')} orphans={len(r.get('orphans') or [])}")

if __name__ == "__main__":
    main()
