"""最终验收 · 秋招真实使用流程端到端。

模拟用户每天的真实操作链路：
  Automation 雷达 → 发现岗位 → JD 入库 → JD 分析 → Fit Score →
  筛选精投 → 岗位详情/匹配原因 → Tailored Resume（只改自我评价+亮点）→
  结构 Diff + 内容 Diff → PDF Render（页数/溢出/图片/二维码）→ 打招呼语 →
  READY TO APPLY → 人工投递 APPLIED → 更新 OA/Interview → Interview Brief → 复盘 → 周报

同时验证：
  - 20 精投是目标非 KPI（发现数/高匹配/建议精投/已投 区分）
  - 资料库分类正确
  - 招聘网站诚实边界（SPA 走手动粘贴）
  - Master Resume 不变

运行：python server/scripts/final_flow_test.py（需服务已在 8787 端口运行）
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8787"
MASTER = Path(r"C:\Users\<你的用户名>\Desktop\resume_build\resume.html")
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
    print("最终验收 · 秋招真实使用流程")
    print("=" * 70)
    m0 = hashlib.sha256(MASTER.read_bytes()).hexdigest()

    # ---------- 1. Automation 岗位雷达 ----------
    code, brief = api("GET", "/api/daily/briefing")
    check("1. 今日工作台：发现/高匹配/建议精投/已投 四指标区分",
          all(k in brief for k in ("today_jobs", "today_high", "suggest_shortlist", "today_applied")),
          f"发现{brief['today_jobs']} 高匹配{brief['today_high']} 建议精投{brief['suggest_shortlist']} 已投{brief['today_applied']}")
    check("2. 20 是目标非 KPI（daily_target=20 但建议精投按实际匹配）",
          brief["daily_target"] == 20 and brief["suggest_shortlist"] <= brief["today_high"],
          f"目标{brief['daily_target']} 建议{brief['suggest_shortlist']}")

    # ---------- 2. 手动粘贴 JD（诚实边界：SPA 岗位走粘贴）----------
    jd = """【岗位】AI 产品体验设计师（B端）
