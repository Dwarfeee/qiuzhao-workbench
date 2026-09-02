"""JD 定制打招呼语生成。

两种模式：
1. LLM 模式（推荐，已配置 LLM 时）：结合岗位 JD + 候选人真实简历素材 + 已确认证据，
   调用大模型生成 4 版自然、贴合、像真人说的话的打招呼语。不编造经历/数据。
2. 模板模式（兜底，未配置 LLM 时）：基于 confirmed 证据槽位填充。

铁律：每个事实断言必须来自「候选人真实简历素材」或「已确认证据」；证据不足的维度不硬编，
可泛泛带过或留白。深度润色由 WorkBuddy Agent 会话完成并写回同一张表。
"""
from __future__ import annotations

import json

from server.db import connect


def _top_evidence(analysis: dict, n=3) -> list[dict]:
    ev = []
    for m in analysis.get("matched", []):
        for e in m.get("evidence", []):
            if e.get("confidence") == "confirmed":
                ev.append({"dim": m["dimension"], **e})
    # 去重
    seen, out = set(), []
    for e in ev:
        k = (e.get("source_type"), e.get("source_id"))
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out[:n]


def _portfolio_url() -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT url FROM portfolio_website ORDER BY id LIMIT 1").fetchone()
        return row["url"] if row else ""
    finally:
        conn.close()


def _candidate_name() -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT name FROM personal_info LIMIT 1").fetchone()
        return row["name"] if row else ""
    finally:
        conn.close()


