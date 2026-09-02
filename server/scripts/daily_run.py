"""每日自动化执行入口（WorkBuddy Automations 定时调用的脚本）。

模式：
  python daily_run.py radar   — 工作日岗位雷达：抓可抓来源 → 去重 → JD 分析 → 评分 → 今日推荐
  python daily_run.py brief   — 每日简报：生成今日待办 + 截止提醒 + 面试提醒（写 notification）
  python daily_run.py review  — 周末周报：Weekly Review + 下周策略

诚实原则：
- 真实能抓的（服务端渲染站，如广东人才网）就抓；SPA/签名/反爬的一律不硬来，走手动粘贴 JD；
- 绝不自动投递，最终投递永远由人工确认；
- 20 是目标不是 KPI，宁缺毋滥。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from server import config  # noqa: E402
from server.db import connect  # noqa: E402
from server.services import daily, fit, radar, review  # noqa: E402


# 已适配可抓取来源（服务端渲染，无需登录/签名）——各站一个抓取函数，返回 [{company,title,url,jd}]
# 抓取规则：从列表页提取 jobs_show 链接 → 抓详情页 → 提取 JD 文本
ADAPTED_SOURCES = {
    "gdrc": {
        "base": "https://www.gdrc.com/",
        "list_url": "https://www.gdrc.com/index.php?m=&c=jobs&a=jobs_list",
        "detail_re": re.compile(r"jobs_show&id=(\d+)"),
        "detail_tpl": "https://www.gdrc.com/index.php?m=&c=jobs&a=jobs_show&id={id}",
        "source_name": "广东人才网",
    },
}


def _fetch(url: str) -> str:
    import httpx
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _extract_jd_fields(html: str) -> dict:
    """从服务端渲染的岗位详情页提取 JD 文本。

    广东人才网详情页的 <title> 是通用「求职招聘详情」，真实岗位信息在正文：
      第 1 行：{岗位}-{公司}-{来源}（即标题行）
      正文含「岗位职责：/岗位要求：」等 JD 正文。
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    title = ""
    if lines and re.match(r".+-.+-.+", lines[0]):
        title = lines[0]
    else:
        # 兜底：找形如「XX-XX-XX」的第一行
        for l in lines[:5]:
            if l.count("-") >= 2:
                title = l
                break
    text = re.sub(r"\n{2,}", "\n", text)
    return {"title": title, "jd_text": text[:3000]}


def run_radar(daily_target: int = 20, source_ids: list[int] | None = None) -> dict:
    """工作日岗位雷达：对已适配来源抓取 → 去重 → 分析评分。"""
    settings = daily.get_settings()
    target = int(settings.get("daily_target", daily_target))
    imported = 0
    filtered = 0   # 非目标方向（非 UI/UX）被过滤掉的岗位数
    errors = []
    conn = connect()
    try:
        enabled_sources = [dict(r) for r in conn.execute(
            "SELECT * FROM job_source WHERE enabled=1 ORDER BY priority DESC, id")]
    finally:
        conn.close()
    if source_ids:
        enabled_sources = [s for s in enabled_sources if s["id"] in source_ids]

    # 只处理已适配的服务端渲染来源（SPA/签名的一律跳过，交给手动粘贴）
    for src in enabled_sources:
        key = next((k for k, cfg in ADAPTED_SOURCES.items() if cfg["base"] in (src["url"] or "")), None)
        if not key:
            continue
        cfg = ADAPTED_SOURCES[key]
        try:
            list_html = _fetch(cfg["list_url"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cfg['source_name']} 列表抓取失败: {e}")
            continue
        ids = cfg["detail_re"].findall(list_html)
        # 去重 + 限量
        seen, added = 0, 0
        for jid in ids:
            if added >= 3:  # 每次每来源最多抓 3 个，避免过度抓取
                break
            detail_url = cfg["detail_tpl"].format(id=jid)
            try:
                detail_html = _fetch(detail_url)
            except Exception as e:  # noqa: BLE001
                errors.append(f"详情抓取失败 {jid}: {e}")
                continue
            jd = _extract_jd_fields(detail_html)
            if not jd["jd_text"]:
                continue
            # 从标题粗略提取公司/岗位（服务端渲染详情页标题形如「岗位-公司-来源」）
            company, title = _split_title(jd["title"], src["source_name"])
            if not company or not title:
                continue
            # 方向硬过滤：只保留目标方向（UI/UX 等），其他方向直接跳过，不入库
            if not fit.is_target_direction(fit.guess_direction(title, jd["jd_text"])):
                filtered += 1
                continue
            r = fit.add_job(company=company, title=title, location="", url=detail_url,
                            source=cfg["source_name"], source_url=cfg["base"],
                            job_type="校招", jd_text=jd["jd_text"])
            if r.get("error") == "duplicate":
                seen += 1
                continue
            if r.get("job_id"):
                added += 1
                imported += 1
                fit.analyze_job(r["job_id"])  # 自动分析评分
        if added == 0 and seen > 0:
            errors.append(f"{cfg['source_name']} 无新岗位（已去重 {seen}）")

    # 生成今日推荐（A/B/C 分级）
    cand = radar.today_candidates(50)
    summary = (f"今日雷达：新增 {imported} 个 UI/UX 岗位，A级 {cand['strong']} / B级 {cand['medium']} "
               f"/ C级 {cand['weak']} / 过滤 D {cand['D_filtered']}"
               + (f"；非 UI/UX 方向已过滤 {filtered} 个" if filtered else ""))
    daily.record_run("job_radar", "ok" if imported >= 0 else "partial", summary)
    if imported > 0 or cand["strong"] > 0:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO notification (kind, title, content, channel) VALUES (?,?,?,?)",
                ("daily_summary", "今日岗位雷达", summary, "session"))
            conn.commit()
        finally:
            conn.close()
    return {"imported": imported, "filtered_non_uiux": filtered,
            "candidates": cand, "summary": summary, "errors": errors}


