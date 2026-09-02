import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from server import config
from server.services.resume import extract_anchors, apply_tailoring, measure_layout

def main():
    html = config.MASTER_HTML.read_text(encoding="utf-8")
    anchors = extract_anchors(html)
    se0 = [{"cat": it["cat"], "txt": it["text"]} for it in anchors["self_eval_items"]]
    hl0 = [{"cat": it["cat"], "lines": it["lines"]} for it in anchors["highlight_items"]]
    vdir = ROOT / "resume" / "_diag"

    def run(name, se, hl):
        t = apply_tailoring(html, se, hl)
        tmp = vdir / f"_s.html"; tmp.write_text(t, encoding="utf-8")
        r = measure_layout(tmp, vdir)
        print(f"[{name}] overflowPx={r.get('overflowPx')} maxBottom={r.get('maxBottom')}")

    # 场景A：所有亮点每行强制 +1 行（模拟 LLM 把短句扩成完整句，换行变多）
    hlA = []
    for it in anchors["highlight_items"]:
        newlines = []
        for l in it["lines"]:
            newlines.append(l + "，并在实际工作中结合业务目标持续打磨产品细节与交互体验")
        hlA.append({"cat": it["cat"], "lines": newlines})
    run("亮点每行+扩写(多换行)", se0, hlA)

    # 场景B：一条自我评价翻倍长度（极端）
    seB = [dict(it) for it in se0]
    seB[0] = {"cat": seB[0]["cat"], "txt": seB[0]["txt"] + "。" + seB[0]["txt"]}
    run("自我评价首条翻倍", seB, hl0)

if __name__ == "__main__":
    main()
