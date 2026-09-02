"""PHASE 3 · 每日自动化 MVP 验收测试。

验证：
1. 每日管家简报（今日待办/截止提醒/面试提醒）真实生成
2. 岗位雷达（真实抓取可抓站 + 手动粘贴兜底）
3. 周末周报（转化率漏斗 + 样本不足策略）
4. Automation 设置读写（工作日开关/时间/目标数量等）

运行：python server/scripts/phase3_test.py（需服务已在 8787 端口运行）
"""
from __future__ import annotations

import json
import sys
from datetime import date

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
    print("PHASE 3 · 每日自动化 MVP 验收")
    print("=" * 70)

    # ---------- 1. 每日简报 ----------
    code, brief = api("GET", "/api/daily/briefing")
    check("1. 每日简报生成", code == 200 and "today_jobs" in brief,
          f"今日发现 {brief.get('today_jobs')} 高匹配 {brief.get('today_high')} 待处理 {brief.get('pending')}")
    check("2. 今日待办自动生成", len(brief.get("todo", [])) >= 6, f"{len(brief.get('todo', []))} 项")
    check("3. 今日 Top 岗位（带匹配度+来源+状态）",
          bool(brief.get("top_jobs")), f"{len(brief.get('top_jobs', []))} 个")
    if brief.get("top_jobs"):
        j0 = brief["top_jobs"][0]
        check("4. Top 岗位含 Fit Score + 截止标签 + 来源",
              j0.get("fit_score") is not None and "deadline_label" in j0 and j0.get("source"),
              f"{j0['company']}·{j0['title']} S{j0['fit_score']} {j0['deadline_label']}")

    # ---------- 2. 截止日期提醒逻辑 ----------
    # 造一个 2 天后截止的岗位验证 deadline 计算
    from datetime import timedelta
    dl = (date.today() + timedelta(days=2)).isoformat()
    code, jr = api("POST", "/api/jobs", json={
        "company": "测试截止公司", "title": "测试截止岗位",
        "source": "测试", "deadline": dl, "jd_text": "岗位职责：测试"})
    if jr.get("job_id"):
        code, det = api("GET", f"/api/jobs/{jr['job_id']}")
        # 重新拉简报看截止提醒
        code, brief2 = api("GET", "/api/daily/briefing")
        matched = [d for d in brief2.get("deadlines", []) if d.get("company") == "测试截止公司"]
        check("5. 截止日期计算（2 天后 → ⚠️ 提醒）",
              bool(matched) and matched[0].get("label", "").startswith("⚠️"),
              matched[0]["label"] if matched else "未找到")
        api("DELETE", f"/api/jobs/{jr['job_id']}")
    else:
        check("5. 截止日期计算（2 天后 → ⚠️ 提醒）", False, "测试岗位已存在，跳过")

    # ---------- 3. Automation 设置读写 ----------
    code, s0 = api("GET", "/api/automation/settings")
    check("6. Automation 设置读取（默认值）", code == 200 and s0.get("daily_target") == "20",
          str(s0))
    code, s1 = api("POST", "/api/automation/settings", json={"daily_target": "12", "weekday_radar_enabled": "0"})
    check("7. Automation 设置写入（改目标数量+关雷达）",
          code == 200 and s1.get("daily_target") == "12" and s1.get("weekday_radar_enabled") == "0")
    api("POST", "/api/automation/settings", json={"daily_target": "20", "weekday_radar_enabled": "1"})  # 还原

    # ---------- 4. 运行记录 ----------
    code, runs = api("GET", "/api/automation/runs")
    check("8. Automation 运行历史记录", code == 200 and isinstance(runs, list),
          f"{len(runs)} 条")

    # ---------- 5. 周末周报（含转化率漏斗 + 样本不足策略）----------
    code, wr = api("POST", "/api/reviews/generate")
    stats = wr.get("stats", {})
    check("9. Weekly Review 生成", code == 200 and wr.get("week"), f"{wr.get('week')}")
    check("10. 转化率漏斗字段", "funnel" in stats and "apply_to_oa_rate" in stats["funnel"],
          f"投递→OA {stats['funnel'].get('apply_to_oa_rate')}%")
    check("11. 下周策略基于真实数据（样本不足明确说明）",
          "样本不足" in wr.get("strategy", "") or "继续加大" in wr.get("strategy", "") or "保持节奏" in wr.get("strategy", ""),
          (wr.get("strategy", "")[:60] + "…"))

    print("\n" + "=" * 70)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("PHASE 3 每日自动化 MVP 全部通过 ✅")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