def _split_title(raw_title: str, source_name: str) -> tuple[str, str]:
    """从详情页标题解析公司/岗位。

    广东人才网详情页正文首行形如「设计工程师-广东新粤交通投资有限公司-广东人才网」，
    即「岗位-公司-来源」。公司名可能含『-』（如「广东-XX公司」），故采用：
      取最后一段为来源（丢弃），倒数第二段到第一段之间的部分为公司，第一段为岗位。
    返回 (company, title)。
    """
    t = (raw_title or "").strip().strip("- ")
    parts = [p.strip() for p in t.split("-") if p.strip()]
    if len(parts) >= 3:
        # 最后一段是来源站点名，丢弃；倒数第二段是公司；其余（除第一段岗位）并入公司
        title = parts[0]
        company = "-".join(parts[1:-1])  # 中间全部属于公司名（容忍公司名含 -）
        return company, title
    if len(parts) == 2:
        return parts[1], parts[0]  # 公司, 岗位
    return "", parts[0] if parts else ""


def run_brief() -> dict:
    """每日简报：今日待办 + 截止提醒 + 面试提醒 → 写 notification（含 webhook 去重）。"""
    b = daily.today_briefing()
    # 每日早报（webhook 通知）
    daily.notify_daily_brief(b)
    # 截止提醒
    for d in b["deadlines"]:
        if d.get("days") is not None and d["days"] <= 3:
            conn = connect()
            try:
                exist = conn.execute(
                    "SELECT id FROM notification WHERE kind='deadline' AND content LIKE ? "
                    "AND date(created_at)=date('now','localtime')",
                    (f"%{d['company']}%{d['title']}%",)).fetchone()
                if not exist:
                    daily.notify_deadline(d, d["days"])
            finally:
                conn.close()
    # 面试提醒
    for iv in b["upcoming_interviews"]:
        if iv.get("in_hours") is not None and iv["in_hours"] <= 24:
            window = "24h" if iv["in_hours"] > 2 else ("2h" if iv["in_hours"] > 0.5 else "30m")
            daily.notify_interview(iv, window)
    daily.record_run("daily_brief", "ok",
                     f"今日发现 {b['today_jobs']} · 待处理 {b['pending']} · 截止提醒 {len(b['deadlines'])} · 面试 {len(b['upcoming_interviews'])}")
    return {"briefing": b}


def run_review() -> dict:
    r = review.generate_weekly_review()
    daily.notify_weekly_review(r["week"], r["stats"], r["strategy"])
    daily.record_run("weekly_review", "ok",
                     f"周报 {r['week']}：发现 {r['stats']['discovered']} · 投递 {r['stats']['applied']}")
    return r


def run_plan(replan: bool = True) -> dict:
    """周日 21:00：生成本周投递计划（周一~周五每天投哪些）并推送微信。"""
    from server.services import planner
    if replan:
        cleared = planner.clear_plan()
    else:
        cleared = 0
    plan = planner.build_plan()
    daily.notify_weekly_plan(plan)
    # 推送
    from server.services import notifier
    disp = notifier.dispatch_pending()
    summary = (f"本周排期：{plan['total']} 个岗位 / {plan['days']} 天 / "
               f"每天 {plan['daily_quota']} 个")
    daily.record_run("weekly_plan", "ok",
                     f"{summary}（重排 {cleared} 个）· 推送 成功{disp.get('sent',0)}")
    return {"plan": plan, "cleared": cleared, "dispatch": disp, "summary": summary}


def run_today() -> dict:
    """工作日 07:00：推送今日任务（今日投递名单 + 今日面试 + 临近截止）。"""
    from server.services import notifier, planner
    t = planner.get_today_tasks()
    daily.notify_today_tasks(t)
    disp = notifier.dispatch_pending()
    summary = (f"今日任务：投递 {len(t['jobs'])} 个 · 面试 {len(t['interviews'])} 场 · "
               f"临近截止 {len(t['deadlines'])} 个")
    daily.record_run("today_tasks", "ok", f"{summary} · 推送 成功{disp.get('sent',0)}")
    return {"tasks": {k: v for k, v in t.items() if k != "jobs"},
            "job_count": len(t["jobs"]), "dispatch": disp, "summary": summary}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "brief"
    if mode == "radar":
        out = run_radar()
    elif mode == "brief":
        out = run_brief()
    elif mode == "review":
        out = run_review()
    elif mode == "plan":
        out = run_plan(replan="--keep" not in sys.argv)
    elif mode == "today":
        out = run_today()
    else:
        print("用法：daily_run.py [radar|brief|review|plan|today]")
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
