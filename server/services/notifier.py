"""Notifier 通知分发服务：事件入队（去重）+ Webhook Dispatcher + 失败重试。

架构：
  Event（业务逻辑触发）
    ↓ enqueue(kind, event_key, title, content)
  notification 表（event_key 去重，status=pending）
    ↓ dispatch()
  Dispatcher（按 channel / 配置的 webhook 类型分发）
    ↓
  Web 通知页（session）+ 微信/Webhook（Server酱/PushPlus/企业微信机器人）

安全与可靠性：
- Secret/Token 从 .env 读取，绝不写死、不进日志；
- 发送失败只标 status=failed + error_message，绝不影响岗位/投递/面试/自动化主流程；
- event_key 去重，同一事件不会重复发送。
"""
from __future__ import annotations

import json

import httpx

from server import config
from server.db import connect

# 支持的 Webhook 类型
WEBHOOK_TYPES = ("serverchan", "pushplus", "wecom")

# 通知偏好默认值（key → default，存 automation_settings 复用同一表）
NOTIFY_PREFS = {
    "notify_enabled": "1",          # 微信/Webhook 总开关
    "notify_daily_brief": "1",      # 每日早报
    "notify_high_match": "1",       # 高匹配岗位
    "notify_deadline": "1",         # 截止提醒
    "notify_interview": "1",        # 面试提醒
    "notify_status_change": "1",    # 状态变化
    "notify_weekly_review": "1",    # 周报
    "webhook_type": "",             # serverchan / pushplus / wecom（空=未配置）
    "webhook_key": "",              # 见 .env，此处仅存类型标识
}


def _webhook_secret() -> dict:
    """从 .env 读取 webhook 配置（Secret 不落库、不进日志）。"""
    env = config.load_env()
    return {
        "type": env.get("WEBHOOK_TYPE", "").strip(),
        "serverchan_key": env.get("SERVERCHAN_SENDKEY", "").strip(),
        "pushplus_token": env.get("PUSHPLUS_TOKEN", "").strip(),
        "wecom_key": env.get("WECOM_WEBHOOK_KEY", "").strip(),
    }


def get_prefs() -> dict:
    conn = connect()
    try:
        rows = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM automation_settings")}
    finally:
        conn.close()
    merged = dict(NOTIFY_PREFS)
    merged.update(rows)
    return merged


def save_prefs(patch: dict) -> dict:
    conn = connect()
    try:
        for k, v in patch.items():
            if k in NOTIFY_PREFS:
                conn.execute(
                    "INSERT INTO automation_settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated_at=datetime('now','localtime')", (k, str(v)))
        conn.commit()
    finally:
        conn.close()
    return get_prefs()


# ---------------------------------------------------------------- 事件入队（去重）

def enqueue(kind: str, title: str, content: str, event_key: str | None = None,
            channel: str = "session") -> dict:
    """入队通知。event_key 去重：已存在同 key 且未 failed 则跳过。"""
    conn = connect()
    try:
        if event_key:
            exist = conn.execute(
                "SELECT id FROM notification WHERE event_key=?", (event_key,)).fetchone()
            if exist:
                return {"ok": False, "reason": "dedup", "notification_id": exist["id"]}
        cur = conn.execute(
            "INSERT INTO notification (kind, title, content, channel, event_key, status) "
            "VALUES (?,?,?,?,?, 'pending')",
            (kind, title, content, channel, event_key))
        conn.commit()
        return {"ok": True, "notification_id": cur.lastrowid}
    finally:
        conn.close()


# ---------------------------------------------------------------- Webhook 发送

def _send_serverchan(key: str, title: str, content: str) -> tuple[bool, str]:
    url = f"https://sctapi.ftqq.com/{key}.send"
    # Server酱 desp 走 Markdown 渲染：标准 Markdown 中单个 \n 会被合并成空格，
    # 只有「行尾两个空格 + 换行」才是硬换行。这里统一转成硬换行，保证微信里每条一行。
    content = (content or "").replace("\n", "  \n")
    r = httpx.post(url, data={"title": title, "desp": content}, timeout=15)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    j = r.json()
    if j.get("code") != 0:
        return False, f"Server酱错误: {j.get('message', j)}"
    return True, ""


