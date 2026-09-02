"""Candidate Knowledge Base 服务：入库、结构化、检索、关联。

事实原则：
- 只把「我上传的资料里真实存在的内容」写成 confidence='confirmed'
- 结构化推断（分类、标签、关联建议）一律 'possible'
- 找不到证据时返回空列表并明确告知，绝不编造
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from server import config
from server.db import connect, search_kb
from server.services import parser

CAT_DIRS = {
    "resume": "resumes", "portfolio": "portfolios", "project": "projects",
    "award": "awards", "certificate": "certificates", "education": "education",
    "experience": "experience", "personal": "other", "skill": "other", "other": "other",
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 公共

def _upsert_knowledge_item(conn, source_type: int | str, source_id: int, title: str,
                           text: str, summary: str = "", tags=None, confidence="confirmed"):
    conn.execute(
        """INSERT INTO knowledge_item (source_type, source_id, title, text, summary, tags, confidence)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(source_type, source_id) DO UPDATE SET
             title=excluded.title, text=excluded.text, summary=excluded.summary,
             tags=excluded.tags, confidence=excluded.confidence, updated_at=datetime('now','localtime')""",
        (source_type, source_id, title, text, summary, json.dumps(tags or [], ensure_ascii=False), confidence))


def add_relation(conn, ft, fi, tt, ti, relation, confidence, reason=""):
    try:
        conn.execute(
            "INSERT INTO knowledge_relation (from_type, from_id, to_type, to_id, relation, confidence, reason) "
            "VALUES (?,?,?,?,?,?,?)",
            (ft, fi, tt, ti, relation, confidence, reason))
    except sqlite3.IntegrityError:
        pass


# ---------------------------------------------------------------- 文件上传

def ingest_file(file_name: str, content: bytes, category: str, source: str = "web上传",
                tags=None) -> dict:
    """上传 → 保存 → 解析 → document + knowledge_item（自动进 FTS）。"""
    if category not in CAT_DIRS:
        category = "other"
    safe = re.sub(r"[^\w\u4e00-\u9fff.\-()（） ]+", "_", file_name)
    stem = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}_{safe}"
    dest_dir = config.CANDIDATE_DIR / CAT_DIRS[category]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / stem
    dest.write_bytes(content)

    ext = dest.suffix.lower().lstrip(".")
    text, parse_status, ocr_used = "", "parsed", False
    if ext == "pdf":
        try:
            text = parser.pdf_text(dest)
            if not text.strip():  # 扫描版 PDF：文本层为空
                parse_status = "failed"
        except Exception:  # noqa: BLE001
            parse_status = "failed"
    elif ext in ("png", "jpg", "jpeg", "webp", "bmp"):
        got = parser.ocr_text(dest)
        if got is None:
            text, parse_status = "", "failed"      # OCR 不可用
        else:
            text, ocr_used = got, True
    elif ext in ("html", "htm", "txt", "md"):
        raw = content.decode("utf-8", errors="ignore")
        text = parser.html_text(raw) if ext in ("html", "htm") else raw
    else:
        parse_status = "unparsed"

    text = parser.clean_text(text)
    summary = text[:180] if text else ""

    conn = connect()
    try:
        cur = conn.execute(
            """INSERT INTO document (file_name, file_type, file_path, category, source, tags,
                                     parse_status, summary, confidence)
               VALUES (?,?,?,?,?,?,?,?, 'confirmed')""",
            (file_name, ext, str(dest), category, source,
             json.dumps(tags or [], ensure_ascii=False), parse_status, summary))
        doc_id = cur.lastrowid
        if text:
            _upsert_knowledge_item(conn, "document", doc_id, file_name, text, summary,
                                   tags or [category], "confirmed")
        conn.commit()
        return {"doc_id": doc_id, "file_path": str(dest), "parse_status": parse_status,
                "ocr_used": ocr_used, "text_chars": len(text), "summary": summary}
    finally:
        conn.close()


def reparse_document(doc_id: int) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM document WHERE id=?", (doc_id,)).fetchone()
        if not row:
            return {"error": "document not found"}
    finally:
        conn.close()
    p = Path(row["file_path"])
    if not p.exists():
        return {"error": "file missing"}
    if row["file_type"] == "pdf":
        text = parser.clean_text(parser.pdf_text(p))
    elif row["file_type"] in ("png", "jpg", "jpeg", "webp", "bmp"):
        got = parser.ocr_text(p)
        text = got if got else ""
    else:
        text = parser.clean_text(p.read_text(encoding="utf-8", errors="ignore"))
    status = "parsed" if text.strip() else "failed"
    conn = connect()
    try:
        conn.execute("UPDATE document SET parse_status=?, summary=?, updated_at=datetime('now','localtime') WHERE id=?",
                     (status, text[:180], doc_id))
        if text:
            _upsert_knowledge_item(conn, "document", doc_id, row["file_name"], text, text[:180],
                                   json.loads(row["tags"] or "[]"), "confirmed")
        else:
            conn.execute("DELETE FROM knowledge_item WHERE source_type='document' AND source_id=?", (doc_id,))
        conn.commit()
        return {"doc_id": doc_id, "parse_status": status, "text_chars": len(text)}
    finally:
        conn.close()


# ---------------------------------------------------------------- 网页作品集

def ingest_portfolio_url(url: str, title: str = "") -> dict:
    r = parser.fetch_url(url)
    if not r["ok"]:
        return {"error": f"抓取失败：{r.get('error')}"}
    text = parser.clean_text(r["text"])
    title = title or r["title"] or url
    summary = text[:200]
    tags = ["portfolio", "网页作品集"]
    conn = connect()
    try:
        cur = conn.execute(
            """INSERT INTO portfolio_website (title, site_name, url, fetch_time, content_summary,
                                             extracted_text, tags, confidence)
               VALUES (?,?,?,?,?,?,?, 'confirmed')""",
            (title, r.get("site_name") or "", url, now(), summary, text,
             json.dumps(tags, ensure_ascii=False)))
        pw_id = cur.lastrowid
        _upsert_knowledge_item(conn, "website", pw_id, title, text, summary, tags, "confirmed")
        conn.commit()
        return {"portfolio_website_id": pw_id, "title": title, "fetch_time": now(),
                "text_chars": len(text), "summary": summary}
    finally:
        conn.close()


# ---------------------------------------------------------------- 结构化实体（从 Master Resume 等确认资料提取）

def upsert_entity(kind: str, data: dict, confidence: str = "confirmed") -> int:
    """kind: project/experience/education/award/certificate/skill/portfolio"""
    conn = connect()
    try:
        if kind == "project":
            cur = conn.execute(
                "INSERT INTO project (name, description, role, outcome, metrics, github_url, web_url, tags, confidence) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (data.get("name"), data.get("description"), data.get("role"), data.get("outcome"),
                 json.dumps(data.get("metrics", {}), ensure_ascii=False), data.get("github_url"),
                 data.get("web_url"), json.dumps(data.get("tags", []), ensure_ascii=False), confidence))
            pid = cur.lastrowid
            _upsert_knowledge_item(conn, "project", pid, data["name"],
                                   f"{data.get('role','')} {data.get('description','')} {data.get('outcome','')}",
                                   data.get("description", ""), data.get("tags", []), confidence)
        elif kind == "experience":
            cur = conn.execute(
                "INSERT INTO experience (company, title, start_date, end_date, content, projects, outcome, metrics, confidence) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (data.get("company"), data.get("title"), data.get("start_date"), data.get("end_date"),
                 data.get("content"), json.dumps(data.get("projects", []), ensure_ascii=False),
                 data.get("outcome"), json.dumps(data.get("metrics", {}), ensure_ascii=False), confidence))
            pid = cur.lastrowid
            _upsert_knowledge_item(conn, "experience", pid, f"{data['company']}·{data.get('title','')}",
                                   data.get("content", ""), (data.get("content") or "")[:150],
                                   data.get("tags", []), confidence)
        elif kind == "education":
            cur = conn.execute(
                "INSERT INTO education (school, major, degree, start_date, end_date, gpa, honors, confidence) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (data.get("school"), data.get("major"), data.get("degree"), data.get("start_date"),
                 data.get("end_date"), data.get("gpa"), data.get("honors"), confidence))
            pid = cur.lastrowid
            _upsert_knowledge_item(conn, "education", pid,
                                   f"{data['school']} {data.get('major','')}",
                                   f"{data.get('degree','')} GPA {data.get('gpa','')} 排名{data.get('rank','')} 荣誉：{data.get('honors','')}",
                                   "", data.get("tags", []), confidence)
        elif kind == "award":
            cur = conn.execute(
                "INSERT INTO award (award_name, competition, issuer, award_date, level, rank, related_project, abilities, confidence) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (data.get("award_name"), data.get("competition"), data.get("issuer"), data.get("award_date"),
                 data.get("level"), data.get("rank"), data.get("related_project"),
                 json.dumps(data.get("abilities", []), ensure_ascii=False), confidence))
            pid = cur.lastrowid
            _upsert_knowledge_item(conn, "award", pid, data.get("award_name"),
                                   f"{data.get('competition','')} {data.get('level','')} {data.get('award_date','')}",
                                   f"{data.get('level','')}·{data.get('competition','')}",
                                   data.get("tags", []), confidence)
        elif kind == "skill":
            cur = conn.execute(
                "INSERT INTO skill (name, category, proficiency, evidence, confidence) VALUES (?,?,?,?,?)",
                (data.get("name"), data.get("category"), data.get("proficiency"),
                 json.dumps(data.get("evidence", []), ensure_ascii=False), confidence))
            pid = cur.lastrowid
            _upsert_knowledge_item(conn, "skill", pid, data.get("name"), data.get("name"), "",
                                   data.get("tags", []), confidence)
        else:
            raise ValueError(f"unknown kind: {kind}")
        conn.commit()
        return pid
    finally:
        conn.close()


def save_personal_info(data: dict) -> dict:
    conn = connect()
    try:
        cols = ["name", "email", "phone", "city", "target_cities", "target_directions",
                "target_positions", "target_industries", "work_preference",
                "salary_expectation", "available_date", "notes"]
        row = {c: data.get(c) for c in cols}
        for k in ("target_cities", "target_directions", "target_positions", "target_industries"):
            v = row[k]
            row[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else (v or "")
        conn.execute("DELETE FROM personal_info")
        conn.execute(
            f"INSERT INTO personal_info (id, {', '.join(cols)}) VALUES (1, {', '.join('?' * len(cols))})",
            [row[c] for c in cols])
        p = row
        text = " ".join(str(x) for x in p.values() if x)
        _upsert_knowledge_item(conn, "personal_info", 1, f"个人信息·{row.get('name','')}", text, text[:150], ["personal"], "confirmed")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------------------------------------------------------------- 检索

def kb_search(query: str, limit: int = 30) -> list[dict]:
    rows = search_kb(query, limit)
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        out.append(d)
    return out


def kb_stats() -> dict:
    conn = connect()
    try:
        counts = {}
        for t in ("document", "project", "experience", "education", "award", "certificate",
                  "skill", "portfolio", "portfolio_website"):
            counts[t] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        # 奖项按「项数」统计（同类奖项多张证书合并为 N 项）
        counts["award"] = conn.execute(
            "SELECT COALESCE(SUM(count), 0) FROM award").fetchone()[0]
        counts["total"] = conn.execute("SELECT count(*) FROM knowledge_item").fetchone()[0]
        counts["confirmed"] = conn.execute(
            "SELECT count(*) FROM knowledge_item WHERE confidence='confirmed'").fetchone()[0]
        recent = [dict(r) for r in conn.execute(
            "SELECT id, source_type, title, summary, updated_at FROM knowledge_item "
            "ORDER BY updated_at DESC LIMIT 8")]
        return {"counts": counts, "recent": recent}
    finally:
        conn.close()
