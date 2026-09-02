"""Interview Brief 面试准备简报。

核心原则（与简历同等重要）：
- 所有内容来自 Candidate KB + Master Resume + Portfolio + Awards + Projects + 真实投递记录 + 真实 JD；
- 找不到证据的维度明确标注【资料不足】，绝不为了"完整答案"编造经历；
- 只汇总和检索，不做事实性杜撰。
"""
from __future__ import annotations

import json

from server.db import connect, search_kb

# 面试简报维度 → 检索词（用于从 KB 找真实证据）
BRIEF_QUERIES = {
    "AI产品/Agent": ["AI Agent 交互", "AI 输出可信度", "异动归因", "洞察分析师"],
    "B端/复杂业务": ["B端", "复杂业务", "数据管理部", "40+ 页面", "数据分析"],
    "用户研究": ["用户访谈", "原型测试", "用户走查", "10 位用户"],
    "交互/信息架构": ["信息架构", "信息链路", "交互设计", "信息层级"],
    "视觉/UI": ["UI/UX", "视觉传达", "界面设计", "Figma", "Adobe"],
    "Web/前端实现": ["Vibe Coding", "Web 端", "网页作品集", "netlify"],
}


def _kb_evidence(query: str, limit: int = 4) -> list[dict]:
    out, seen = [], set()
    for r in search_kb(query, 5):
        key = (r["source_type"], r["source_id"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"source_type": r["source_type"], "source_id": r["source_id"],
                    "title": r["title"], "quote": (r["text"] or "")[:120],
                    "confidence": r["confidence"]})
        if len(out) >= limit:
            break
    return out


def build_interview_brief(interview_id: int) -> dict:
    """从面试记录 + JD + KB 生成面试准备简报。"""
    conn = connect()
    try:
        iv = conn.execute("SELECT * FROM interview WHERE id=?", (interview_id,)).fetchone()
        if not iv:
            return {"error": "interview not found"}
        job = None
        if iv["application_id"]:
            row = conn.execute(
                """SELECT j.* FROM application a JOIN job j ON j.id=a.job_id
                   WHERE a.id=?""", (iv["application_id"],)).fetchone()
            job = dict(row) if row else None
        # 历史面试记录（同公司）
        prev_interviews = [dict(r) for r in conn.execute(
            "SELECT * FROM interview WHERE company=? AND id != ? ORDER BY scheduled_at DESC LIMIT 5",
            (iv["company"], interview_id))]
        # 历史问题库（同公司/相似岗位）
        prev_questions = [dict(r) for r in conn.execute(
            "SELECT * FROM interview_question WHERE company=? ORDER BY id DESC LIMIT 20",
            (iv["company"],))]
    finally:
        conn.close()

    jd = (job or {}).get("jd_text", "") or ""
    match_analysis = json.loads((job or {}).get("match_analysis") or "null") if job else None
    matched = (match_analysis or {}).get("matched", []) if match_analysis else []

    # 1) 岗位最看重什么：从 JD 里已匹配的维度 + 证据
    strengths = []
    for m in matched:
        ev = m.get("evidence", [])[:3]
        strengths.append({
            "dimension": m["dimension"],
            "evidence": [{"title": e["title"], "source_type": e["source_type"],
                          "confidence": e["confidence"]} for e in ev],
        })

    # 2) 我的相关项目/经历（检索 KB）
    related = []
    for q in ["AI Agent 交互", "异动归因", "用户访谈", "信息架构", "B端", "Vibe Coding"]:
        for e in _kb_evidence(q, 3):
            if e["source_id"] not in [x["source_id"] for x in related] and e["source_type"] in ("project", "experience", "website", "award"):
                related.append(e)

    # 3) 可能被问到的问题（从 JD 维度推导 + 历史问题库）
    possible_questions = _derive_questions(jd, matched, prev_questions)

    # 4) 薄弱点（JD 要求但 KB 无证据的维度）
    gaps = []
    if match_analysis:
        for g in match_analysis.get("gaps", []):
            gaps.append({"dimension": g["dimension"], "note": g.get("note", "资料不足")})

    # 5) 面试前准备
    prep = []
    if job and job.get("deadline"):
        prep.append(f"确认岗位截止日期：{job['deadline']}")
    prep.append("重看该岗位 Tailored Resume 的自我评价与个人亮点，确保口述一致")
    if gaps:
        prep.append("重点准备薄弱维度：" + "、".join(g["dimension"] for g in gaps))
    prep.append("准备 1-2 个能体现「AI 产品 / B 端复杂业务」的真实项目故事（芒果数问实习、AI 实习猎手）")

    return {
        "interview_id": interview_id, "company": iv["company"], "position": iv["position"],
        "round": iv["round"], "scheduled_at": iv["scheduled_at"],
        "job": {"company": (job or {}).get("company"), "title": (job or {}).get("title"),
                "fit_score": (job or {}).get("fit_score"), "grade": (job or {}).get("grade"),
                "jd_text": jd[:2000]} if job else None,
        "strengths": strengths, "related_materials": related,
        "possible_questions": possible_questions, "gaps": gaps,
        "prep_checklist": prep, "prev_interviews": prev_interviews,
        "prev_questions": prev_questions,
        "data_sufficiency": "insufficient" if not strengths and not related else "ok",
    }


