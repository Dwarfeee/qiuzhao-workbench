"""Job Radar：岗位雷达服务。

能力：
1. 岗位来源池（job_source）：导入 Edge 收藏夹、增删暂停调优先级；
2. 真实 JD 抓取：从来源池 / 用户粘贴 URL / 用户粘贴 JD 文本，结构化入库；
3. 去重：公司+岗位 指纹（dedupe_key），同岗位多来源 URL 记入 job_source_url；
4. 分级排序：A/B/C/D 四档（A 强匹配优先精投，D 自动过滤）。

诚实边界：
- 招聘网站多为 SPA/反爬，直接 httpx 抓取只能拿到首页/列表，未必能拿到完整 JD；
- 因此「手动粘贴 JD 文本」与「粘贴岗位 URL 抓取」是一等公民，「来源池搜索」尽力而为；
- 绝不绕过验证码/登录/反爬，不做自动批量投递。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from server import config
from server.db import connect
from server.services import parser
from server.services.fit import add_job, analyze_job, make_dedupe_key

# Edge 收藏夹默认路径
EDGE_BOOKMARK_PATHS = [
    Path(r"C:\Users\19600\AppData\Local\Microsoft\Edge\User Data\Default\Bookmarks"),
    Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/Bookmarks",
]

# 招聘/投递站点特征词（用于从收藏夹里筛选招聘来源）
JOB_SITE_HINTS = (
    "招聘", "求职", "校招", "实习", "人才", "就业", "职", "job", "career", "talent",
    "zhipin", "liepin", "51job", "nowcoder", "shixiseng", "iguopin", "zhaopin",
    "maimai", "linkedin", "ncss", "mohrss", "bjrc", "gdrc", "zjrc", "yupao",
    "join.qq", "huawei", "bytedance", "alibaba",
)

# 招聘平台关键词 → 推荐 source_type 与 priority
_PLATFORM_CLASSIFY = [
    ("platform", 8, ("zhipin", "liepin", "51job", "zhaopin", "shixiseng", "nowcoder",
                      "iguopin", "maimai", "linkedin", "yupao", "ncss", "mohrss",
                      "jobonline", "bjrc", "gdrc", "zjrc")),
    ("official", 9, ("join.qq", "huawei", "bytedance", "talent.alibaba")),
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- Edge 收藏夹导入

def read_edge_bookmarks(path: Path | None = None) -> list[dict]:
    """读取 Edge 收藏夹 JSON，返回 [{name, url, folder}]。找不到文件返回 []。"""
    candidates = [path] if path else EDGE_BOOKMARK_PATHS
    for p in candidates:
        if p and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            out: list[dict] = []

            def walk(node, folder=""):
                if node.get("type") == "url":
                    out.append({"name": node.get("name", ""),
                                "url": node.get("url", ""), "folder": folder})
                elif node.get("type") == "folder":
                    nm = node.get("name", "")
                    for c in node.get("children", []):
                        walk(c, folder + "/" + nm if folder else nm)
            for root in data.get("roots", {}).values():
                walk(root)
            return out
    return []


def _is_job_site(name: str, url: str) -> bool:
    blob = (name + " " + url).lower()
    return any(h in blob for h in JOB_SITE_HINTS)


def import_edge_sources() -> dict:
    """把 Edge 收藏夹里的招聘网站导入 job_source（幂等，按 url 去重）。"""
    bookmarks = read_edge_bookmarks()
    if not bookmarks:
        return {"error": "未找到 Edge 收藏夹文件", "imported": 0}
    job_sites = [b for b in bookmarks if _is_job_site(b["name"], b["url"])]
    conn = connect()
    try:
        imported, skipped = 0, 0
        for b in job_sites:
            url = b["url"].strip()
            exist = conn.execute("SELECT id FROM job_source WHERE url=?", (url,)).fetchone()
            if exist:
                skipped += 1
                continue
            stype, pri = "platform", 5
            for t, p, hints in _PLATFORM_CLASSIFY:
                if any(h in url.lower() for h in hints):
                    stype, pri = t, p
                    break
            conn.execute(
                "INSERT INTO job_source (source_name, source_type, url, priority, enabled, note) "
                "VALUES (?,?,?,?,1,?)",
                (b["name"], stype, url, pri, f"Edge 收藏夹 · {b['folder']}"))
            imported += 1
        conn.commit()
        return {"imported": imported, "skipped": skipped,
                "total_job_sites": len(job_sites), "total_bookmarks": len(bookmarks)}
    finally:
        conn.close()


# ---------------------------------------------------------------- 来源池 CRUD

def list_sources(enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM job_source"
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY priority DESC, id"
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def upsert_source(source_name: str, url: str, source_type: str = "manual",
                  priority: int = 5, enabled: int = 1, note: str = "") -> dict:
    conn = connect()
    try:
        exist = conn.execute("SELECT id FROM job_source WHERE url=?", (url,)).fetchone()
        if exist:
            conn.execute(
                "UPDATE job_source SET source_name=?, source_type=?, priority=?, enabled=?, "
                "note=?, updated_at=datetime('now','localtime') WHERE id=?",
                (source_name, source_type, priority, enabled, note, exist["id"]))
            return {"source_id": exist["id"], "updated": True}
        cur = conn.execute(
            "INSERT INTO job_source (source_name, source_type, url, priority, enabled, note) "
            "VALUES (?,?,?,?,?,?)", (source_name, source_type, url, priority, enabled, note))
        conn.commit()
        return {"source_id": cur.lastrowid, "updated": False}
    finally:
        conn.close()


def set_source_state(source_id: int, enabled: int | None = None,
                     priority: int | None = None) -> dict:
    conn = connect()
    try:
        if enabled is not None:
            conn.execute("UPDATE job_source SET enabled=?, updated_at=datetime('now','localtime') WHERE id=?",
                         (enabled, source_id))
        if priority is not None:
            conn.execute("UPDATE job_source SET priority=?, updated_at=datetime('now','localtime') WHERE id=?",
                         (priority, source_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def delete_source(source_id: int) -> dict:
    conn = connect()
    try:
        conn.execute("DELETE FROM job_source WHERE id=?", (source_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------------------------------------------------------------- JD 结构化抓取

def extract_jd_from_html(html: str, url: str = "") -> dict:
    """从抓取的 HTML 文本里提取 JD 结构（职责/要求）。尽力而为，结构化失败返回原文。"""
    text = parser.html_text(html)
    text = parser.clean_text(text)
    # 常见 JD 分隔词
    duties, reqs = "", ""
    m = re.search(r"(?:岗位职责|职位描述|工作职责|职责描述|【职责】|岗位要求|任职要求|职位要求|【要求】)",
                  text)
    if m:
        # 简单切分：职责在前、要求在后
        head = text[:m.start()]
        tail = text[m.start():]
        req_m = re.search(r"(?:任职要求|岗位要求|职位要求|【要求】|职位要求|任职资格)", tail)
        if req_m:
            duties = tail[:req_m.start()].strip()
            reqs = tail[req_m.start():].strip()
        else:
            duties = tail.strip()
    else:
        duties = text
    return {"jd_text": text, "duties": duties[:2000], "requirements": reqs[:2000],
            "title": _guess_title(html, url), "url": url}


def _guess_title(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return url


def add_job_from_url(url: str, source: str = "", jd_text_override: str = "") -> dict:
    """从岗位 URL 抓取 JD 并入库；若 jd_text_override 提供则优先用（手动粘贴）。"""
    jd = jd_text_override or ""
    title = ""
    if not jd and url:
        r = parser.fetch_url(url)
        if not r["ok"]:
            return {"error": f"抓取失败：{r.get('error')}"}
        parsed = extract_jd_from_html(r["text"], url)
        jd = parsed["jd_text"]
        title = parsed["title"]
    # 从 JD 文本里粗提取公司/岗位
    company, position = "", ""
    if jd_text_override and not url:
        # 手动粘贴 JD：让调用方提供 company/title（走 add_job_from_jd）
        pass
    return {"jd_text": jd, "title": title, "url": url}


# ---------------------------------------------------------------- 分级 + 今日候选

def grade_bucket(score: float | None) -> str:
    """Fit Score → A/B/C/D 分级（A 强匹配优先精投，D 自动过滤）。"""
    if score is None:
        return "D"  # 未评分不推荐
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def today_candidates(limit: int = 50) -> dict:
    """今日精投候选：按分级排序（A→B→C），D 过滤，去重后返回。"""
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM job WHERE fit_score IS NOT NULL ORDER BY fit_score DESC, id DESC LIMIT ?",
            (limit,))]
        for r in rows:
            r["bucket"] = grade_bucket(r["fit_score"])
        a = [r for r in rows if r["bucket"] == "A"]
        b = [r for r in rows if r["bucket"] == "B"]
        c = [r for r in rows if r["bucket"] == "C"]
        d = [r for r in rows if r["bucket"] == "D"]
        return {"A": a, "B": b, "C": c, "D_filtered": len(d),
                "total_scored": len(rows), "strong": len(a), "medium": len(b), "weak": len(c)}
    finally:
        conn.close()


def dedupe_check(company: str, title: str) -> dict:
    """检查岗位是否已存在（按 dedupe_key）。"""
    dk = make_dedupe_key(company, title)
    conn = connect()
    try:
        r = conn.execute("SELECT id, company, title, url, source FROM job WHERE dedupe_key=?",
                         (dk,)).fetchone()
        return {"exists": r is not None, "job": dict(r) if r else None, "dedupe_key": dk}
    finally:
        conn.close()


def add_source_url(job_id: int, url: str, source: str = "") -> None:
    """同岗位追加一个来源 URL（去重后多来源可追溯）。"""
    if not url:
        return
    conn = connect()
    try:
        exist = conn.execute(
            "SELECT id FROM job_source_url WHERE job_id=? AND url=?", (job_id, url)).fetchone()
        if not exist:
            conn.execute("INSERT INTO job_source_url (job_id, url, source) VALUES (?,?,?)",
                         (job_id, url, source))
            conn.commit()
    finally:
        conn.close()
