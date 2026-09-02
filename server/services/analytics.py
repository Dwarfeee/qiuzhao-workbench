"""秋招数据分析服务：总览、Funnel、方向分析、Fit Score 转化、来源分析、投递质量。

诚实原则：
- 所有指标来自 jobs.db 真实数据；
- 转化率样本不足（分母 < 5）时明确标注「样本不足」，不强行下结论；
- 不预设岗位方向，从真实数据自动聚类。
"""
from __future__ import annotations

from datetime import date

from server.db import connect

# 应用状态 → 漏斗层级
APPLIED_STATUSES = ("Applied", "Online Assessment", "Interview", "Offer")


def _funnel(conn) -> list[dict]:
    """秋招漏斗：发现→高匹配→精投→Ready→Applied→OA→Interview→Offer，每层数量+转化率。"""
    discovered = conn.execute("SELECT count(*) FROM job").fetchone()[0]
    high_match = conn.execute("SELECT count(*) FROM job WHERE grade IN ('S','A')").fetchone()[0]
    shortlisted = conn.execute("SELECT count(*) FROM application").fetchone()[0]
    ready = conn.execute("SELECT count(*) FROM application WHERE status='Ready to Apply'").fetchone()[0]
    applied = conn.execute(
        "SELECT count(*) FROM application WHERE status IN ('Applied','Online Assessment','Interview','Offer') "
        "OR applied_date IS NOT NULL").fetchone()[0]
    oa = conn.execute("SELECT count(*) FROM application WHERE status='Online Assessment'").fetchone()[0]
    interviewing = conn.execute(
        "SELECT count(*) FROM application WHERE status IN ('Interview','Offer')").fetchone()[0]
    offer = conn.execute("SELECT count(*) FROM application WHERE status='Offer'").fetchone()[0]

    layers = [
        ("岗位发现", discovered, None),
        ("高匹配 (S/A)", high_match, None),
        ("精投", shortlisted, None),
        ("Ready to Apply", ready, None),
        ("Applied", applied, None),
        ("OA", oa, None),
        ("Interview", interviewing, None),
        ("Offer", offer, None),
    ]
    # 相邻层转化率（上一层的基数）
    prev = None
    for i, (label, n, _) in enumerate(layers):
        rate = None
        if i >= 1 and prev and prev > 0 and n is not None:
            rate = round(n / prev * 100, 1)
        layers[i] = {"label": label, "count": n, "rate_vs_prev": rate,
                     "sufficient": prev is None or prev >= 5}
        prev = n
    return layers


def overview() -> dict:
    conn = connect()
    try:
        total_jobs = conn.execute("SELECT count(*) FROM job").fetchone()[0]
        shortlisted = conn.execute("SELECT count(*) FROM application").fetchone()[0]
        applied = conn.execute(
            "SELECT count(*) FROM application WHERE status IN ('Applied','Online Assessment','Interview','Offer') "
            "OR applied_date IS NOT NULL").fetchone()[0]
        oa = conn.execute("SELECT count(*) FROM application WHERE status='Online Assessment'").fetchone()[0]
        interviewing = conn.execute(
            "SELECT count(*) FROM application WHERE status IN ('Interview','Offer')").fetchone()[0]
        offer = conn.execute("SELECT count(*) FROM application WHERE status='Offer'").fetchone()[0]
        rejected = conn.execute("SELECT count(*) FROM application WHERE status='Rejected'").fetchone()[0]
        return {
            "total_jobs": total_jobs, "shortlisted": shortlisted, "applied": applied,
            "oa": oa, "interviewing": interviewing, "offer": offer, "rejected": rejected,
        }
    finally:
        conn.close()


def fit_score_conversion() -> dict:
    """Fit Score 分档 → OA/面试转化（验证「AI 判断适合的岗位是否真的更容易进面试」）。"""
    buckets = [
        ("90+", "fit_score >= 90"), ("80-89", "fit_score >= 80 AND fit_score < 90"),
        ("70-79", "fit_score >= 70 AND fit_score < 80"), ("60-69", "fit_score >= 60 AND fit_score < 70"),
        ("<60", "fit_score < 60"),
    ]
    conn = connect()
    out = []
    try:
        for label, cond in buckets:
            applied = conn.execute(
                f"SELECT count(*) FROM application a JOIN job j ON j.id=a.job_id "
                f"WHERE j.{cond} AND (a.status IN ('Applied','Online Assessment','Interview','Offer') "
                f"OR a.applied_date IS NOT NULL)").fetchone()[0]
            oa = conn.execute(
                f"SELECT count(*) FROM application a JOIN job j ON j.id=a.job_id "
                f"WHERE j.{cond} AND a.status='Online Assessment'").fetchone()[0]
            interviewing = conn.execute(
                f"SELECT count(*) FROM application a JOIN job j ON j.id=a.job_id "
                f"WHERE j.{cond} AND a.status IN ('Interview','Offer')").fetchone()[0]
            offer = conn.execute(
                f"SELECT count(*) FROM application a JOIN job j ON j.id=a.job_id "
                f"WHERE j.{cond} AND a.status='Offer'").fetchone()[0]
            out.append({
                "bucket": label, "applied": applied, "oa": oa,
                "interview": interviewing, "offer": offer,
                "oa_rate": round(oa / applied * 100, 1) if applied else None,
                "interview_rate": round(interviewing / applied * 100, 1) if applied else None,
                "sufficient": applied >= 5,
            })
        return out
    finally:
        conn.close()


