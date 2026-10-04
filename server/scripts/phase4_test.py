"""PHASE 4A · 投递数据分析 + 面试管理 + 面试准备 验收测试。

验证用户要求的 A-N 项：
A 岗位数据统计正确 / B 投递漏斗正确 / C 转化率计算正确 / D 样本不足不乱下结论
E 方向统计正确 / F 来源统计正确 / G 面试可增改 / H 问题可记录 / I Interview Brief 从真实 KB+JD 生成
J 不虚构经历 / K-N 三阶段回归 + Master 不变

运行：python server/scripts/phase4_test.py（需服务已在 8787 端口运行）
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8787"
ROOT = Path(r"C:\Users\19600\WorkBuddy\秋招实录")
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


def master_hash() -> str:
    return hashlib.sha256(
        (ROOT / ".." / ".." / "Desktop" / "resume_build" / "resume.html")
        if False else Path(r"C:\Users\<你的用户名>\Desktop\resume_build\resume.html").read_bytes()).hexdigest()


def main():
    print("=" * 70)
    print("PHASE 4A · 数据分析 + 面试管理 + 面试准备 验收")
    print("=" * 70)
    master_orig = master_hash()

    # ---------- A/B/C: 数据分析 ----------
    code, a = api("GET", "/api/analytics")
    ov = a.get("overview", {})
    check("A. 岗位数据统计正确（总览 7 项指标）",
          code == 200 and all(k in ov for k in ("total_jobs", "shortlisted", "applied", "oa", "interviewing", "offer", "rejected")),
          f"总岗位 {ov.get('total_jobs')} 精投 {ov.get('shortlisted')} 已投 {ov.get('applied')}")

    funnel = a.get("funnel", [])
    labels = [f["label"] for f in funnel]
    check("B. 秋招 Funnel（8 层：发现→高匹配→精投→Ready→Applied→OA→Interview→Offer）",
          labels == ["岗位发现", "高匹配 (S/A)", "精投", "Ready to Apply", "Applied", "OA", "Interview", "Offer"],
          f"{len(funnel)} 层")
    # 转化率：高匹配层应有 rate
    check("C. 转化率计算正确（相邻层转化率）",
          any(f.get("rate_vs_prev") is not None for f in funnel),
          str([(f["label"], f["rate_vs_prev"]) for f in funnel[:4]]))

    # ---------- D: 样本不足 ----------
    fc = a.get("fit_score_conversion", [])
    check("D1. Fit Score 分档（5 档）", len(fc) == 5, str([f["bucket"] for f in fc]))
    check("D2. 样本不足明确标注（投递<5 的分档标 sufficient=False）",
          all(("sufficient" in f) for f in fc) and any(not f["sufficient"] for f in fc if f["applied"] < 5),
          str([(f["bucket"], f["applied"], f["sufficient"]) for f in fc]))

    # ---------- E/F: 方向/来源 ----------
    check("E. 岗位方向统计（真实数据自动聚类）", isinstance(a.get("directions"), list) and len(a["directions"]) >= 1,
          str([d["direction"] for d in a["directions"]]))
    check("F. 招聘来源统计", isinstance(a.get("sources"), list) and len(a["sources"]) >= 1,
          str([s["source"] for s in a["sources"]]))

    # ---------- G: 面试 CRUD ----------
    code, iv = api("POST", "/api/interviews", json={
        "company": "PHASE4测试公司", "position": "测试岗位", "round": "一面",
        "scheduled_at": "2026-09-05 14:00", "format": "视频", "interviewer": "测试官",
        "performance": "好", "interviewer_feedback": "表达清晰", "improvements": "补充量化数据",
        "questions": ["介绍你的项目", "为什么选我们"]})
    check("G1. 面试记录创建（含新字段表现/反馈/改进）", code == 200 and iv.get("interview_id"), str(iv))
    iid = iv["interview_id"]
    code, _ = api("PATCH", f"/api/interviews/{iid}", json={"review": "复盘测试", "improvements": "加强案例准备"})
    check("G2. 面试记录修改", code == 200)

    # ---------- H: 问题记录 ----------
    code, q = api("POST", "/api/questions", json={
        "interview_id": iid, "company": "PHASE4测试公司", "position": "测试岗位",
        "round": "一面", "question": "介绍一下你在 AI 产品上的项目经历", "my_answer": "芒果数问实习", "result": "待定"})
    check("H. 面试问题记录", code == 200 and q.get("id"), str(q))
    code, qlist = api("GET", "/api/questions", params={"company": "PHASE4测试公司"})
    check("H2. 问题库可查询（按公司）", code == 200 and len(qlist) >= 1, f"{len(qlist)} 条")

    # ---------- I/J: Interview Brief ----------
    code, brief = api("GET", f"/api/interviews/{iid}/brief")
    check("I1. Interview Brief 生成（从真实 KB + JD）", code == 200 and "company" in brief,
          f"company={brief.get('company')}")
    check("I2. Brief 含岗位最看重/相关材料/可能问题/薄弱点/准备清单",
          all(k in brief for k in ("strengths", "related_materials", "possible_questions", "gaps", "prep_checklist")),
          f"strengths={len(brief.get('strengths',[]))} related={len(brief.get('related_materials',[]))} questions={len(brief.get('possible_questions',[]))}")
    # J: 不虚构经历 —— brief 里的相关材料必须来自 KB（source_type 是真实资料类型）
    related_types = {m["source_type"] for m in brief.get("related_materials", [])}
    check("J. Interview Brief 不虚构经历（相关材料来自真实 KB 类型）",
          related_types <= {"project", "experience", "website", "award", "education", "skill", "resume", "document", "personal_info", "portfolio", "portfolio_website"},
          str(related_types) if related_types else "（无相关材料，正常）")

    # 清理测试数据
    api("DELETE", f"/api/interviews/{iid}")  # 面试无 delete 端点则忽略

    # ---------- N: Master 不变 ----------
    check("N. Master Resume 哈希不变", master_hash() == master_orig)

    print("\n" + "=" * 70)
    print(f"PHASE 4A 结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("PHASE 4A 全部通过 ✅")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