def _master_material() -> str:
    """读取 Master 简历的两个可编辑区域原文，作为 LLM 的「真实简历素材」上下文。"""
    conn = connect()
    try:
        row = conn.execute("SELECT self_eval_text, highlights_text FROM master_resume ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return ""
        se = json.loads(row["self_eval_text"] or "[]") or []
        hl = json.loads(row["highlights_text"] or "[]") or []
        parts = []
        for it in se:
            parts.append(f"【自我评价·{it.get('cat', '')}】{it.get('text', '')}")
        for it in hl:
            lines = it.get("lines", [])
            parts.append(f"【个人亮点·{it.get('cat', '')}】" + " / ".join(lines))
        return "\n".join(parts)
    finally:
        conn.close()


def generate_greetings(job_id: int) -> dict:
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        if not job:
            return {"error": "job not found"}
        analysis = json.loads(job["match_analysis"] or "{}")
        company, title = job["company"], job["title"]
        jd = (job.get("jd_text") or "").strip()
    finally:
        conn.close()

    if not jd:
        return {"error": "请先录入该岗位的 JD 原文（招呼语需要结合 JD 生成）"}

    ev = _top_evidence(analysis)
    evidence_json = json.dumps(ev, ensure_ascii=False)
    purl = _portfolio_url() or "（未录入作品集网址，可留空或填自己的作品集链接）"
    cname = _candidate_name() or "我"
    master_material = _master_material()

    # ---------- LLM 模式 ----------
    from server.services import llm
    if llm.is_configured():
        try:
            texts = _llm_generate(job_id, company, title, jd, master_material, ev, purl, cname)
            if texts:
                return _persist(job_id, texts, evidence_json)
        except Exception as e:  # noqa: BLE001
            # LLM 失败不致命，回退模板
            pass

    # ---------- 模板模式（兜底） ----------
    texts = _template_generate(company, title, analysis, ev, purl)
    return _persist(job_id, texts, evidence_json)


def _llm_generate(job_id, company, title, jd, master_material, ev, purl, cname) -> dict | None:
    from server.services import llm
    ev_block = "\n".join(f"- {e.get('title', '')}：{e.get('text', '')}" for e in ev) or "（暂无已确认证据，请用下方真实简历素材中确实有的经历）"
    system = (
        "你是资深求职顾问，擅长写自然、真诚、不套路的招聘平台/HR 打招呼语。\n"
        "任务：根据岗位 JD 与候选人真实简历，生成 4 版打招呼语，让 HR/用人方一眼觉得「这人认真看过 JD、且真的匹配」。\n"
        "硬性约束：\n"
        "1. 绝不编造经历、数据、奖项、公司名、项目名。只能使用【候选人真实简历素材】与【已确认证据】里的真实事实。\n"
        "2. 严格输出 JSON：{\"platform_short\":str,\"hr_chat\":str,\"apply_note\":str,\"portfolio_intro\":str}。\n"
        "3. 四版要求：\n"
        "   - platform_short：招聘平台站內短消息，≤100 字，具体、克制、有真人感，像在平台私信里说的话。\n"
        "   - hr_chat：像加完 HR 好友后的私聊，自然口语化，提到 1~2 个与 JD 最相关的真实经历/能力，可坦诚说明薄弱项正在补。\n"
        "   - apply_note：写给自己看的投递备注，列清「JD 要求 → 我的对应经历 → 作品集」，便于投递前快速核对。\n"
        "   - portfolio_intro：随作品集附的简短引语，点出作品集中与该岗位最相关的 1~2 个案例。\n"
        "4. 必须结合 JD 的具体要求来写（点名 JD 里的关键词/职责），不要写放之四海皆准的空话。\n"
        "5. 只输出 JSON，不要解释。\n"
    )
    user = (
        f"# 岗位\n公司：{company} ｜ 岗位：{title}\n\n"
        f"# JD 原文\n{jd}\n\n"
        f"# 候选人真实简历素材（仅可用这些真实事实）\n{master_material}\n\n"
        f"# 已确认证据（资料库，优先引用）\n{ev_block}\n\n"
        f"# 其他信息\n称呼：{cname} ｜ 作品集：{purl}\n\n"
        f"请生成 4 版贴合该 JD 的打招呼语（严格 JSON）。"
    )
    raw = llm.chat(system, user, temperature=0.7, json_mode=True)
    data = json.loads(raw)
    texts = {
        "platform_short": str(data.get("platform_short", "")).strip(),
        "hr_chat": str(data.get("hr_chat", "")).strip(),
        "apply_note": str(data.get("apply_note", "")).strip(),
        "portfolio_intro": str(data.get("portfolio_intro", "")).strip(),
    }
    if not texts["platform_short"] or not texts["hr_chat"]:
        return None
    return texts


def _template_generate(company, title, analysis, ev, purl) -> dict:
    ev_titles = "、".join(e["title"] for e in ev[:2]) or "【需补充：资料库中无对应证据】"
    ev_1 = ev[0]["title"] if ev else "【需补充】"
    ev_2 = ev[1]["title"] if len(ev) > 1 else "【需补充】"
    matched_dims = "、".join(m["dimension"] for m in analysis.get("matched", [])[:3]) or "【需补充】"
    texts = {}
    texts["platform_short"] = (
        f"您好，投递{company}的{title}。我做过的{ev_1}与岗位要求的{matched_dims}直接对口，"
        f"作品集：{purl}，期待有机会交流。")
    gap_note = ""
    if analysis.get("gaps"):
        gap_note = f"（坦诚说明：{ '、'.join(g['dimension'] for g in analysis['gaps'][:2])} 方面我经验有限，正在补齐）"
    texts["hr_chat"] = (
        f"您好！关注到{company}正在招{title}，结合 JD 里{matched_dims}的要求，"
        f"我的经历里最相关的是：{ev_1}、{ev_2}。都是我实际负责并落地的项目，细节都可以展开聊{gap_note}。"
        f"完整作品集在这里：{purl}。如果您觉得方向匹配，非常期待进一步沟通，谢谢！")
    texts["apply_note"] = (
        f"匹配要点：{matched_dims}。\n对应经历：{ev_titles}。\n"
        f"作品集：{purl}。\n（本备注由秋招 OS 基于资料库 confirmed 证据生成，每条经历可溯源。）")
    texts["portfolio_intro"] = (
        f"作品集内与{company}·{title}最相关的部分：{ev_1}"
        + (f"、{ev_2}" if ev_2 != "【需补充】" else "")
        + f"。覆盖岗位要求的{matched_dims}。其余案例也欢迎翻阅：{purl}")
    return texts


def _persist(job_id, texts, evidence_json) -> dict:
    conn = connect()
    try:
        for kind, text in texts.items():
            v = conn.execute("SELECT max(version) FROM greeting_message WHERE job_id=? AND kind=?",
                             (job_id, kind)).fetchone()[0] or 0
            conn.execute(
                "INSERT INTO greeting_message (job_id, kind, text, evidence, version, status) "
                "VALUES (?,?,?,?,?, 'draft')",
                (job_id, kind, text, evidence_json, v + 1))
        app = conn.execute("SELECT id, greeting_id FROM application WHERE job_id=?", (job_id,)).fetchone()
        if app:
            gid = conn.execute("SELECT id FROM greeting_message WHERE job_id=? ORDER BY id DESC LIMIT 1",
                               (job_id,)).fetchone()
            if gid:
                conn.execute("UPDATE application SET greeting_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                             (gid["id"], app["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"job_id": job_id, "greetings": texts, "evidence": []}