def _derive_questions(jd: str, matched: list[dict], prev_questions: list[dict]) -> list[dict]:
    """从 JD 维度 + 历史问题库推导可能被问的问题。"""
    out = []
    # JD 维度 → 通用问题模板（引导性问题，答案仍需用户基于真实经历作答）
    for m in matched:
        dim = m["dimension"]
        tpl = {
            "AI产品/Agent": "介绍一下你在 AI 产品 / Agent 交互上的真实项目经历。",
            "B端/复杂业务": "举一个你处理 B 端复杂业务的例子，说说你是怎么做信息拆解的。",
            "用户研究": "讲讲你做用户访谈 / 可用性测试的方法和结论。",
            "交互/信息架构": "描述一个你负责的信息架构设计，为什么这么组织。",
            "视觉/UI": "你的视觉设计流程是怎样的？",
            "Web/前端实现": "你的 Web 端落地或 Vibe Coding 实践有哪些？",
        }.get(dim)
        if tpl:
            out.append({"question": tpl, "dimension": dim, "source": "JD 维度推导"})
    # 历史真实问题（标注"你以前遇到过"）
    seen_q = set()
    for q in prev_questions:
        key = (q.get("question") or "")[:20]
        if key and key not in seen_q:
            seen_q.add(key)
            out.append({"question": q["question"], "dimension": q.get("round", ""),
                        "source": f"历史真实问题 · {q.get('round','')}", "prev": True})
    return out


# ---------------------------------------------------------------- 面试问题库 CRUD

def add_question(interview_id: int | None, company: str, position: str, round_: str,
                 question: str, my_answer: str = "", result: str = "",
                 review: str = "", tags: list[str] | None = None) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO interview_question (interview_id, company, position, round, question, "
            "my_answer, result, review, tags) VALUES (?,?,?,?,?,?,?,?,?)",
            (interview_id, company, position, round_, question, my_answer, result, review,
             json.dumps(tags or [], ensure_ascii=False)))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_questions(company: str = "", limit: int = 100) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM interview_question"
        args = []
        if company:
            sql += " WHERE company=?"
            args.append(company)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in conn.execute(sql, args)]
        for r in rows:
            try:
                r["tags"] = json.loads(r["tags"] or "[]")
            except Exception:  # noqa: BLE001
                r["tags"] = []
        return rows
    finally:
        conn.close()


def similar_questions(question: str, limit: int = 5) -> list[dict]:
    """基于关键词匹配历史相似问题（提示"这个问题你以前遇到过"）。"""
    if not question:
        return []
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM interview_question ORDER BY id DESC LIMIT 500")]
    finally:
        conn.close()
    # 简单关键词重叠打分
    qset = set(question)
    scored = []
    for r in rows:
        rset = set(r["question"] or "")
        if not rset:
            continue
        overlap = len(qset & rset) / max(len(rset), 1)
        if overlap >= 0.5:
            scored.append((overlap, r))
    scored.sort(key=lambda x: -x[0])
    return [dict(r) for _, r in scored[:limit]]
