"""投递排期：把待投岗位分配到工作日（每天 N 个），供周日推送本周计划、每日推送今日任务。

排期规则（用户确认）：
- 阶段 1（前期：从 start_date 起前 practice_days 天，默认 10 天）：**小厂优先**拿来练手；
- 阶段 2（之后）：综合优先 = 截止紧急度 + 匹配度 + 薪资。

只排「目标方向（UI/UX）且未投递」的岗位。绝不虚构岗位，没有就不排。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from server.db import connect, get_setting, set_setting

DEFAULT_DAILY_QUOTA = 8
DEFAULT_PRACTICE_DAYS = 7
# 公司人数区间三档
SIZE_OPTIONS = ("0-99", "100-499", "500+", "未标记")
# 练手期排序权重：0-99（小厂）最高，用来练手
_SIZE_WEIGHT = {"0-99": 100.0, "100-499": 60.0, "500+": 20.0}


def practice_start_date() -> date:
    """练手期起点：首次启用锚定今天，之后读设置（用户可在设置页改）。"""
    v = get_setting("practice_start_date")
    if v:
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except Exception:
            pass
    today = date.today()
    set_setting("practice_start_date", today.isoformat())
    return today


def interview_ready() -> bool:
    """用户是否已在弹窗里回答『做好了面试准备』。"""
    return get_setting("interview_ready") == "1"


def in_practice_window(today: date | None = None) -> bool:
    """今天是否处于前 7 天练手期（含起点当天，共 7 天）。"""
    today = today or date.today()
    return today <= practice_start_date() + timedelta(days=6)


def eligible_sizes(today: date | None = None) -> set[str]:
    """今天可投递的公司规模集合（未标记永远可投，因无法归类）。

    规则：
    - 练手期（前 7 天）：只投 0-99 与 100-499（跳过 500+ 大厂练手）
    - 常规期 + 已准备好面试：只投 100-499 与 500+（跳过 0-99 小厂）
    - 常规期 + 未准备好：三档全投
    """
    if in_practice_window(today):
        return {"0-99", "100-499"}
    if interview_ready():
        return {"100-499", "500+"}
    return {"0-99", "100-499", "500+"}


def _size_eligible(scale) -> bool:
    """该岗位规模今天是否可投（未标记视为可投）。"""
    s = (scale or "").strip()
    if not s:
        return True
    return s in eligible_sizes()


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _next_weekdays(start: date, days: int) -> list[date]:
    """从 start 起取 days 个工作日（周一~周五）。"""
    out, d = [], start
    while len(out) < days:
        if d.weekday() < 5:      # 0=周一 ... 4=周五
            out.append(d)
        d += timedelta(days=1)
    return out


def _deadline_urgency(deadline: str | None, today: date) -> float:
    """截止紧急度 0~1：越急越高；无截止日期给中间值 0.3（不猜测，只做排序权重）。"""
    if not deadline:
        return 0.3
    try:
        dl = datetime.strptime(str(deadline)[:10], "%Y-%m-%d").date()
    except Exception:
        return 0.3
    days = (dl - today).days
    if days < 0:
        return 0.0              # 已截止，排最后
    if days <= 3:
        return 1.0
    if days <= 7:
        return 0.8
    if days <= 14:
        return 0.6
    return 0.4


def _priority_score(job: dict, today: date, practice_mode: bool) -> float:
    """综合优先级（越大越优先）。"""
    if practice_mode:
        # 阶段1：小厂(0-99)优先练手（0-99 100 / 100-499 60 / 500+ 20 / 未标记 50）
        base = _SIZE_WEIGHT.get(job.get("company_scale") or "", 50.0)
        fit = job.get("fit_score") or 0
        return base * 10 + fit          # 规模主导，匹配度做微调
    # 阶段2：截止紧急度 40 + 匹配度 40 + 薪资 20
    urg = _deadline_urgency(job.get("deadline"), today)      # 0~1
    fit = (job.get("fit_score") or 0) / 100.0                # 0~1
    sal = job.get("salary_min")
    sal_score = 0.0
    if sal:
        sal_score = max(0.0, min(1.0, (sal - 5) / 15.0))     # 5K→0, 20K→1
    return urg * 40 + fit * 40 + sal_score * 20


def build_plan(start_date: date | None = None,
               daily_quota: int = DEFAULT_DAILY_QUOTA,
               practice_days: int = DEFAULT_PRACTICE_DAYS,
               practice_start: date | None = None) -> dict:
    """生成投递排期：把待投岗位分配到工作日。

    practice_start：练手期起点（None 则用今天）。
    前 practice_days 天为练手期（小厂优先），之后为综合优先期。
    返回 {plan:{日期:[岗位...]}, total, days, practice_mode}
    """
    today = date.today()
    start = start_date or today
    ps = practice_start or today
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM job WHERE status IN ('New','Shortlisted') "
            "ORDER BY COALESCE(fit_score,0) DESC, id")]
    finally:
        conn.close()

    # 只排目标方向、未排期、且今天规模可投的岗位（已排期的保留其日期）
    pending = [r for r in rows if r.get("status") in ("New", "Shortlisted")
               and not r.get("planned_date")
               and (r.get("direction") in ("UI/UX", "AI产品"))
               and _size_eligible(r.get("company_scale"))]
    already = [r for r in rows if r.get("planned_date")]

    # 需要分配到的工作日
    n_days = max(1, (len(pending) + daily_quota - 1) // daily_quota)
    workdays = _next_weekdays(start, n_days)

    for j in pending:
        j["_score"] = _priority_score(j, today, practice_mode=True)   # 先算练手期分数

    # 阶段划分：练手期覆盖的工作日数量
    practice_workdays = len([d for d in workdays if d < ps + timedelta(days=practice_days)])
    practice_slots = practice_workdays * daily_quota

    # 排序：先按阶段分配
    pending.sort(key=lambda j: (
        # 练手期槽位内：小厂优先；超出后：综合优先
        -j["_score"] if practice_slots > 0 else 0,
    ))
    # 重新计算：练手期槽位用 practice 分数，其余用综合分数
    practice_part = pending[:practice_slots]
    normal_part = pending[practice_slots:]
    for j in pending:
        j["_score"] = _priority_score(j, today, practice_mode=True)
    practice_part.sort(key=lambda j: -j["_score"])
    for j in normal_part:
        j["_score"] = _priority_score(j, today, practice_mode=False)
    normal_part.sort(key=lambda j: -j["_score"])
    ordered = practice_part + normal_part

    # 落库
    plan: dict[str, list[dict]] = {d.isoformat(): [] for d in workdays}
    conn = connect()
    try:
        for i, j in enumerate(ordered):
            if i >= len(workdays) * daily_quota:
                break
            d = workdays[i // daily_quota]
            conn.execute(
                "UPDATE job SET planned_date=?, updated_at=datetime('now','localtime') WHERE id=?",
                (d.isoformat(), j["id"]))
            j["planned_date"] = d.isoformat()
            plan[d.isoformat()].append(j)
        conn.commit()
    finally:
        conn.close()

    # 已排期（之前就有的）并入展示
    for j in already:
        plan.setdefault(j["planned_date"], []).append(j)

    return {"plan": plan, "total": len(ordered) + len(already),
            "days": len(workdays), "daily_quota": daily_quota,
            "practice_days": practice_days,
            "practice_mode": practice_slots > 0,
            "start": start.isoformat()}


def get_today_tasks(d: date | None = None) -> dict:
    """取某天的任务：计划投递的岗位 + 当天面试 + 临近截止。"""
    d = d or date.today()
    ds = d.isoformat()
    conn = connect()
    try:
        jobs = [dict(r) for r in conn.execute(
            "SELECT * FROM job WHERE planned_date=? ORDER BY COALESCE(fit_score,0) DESC",
            (ds,))]
        interviews = [dict(r) for r in conn.execute(
            "SELECT * FROM interview WHERE date(scheduled_at)=? ORDER BY scheduled_at",
            (ds,))]
        # 3 天内截止
        deadlines = []
        for r in conn.execute(
                "SELECT id, company, title, deadline FROM job "
                "WHERE deadline IS NOT NULL AND deadline<>''"):
            try:
                dl = datetime.strptime(str(r["deadline"])[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            days = (dl - d).days
            if 0 <= days <= 3:
                deadlines.append({"id": r["id"], "company": r["company"],
                                  "title": r["title"], "deadline": r["deadline"],
                                  "days": days})
        deadlines.sort(key=lambda x: x["days"])
    finally:
        conn.close()
    return {"date": ds, "weekday": "一二三四五六日"[d.weekday()],
            "jobs": jobs, "interviews": interviews, "deadlines": deadlines}


def clear_plan() -> int:
    """清空所有排期（重新排期前调用）。"""
    conn = connect()
    try:
        n = conn.execute(
            "SELECT count(*) FROM job WHERE planned_date IS NOT NULL").fetchone()[0]
        conn.execute("UPDATE job SET planned_date=NULL WHERE planned_date IS NOT NULL")
        conn.commit()
        return n
    finally:
        conn.close()


def weekly_added_count(monday: date | None = None) -> int:
    """本周（周一至今）新增岗位数，用于『每周 50 个』进度跟踪。"""
    d = monday or _monday_of(date.today())
    conn = connect()
    try:
        return conn.execute(
            "SELECT count(*) FROM job WHERE date(created_at) >= ?",
            (d.isoformat(),)).fetchone()[0]
    finally:
        conn.close()


def get_daily_delivery_list(d: date | None = None) -> dict:
    """今日待投清单：精投中心里、今天规模可投、按匹配度排序、限量 daily_quota。"""
    d = d or date.today()
    sizes = eligible_sizes(d)
    quota = int(get_setting("daily_quota", str(DEFAULT_DAILY_QUOTA)) or DEFAULT_DAILY_QUOTA)
    weekly_target = int(get_setting("weekly_target", "50") or 50)
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT j.id, j.company, j.title, j.grade, j.fit_score, j.company_scale, "
            "j.deadline, a.status AS app_status "
            "FROM application a JOIN job j ON j.id=a.job_id "
            "WHERE a.status IN ('Shortlisted','Tailoring','Ready to Apply') "
            "ORDER BY COALESCE(j.fit_score,0) DESC, j.id")]
    finally:
        conn.close()
    eligible = [r for r in rows if _size_eligible(r.get("company_scale"))]
    return {
        "date": d.isoformat(),
        "weekday": "一二三四五六日"[d.weekday()],
        "phase": "练手期(前7天)" if in_practice_window(d) else "常规期",
        "interview_ready": interview_ready(),
        "sizes_eligible": sorted(sizes),
        "sizes_excluded": [s for s in ("0-99", "100-499", "500+") if s not in sizes],
        "daily_quota": quota,
        "weekly_target": weekly_target,
        "week_added": weekly_added_count(),
        "total_eligible": len(eligible),
        "jobs": eligible[:quota],
    }