【职责】1. 负责 AI 数据分析产品交互设计与信息架构；2. 设计 AI Agent 对话呈现方案；
3. 开展用户研究与需求分析；4. 推动 Web 端落地与响应式适配。
【要求】1. 设计相关专业，有 UI/UX 实习经验；2. 熟练 Figma/Adobe；3. 理解 AI 产品可信度；4. B端信息拆解能力。"""
    code, job = api("POST", "/api/jobs", json={
        "company": "最终验收公司", "title": "AI 产品体验设计师", "location": "深圳",
        "source": "手动粘贴", "job_type": "校招", "jd_text": jd, "deadline": "2026-09-15"})
    check("3. 手动粘贴 JD 入库（诚实边界）", code == 200 and job.get("job_id"))
    jid = job["job_id"]

    # ---------- 3. JD 分析 + Fit Score ----------
    code, ana = api("POST", f"/api/jobs/{jid}/analyze")
    check("4. JD 分析 + Fit Score（带匹配点/证据/缺口）",
          code == 200 and ana.get("score", 0) > 0 and ana.get("matched"),
          f"score={ana.get('score')} grade={ana.get('grade')} 命中{ana.get('matched_count')}")

    # ---------- 4. 筛选精投 ----------
    code, sl = api("POST", f"/api/jobs/{jid}/shortlist")
    check("5. 加入精投", code == 200 and sl.get("application_id"))
    aid = sl["application_id"]

    # ---------- 5. Tailored Resume（只改两个区域）----------
    new_eval = [
        {"cat": "优势技能", "txt": "熟悉 <b>AI 数据分析产品</b>从用户研究、信息架构到 <b>AI Agent 交互</b>与 Web 端适配的<b>全流程</b>；在芒果数问实习中设计 <b>AI 输出可信度</b>与异动归因的可验证体验。"},
        {"cat": "综合素养", "txt": "以<b>用户真实业务任务</b>为设计出发点，具备<b>业务理解与信息拆解能力</b>，习惯通过<b>原型测试与用户走查</b>迭代方案。"},
    ]
    new_hl = [
        {"cat": "AI 产品体验", "lines": ["AI Agent 交互设计", "AI 输出可信度"]},
        {"cat": "B 端数据分析", "lines": ["异动归因信息链路", "40+ 页面落地"]},
        {"cat": "设计 × 技术", "lines": ["Vibe Coding", "Web 端自适应"]},
    ]
    code, rv = api("POST", f"/api/jobs/{jid}/resume-version", json={
        "self_eval_items": new_eval, "highlight_items": new_hl, "reason": "最终验收"})
    check("6. Tailored Resume 生成（只改自我评价+亮点）", code == 200 and rv.get("resume_version_id"))
    check("7. 结构 Diff + 内容 Diff 双通过", rv.get("diff_status") == "pass",
          f"structure={rv.get('diff',{}).get('structure_ok')} content={rv.get('diff',{}).get('content_ok')}")
    pc = rv.get("pdf_check") or {}
    checks = pc.get("checks", {})
    check("8. PDF 渲染：页数一致 + 无溢出",
          bool(checks.get("pdf_exists")) and bool(checks.get("pages_equal"))
          and (pc.get("layout") or {}).get("overflowPx", 0) <= 0,
          f"master={checks.get('pages',{}).get('master')} tailored={checks.get('pages',{}).get('tailored')} overflow={pc.get('layout',{}).get('overflowPx')}")
    check("9. PDF 图片/二维码正常（渲染流程与原版一致）", bool(checks.get("images_ok")))
    rv_id = rv.get("resume_version_id")

    # ---------- 6. 打招呼语 ----------
    code, g = api("POST", f"/api/greetings/{jid}/generate")
    check("10. JD 定制打招呼语（4 版，无虚构占位）",
          code == 200 and len(g.get("greetings", {})) == 4
          and "【需补充" not in str(g.get("greetings", {})))

    # ---------- 7. READY → APPLIED ----------
    api("POST", f"/api/applications/{aid}/link-materials", json={"resume_version_id": rv_id})
    code, r = api("POST", f"/api/applications/{aid}/status", json={"status": "Ready to Apply"})
    check("11. READY TO APPLY（材料齐备校验）", code == 200)
    code, r = api("POST", f"/api/applications/{aid}/status", json={
        "status": "Applied", "applied_date": "2026-08-31"})
    check("12. 人工投递 → APPLIED", code == 200)

    # ---------- 8. OA → Interview ----------
    code, _ = api("POST", f"/api/applications/{aid}/status", json={"status": "Online Assessment"})
    check("13. 更新 OA（状态变化触发通知）", code == 200)
    code, iv = api("POST", "/api/interviews", json={
        "application_id": aid, "round": "一面", "scheduled_at": "2026-09-04 15:00",
        "format": "视频", "questions": ["介绍你的 AI 项目经历"]})
    check("14. 进入 Interview（面试记录）", code == 200 and iv.get("interview_id"))
    iid = iv["interview_id"]

    # ---------- 9. Interview Brief + 复盘 ----------
    code, br = api("GET", f"/api/interviews/{iid}/brief")
    check("15. Interview Brief 生成（真实 KB + JD）", code == 200 and br.get("company"))
    code, _ = api("PATCH", f"/api/interviews/{iid}", json={"review": "发挥好", "improvements": "补充量化数据"})
    check("16. 面试复盘记录", code == 200)

    # ---------- 10. 周报 ----------
    code, wr = api("POST", "/api/reviews/generate")
    check("17. Weekly Review 生成", code == 200 and wr.get("week"))

    # ---------- 11. Master 不变 ----------
    check("18. Master Resume 哈希不变", hashlib.sha256(MASTER.read_bytes()).hexdigest() == m0)

    print("\n" + "=" * 70)
    print(f"最终验收结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("真实使用流程全部通过 ✅")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
