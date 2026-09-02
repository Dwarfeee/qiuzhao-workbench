"""周度复盘 Weekly Review。周六自动生成（Automation），也可手动触发。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from server.db import connect


def _week_bounds(today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def iso_week(today: date | None = None) -> str:
    d = today or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def generate_weekly_review(week_offset: int = 0) -> dict:
    base = date.today() - timedelta(weeks=week_offset)
    start, end = _week_bounds(base)
    week = iso_week(base)
    conn = connect()
    try:
        jobs = conn.execute(
            "SELECT * FROM job WHERE discovered_date BETWEEN ? AND ?", (start, end)).fetchall()
        apps = conn.execute(
            "SELECT * FROM application WHERE date(created_at) BETWEEN ? AND ?", (start, end)).fetchall()
        applied = [a for a in apps if a["status"] in ("Applied",) or (a["applied_date"] and start <= a["applied_date"] <= end)]
        all_applied = conn.execute(
            "SELECT count(*) FROM application WHERE status IN ('Applied','Online Assessment','Interview','Offer') "
            "OR applied_date IS NOT NULL").fetchone()[0]
        oa = conn.execute(
            "SELECT count(*) FROM application WHERE status='Online Assessment'").fetchone()[0]
        interviewing = conn.execute(
            "SELECT count(*) FROM application WHERE status='Interview'").fetchone()[0]
        offers = conn.execute("SELECT count(*) FROM application WHERE status='Offer'").fetchone()[0]
        rejects = conn.execute(
            "SELECT count(*) FROM application WHERE status='Rejected' "
            f"AND date(updated_at) BETWEEN ? AND ?", (start, end)).fetchone()[0]
        interviews = conn.execute(
            "SELECT * FROM interview WHERE date(scheduled_at) BETWEEN ? AND ?", (start, end)).fetchall()

        # 面试轮次分布
        round_dist = {}
        for r in conn.execute("SELECT round, count(*) c FROM interview GROUP BY round"):
            round_dist[r["round"]] = r["c"]

        # 方向分析（发现数 + 平均分 + 各方向面试/Offer 转化）
        dir_stats = {}
        for j in jobs:
            d = j["direction"] or "其他"
            s = dir_stats.setdefault(d, {"discovered": 0, "scores": [], "grades": []})
            s["discovered"] += 1
            if j["fit_score"] is not None:
                s["scores"].append(j["fit_score"])
                s["grades"].append(j["grade"])
        # 方向级转化率：按 job.direction 关联 application/面试
        dir_apps = {}
        for r in conn.execute(
                """SELECT j.direction d, a.status, count(*) c FROM application a
                   JOIN job j ON j.id=a.job_id GROUP BY j.direction, a.status"""):
            d = r["d"] or "其他"
            dd = dir_apps.setdefault(d, {})
            dd[r["status"]] = r["c"]
        for d, s in dir_stats.items():
            s["avg_score"] = round(sum(s["scores"]) / len(s["scores"]), 1) if s["scores"] else None
            s["grades"] = {g: s["grades"].count(g) for g in set(s["grades"])}
            s.pop("scores", None)
            dd = dir_apps.get(d, {})
            s["applied"] = sum(dd.get(k, 0) for k in ("Applied", "Online Assessment", "Interview", "Offer"))
            s["interviewed"] = dd.get("Interview", 0) + dd.get("Offer", 0)
            s["offers"] = dd.get("Offer", 0)

        # 转化率漏斗
        total_apps_ever = conn.execute("SELECT count(*) FROM application").fetchone()[0]
        funnel = {
            "applied": len(applied),
            "oa": oa, "interview": interviewing, "offer": offers,
            "apply_to_oa_rate": round(oa / len(applied) * 100, 1) if applied else None,
            "oa_to_interview_rate": round(interviewing / oa * 100, 1) if oa else None,
            "interview_to_offer_rate": round(offers / interviewing * 100, 1) if interviewing else None,
            "apply_to_offer_rate": round(offers / len(applied) * 100, 1) if applied else None,
        }

        stats = {
            "week": week, "range": f"{start} ~ {end}",
            "discovered": len(jobs), "shortlisted": len(apps),
            "applied": len(applied), "oa": oa, "interviews": len(interviews),
            "offers": offers, "rejects": rejects,
            "total_applications": total_apps_ever,
            "apply_rate": round(len(applied) / len(apps) * 100, 1) if apps else 0.0,
            "interview_rate": round(interviewing / all_applied * 100, 1) if all_applied else 0.0,
            "funnel": funnel, "round_dist": round_dist,
        }

        strategy = _build_strategy(stats, dir_stats)

        conn.execute("DELETE FROM weekly_review WHERE week=?", (week,))
        conn.execute(
            "INSERT INTO weekly_review (week, stats, insights, strategy_next_week) VALUES (?,?,?,?)",
            (week, json.dumps(stats, ensure_ascii=False),
             json.dumps(dir_stats, ensure_ascii=False), strategy))
        conn.execute(
            "INSERT INTO notification (kind, title, content, channel) VALUES (?,?,?,?)",
            ("weekly_review", f"周报 {week}",
             f"发现{len(jobs)}·精投{len(apps)}·投递{len(applied)}·OA{oa}·面试{len(interviews)}·Offer{offers}",
             "session"))
        conn.commit()
        return {"week": week, "stats": stats, "directions": dir_stats, "strategy": strategy}
    finally:
        conn.close()


def _build_strategy(stats: dict, dir_stats: dict) -> str:
    """基于真实数据的下周策略。样本不足时明确说明，不编造结论。"""
    lines: list[str] = []
    applied = stats.get("applied", 0)

    # 样本不足 → 明确告知
    if applied < 5:
        lines.append("【样本不足】本周有效投递少于 5，暂无法对转化率下结论，先保持稳定投递节奏。")
        if stats.get("discovered", 0) < 10:
            lines.append("【扩大检索】本周发现岗位偏少，建议扩宽来源（公司官网 / 招聘站 / 手动粘贴 JD）。")
        return "\n".join(lines)

    # 转化率漏斗
    funnel = stats.get("funnel", {})
    f = funnel.get("apply_to_oa_rate"), funnel.get("oa_to_interview_rate"), funnel.get("interview_to_offer_rate")
    lines.append(
        f"【本周转化】投递→OA {f[0] if f[0] is not None else '—'}% · "
        f"OA→面试 {f[1] if f[1] is not None else '—'}% · "
        f"面试→Offer {f[2] if f[2] is not None else '—'}%")

    # 方向分析：哪个方向值得继续/减少
    scored = [d for d, s in dir_stats.items() if s.get("avg_score") is not None]
    if scored:
        best = max(scored, key=lambda d: dir_stats[d]["avg_score"])
        lines.append(f"【继续加大】「{best}」方向平均匹配度最高（{dir_stats[best]['avg_score']} 分），下周优先搜此类岗位。")
    # 高匹配但无面试的方向（转化率低信号）
    for d, s in dir_stats.items():
        if s.get("applied", 0) >= 5 and s.get("interviewed", 0) == 0:
            lines.append(f"【减少】「{d}」已投 {s['applied']} 但零面试，建议复盘该方向的简历/打招呼语针对性，暂缓加量。")
    # 有面试/Offer 的方向
    for d, s in dir_stats.items():
        if s.get("offers", 0) > 0:
            lines.append(f"【重点关注】「{d}」方向已产出 Offer，可总结成功画像，复制打法。")
        elif s.get("interviewed", 0) > 0:
            lines.append(f"【重点关注】「{d}」方向有面试进展，集中精力准备，别分散。")

    # 效率诊断
    if stats.get("apply_rate", 0) is not None and stats.get("apply_rate", 0) < 50 and stats.get("shortlisted", 0) > 0:
        lines.append("【效率】精投→投递转化偏低，优先清理 Tailoring/Ready 队列。")

    if not any(("继续加大" in l or "重点关注" in l or "减少" in l or "效率" in l) for l in lines):
        lines.append("【保持节奏】各指标正常，周末休整。")
    return "\n".join(lines)


def list_reviews() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM weekly_review ORDER BY week DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d["stats"] or "{}")
            d["insights"] = json.loads(d["insights"] or "{}")
            out.append(d)
        return out
    finally:
        conn.close()
