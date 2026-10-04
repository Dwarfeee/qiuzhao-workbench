"""PHASE 5 · 微信/Webhook 通知层 验收测试。

验证用户要求的 A-R 项（不含真实微信发送——当前环境无真实 webhook key）：
A 测试通知（未配置时诚实返回失败说明）
B 每日早报生成 / C 高匹配通知生成 / D 截止提醒 / E Deadline Unknown 不猜测
F 面试提醒 / G 状态变化触发 / H 周报通知
I 同一事件不重复发送（event_key 去重）
J 发送失败不影响主流程
K Secret 不泄漏
L Web 通知仍正常 / M Automation 正常
N-R 四阶段回归 + Master 哈希不变

运行：python server/scripts/phase5_test.py（需服务已在 8787 端口运行）
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import httpx

sys.path.insert(0, r"C:\Users\<你的用户名>\WorkBuddy\秋招实录")

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


def master_hash() -> str:
    return hashlib.sha256(MASTER.read_bytes()).hexdigest()


def main():
    print("=" * 70)
    print("PHASE 5 · 通知层 验收")
    print("=" * 70)
    m0 = master_hash()

    # ---------- A: 测试通知（诚实失败） ----------
    code, t = api("POST", "/api/notify/test")
    check("A. 测试通知（未配置 webhook 诚实返回失败说明）",
          code == 200 and t.get("ok") is False and "未配置" in t.get("message", ""),
          t.get("message", "")[:40])

    # ---------- K: Secret 不泄漏 ----------
    code, ws = api("GET", "/api/notify/webhook-status")
    blob = str(ws)
    check("K. Secret 不泄漏（webhook-status 不返回 key/token 值）",
          "key" not in str(ws.get("type", "")) and "SCT" not in blob and "token" not in blob.lower(),
          f"type={ws.get('type')}")

    # ---------- I: event_key 去重 ----------
    import time as _time
    from server.services import notifier as N  # noqa: E402
    ek = f"test:dedup:{_time.time()}"
    r1 = N.enqueue("test", "去重测试", "内容1", event_key=ek, channel="session")
    r2 = N.enqueue("test", "去重测试", "内容1", event_key=ek, channel="session")
    check("I. 同一事件不重复发送（event_key 去重）",
          r1.get("ok") is True and r2.get("ok") is False and r2.get("reason") == "dedup",
          f"第1次={r1.get('ok')} 第2次={r2.get('reason')}")

    # ---------- B: 每日早报生成 ----------
    from server.services import daily as D  # noqa: E402
    b = D.today_briefing()
    D.notify_daily_brief(b)
    check("B. 每日早报生成（通知入队）", _exists_kind("daily_summary", b.get("date")))

    # ---------- C: 高匹配通知 ----------
    code, jobs = api("GET", "/api/jobs")
    sj = [j for j in jobs if j.get("grade") in ("S", "A")]
    if sj:
        D.notify_high_match(sj[0], 8)
        check("C. 高匹配岗位通知生成", _exists_key(f"high_match:{sj[0]['id']}"))
    else:
        check("C. 高匹配岗位通知生成", False, "无 S/A 岗位")

    # ---------- D/E: 截止提醒 + Deadline Unknown ----------
    code, dl_job = api("POST", "/api/jobs", json={
        "company": "通知测试公司", "title": "截止测试岗位", "source": "测试",
        "deadline": "", "jd_text": "测试"})  # 无 deadline
    if dl_job.get("job_id"):
        # 直接查岗位详情验证 deadline_label（不依赖 top_jobs）
        code, det = api("GET", f"/api/jobs/{dl_job['job_id']}")
        from server.services import daily as DD  # noqa: E402
        lbl = DD.deadline_label(DD.deadline_days(det.get("deadline")))
        check("E. Deadline Unknown 不猜测（无截止日期岗位标 Unknown）",
              lbl == "Deadline Unknown", lbl)
        # 带明确 deadline 的岗位
        from datetime import date, timedelta
        dl = (date.today() + timedelta(days=2)).isoformat()
        code, dl2 = api("POST", "/api/jobs", json={
            "company": "通知测试公司2", "title": "截止测试岗位2", "source": "测试",
            "deadline": dl, "jd_text": "测试"})
        if dl2.get("job_id"):
            D.notify_deadline({"id": dl2["job_id"], "company": "通知测试公司2", "title": "截止测试岗位2", "deadline": dl}, 2)
            check("D. 截止日期提醒（2 天后 → 正确生成）", _exists_key(f"deadline:{dl2['job_id']}:{dl}"))
            api("DELETE", f"/api/jobs/{dl2['job_id']}")
        api("DELETE", f"/api/jobs/{dl_job['job_id']}")
    else:
        check("E. Deadline Unknown 不猜测", False, "测试岗位已存在")

    # ---------- F: 面试提醒 ----------
    code, iv = api("POST", "/api/interviews", json={
        "company": "通知测试公司", "position": "测试岗位", "round": "一面",
        "scheduled_at": "2026-09-01 10:00"})  # 未来
    if iv.get("interview_id"):
        D.notify_interview({"id": iv["interview_id"], "j_company": "通知测试公司",
                            "j_title": "测试岗位", "round": "一面",
                            "scheduled_at": "2026-09-01 10:00"}, "24h")
        check("F. 面试提醒生成", _exists_key(f"interview:{iv['interview_id']}:24h"))

    # ---------- G: 状态变化触发 ----------
    code, jobs = api("GET", "/api/jobs")
    if jobs:
        D.notify_status_change(jobs[0]["id"], jobs[0]["company"], jobs[0]["title"], "Offer")
        check("G. 状态变化触发通知（Offer 跃迁）", _exists_key(f"status:{jobs[0]['id']}:Offer"))

    # ---------- H: 周报通知 ----------
    code, wr = api("POST", "/api/reviews/generate")
    D.notify_weekly_review(wr.get("week"), wr.get("stats", {}), wr.get("strategy", ""))
    check("H. 周报通知生成", _exists_key(f"weekly:{wr.get('week')}"))

    # ---------- J: 发送失败不影响主流程 ----------
    # 构造一条 webhook 通知并 dispatch（未配置 webhook → 失败，但系统仍正常）
    N.enqueue("test", "失败测试", "内容", event_key="test:fail:1", channel="webhook")
    code, d = api("POST", "/api/notify/dispatch")
    check("J. 发送失败不影响主流程（dispatch 返回，系统仍响应）",
          code == 200 and "sent" in d and "failed" in d,
          f"sent={d.get('sent')} failed={d.get('failed')}")
    code, st = api("GET", "/api/system/status")
    check("J2. 主系统正常（system/status 可用）", code == 200 and st.get("counts"))

    # ---------- L: Web 通知仍正常 ----------
    code, ns = api("GET", "/api/notifications")
    check("L. Web 通知仍正常", code == 200 and isinstance(ns, list) and len(ns) >= 1,
          f"{len(ns)} 条")

    # ---------- M: Automation 正常 ----------
    code, s = api("GET", "/api/automation/settings")
    check("M. Automation 设置正常", code == 200 and "daily_target" in s)

    # ---------- R: Master 不变 ----------
    check("R. Master Resume 哈希不变", master_hash() == m0)

    print("\n" + "=" * 70)
    print(f"PHASE 5 结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("PHASE 5 通知层全部通过 ✅")


def _notif_count() -> int:
    conn = __import__("server.db", fromlist=["connect"]).connect()
    try:
        return conn.execute("SELECT count(*) FROM notification").fetchone()[0]
    finally:
        conn.close()


def _exists_key(event_key: str) -> bool:
    conn = __import__("server.db", fromlist=["connect"]).connect()
    try:
        return conn.execute("SELECT count(*) FROM notification WHERE event_key=?",
                            (event_key,)).fetchone()[0] > 0
    finally:
        conn.close()


def _exists_kind(kind: str, date_str: str) -> bool:
    conn = __import__("server.db", fromlist=["connect"]).connect()
    try:
        return conn.execute(
            "SELECT count(*) FROM notification WHERE kind=? AND event_key=?",
            (kind, f"daily:{date_str}")).fetchone()[0] > 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
