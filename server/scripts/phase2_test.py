"""PHASE 2 · 岗位雷达闭环验收测试。

链路：
  岗位来源池（Edge 收藏夹）→ 真实抓取 JD → 写入 jobs.db → JD 分析 →
  KB 匹配 → Fit Score → A/B/C/D 分级 → 精投候选 → Tailored Resume →
  Structural Diff → PDF Render → 打招呼语 → READY TO APPLY → 人工确认 → APPLIED

真实来源：
  1. 广东人才网 gdrc.com（服务端渲染，可抓真实完整 JD）——「设计工程师」岗位
  2. 手动粘贴 JD（一等公民路径，用户明确支持）

运行：python server/scripts/phase2_test.py（需服务已在 8787 端口运行）
"""
from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8787"
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def api(method: str, path: str, **kw):
    r = httpx.request(method, BASE + path, timeout=180, **kw)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


def main():
    print("=" * 70)
    print("PHASE 2 · 岗位雷达 + 精投 + 投递 闭环验收")
    print("=" * 70)

    # ---------- 1. 来源池 ----------
    code, srcs = api("GET", "/api/sources")
    check("1. 岗位来源池（Edge 收藏夹已导入）", code == 200 and len(srcs) >= 20,
          f"{len(srcs)} 个来源")
    officials = [s for s in srcs if s["source_type"] == "official"]
    check("2. 官方招聘官网优先级最高（priority=9）",
          bool(officials) and all(s["priority"] == 9 for s in officials),
          f"{len(officials)} 个官方站")

    # ---------- 2. 真实抓取 JD（广东人才网，服务端渲染）----------
    gdrc_jd_url = "https://www.gdrc.com/index.php?m=&c=jobs&a=jobs_show&id=140505"
    code, fetched = api("POST", "/api/radar/fetch-url", json={"url": gdrc_jd_url})
    jd_text = fetched.get("jd_text", "")
    check("3. 真实抓取 JD（广东人才网·设计工程师）", code == 200 and len(jd_text) > 500,
          f"{len(jd_text)} 字")

    # ---------- 3. 写入 jobs.db（含来源追溯）----------
    code, job1 = api("POST", "/api/jobs", json={
        "company": "广东新粤交通投资有限公司", "title": "设计工程师",
        "location": "广东/广州", "url": gdrc_jd_url,
        "source": "广东人才网", "source_url": "https://www.gdrc.com/",
        "job_type": "校招", "jd_text": jd_text})
    check("4. 岗位入库（含 source_url/job_type 追溯）", code == 200 and job1.get("job_id"),
          f"direction={job1.get('direction')}")
    jid1 = job1["job_id"]

    # 去重验证：同岗位再次添加应被拦截，且追加来源 URL
    code, dup = api("POST", "/api/jobs", json={
        "company": "广东新粤交通投资有限公司", "title": "设计工程师",
        "url": "https://www.gdrc.com/index.php?m=&c=jobs&a=jobs_show&id=140505",
        "source": "广东人才网"})
    check("5. 岗位去重（同岗位拦截）", dup.get("error") == "duplicate", f"job_id={dup.get('job_id')}")

    # ---------- 4. JD 分析 + Fit Score + 分级 ----------
    code, ana1 = api("POST", f"/api/jobs/{jid1}/analyze")
    check("6. JD 分析 + Fit Score", code == 200 and ana1.get("score", 0) > 0,
          f"score={ana1.get('score')} grade={ana1.get('grade')}")

    # ---------- 5. 手动粘贴匹配 JD（真实来源，一等公民）----------
    matched_jd = """【岗位】AI 产品体验设计师（B端方向）
【职责】
1. 负责 AI 数据分析产品的交互设计与信息架构，将复杂分析结论转化为可理解、可验证的产品体验；
2. 与产品、算法、前端协作，设计 AI Agent 对话与推理结果的呈现方案；
3. 深入 B 端业务场景，开展用户研究与需求分析，输出设计规范；
4. 推动 Web 端设计落地与响应式适配，参与设计走查与可用性测试。
【要求】
1. 本科及以上，设计相关专业，有 UI/UX 实习或项目经验；
2. 熟练使用 Figma、Adobe 等设计工具，具备 prototyping 能力；
3. 对 AI 产品、大模型应用有理解，关注 AI 输出的可信度与可解释性；
4. 具备 B 端复杂业务的信息拆解能力，有数据可视化经验优先。"""
    code, job2 = api("POST", "/api/jobs", json={
        "company": "云枢智能", "title": "AI 产品体验设计师",
        "location": "深圳", "source": "手动粘贴", "job_type": "校招",
        "jd_text": matched_jd})
    check("7. 手动粘贴匹配 JD 入库", code == 200 and job2.get("job_id"))
    jid2 = job2["job_id"]
    code, ana2 = api("POST", f"/api/jobs/{jid2}/analyze")
    check("8. 匹配岗位 Fit Score（应 A 级）", code == 200 and ana2.get("grade") in ("S", "A"),
          f"score={ana2.get('score')} grade={ana2.get('grade')}")

    # ---------- 6. 分级候选 ----------
    code, cand = api("GET", "/api/radar/candidates")
    check("9. 今日精投候选分级（A/B/C/D）", code == 200 and "A" in cand and "B" in cand,
          f"A={cand.get('strong')} B={cand.get('medium')} C={cand.get('weak')} D过滤={cand.get('D_filtered')}")

    # ---------- 7. 精投 → Tailored → Diff → PDF → 打招呼语 ----------
    code, sl = api("POST", f"/api/jobs/{jid2}/shortlist")
    check("10. 加入精投", code == 200 and sl.get("application_id"))
    aid = sl["application_id"]

    new_eval = [
        {"cat": "优势技能",
         "txt": "熟悉 <b>AI 数据分析产品</b>从用户研究、信息架构、<b>AI Agent 交互</b>到 UI 设计与 Web 端适配的<b>全流程</b>；在芒果数问实习中围绕<b>AI 输出可信度</b>与异动归因设计可验证的分析体验；擅长 <b>B 端复杂信息</b>结构化拆解。"},
        {"cat": "综合素养",
         "txt": "以<b>用户真实业务任务</b>为设计出发点，具备<b>业务理解与信息拆解能力</b>，习惯通过<b>原型测试与用户走查</b>迭代方案；与产品及前端跨角色协作推进 40+ 页面落地。"},
    ]
    new_hl = [
        {"cat": "AI 产品体验", "lines": ["AI Agent 交互设计", "AI 输出可信度"]},
        {"cat": "B 端数据分析", "lines": ["异动归因信息链路", "40+ 页面落地"]},
        {"cat": "设计 × 技术", "lines": ["Vibe Coding", "Web 端自适应"]},
    ]
    code, rv = api("POST", f"/api/jobs/{jid2}/resume-version", json={
        "self_eval_items": new_eval, "highlight_items": new_hl,
        "reason": "按 JD 强调 AI 输出可信度、B 端数据分析与 Web 落地"})
    check("11. Tailored 版本生成（只改自我评价+亮点）", code == 200 and rv.get("resume_version_id"))
    check("12. 结构 + 内容 Diff 通过", rv.get("diff_status") == "pass",
          f"structure={rv.get('diff',{}).get('structure_ok')} content={rv.get('diff',{}).get('content_ok')}")
    pc = rv.get("pdf_check") or {}
    checks = pc.get("checks", {})
    check("13. PDF 渲染 + 页数一致 + 无溢出",
          bool(checks.get("pdf_exists")) and bool(checks.get("pages_equal"))
          and (pc.get("layout") or {}).get("overflowPx", 0) <= 0,
          f"master={checks.get('pages',{}).get('master')} overflow={pc.get('layout',{}).get('overflowPx')}")
    rv_id = rv.get("resume_version_id")

    code, g = api("POST", f"/api/greetings/{jid2}/generate")
    check("14. JD 定制打招呼语（4 版）", code == 200 and len(g.get("greetings", {})) == 4)

    # ---------- 8. Ready → 人工确认 → Applied ----------
    code, _ = api("POST", f"/api/applications/{aid}/link-materials", json={"resume_version_id": rv_id})
    check("15. 简历版本挂载（Diff pass 才允许）", code == 200)
    code, _ = api("POST", f"/api/applications/{aid}/status", json={"status": "Ready to Apply"})
    check("16. READY TO APPLY", code == 200)
    code, _ = api("POST", f"/api/applications/{aid}/status", json={
        "status": "Applied", "applied_date": "2026-08-31"})
    check("17. 人工确认 → APPLIED", code == 200)

    print("\n" + "=" * 70)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("PHASE 2 闭环全部通过 ✅")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