def _send_pushplus(token: str, title: str, content: str) -> tuple[bool, str]:
    r = httpx.post("https://www.pushplus.plus/send", json={
        "token": token, "title": title, "content": content,
        "template": "txt"}, timeout=15)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    j = r.json()
    if j.get("code") not in (200, 0):
        return False, f"PushPlus错误: {j.get('msg', j)}"
    return True, ""


def _send_wecom(key: str, title: str, content: str) -> tuple[bool, str]:
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    r = httpx.post(url, json={
        "msgtype": "markdown",
        "markdown": {"content": f"**{title}**\n\n{content}"}}, timeout=15)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    j = r.json()
    if j.get("errcode") != 0:
        return False, f"企业微信错误: {j.get('errmsg', j)}"
    return True, ""


def _dispatch_webhook(title: str, content: str) -> tuple[bool, str]:
    """按 .env 配置的 webhook 类型发送。未配置返回 False + 说明。"""
    sec = _webhook_secret()
    wt = sec["type"]
    if wt == "serverchan":
        if not sec["serverchan_key"]:
            return False, "未配置 SERVERCHAN_SENDKEY"
        return _send_serverchan(sec["serverchan_key"], title, content)
    if wt == "pushplus":
        if not sec["pushplus_token"]:
            return False, "未配置 PUSHPLUS_TOKEN"
        return _send_pushplus(sec["pushplus_token"], title, content)
    if wt == "wecom":
        if not sec["wecom_key"]:
            return False, "未配置 WECOM_WEBHOOK_KEY"
        return _send_wecom(sec["wecom_key"], title, content)
    return False, "未配置 webhook（.env 中设置 WEBHOOK_TYPE 及对应 Secret）"


def dispatch_one(notification_id: int) -> dict:
    """发送单条 pending 通知。成功→sent；失败→failed+error_message（不影响主流程）。"""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM notification WHERE id=?", (notification_id,)).fetchone()
        if not row:
            return {"error": "not found"}
        if row["status"] == "sent":
            return {"ok": True, "already": True}
        ok, err = _dispatch_webhook(row["title"], row["content"])
        if ok:
            conn.execute("UPDATE notification SET status='sent', sent_at=datetime('now','localtime'), "
                         "error_message=NULL WHERE id=?", (notification_id,))
            conn.commit()
            return {"ok": True, "status": "sent"}
        conn.execute("UPDATE notification SET status='failed', error_message=? WHERE id=?",
                     (err[:300], notification_id))
        conn.commit()
        return {"ok": False, "status": "failed", "error": err}
    finally:
        conn.close()


def dispatch_pending(limit: int = 20) -> dict:
    """批量发送所有 pending 通知（返回成功/失败计数）。"""
    conn = connect()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM notification WHERE status='pending' ORDER BY id LIMIT ?", (limit,))]
    finally:
        conn.close()
    sent = failed = 0
    for nid in ids:
        r = dispatch_one(nid)
        if r.get("ok"):
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "total": len(ids)}


def retry_failed(limit: int = 20) -> dict:
    """重试 failed 通知。"""
    conn = connect()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM notification WHERE status='failed' ORDER BY id LIMIT ?", (limit,))]
    finally:
        conn.close()
    sent = 0
    for nid in ids:
        r = dispatch_one(nid)
        if r.get("ok"):
            sent += 1
    return {"retried": len(ids), "recovered": sent}


def send_test_notification() -> dict:
    """测试通知：发送一条测试消息到 webhook。"""
    ok, err = _dispatch_webhook("秋招工作台通知测试", "秋招工作台微信通知测试成功。")
    if ok:
        return {"ok": True, "message": "测试通知发送成功"}
    return {"ok": False, "message": f"测试通知发送失败：{err}"}


def webhook_status() -> dict:
    """返回 webhook 配置状态（不暴露 Secret 值，只报是否已配置）。"""
    sec = _webhook_secret()
    wt = sec["type"]
    configured = (wt == "serverchan" and bool(sec["serverchan_key"])) or \
                 (wt == "pushplus" and bool(sec["pushplus_token"])) or \
                 (wt == "wecom" and bool(sec["wecom_key"]))
    return {"type": wt or None, "configured": configured,
            "available_types": list(WEBHOOK_TYPES)}