def direction_analysis() -> dict:
    """按岗位方向（真实数据自动聚类）统计：数量/精投/投递/OA/面试/Offer + 转化率。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT COALESCE(NULLIF(j.direction,''), '其他') d,
                      count(DISTINCT j.id) total,
                      count(DISTINCT a.id) shortlisted,
                      sum(CASE WHEN a.status IN ('Applied','Online Assessment','Interview','Offer') OR a.applied_date IS NOT NULL THEN 1 ELSE 0 END) applied,
                      sum(CASE WHEN a.status='Online Assessment' THEN 1 ELSE 0 END) oa,
                      sum(CASE WHEN a.status IN ('Interview','Offer') THEN 1 ELSE 0 END) interviewing,
                      sum(CASE WHEN a.status='Offer' THEN 1 ELSE 0 END) offer
               FROM job j LEFT JOIN application a ON a.job_id=j.id
               GROUP BY d ORDER BY total DESC""").fetchall()
        out = []
        for r in rows:
            applied = r["applied"] or 0
            oa = r["oa"] or 0
            interviewing = r["interviewing"] or 0
            out.append({
                "direction": r["d"], "total": r["total"], "shortlisted": r["shortlisted"] or 0,
                "applied": applied, "oa": oa, "interviewing": interviewing, "offer": r["offer"] or 0,
                "apply_to_oa_rate": round(oa / applied * 100, 1) if applied else None,
                "oa_to_interview_rate": round(interviewing / oa * 100, 1) if oa else None,
                "interview_to_offer_rate": round((r["offer"] or 0) / interviewing * 100, 1) if interviewing else None,
                "sufficient": applied >= 5,
            })
        return out
    finally:
        conn.close()


def source_analysis() -> dict:
    """按招聘来源统计：发现/精投/投递/OA/面试（哪个渠道最值得花时间）。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT COALESCE(NULLIF(j.source,''), '未知') s,
                      count(DISTINCT j.id) total,
                      count(DISTINCT a.id) shortlisted,
                      sum(CASE WHEN a.status IN ('Applied','Online Assessment','Interview','Offer') OR a.applied_date IS NOT NULL THEN 1 ELSE 0 END) applied,
                      sum(CASE WHEN a.status='Online Assessment' THEN 1 ELSE 0 END) oa,
                      sum(CASE WHEN a.status IN ('Interview','Offer') THEN 1 ELSE 0 END) interviewing,
                      sum(CASE WHEN a.status='Offer' THEN 1 ELSE 0 END) offer
               FROM job j LEFT JOIN application a ON a.job_id=j.id
               GROUP BY s ORDER BY total DESC""").fetchall()
        out = []
        for r in rows:
            applied = r["applied"] or 0
            out.append({
                "source": r["s"], "total": r["total"], "shortlisted": r["shortlisted"] or 0,
                "applied": applied, "oa": r["oa"] or 0, "interviewing": r["interviewing"] or 0,
                "offer": r["offer"] or 0,
                "interview_rate": round((r["interviewing"] or 0) / applied * 100, 1) if applied else None,
                "sufficient": applied >= 5,
            })
        return out
    finally:
        conn.close()


def quality_today() -> dict:
    """投递质量：今日投递中 S/A/B/C/D 分布。"""
    today = date.today().isoformat()
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT j.grade, count(*) c FROM application a JOIN job j ON j.id=a.job_id
               WHERE a.applied_date=? GROUP BY j.grade""", (today,)).fetchall()
        dist = {r["grade"]: r["c"] for r in rows if r["grade"]}
        total = sum(dist.values())
        high = dist.get("S", 0) + dist.get("A", 0)
        return {"today": today, "total": total,
                "S_A": high, "B": dist.get("B", 0),
                "C_D": dist.get("C", 0) + dist.get("D", 0),
                "quality_rate": round(high / total * 100, 1) if total else None,
                "dist": dist}
    finally:
        conn.close()


def full_analytics() -> dict:
    """聚合全部数据分析。"""
    return {
        "overview": overview(),
        "funnel": _funnel(connect()),
        "fit_score_conversion": fit_score_conversion(),
        "directions": direction_analysis(),
        "sources": source_analysis(),
        "quality_today": quality_today(),
    }
