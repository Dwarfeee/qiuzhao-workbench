"""每日管家 Daily Manager：今日简报、待办、截止提醒、面试提醒、周末判断、Automation 设置。

核心目标：让用户每天打开工作台时，系统已经告诉他「今天应该做什么」。

诚实原则：
- 所有数据来自 jobs.db 真实记录，不编造；
- 截止日期只在 JD 里明确存在时计算，否则标记 Deadline Unknown；
- 周末自动切换 Review Mode，不执行常规投递任务。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from server.db import connect

# Automation 设置默认值（key → default）
DEFAULT_SETTINGS = {
    "weekday_radar_enabled": "1",       # 工作日岗位雷达开关
    "radar_run_time": "08:30",          # 岗位雷达运行时间
    "daily_target": "20",               # 每日岗位目标数量（目标非 KPI）
    "deadline_reminder": "1",           # 截止提醒开关
    "interview_reminder": "1",          # 面试提醒开关
    "review_run_time": "10:00",         # 周报运行时间
    "weekend_review": "1",              # 周末 Review Mode
}

# 待办模板（每日自动生成，基于真实状态动态填充）
TODO_TEMPLATE = [
    ("查看今日岗位", "radar"),
    ("确认精投岗位", "shortlist"),
    ("生成 Tailored Resume", "resume"),
    ("检查 PDF 渲染", "pdf"),
    ("复制打招呼语", "greeting"),
    ("完成人工投递", "apply"),
    ("更新投递状态", "status"),
]


def is_weekend(d: date | None = None) -> bool:
    d = d or date.today()
    return d.weekday() >= 5  # 5=周六 6=周日


def get_settings() -> dict:
    """读取 automation_settings，缺失键用默认值。"""
    conn = connect()
    try:
        rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM automation_settings")}
    finally:
        conn.close()
    merged = dict(DEFAULT_SETTINGS)
    merged.update(rows)
    return merged


def save_settings(patch: dict) -> dict:
    """写入 automation_settings（只接受已知键）。"""
    conn = connect()
    try:
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO automation_settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated_at=datetime('now','localtime')",
                    (k, str(v)))
        conn.commit()
    finally:
        conn.close()
    return get_settings()


def deadline_days(deadline: str | None) -> int | None:
    """计算距离截止还有多少天。无法解析返回 None（Deadline Unknown）。"""
    if not deadline:
        return None
    try:
        d = datetime.strptime(deadline[:10], "%Y-%m-%d").date()
        return (d - date.today()).days
    except (ValueError, TypeError):
        return None


def deadline_label(days: int | None) -> str:
    """截止标签：TODAY / 2 天后截止 / Deadline Unknown。"""
    if days is None:
        return "Deadline Unknown"
    if days < 0:
        return "已截止"
    if days == 0:
        return "🔴 TODAY"
    if days <= 3:
        return f"⚠️ {days} 天后截止"
    return f"{days} 天后"


def today_briefing() -> dict:
    """今日秋招简报：今日发现/高匹配/待处理/已投 + 今日 Top 岗位 + 待办 + 提醒。"""
    conn = connect()
    try:
        today = date.today().isoformat()
        today_jobs = conn.execute(
            "SELECT count(*) FROM job WHERE discovered_date=?", (today,)).fetchone()[0]
        today_high = conn.execute(
            "SELECT count(*) FROM job WHERE discovered_date=? AND grade IN ('S','A')",
            (today,)).fetchone()[0]
        today_applied = conn.execute(
            "SELECT count(*) FROM application WHERE applied_date=?", (today,)).fetchone()[0]
        pending = conn.execute(
            "SELECT count(*) FROM application WHERE status IN ('Shortlisted','Tailoring','Ready to Apply')"
        ).fetchone()[0]

        # 今日 Top 岗位（S/A/B 优先，带匹配原因 + 状态）
        top_jobs = [dict(r) for r in conn.execute(
            """SELECT j.id, j.company, j.title, j.location, j.source, j.url, j.deadline,
                      j.fit_score, j.grade, j.direction, j.status AS job_status, j.discovered_date,
                      a.id AS app_id, a.status AS app_status, rv.diff_status
               FROM job j
               LEFT JOIN application a ON a.job_id = j.id
               LEFT JOIN resume_version rv ON rv.id = a.resume_version_id
               WHERE j.fit_score IS NOT NULL
               ORDER BY CASE j.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2
                        WHEN 'C' THEN 3 ELSE 4 END, j.fit_score DESC, j.id DESC
               LIMIT 20""")]
        for j in top_jobs:
            d = deadline_days(j.get("deadline"))
            j["deadline_label"] = deadline_label(d)
            j["deadline_days"] = d

        # 截止提醒
        deadlines = []
        for r in conn.execute(
                "SELECT id, company, title, deadline, fit_score, grade FROM job "
                "WHERE deadline IS NOT NULL AND deadline != '' AND deadline >= date('now','localtime') "
                "ORDER BY deadline LIMIT 8"):
            r = dict(r)
            r["days"] = deadline_days(r["deadline"])
            r["label"] = deadline_label(r["days"])
            deadlines.append(r)

        # 面试提醒
        interviews = [dict(r) for r in conn.execute(
            """SELECT i.*, j.title AS j_title, j.company AS j_company
               FROM interview i
               LEFT JOIN application a ON a.id = i.application_id
               LEFT JOIN job j ON j.id = a.job_id
               WHERE i.scheduled_at >= datetime('now','localtime')
               ORDER BY i.scheduled_at LIMIT 8""")]
        for iv in interviews:
            if iv.get("scheduled_at"):
                try:
                    sdt = datetime.strptime(iv["scheduled_at"][:16], "%Y-%m-%d %H:%M")
                    delta = sdt - datetime.now()
                    iv["in_hours"] = round(delta.total_seconds() / 3600, 1)
                except (ValueError, TypeError):
                    iv["in_hours"] = None
            else:
                iv["in_hours"] = None

        # 今日待办（基于真实状态动态勾选）
        todo = build_todo(conn)

        # 今日目标（来自 Automation 设置，目标非 KPI）
        settings = get_settings()
        daily_target = int(settings.get("daily_target", 20))
        # 建议精投 = S/A 级且尚未投递的岗位数（宁缺毋滥，不凑数）
        suggest_shortlist = conn.execute(
            "SELECT count(*) FROM job WHERE grade IN ('S','A') AND "
            "(status IS NULL OR status NOT IN ('Applied','Online Assessment','Interview','Offer','Rejected'))"
        ).fetchone()[0]

        return {
            "date": today, "is_weekend": is_weekend(),
            "daily_target": daily_target,
            "today_jobs": today_jobs, "today_high": today_high,
            "suggest_shortlist": suggest_shortlist,
            "pending": pending, "today_applied": today_applied,
            "top_jobs": top_jobs, "deadlines": deadlines,
            "upcoming_interviews": interviews, "todo": todo,
        }
    finally:
        conn.close()


def build_todo(conn) -> list[dict]:
    """基于真实状态生成今日待办（勾选状态反映数据库实际进度）。"""
    jobs_today = conn.execute(
        "SELECT count(*) FROM job WHERE discovered_date=date('now','localtime')").fetchone()[0]
    shortlist_pending = conn.execute(
        "SELECT count(*) FROM application WHERE status='Shortlisted'").fetchone()[0]
    ready = conn.execute(
        "SELECT count(*) FROM application WHERE status='Ready to Apply'").fetchone()[0]
    tailoring = conn.execute(
        "SELECT count(*) FROM application WHERE status='Tailoring'").fetchone()[0]
    has_greeting = conn.execute(
        "SELECT count(*) FROM greeting_message WHERE status='draft'").fetchone()[0]
    interviews = conn.execute(
        "SELECT count(*) FROM interview WHERE scheduled_at >= datetime('now','localtime')").fetchone()[0]

    return [
        {"task": "查看今日岗位", "detail": f"今日发现 {jobs_today} 个岗位",
         "done": jobs_today == 0},
        {"task": "确认精投岗位", "detail": f"{shortlist_pending} 个待加入精投",
         "done": shortlist_pending == 0},
        {"task": "生成 Tailored Resume", "detail": f"{tailoring} 个定制中",
         "done": tailoring == 0},
        {"task": "检查 PDF 渲染", "detail": "Diff 通过即可投递", "done": False},
        {"task": "复制打招呼语", "detail": f"{has_greeting} 条待用", "done": False},
        {"task": "完成人工投递", "detail": f"{ready} 个 Ready 待投",
         "done": ready == 0},
        {"task": "更新投递状态", "detail": "投递后标记 Applied", "done": False},
        {"task": "准备面试", "detail": f"{interviews} 场面试待准备", "done": interviews == 0},
    ]


def record_run(automation_name: str, status: str, output_summary: str) -> None:
    """记录自动化运行历史。"""
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO automation_run (automation_name, run_at, status, output_summary) "
            "VALUES (?, datetime('now','localtime'), ?, ?)",
            (automation_name, status, output_summary))
        conn.commit()
    finally:
        conn.close()


def list_runs(limit: int = 30) -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM automation_run ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def weekend_review() -> dict:
    """周末复盘数据：本周数据 + 方向/来源表现 + Fit Score 相关性 + 反复问题 + 下周建议。"""
    from server.services import analytics, review
    stats = review.generate_weekly_review().get("stats", {})
    ana = analytics.full_analytics()
    # 反复出现的面试问题（按问题文本聚合 top）
    conn = connect()
    try:
        top_q = [dict(r) for r in conn.execute(
            """SELECT question, count(*) c FROM interview_question
               GROUP BY question ORDER BY c DESC LIMIT 5""")]
    finally:
        conn.close()
    return {
        "week": stats.get("week"), "range": stats.get("range"),
        "stats": stats, "directions": ana.get("directions", []),
        "sources": ana.get("sources", []),
        "fit_score_conversion": ana.get("fit_score_conversion", []),
        "recurring_questions": top_q,
        "sample_sufficient": stats.get("applied", 0) >= 5,
    }


# ---------------------------------------------------------------- 通知生成（事件去重入队）

def notify_daily_brief(briefing: dict) -> int:
    """每日早报 → 通知（event_key 按日期去重）。"""
    from server.services import notifier
    today = briefing.get("date", "")
    prefs = notifier.get_prefs()
    if prefs.get("notify_daily_brief") != "1":
        return 0
    top = briefing.get("top_jobs", [])[:3]
    top_lines = "\n".join(f"{i+1}. {j['company']} / {j['title']}（{j.get('grade','')} {j.get('fit_score','')}）"
                          for i, j in enumerate(top)) or "（暂无）"
    deadlines = briefing.get("deadlines", [])
    dl_lines = "\n".join(f"{d['company']} / {d['title']} — {d['label']}" for d in deadlines[:5]) or "（无）"
    interviews = briefing.get("upcoming_interviews", [])
    iv_lines = "\n".join(f"{i.get('j_company','')} / {i.get('j_title','')} {i.get('round','')} {i.get('scheduled_at','')}"
                         for i in interviews[:3]) or "（无）"
    content = (f"今日发现：{briefing.get('today_jobs', 0)}\n"
               f"高匹配岗位：{briefing.get('today_high', 0)}\n"
               f"待处理：{briefing.get('pending', 0)}\n"
               f"今日已投：{briefing.get('today_applied', 0)}\n\n"
               f"今日重点：\n{top_lines}\n\n"
               f"今日截止：\n{dl_lines}\n\n"
               f"今日面试：\n{iv_lines}\n\n"
               f"打开工作台查看全部岗位")
    r = notifier.enqueue("daily_summary", "🌅 今日秋招", content,
                         event_key=f"daily:{today}", channel="webhook")
    return 1 if r.get("ok") else 0


def notify_high_match(job: dict, matched_count: int) -> int:
    """高匹配岗位 → 通知（event_key 按岗位去重）。"""
    from server.services import notifier
    prefs = notifier.get_prefs()
    if prefs.get("notify_high_match") != "1":
        return 0
    content = (f"公司：{job['company']}\n"
               f"岗位：{job['title']}\n"
               f"城市：{job.get('location') or '—'}\n"
               f"Fit Score：{job.get('fit_score')}（{job.get('grade')}）\n"
               f"截止：{job.get('deadline') or 'Deadline Unknown'}\n\n"
               f"命中 {matched_count} 个匹配维度。打开工作台查看 JD 与匹配分析。")
    r = notifier.enqueue("new_s_grade", f"⭐ 高匹配岗位：{job['company']}·{job['title']}",
                         content, event_key=f"high_match:{job['id']}", channel="webhook")
    return 1 if r.get("ok") else 0


def notify_deadline(job: dict, days: int) -> int:
    """截止提醒 → 通知（event_key 按岗位+日期去重）。"""
    from server.services import notifier
    prefs = notifier.get_prefs()
    if prefs.get("notify_deadline") != "1":
        return 0
    label = "🔴 TODAY" if days == 0 else f"⚠️ {days} 天后截止"
    content = (f"{job['company']}\n{job['title']}\n\n"
               f"截止：{job.get('deadline')}\n状态：尚未投递\n建议：今天处理")
    r = notifier.enqueue("deadline", f"{label}：{job['company']}·{job['title']}",
                         content, event_key=f"deadline:{job['id']}:{job.get('deadline')}",
                         channel="webhook")
    return 1 if r.get("ok") else 0


def notify_interview(iv: dict, window: str) -> int:
    """面试提醒 → 通知（event_key 按面试+提醒窗口去重）。"""
    from server.services import notifier
    prefs = notifier.get_prefs()
    if prefs.get("notify_interview") != "1":
        return 0
    content = (f"{iv.get('scheduled_at')}\n\n公司：{iv.get('j_company','')}\n"
               f"岗位：{iv.get('j_title','')}\n轮次：{iv.get('round','')}\n\n"
               f"打开工作台查看 Interview Brief")
    r = notifier.enqueue("interview", f"面试提醒：{iv.get('j_company','')}·{iv.get('round','')}",
                         content, event_key=f"interview:{iv['id']}:{window}", channel="webhook")
    return 1 if r.get("ok") else 0


def notify_status_change(job_id: int, company: str, title: str, new_status: str) -> int:
    """状态变化 → 通知（event_key 按岗位+新状态去重）。"""
    from server.services import notifier
    prefs = notifier.get_prefs()
    if prefs.get("notify_status_change") != "1":
        return 0
    # 只对关键跃迁提醒
    if new_status not in ("Online Assessment", "Interview", "Offer", "Rejected"):
        return 0
    content = f"{company}\n{title}\n\n状态：{new_status}"
    r = notifier.enqueue("status_change", f"秋招进展：{company}·{title}",
                         content, event_key=f"status:{job_id}:{new_status}", channel="webhook")
    return 1 if r.get("ok") else 0


def notify_weekly_review(week: str, stats: dict, strategy: str) -> int:
    """周报 → 通知（event_key 按周次去重）。"""
    from server.services import notifier
    prefs = notifier.get_prefs()
    if prefs.get("notify_weekly_review") != "1":
        return 0
    f = stats.get("funnel", {})
    sample_sufficient = stats.get("applied", 0) >= 5
    content = (f"本周：\n发现 {stats.get('discovered',0)}\n高匹配 —\n"
               f"精投 {stats.get('shortlisted',0)}\n投递 {stats.get('applied',0)}\n"
               f"OA {stats.get('oa',0)}\n面试 {stats.get('interviews',0)}\n"
               f"Offer {stats.get('offers',0)}\n\n"
               f"转化率：投递→OA {f.get('apply_to_oa_rate') if f.get('apply_to_oa_rate') is not None else '—'}%\n\n"
               + ("" if sample_sufficient else "【样本不足】\n")
               + (strategy or ""))
    r = notifier.enqueue("weekly_review", f"📊 本周秋招周报 {week}", content,
                         event_key=f"weekly:{week}", channel="webhook")
    return 1 if r.get("ok") else 0


# ---------------------------------------------------------------- 投递计划 / 今日任务

def notify_weekly_plan(plan: dict) -> int:
    """周日 21:00：推送本周投递计划（周一~周五每天投哪些岗位）。"""
    from server.services import notifier
    prefs = notifier.get_prefs()
    if prefs.get("notify_daily_brief") != "1":
        return 0
    p = plan.get("plan", {})
    lines = [f"📅 本周投递计划（共 {plan.get('total', 0)} 个岗位 / "
             f"{plan.get('days', 0)} 天）"]
    if plan.get("practice_mode"):
        lines.append(f"前 {plan.get('practice_days')} 天：小厂优先练手")
    lines.append("")
    for ds in sorted(p.keys()):
        jobs = p[ds]
        if not jobs:
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            wd = "一二三四五六日"[d.weekday()]
        except Exception:
            wd = ""
        lines.append(f"【周{wd} {ds}】{len(jobs)} 个")
        for i, j in enumerate(jobs, 1):
            sal = f" · {j['salary']}" if j.get("salary") else ""
            fit = f" · {int(j['fit_score'])}分" if j.get("fit_score") else ""
            lines.append(f"{i}. {j.get('company','—')} · {j.get('title','—')}{fit}{sal}")
        lines.append("")
    content = "\n".join(lines).strip()
    if not content or len(p) == 0:
        content = "本周暂无待投递岗位。\n\n请在周六周日把岗位批量粘贴进工作台，周日晚上会自动排期。"
    r = notifier.enqueue("weekly_plan", "📅 本周投递计划（周一~周五）",
                         content, event_key=f"plan:{plan.get('start')}",
                         channel="webhook")
    return 1 if r.get("ok") else 0


def notify_today_tasks(t: dict) -> int:
    """工作日 07:00：推送今日任务（今天要投的岗位 + 今日面试 + 临近截止）。"""
    from server.services import notifier
    prefs = notifier.get_prefs()
    if prefs.get("notify_daily_brief") != "1":
        return 0
    ds = t.get("date")
    lines = [f"☀️ 今日秋招 · {ds}（周{t.get('weekday','')}）", ""]

    jobs = t.get("jobs", [])
    lines.append(f"【今日投递名单】{len(jobs)} 个")
    if jobs:
        for i, j in enumerate(jobs, 1):
            sal = f" · {j['salary']}" if j.get("salary") else ""
            fit = f" · {int(j['fit_score'])}分" if j.get("fit_score") else ""
            scale = f" · {j['company_scale']}" if j.get("company_scale") else ""
            lines.append(f"{i}. {j.get('company','—')}｜{j.get('title','—')}{fit}{scale}{sal}")
    else:
        lines.append("  （今日无排期岗位）")
    lines.append("")

    ivs = t.get("interviews", [])
    lines.append(f"【今日面试】{len(ivs)} 场")
    if ivs:
        for i in ivs:
            tm = (i.get("scheduled_at") or "")[-8:-3]
            lines.append(f"  🕐 {tm} · {i.get('company','—')}｜{i.get('position','—')}")
            lines.append(f"     轮次：{i.get('round','—')}"
                         + (f" · 形式：{i['format']}" if i.get("format") else "")
                         + (f" · 面试官：{i['interviewer']}" if i.get("interviewer") else ""))
            if i.get("next_step"):
                lines.append(f"     下一步：{i['next_step']}")
    else:
        lines.append("  （今日无面试）")
    lines.append("")

    dls = t.get("deadlines", [])
    if dls:
        lines.append(f"【临近截止】{len(dls)} 个")
        for d in dls:
            tag = "🔴 TODAY" if d["days"] == 0 else f"⚠️ {d['days']} 天后"
            lines.append(f"  {tag} · {d['company']}｜{d['title']}（{d['deadline']}）")
        lines.append("")

    lines.append("打开「秋招工作台」桌面 App 查看完整 JD 与定制材料。")
    content = "\n".join(lines).strip()
    r = notifier.enqueue("today_tasks", f"☀️ 今日秋招 {ds}", content,
                         event_key=f"today:{ds}", channel="webhook")
    return 1 if r.get("ok") else 0
