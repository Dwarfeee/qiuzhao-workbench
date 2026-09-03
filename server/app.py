"""秋招 OS · FastAPI 主应用。

所有入口（Web 前端 / WorkBuddy Agent / Automation / 未来微信 Channel）
统一走这一套 REST API → 同一个 jobs.db。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


class NoCacheStaticFiles(StaticFiles):
    """静态文件响应附加 no-cache 头，避免桌面 App（WebView2）缓存旧版 JS/CSS。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: E402
from server.db import connect, init_db  # noqa: E402
from server.services import daily, extract, fit, greeting, interview_brief, kb, llm, notifier, parser, radar, resume, review, analytics  # noqa: E402

# 启动时执行幂等迁移（补新表/新列）
init_db()

app = FastAPI(title="秋招 OS", version="1.0")

# Edge 扩展（MV3）从浏览器页面向本地服务发请求需要 CORS。
# 这是个人本地私有服务，放开 origins/methods/headers（不携带凭据）。
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================================================ 资料库 KB

@app.get("/api/kb/stats")
def kb_stats_api():
    return kb.kb_stats()


@app.post("/api/kb/upload")
async def kb_upload(file: UploadFile = File(...), category: str = Form("other"),
                    tags: str = Form("[]")):
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "文件超过 50MB")
    try:
        tags_list = json.loads(tags)
    except Exception:  # noqa: BLE001
        tags_list = []
    r = kb.ingest_file(file.filename or "unnamed", content, category, "web上传", tags_list)
    if r.get("parse_status") == "failed":
        r["notice"] = "文本解析失败（可能是扫描件/纯图片且 OCR 不可用），文件已保存，可补充人工转录。"
    return r


@app.post("/api/kb/url")
def kb_url(url: str = Form(...), title: str = Form("")):
    return kb.ingest_portfolio_url(url, title)


@app.get("/api/kb/search")
def kb_search_api(q: str = "", limit: int = 30):
    return {"q": q, "items": kb.kb_search(q, limit)}


@app.get("/api/kb/documents")
def kb_documents():
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, file_name, file_type, category, source, tags, parse_status, summary, "
            "file_path, confidence, updated_at FROM document ORDER BY id DESC")]
        for r in rows:
            r["tags"] = json.loads(r["tags"] or "[]")
        return rows
    finally:
        conn.close()


@app.post("/api/kb/documents/{doc_id}/reparse")
def kb_reparse(doc_id: int):
    return kb.reparse_document(doc_id)


@app.delete("/api/kb/documents/{doc_id}")
def kb_delete_doc(doc_id: int):
    conn = connect()
    try:
        row = conn.execute("SELECT file_path FROM document WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        conn.execute("DELETE FROM knowledge_item WHERE source_type='document' AND source_id=?", (doc_id,))
        conn.execute("DELETE FROM document WHERE id=?", (doc_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/kb/personal")
def get_personal():
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM personal_info WHERE id=1").fetchone()
        if not row:
            return {}
        d = dict(row)
        for k in ("target_cities", "target_directions", "target_positions", "target_industries"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except Exception:  # noqa: BLE001
                d[k] = []
        return d
    finally:
        conn.close()


@app.post("/api/kb/personal")
def save_personal(data: dict):
    return kb.save_personal_info(data)


@app.post("/api/kb/entity")
def add_entity(kind: str, data: dict, confidence: str = "confirmed"):
    if confidence not in ("confirmed", "possible", "unverified"):
        raise HTTPException(400, "confidence 非法")
    if confidence != "confirmed" and not data.get("_ack"):
        raise HTTPException(400, "非 confirmed 资料需在会话中由本人确认")
    return {"id": kb.upsert_entity(kind, data, confidence)}


# ================================================================ Master Resume

@app.post("/api/resume/import-master")
def import_master():
    return resume.import_master()


@app.get("/api/resume/master")
def get_master():
    m = resume.get_master()
    if not m:
        return {}
    m["anchors"] = json.loads(m["anchors"] or "{}")
    m["self_eval_text"] = json.loads(m["self_eval_text"] or "[]")
    m["highlights_text"] = json.loads(m["highlights_text"] or "[]")
    return m


@app.post("/api/jobs/{job_id}/resume-version")
def create_resume_version(job_id: int, payload: dict):
    se = payload.get("self_eval_items")
    hl = payload.get("highlight_items")
    if not isinstance(se, list) or not isinstance(hl, list) or not se or not hl:
        raise HTTPException(400, "self_eval_items / highlight_items 必须是非空数组")
    for it in se:
        if not it.get("cat") or not it.get("txt"):
            raise HTTPException(400, "自我评价每项需含 cat 与 txt")
    for it in hl:
        if not it.get("cat") or not isinstance(it.get("lines"), list) or not it["lines"]:
            raise HTTPException(400, "个人亮点每项需含 cat 与 lines 数组")
    r = resume.create_tailored_version(job_id, se, hl, payload.get("reason", ""))
    # 自动把 diff 通过的版本挂到该岗位的 application，避免精投中心一直显示「简历缺失」
    if isinstance(r, dict) and r.get("diff_status") == "pass" and r.get("resume_version_id"):
        try:
            conn = connect()
            conn.execute(
                "UPDATE application SET resume_version_id=?, updated_at=datetime('now','localtime') "
                "WHERE job_id=? AND (resume_version_id IS NULL OR resume_version_id=?)",
                (r["resume_version_id"], job_id, r["resume_version_id"]))
            conn.commit(); conn.close()
        except Exception:  # noqa: BLE001
            pass
    return r


@app.post("/api/jobs/{job_id}/resume/generate-draft")
def generate_resume_draft_api(job_id: int):
    """按 JD 用 LLM 生成定制简历草稿（自我评价 / 个人亮点）。未配 LLM 返回明确错误。"""
    return resume.generate_resume_draft(job_id)


@app.get("/api/resume/versions")
def list_resume_versions():
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, job_id, company, position, dir_path, pdf_path, diff_status, status, "
            "reason, created_at, updated_at FROM resume_version ORDER BY id DESC")]
        return rows
    finally:
        conn.close()


@app.get("/api/resume/versions/{rv_id}")
def get_resume_version(rv_id: int):
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM resume_version WHERE id=?", (rv_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        d = dict(r)
        for k in ("orig_self_eval", "new_self_eval", "orig_highlights", "new_highlights",
                  "diff_json", "pdf_check_json"):
            try:
                d[k] = json.loads(d[k] or "null")
            except Exception:  # noqa: BLE001
                d[k] = None
        d["diff_status"] = d.get("diff_status")
        return d
    finally:
        conn.close()


@app.get("/api/resume/versions/{rv_id}/pdf")
def get_version_pdf(rv_id: int):
    conn = connect()
    try:
        r = conn.execute("SELECT pdf_path, diff_status FROM resume_version WHERE id=?", (rv_id,)).fetchone()
    finally:
        conn.close()
    if not r or not r["pdf_path"] or not Path(r["pdf_path"]).exists():
        raise HTTPException(404, "PDF 不存在")
    if r["diff_status"] != "pass":
        raise HTTPException(403, "Diff 未通过，PDF 已锁定（不允许投递）")
    return FileResponse(r["pdf_path"], media_type="application/pdf",
                        filename=Path(r["pdf_path"]).name)


# ================================================================ 岗位

@app.get("/api/jobs")
def list_jobs(status: str = "", direction: str = "", q: str = "", limit: int = 200):
    sql, args = "SELECT * FROM job WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if direction:
        sql += " AND direction=?"
        args.append(direction)
    if q:
        sql += " AND (company LIKE ? OR title LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY COALESCE(fit_score,0) DESC, id DESC LIMIT ?"
    args.append(limit)
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(sql, args)]
        for r in rows:
            r["match_analysis"] = json.loads(r["match_analysis"] or "null")
            r["evidence"] = json.loads(r["evidence"] or "null")
        return rows
    finally:
        conn.close()


@app.post("/api/jobs")
def add_job_api(payload: dict):
    if not payload.get("company") or not payload.get("title"):
        raise HTTPException(400, "company 与 title 必填")
    return fit.add_job(**{k: payload.get(k, "") for k in
                          ("company", "title", "location", "url", "source", "jd_text",
                           "publish_date", "deadline", "source_url", "job_type")})


# ================================================================ 网申信息总汇（可增删的复用字段）
@app.get("/api/netapply")
def list_netapply(category: str = ""):
    conn = connect()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM net_apply_info WHERE category=? ORDER BY order_no, id",
                (category,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM net_apply_info ORDER BY order_no, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/netapply")
def add_netapply(payload: dict):
    category = (payload.get("category") or "基本信息").strip()
    label = (payload.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "label 必填")
    kind = (payload.get("kind") or "text").strip()
    if kind not in ("text", "file", "link"):
        kind = "text"
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO net_apply_info (category, label, value, hint, kind, file_path, file_name, "
            "file_ext, link_url, extracted_text, order_no, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'), datetime('now','localtime'))",
            (category, label, payload.get("value", "") or "", payload.get("hint", "") or "", kind,
             payload.get("file_path") or "", payload.get("file_name") or "",
             payload.get("file_ext") or "", payload.get("link_url") or "",
             payload.get("extracted_text") or "",
             int(payload.get("order_no", 0) or 0)))
        conn.commit()
        return {"id": cur.lastrowid, "ok": True}
    finally:
        conn.close()


@app.patch("/api/netapply/{nid}")
def update_netapply(nid: int, payload: dict):
    conn = connect()
    try:
        fields, vals = []
        for col in ("category", "label", "value", "hint", "order_no", "kind",
                    "file_path", "file_name", "file_ext", "link_url", "extracted_text"):
            if col in payload:
                fields.append(f"{col}=?")
                vals.append(payload[col])
        if not fields:
            return {"ok": True}
        vals.append(nid)
        conn.execute(
            f"UPDATE net_apply_info SET {','.join(fields)}, updated_at=datetime('now','localtime') "
            f"WHERE id=?", vals)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/netapply/{nid}")
def delete_netapply(nid: int):
    conn = connect()
    try:
        row = conn.execute("SELECT file_path FROM net_apply_info WHERE id=?", (nid,)).fetchone()
        conn.execute("DELETE FROM net_apply_info WHERE id=?", (nid,))
        conn.commit()
        if row and row["file_path"]:
            try:
                p = Path(row["file_path"])
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return {"ok": True}
    finally:
        conn.close()


# 网申信息总汇：附件上传（PDF/Word/图片/等）。文件存本地 uploads/netapply/，库里只存路径。
_NETAPPLY_EXTS = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".md": "text/markdown",
}


def _llm_refine_doc(raw_text: str, file_name: str = "") -> str:
    """用 LLM 把从文档提取的原文整理为清晰、结构化、可识别的纯文本（只在配置了 LLM 时启用）。

    这是用户「在工作台网申信息汇总界面调用 LLM 扫描文档内容成可识别文本」诉求的实现：
    原始提取可能含换行错乱/乱码/排版噪声，经 LLM 整理后更易被填表/ATS 端点作为事实依据读取。
    未配置 LLM 或提取本身失败时，原样返回，保证功能不降级。
    """
    from server.services import llm
    if not raw_text or not llm.is_configured():
        return raw_text
    if raw_text.startswith("[") and "提取失败" in raw_text:
        return raw_text
    if "暂不支持 OCR" in raw_text:
        return raw_text
    try:
        system = (
            "你是文档识别与整理助手。下面是从用户上传的 Word/PDF/TXT 文档中提取的原文，"
            "可能含有换行错乱、乱码、多余空格、扫描噪声或排版碎片。请在不编造任何信息的前提下，"
            "将其整理为清晰、结构化、便于机器后续读取与匹配的纯文本：保留全部事实信息，"
            "按原文档的章节/要点合理分段，去除明显乱码与无意义空白。"
            "只返回整理后的文本本身，不要任何解释，也不要用 markdown 代码块包裹。"
        )
        user = f"文件名：{file_name}\n\n提取原文：\n{raw_text[:6000]}"
        out = llm.chat(system, user, json_mode=False, temperature=0, timeout=60)
        return out.strip() if out and out.strip() else raw_text
    except Exception:  # noqa: BLE001
        return raw_text


@app.post("/api/netapply/upload")
async def netapply_upload(file: UploadFile = File(...)):
    import re
    raw = (file.filename or "file").strip()
    ext = Path(raw).suffix.lower()
    if ext not in _NETAPPLY_EXTS:
        raise HTTPException(400, f"不支持的文件类型：{ext or '无扩展名'}（仅支持 PDF/Word/PNG/JPG 等）")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/*?:<>"|]', '_', Path(raw).stem) or "file"
    # 防重名
    dst = config.UPLOAD_DIR / f"{safe}{ext}"
    i = 1
    while dst.exists():
        dst = config.UPLOAD_DIR / f"{safe}_{i}{ext}"
        i += 1
    data = await file.read()
    dst.write_bytes(data)
    extracted = extract.extract_text(str(dst))
    extracted = _llm_refine_doc(extracted, raw)
    return {"ok": True, "file_path": str(dst), "file_name": raw, "file_ext": ext.lstrip('.'),
            "extracted_text": extracted}


@app.get("/api/netapply/{nid}/file")
def netapply_file(nid: int):
    conn = connect()
    try:
        row = conn.execute("SELECT file_path, file_name FROM net_apply_info WHERE id=?", (nid,)).fetchone()
    finally:
        conn.close()
    if not row or not row["file_path"]:
        raise HTTPException(404, "该字段没有附件")
    p = Path(row["file_path"])
    if not p.exists():
        raise HTTPException(404, "附件文件不存在（可能已被移动）")
    media = _NETAPPLY_EXTS.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(p, media_type=media, filename=row["file_name"] or p.name)


@app.post("/api/netapply/{nid}/extract")
def netapply_extract(nid: int):
    """对已有文件字段重新提取/更新 extracted_text。"""
    conn = connect()
    try:
        row = conn.execute("SELECT file_path, kind, file_name FROM net_apply_info WHERE id=?", (nid,)).fetchone()
        if not row:
            raise HTTPException(404, "字段不存在")
        if row["kind"] != "file" or not row["file_path"]:
            return {"ok": True, "extracted_text": "", "message": "非文件类型或没有附件"}
        text = extract.extract_text(row["file_path"])
        text = _llm_refine_doc(text, row.get("file_name") or "")
        conn.execute("UPDATE net_apply_info SET extracted_text=? WHERE id=?", (text, nid))
        conn.commit()
        return {"ok": True, "extracted_text": text}
    finally:
        conn.close()


# ================================================================ Edge 扩展接口（捕捉→岗位池 / 填表取数）
@app.get("/api/extension/form-data")
def extension_form_data():
    """返回自动填表所需的：个人基本信息(personal_info) + 网申字段列表 + 可选公司列表。"""
    conn = connect()
    try:
        p = conn.execute("SELECT * FROM personal_info WHERE id=1").fetchone()
        personal = dict(p) if p else {}
        rows = conn.execute(
            "SELECT * FROM net_apply_info ORDER BY order_no, id").fetchall()
        fields = [dict(r) for r in rows]
        comps = [r[0] for r in conn.execute(
            "SELECT DISTINCT company FROM job WHERE company IS NOT NULL AND company<>'' "
            "ORDER BY company").fetchall()]
        return {"personal": personal, "fields": fields, "companies": comps}
    finally:
        conn.close()


@app.post("/api/extension/capture")
def extension_capture(payload: dict):
    """Edge 扩展「一键捕捉」：把网页提取的岗位信息加入岗位池。company/title 必填。"""
    if not payload.get("company") or not payload.get("title"):
        raise HTTPException(400, "company 与 title 必填")
    data = {k: payload.get(k, "") for k in
            ("company", "title", "location", "url", "jd_text",
             "publish_date", "deadline", "source_url", "job_type")}
    data["source"] = payload.get("source") or "Edge捕捉"
    return fit.add_job(**data)


@app.post("/api/extension/smart-capture")
def extension_smart_capture(payload: dict):
    """Edge 扩展「智能捕捉」：用 DeepSeek 把网页抽取的岗位文本解析为结构化字段。

    入参：{ raw_text, url, title, h1 }
    返回：{ ok:True, company, title, salary, location, experience, education,
            job_type, jd_text, deadline, publish_date, source }
    若 LLM 未配置或解析失败：{ ok:False, reason, message }（前端回退 guessJob）。
    """
    from urllib.parse import urlparse
    from server.services import llm

    raw = (payload.get("raw_text") or "").strip()
    if not raw:
        raise HTTPException(400, "raw_text 必填")

    if not llm.is_configured():
        return {"ok": False, "reason": "no-llm",
                "message": "未配置 LLM（设置 → LLM 密钥管理 选择 Provider 并填入 Key）"}

    host = ""
    try:
        host = (urlparse(payload.get("url") or "").hostname or "").lower()
    except Exception:
        host = ""
    source = "官网"
    if "zhipin" in host or "boss" in host: source = "BOSS直聘"
    elif "nowcoder" in host: source = "牛客"
    elif "lagou" in host: source = "拉勾"
    elif "liepin" in host: source = "猎聘"
    elif "linkedin" in host: source = "LinkedIn"

    system = (
        "你是招聘信息结构化提取助手。用户会给你从招聘网站「岗位详情页」抽取的纯文本"
        "（已去除左侧岗位列表等无关内容）。请从中提取岗位的结构化字段，并以 JSON 返回。\n"
        "字段定义：\n"
        "- company：招聘公司名（必须是招人的公司，绝不要返回网站名如 BOSS直聘/牛客/拉勾）；若文本里只有岗位没有公司名则返回空字符串。\n"
        "- title：岗位名称（如 UI设计师、后端开发工程师）。\n"
        "- salary：薪资（如 12-20K·13薪；无则空字符串）。\n"
        "- location：工作城市（如 杭州；无则空字符串）。\n"
        "- experience：经验要求（如 1-3年；无则空字符串）。\n"
        "- education：学历要求（如 本科；无则空字符串）。\n"
        "- job_type：校招 / 社招 / 实习 / 兼职（按文本判断，无则空字符串）。\n"
        "- jd_text：岗位职责与任职要求的原文拼接（保留关键句，最多 1500 字；无则空字符串）。\n"
        "- deadline：截止日期（如 2026-10-31；无则空字符串）。\n"
        "- publish_date：发布日期（如 2026-09-01；无则空字符串）。\n"
        "只返回 JSON 对象，不要任何解释或 markdown 代码块。无法确定的字段返回空字符串。"
    )
    user = (
        f"来源站点：{source}\n"
        f"页面标题：{payload.get('title', '')}\n"
        f"页面 H1：{payload.get('h1', '')}\n\n"
        f"岗位详情文本：\n{raw}"
    )
    try:
        out = llm.chat(system, user, json_mode=True, temperature=0.2, timeout=90)
        data = json.loads(out)
        if not isinstance(data, dict):
            raise ValueError("LLM 返回非 JSON 对象")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "parse-error", "message": str(e)[:200]}

    data["source"] = source
    data["ok"] = True
    # 清洗：保证都是字符串
    for k in ("company", "title", "salary", "location", "experience", "education",
              "job_type", "jd_text", "deadline", "publish_date"):
        data[k] = (data.get(k) or "").strip()
    return data


@app.post("/api/extension/smart-fill-map")
def extension_smart_fill_map(payload: dict):
    """Edge 扩展「智能填表」：用 DeepSeek 把网申表单字段 label 映射到规范键。

    入参：{ labels:[字段显示名...], available_keys:[规范键...] }
    返回：{ ok:True, map:{ label: key|null } }
    注意：只发送字段「名称」，绝不发送用户真实个人信息（姓名/电话/身份证等）。
    """
    from server.services import llm

    labels = payload.get("labels") or []
    available_keys = payload.get("available_keys") or []
    if not labels or not available_keys:
        return {"ok": True, "map": {}}

    if not llm.is_configured():
        return {"ok": False, "reason": "no-llm",
                "message": "未配置 LLM（设置 → LLM 密钥管理）"}

    keys_desc = {
        "name": "姓名", "email": "邮箱", "phone": "手机/电话", "school": "毕业院校",
        "major": "专业", "city": "期望城市/工作地", "gender": "性别", "birth": "出生年月",
        "political": "政治面貌", "english": "英语等级(四六级)", "grad": "毕业时间",
        "degree": "学历/学位", "gpa": "GPA/绩点", "address": "地址", "idcard": "身份证号",
    }
    key_list = "\n".join(f"- {k}：{keys_desc.get(k, k)}" for k in available_keys)

    system = (
        "你是表单字段映射助手。给定一组网页表单字段的显示名称（label/placeholder/aria-label），"
        "请把每个字段映射到最合适的「规范键」之一；若都不合适则映射到 null。\n"
        "可用规范键：\n" + key_list + "\n"
        "只返回 JSON 对象，格式为 { \"字段显示名\": \"规范键或null\" }，不要任何解释或 markdown。"
    )
    user = "需要映射的字段名：\n" + "\n".join("- " + l for l in labels)
    try:
        out = llm.chat(system, user, json_mode=True, temperature=0.1, timeout=60)
        mp = json.loads(out)
        if not isinstance(mp, dict):
            raise ValueError("LLM 返回非 JSON 对象")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "parse-error", "message": str(e)[:200]}
    # 只保留合法 key
    clean = {}
    for lab, key in mp.items():
        if key in available_keys:
            clean[lab] = key
        else:
            clean[lab] = None
    return {"ok": True, "map": clean}


def _gather_netapply_entries():
    """汇总「网申信息总汇 + 个人基本信息」，返回可填值条目列表（供语义填表 / ATS 优化复用）。
    对文件类型的字段，会读取/提取其文本内容作为 value。"""
    conn = connect()
    try:
        p = conn.execute("SELECT * FROM personal_info WHERE id=1").fetchone()
        personal = dict(p) if p else {}
        rows = conn.execute(
            "SELECT category, label, value, kind, link_url, file_path, extracted_text FROM net_apply_info "
            "ORDER BY order_no, id").fetchall()
        fields = [dict(r) for r in rows]
    finally:
        conn.close()

    entries = []
    pm = {"name": personal.get("name"), "email": personal.get("email"), "phone": personal.get("phone"),
          "school": personal.get("school"), "major": personal.get("major"), "city": personal.get("city"),
          "gender": personal.get("gender"), "birth": personal.get("birth"), "political": personal.get("political"),
          "english": personal.get("english"), "grad": personal.get("grad"), "degree": personal.get("degree"),
          "gpa": personal.get("gpa"), "address": personal.get("address"), "idcard": personal.get("idcard")}
    for k, v in pm.items():
        if v:
            entries.append({"label": k, "value": str(v).strip()})
    for f in fields:
        if f["kind"] == "link":
            v = f["link_url"] or f["value"]
        elif f["kind"] == "file":
            # 优先用已提取的文本；没有就现场提取
            v = f["extracted_text"] or ""
            if not v and f["file_path"]:
                v = extract.extract_text(f["file_path"])
        else:
            v = f["value"]
        if v and str(v).strip():
            entries.append({"label": f["label"], "value": str(v).strip(), "group": f["category"] or ""})
    return entries


def _resume_text_for_company(company: str):
    """按公司名从简历中心取最新通过的定制简历 PDF，并提取文本（供填表/ATS 做事实依据）。"""
    if not company:
        return ""
    conn = connect()
    try:
        row = conn.execute(
        "SELECT rv.pdf_path FROM resume_version rv "
        "JOIN master_resume mr ON mr.id=rv.master_resume_id "
        "WHERE rv.company LIKE ? AND rv.pdf_path IS NOT NULL AND rv.pdf_path<>'' "
            "AND (rv.diff_status IS NULL OR rv.diff_status='通过') "
            "ORDER BY rv.updated_at DESC LIMIT 1", ("%" + company + "%",)).fetchone()
        if not row or not row["pdf_path"]:
            return ""
        return extract.extract_text(row["pdf_path"])
    finally:
        conn.close()


def _fetch_jd_for_company(company: str):
    """按公司名从岗位池取最新一条 JD 文本（供 ATS 优化做关键词对齐）。"""
    if not company:
        return ""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT jd_text FROM job WHERE company LIKE ? AND jd_text IS NOT NULL AND jd_text<>'' "
            "ORDER BY id DESC LIMIT 1", ("%" + company + "%",)).fetchone()
        return dict(row)["jd_text"] if row else ""
    finally:
        conn.close()


@app.post("/api/extension/fill-netapply")
def extension_fill_netapply(payload: dict):
    """Edge 扩展「LLM 语义填表」：把网页表单字段 label 与用户的网申信息总汇做语义匹配，
    返回 {label: value} 供扩展回填。

    入参：{ labels:[字段显示名...], company: 可选 }
    返回：{ ok:True, map:{ label: value|null } }
    说明：为支持语义检索，会把用户网申字段的（label+值）发送给 LLM；均为用户本人数据。
    """
    from server.services import llm

    labels = payload.get("labels") or []
    company = (payload.get("company") or "").strip()
    if not labels:
        return {"ok": True, "map": {}}

    entries = _gather_netapply_entries()

    if not entries:
        return {"ok": False, "reason": "no-data", "message": "网申信息为空，请先录入信息"}

    if not llm.is_configured():
        return {"ok": False, "reason": "no-llm",
                "message": "未配置 LLM（设置 → LLM 密钥管理），无法做语义匹配"}

    # 若有文件/简历类字段，其 extracted_text 会出现在 entries 里；同时补充简历中心对应公司简历
    resume_text = _resume_text_for_company(company) if company else ""
    entries_txt = "\n".join(f'- [{e.get("group", "")}] {e["label"]}：{e["value"]}' for e in entries)
    system = (
        "你是网申填表助手。任务：给定一组网页表单字段的显示名称（label/placeholder/aria-label），"
        "从下方「用户网申信息」里为每个字段挑出最匹配的值；若信息中没有对应内容则映射到 null。\n"
        "匹配规则：\n"
        "1. 严格按语义匹配（如「毕业院校」匹配院校字段，「政治面貌」匹配政治面貌字段）；\n"
        "2. 「链接/文件」类字段（作品集、个人主页、简历等）只适合填到『作品集/个人主页/URL』类表单字段；\n"
        "3. 简历中心对应公司的简历文本是事实补充，可用于回答「项目/实习/技能/自我评价」类字段，但不要编造未出现的内容；\n"
        "4. 只返回 JSON 对象 { \"表单字段名\": \"对应值或null\" }，不要任何解释或 markdown。"
    )
    user = (
        f"# 用户网申信息总汇\n{entries_txt}\n\n"
        + (f"# 简历中心「{company}」定制简历原文\n{resume_text[:4000]}\n\n" if resume_text else "")
        + f"# 需要填的表单字段\n" + "\n".join("- " + l for l in labels)
    )
    try:
        out = llm.chat(system, user, json_mode=True, temperature=0.1, timeout=90)
        mp = json.loads(out)
        if not isinstance(mp, dict):
            raise ValueError("LLM 返回非 JSON 对象")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "parse-error", "message": str(e)[:200]}
    clean = {lab: (mp.get(lab) or None) for lab in labels}
    return {"ok": True, "map": clean}


@app.post("/api/extension/fill-ats")
def extension_fill_ats(payload: dict):
    """Edge 扩展「ATS 高分简历优化填表」：用 LLM 按 JD 关键词重写叙述类字段，硬事实字段原样返回。

    入参：{ labels:[表单字段显示名...], company:可选, jd:可选(缺则由岗位池按公司取) }
    返回：{ ok:True, map:{ label: 优化后值|null }, jd_used:bool }
    安全：只发送用户本人网申信息 + 公开 JD；硬事实(姓名/邮箱/电话/院校/GPA…)绝不改写。
    """
    from server.services import llm

    labels = payload.get("labels") or []
    if not labels:
        return {"ok": True, "map": {}}

    company = (payload.get("company") or "").strip()
    jd = (payload.get("jd") or "").strip()
    if not jd:
        jd = _fetch_jd_for_company(company)

    entries = _gather_netapply_entries()
    if not entries:
        return {"ok": False, "reason": "no-data", "message": "网申信息为空，请先录入信息"}

    if not llm.is_configured():
        return {"ok": False, "reason": "no-llm",
                "message": "未配置 LLM（设置 → LLM 密钥管理），无法做 ATS 优化"}

    # 融合简历中心对应公司简历文本作为事实依据
    resume_text = _resume_text_for_company(company) if company else ""
    entries_txt = "\n".join(f'- [{e.get("group", "")}] {e["label"]}：{e["value"]}' for e in entries)
    system = (
        "你是 ATS（Applicant Tracking System，申请人追踪系统）简历优化助手。企业用 ATS 对网申表单做关键词匹配与解析评分。"
        "任务：根据用户已有的真实信息（网申信息总汇 + 对应公司简历），为给定的网页表单字段，生成最易被 ATS 打高分的填写内容。\n"
        "铁律与规则：\n"
        "1) 严格忠于用户真实信息，绝不可编造用户没有的经历、公司、技能、数据、证书。\n"
        "2) 以下「硬事实」字段必须原样返回，不得改写或润色：姓名、邮箱、电话/手机、身份证、出生年月、性别、"
        "毕业院校、专业、学历/学位、毕业时间、GPA/绩点、政治面貌、地址、英语等级(四六级)、期望城市。\n"
        "3) 对于「开放性叙述」字段（实习/工作/项目经历、自我评价、个人优势、技能总结、获奖、科研、社团活动、"
        "求职意向等），在遵守第1条前提下，优先从「网申信息总汇」和「简历中心对应公司简历」中提取真实经历，"
        "再用 JD 中的关键词（岗位要求的技能/工具/能力/行业术语）重写，使其更易被 ATS 命中："
        "动词开头、量化成果、使用标准术语；保持简洁（适合表单文本框，通常 ≤ 300 字）。\n"
        "4) 若某字段在用户真实信息中找不到任何对应内容，返回 null（不要编造）。\n"
        "5) 只返回 JSON 对象 { \"表单字段名\": \"填写内容或null\" }，不要任何解释或 markdown。"
    )
    user = (
        f"# 目标岗位 JD\n{jd or '（未提供 JD，按通用 ATS 最佳实践优化：动词开头、量化、关键词突出）'}\n\n"
        f"# 用户网申信息总汇（含已提取的 PDF/Word 文件内容）\n{entries_txt}\n\n"
        + (f"# 简历中心「{company}」定制简历原文\n{resume_text[:4000]}\n\n" if resume_text else "")
        + f"# 需要填的表单字段\n" + "\n".join("- " + l for l in labels)
    )
    try:
        out = llm.chat(system, user, json_mode=True, temperature=0.3, timeout=120)
        mp = json.loads(out)
        if not isinstance(mp, dict):
            raise ValueError("LLM 返回非 JSON 对象")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "parse-error", "message": str(e)[:200]}
    clean = {lab: (mp.get(lab) or None) for lab in labels}
    return {"ok": True, "map": clean, "ats": True, "jd_used": bool(jd)}


@app.post("/api/extension/fill-enhance")
def extension_fill_enhance(payload: dict):
    """一键填表「快速补全」：把『语义匹配 + （可选）ATS 重写』合成单次 LLM 调用，
    返回全部表单字段的 {label: value}。替代原先 fill-netapply / fill-ats / smart-fill-map
    的多次串行调用，使扩展端首次填充即时返回、AI 补全在后台单次完成（对标市面产品）。

    入参：{ labels:[表单字段显示名...], company:可选, ats:bool, jd:可选 }
    返回：{ ok:True, map:{ label: value|null }, ats, jd_used }
    """
    from server.services import llm

    labels = payload.get("labels") or []
    company = (payload.get("company") or "").strip()
    ats = bool(payload.get("ats"))
    if not labels:
        return {"ok": True, "map": {}}

    entries = _gather_netapply_entries()
    resume_text = _resume_text_for_company(company) if company else ""
    if not entries and not resume_text:
        return {"ok": False, "reason": "no-data", "message": "网申信息为空，请先录入信息"}

    if not llm.is_configured():
        return {"ok": False, "reason": "no-llm",
                "message": "未配置 LLM（设置 → LLM 密钥管理），无法智能补全"}

    jd = (payload.get("jd") or "").strip()
    if not jd:
        jd = _fetch_jd_for_company(company)
    entries_txt = "\n".join(f'- [{e.get("group", "")}] {e["label"]}：{e["value"]}' for e in entries)

    if ats:
        system = (
            "你是 ATS（Applicant Tracking System，申请人追踪系统）简历优化助手。企业用 ATS 对网申表单做关键词匹配与解析评分。"
            "任务：根据用户已有的真实信息（网申信息总汇 + 对应公司简历），为给定的网页表单字段，生成最易被 ATS 打高分的填写内容。\n"
            "字段名格式说明：每个字段可能以「分组/板块 > 字段标签」给出，分组表示该字段所属板块。例如\n"
            "「实习经历 > 工作经历」表示「实习经历」板块下的经历描述框，必须返回实习经历，不能返回工作经历。\n"
            "铁律与规则：\n"
            "1) 严格忠于用户真实信息，绝不可编造用户没有的经历、公司、技能、数据、证书。\n"
            "2) 以下「硬事实」字段必须原样返回，不得改写或润色：姓名、邮箱、电话/手机、身份证、出生年月、性别、"
            "毕业院校、专业、学历/学位、毕业时间、GPA/绩点、政治面貌、地址、英语等级(四六级)、期望城市。\n"
            "3) 分组语义优先：\n"
            "   - 分组为「实习经历」的字段，只能从用户真实信息中取实习/实践经历；\n"
            "   - 分组为「工作经历」的字段，只能取正式工作经历；\n"
            "   - 分组为「项目经历」的字段，只能取项目经历；\n"
            "   - 分组为「获奖经历」「荣誉」「奖项」的字段，只能取获奖/荣誉，且若有多项请返回 JSON 数组；\n"
            "   - 分组为「技能」「技能证书」「专业技能」的字段，只能取技能/证书，若有多项请返回数组。\n"
            "   同一字段不能混用不同类型的经历。\n"
            "4) 对于「开放性叙述」字段（实习/工作/项目经历、自我评价、个人优势、技能总结、获奖、科研、社团活动、"
            "求职意向等），在遵守第1、3条前提下，优先从「网申信息总汇」和「简历中心对应公司简历」中提取真实经历，"
            "再用 JD 中的关键词（岗位要求的技能/工具/能力/行业术语）重写，使其更易被 ATS 命中："
            "动词开头、量化成果、使用标准术语；保持简洁（适合表单文本框，通常 ≤ 300 字）。\n"
            "5) 若某字段在用户真实信息中找不到任何对应内容，返回 null（不要编造）。\n"
            "6) 多值字段返回 JSON 数组：获奖、项目、技能、证书等可容纳多项的字段，请返回 {\"字段名\": [\"项1\", \"项2\", ...]}，"
            "扩展会自动拆分到多个输入框/添加新行。如果该字段明显只有一个输入框，可把数组用换行拼接成字符串。\n"
            "7) 只返回 JSON 对象 { \"表单字段名\": \"填写内容、数组或null\" }，不要任何解释或 markdown。"
        )
    else:
        system = (
            "你是网申填表助手。任务：给定一组网页表单字段的显示名称（label/placeholder/aria-label），"
            "从下方「用户网申信息」里为每个字段挑出最匹配的值；若信息中没有对应内容则映射到 null。\n"
            "字段名格式说明：每个字段可能以「分组/板块 > 字段标签」给出，分组表示该字段所属板块。例如\n"
            "「实习经历 > 工作经历」表示「实习经历」板块下的经历描述框，必须返回实习经历，不能返回工作经历。\n"
            "匹配规则：\n"
            "1. 严格按语义匹配，并优先尊重分组：\n"
            "   - 分组为「实习经历」的字段，只能匹配实习/实践经历；\n"
            "   - 分组为「工作经历」的字段，只能匹配正式工作经历；\n"
            "   - 分组为「项目经历」的字段，只能匹配项目经历；\n"
            "   - 分组为「获奖经历」「荣誉」「奖项」的字段，只能匹配获奖/荣誉，若有多项请返回 JSON 数组；\n"
            "   - 分组为「技能」「技能证书」「专业技能」的字段，只能匹配技能/证书，若有多项请返回数组。\n"
            "2. 「链接/文件」类字段（作品集、个人主页、简历等）只适合填到『作品集/个人主页/URL』类表单字段；\n"
            "3. 简历中心对应公司的简历文本是事实补充，可用于回答「项目/实习/技能/自我评价」类字段，但不要编造未出现的内容；\n"
            "4. 多值字段返回 JSON 数组：获奖、项目、技能、证书等可容纳多项的字段，请返回 {\"字段名\": [\"项1\", \"项2\", ...]}。\n"
            "5. 只返回 JSON 对象 { \"表单字段名\": \"对应值、数组或null\" }，不要任何解释或 markdown。"
        )
    user = (
        f"# 目标岗位 JD\n{jd or '（未提供 JD，按通用 ATS 最佳实践优化：动词开头、量化、关键词突出）'}\n\n"
        f"# 用户网申信息总汇（含已提取的 PDF/Word 文件内容）\n{entries_txt}\n\n"
        + (f"# 简历中心「{company}」定制简历原文\n{resume_text[:4000]}\n\n" if resume_text else "")
        + f"# 需要填的表单字段\n" + "\n".join("- " + l for l in labels)
    )
    try:
        out = llm.chat(system, user, json_mode=True, temperature=0.2, timeout=110)
        mp = json.loads(out)
        if not isinstance(mp, dict):
            raise ValueError("LLM 返回非 JSON 对象")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "parse-error", "message": str(e)[:200]}
    clean = {lab: (mp.get(lab) or None) for lab in labels}
    return {"ok": True, "map": clean, "ats": ats, "jd_used": bool(jd)}


@app.post("/api/extension/recommend-resume")
def extension_recommend_resume(payload: dict):
    """Edge 扩展「LLM 检索简历」：按公司名在简历中心检索匹配的定制简历版本。

    入参：{ company:str, position:str可选, jd:str可选 }
    返回：{ ok, resume_version_id, company, position, pdf_url, reason, candidates:[...] }
    """
    from server.services import llm

    company = (payload.get("company") or "").strip()
    position = (payload.get("position") or "").strip()
    jd = (payload.get("jd") or "").strip()
    if not company:
        return {"ok": False, "reason": "no-company", "message": "未提供公司名"}

    conn = connect()
    try:
        vers = conn.execute(
            "SELECT id, company, position, diff_status, status, created_at FROM resume_version "
            "WHERE company LIKE ? ORDER BY created_at DESC", ("%" + company + "%",)).fetchall()
        vers = [dict(v) for v in vers]
    finally:
        conn.close()

    if not vers:
        return {"ok": False, "reason": "no-resume",
                "message": f"简历中心没有「{company}」的定制简历（可先在精投中心生成对应简历）",
                "candidates": []}

    passed = [v for v in vers if v["diff_status"] == "pass"]
    cands = passed or vers
    best = cands[0]
    reason = (f"该岗位与「{best['company']}·{best['position'] or ''}」定制简历最匹配"
              f"（同公司定制版，diff {'通过' if best['diff_status'] == 'pass' else '未通过'}）。")
    if len(cands) > 1 and llm.is_configured():
        try:
            list_txt = "\n".join(
                f'{i + 1}. id={v["id"]} 公司={v["company"]} 岗位={v["position"] or ""} diff={v["diff_status"]}'
                for i, v in enumerate(cands))
            system = (
                "你是简历匹配助手。用户要在某公司的网申/投递页上传简历。下面是从简历中心检索到的"
                "该（或相近）公司的定制简历版本列表。请挑出最合适的一个并简述理由。"
                "只返回 JSON：{ \"pick_id\": 数字, \"reason\": \"中文理由\" }，不要 markdown。")
            user = (f"目标公司：{company}\n目标岗位：{position or '未指定'}\n"
                     f"JD（如有）：{jd[:800]}\n\n候选简历：\n{list_txt}")
            out = llm.chat(system, user, json_mode=True, temperature=0.2, timeout=60)
            rp = json.loads(out)
            chosen = next((v for v in cands if v["id"] == rp.get("pick_id")), None)
            if chosen:
                best = chosen
                reason = rp.get("reason") or reason
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "resume_version_id": best["id"], "company": best["company"],
            "position": best["position"], "pdf_url": f"/api/resume/versions/{best['id']}/pdf",
            "reason": reason, "candidates": cands}


# 已知招聘平台域名（启发式兜底时用于排除）
_BLOCKED_HOST_HINTS = ("zhipin", "nowcoder", "lagou", "liepin", "zhaopin", "51job",
                       "boss", "kanzhun", "linkedin", "yingjiesheng", "jobui", "kanzhun")


def _heuristic_pick(company: str, candidates: list):
    """无 LLM 时的兜底：优先『官方域名带 jobs/career/校园/招聘』且『非招聘平台』的候选。"""
    import re as _re
    import urllib.parse as _up

    def score(c):
        host = _up.urlparse(c["url"]).hostname or ""
        s = 0
        if any(b in host for b in _BLOCKED_HOST_HINTS):
            s -= 10
        if _re.search(r"jobs|career|campus|recruit|zhaopin|hire|graduate|校招|招聘|campus", host, _re.I):
            s += 3
        if company and _re.search(_re.escape(company[:2]), c["title"] + host, _re.I):
            s += 1
        return s

    ranked = sorted(candidates, key=score, reverse=True)
    best = ranked[0]
    return best["url"], best["title"]


@app.post("/api/extension/find-company-site")
def extension_find_company_site(payload: dict):
    """Edge 扩展「来源网站」智能补全：用搜索引擎(默认 Baidu → Bing → DuckDuckGo 兜底)全网检索公司官网，
    再让 LLM 从结果里挑出『带招聘入口的官方站』。

    入参：{ company }
    返回：{ ok:True, url, title, llmLed } 或 { ok:False, reason, message }
    """
    from server.services import search, llm

    company = (payload.get("company") or "").strip()
    if not company:
        return {"ok": False, "reason": "no-company", "message": "缺少公司名"}

    candidates = search.search_company_site(company)
    if not candidates:
        return {"ok": False, "reason": "no-results",
                "message": "全网检索无结果（可能本机无外网或被搜索引擎拦截）"}

    # 没有 LLM 时，用启发式兜底
    if not llm.is_configured():
        url, title = _heuristic_pick(company, candidates)
        return {"ok": True, "url": url, "title": title, "llmLed": False}

    # LLM 从候选里挑官方招聘站
    cand_text = "\n".join(
        f"{i + 1}. {c['url']} —— {c['title']}{('｜' + c['snippet']) if c['snippet'] else ''}"
        for i, c in enumerate(candidates[:8])
    )
    system = (
        "你是招聘官网判定助手。给定一家公司名称和它在搜索引擎里返回的候选网页列表，"
        "请挑出「该公司官方招聘网站」——即公司自己官网里负责招聘/校招/社招的入口页面"
        "（域名通常是公司品牌域名，或 jobs./careers./campus. 子域）。\n"
        "判定要点：\n"
        "- 优先选公司官方域名下的招聘页（如 https://jobs.xxx.com、https://www.xxx.com/campus）。\n"
        "- 排除招聘平台（BOSS直聘/牛客/拉勾/猎聘/智联/前程无忧等）、新闻、百科、股吧、应用商店、第三方聚合页。\n"
        "- 若候选里都没有合适的官方招聘站，返回空字符串。\n"
        "只返回 JSON：{\"url\": \"选中的URL或空字符串\", \"reason\": \"简短理由\"}，不要 markdown。"
    )
    user = f"公司名称：{company}\n\n候选网页：\n{cand_text}"
    try:
        out = llm.chat(system, user, json_mode=True, temperature=0.1, timeout=60)
        data = json.loads(out)
        url = (data.get("url") or "").strip()
        if not url:
            url, title = _heuristic_pick(company, candidates)
            return {"ok": True, "url": url, "title": title, "llmLed": False,
                    "note": "llm 无合适结果，启发式兜底"}
        # 校验 url 必须来自候选，否则退回第一个
        if not any(url == c["url"] for c in candidates):
            url, title = _heuristic_pick(company, candidates)
            return {"ok": True, "url": url, "title": title, "llmLed": False,
                    "note": "llm 返回非候选 url，启发式兜底"}
        title = next((c["title"] for c in candidates if c["url"] == url), "")
        return {"ok": True, "url": url, "title": title, "llmLed": True}
    except Exception as e:  # noqa: BLE001
        url, title = _heuristic_pick(company, candidates)
        return {"ok": True, "url": url, "title": title, "llmLed": False,
                "note": f"llm 异常：{str(e)[:120]}"}


@app.post("/api/jobs/{job_id}/analyze")
def analyze_job_api(job_id: int, payload: dict = None):
    """JD 分析 + 匹配度。body.use_llm=None 时按设置 default_llm_analysis 决定；True 强制 LLM（失败回退规则版）。"""
    use_llm = (payload or {}).get("use_llm") if payload else None
    r = fit.analyze_job(job_id, use_llm=use_llm)
    if r.get("error"):
        raise HTTPException(404, r["error"])
    return r


@app.post("/api/plan/build")
def build_plan_api(payload: dict = None):
    """生成投递排期：把待投岗位分配到工作日（每天 N 个）。"""
    payload = payload or {}
    daily = int(payload.get("daily_quota", 8))
    practice_days = int(payload.get("practice_days", 7))
    from server.services import planner
    if payload.get("clear", True):
        planner.clear_plan()
    p = planner.build_plan(daily_quota=daily, practice_days=practice_days)
    return {"ok": True, "total": p["total"], "days": p["days"],
            "daily_quota": daily, "practice_mode": p["practice_mode"],
            "plan": {k: [{"id": j["id"], "company": j.get("company"),
                          "title": j.get("title"), "salary": j.get("salary"),
                          "fit_score": j.get("fit_score"),
                          "company_scale": j.get("company_scale"),
                          "deadline": j.get("deadline")} for j in v]
                     for k, v in p["plan"].items() if v}}


@app.get("/api/plan/today")
def today_plan_api():
    """今日任务：今天计划投递的岗位 + 今日面试 + 临近截止。"""
    from server.services import planner
    t = planner.get_today_tasks()
    return {"date": t["date"], "weekday": t["weekday"],
            "jobs": [{"id": j["id"], "company": j.get("company"), "title": j.get("title"),
                      "salary": j.get("salary"), "fit_score": j.get("fit_score"),
                      "company_scale": j.get("company_scale"), "deadline": j.get("deadline")}
                     for j in t["jobs"]],
            "interviews": t["interviews"], "deadlines": t["deadlines"]}


@app.get("/api/delivery/today")
def delivery_today_api():
    """今日待投清单：精投中心里、按当前练手/准备策略筛选规模、限量 daily_quota。"""
    from server.services import planner
    return planner.get_daily_delivery_list()


@app.post("/api/delivery/interview-ready")
def delivery_interview_ready_api():
    """用户回答『做好了面试准备』→ 之后跳过 0-99 小厂，只投 100-499 与 500+。"""
    from server.db import set_setting
    set_setting("interview_ready", "1")
    return {"ok": True, "interview_ready": True}


@app.post("/api/delivery/interview-ready/reset")
def delivery_interview_ready_reset_api():
    """（调试/反悔用）清空面试准备标记，恢复可投 0-99。"""
    from server.db import set_setting
    set_setting("interview_ready", "0")
    return {"ok": True, "interview_ready": False}


@app.get("/api/delivery/settings")
def delivery_settings_get():
    """读取投递策略设置：练手起点、面试准备、日配额、周目标、默认 LLM 分析。"""
    from server.db import get_setting
    from server.services import planner
    return {
        "practice_start_date": planner.practice_start_date().isoformat(),
        "interview_ready": planner.interview_ready(),
        "daily_quota": int(get_setting("daily_quota", "8") or 8),
        "weekly_target": int(get_setting("weekly_target", "50") or 50),
        "default_llm_analysis": get_setting("default_llm_analysis", "1") == "1",
    }


@app.post("/api/delivery/settings")
def delivery_settings_save(payload: dict):
    """保存投递策略设置。"""
    from server.db import set_setting
    if payload.get("practice_start_date"):
        set_setting("practice_start_date", payload["practice_start_date"])
    if "daily_quota" in payload:
        set_setting("daily_quota", int(payload["daily_quota"]))
    if "weekly_target" in payload:
        set_setting("weekly_target", int(payload["weekly_target"]))
    if "default_llm_analysis" in payload:
        set_setting("default_llm_analysis", "1" if payload["default_llm_analysis"] else "0")
    return {"ok": True}


@app.post("/api/jobs/bulk")
def add_jobs_bulk_api(payload: dict):
    """批量粘贴 JD：一段文本（用 --- 分隔多条）自动拆分成多个岗位并逐条分析评分。"""
    text = payload.get("text", "")
    if not text or not text.strip():
        raise HTTPException(400, "text 不能为空")
    return fit.add_jobs_bulk(text, payload.get("source", "批量粘贴 JD"))


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: int, payload: dict):
    """更新岗位的可手动维护字段：公司/岗位名/来源/链接/地点/类型/备注/JD/规模/秋招状态/截止。"""
    allowed = {"company", "title", "source", "source_url", "url", "location", "job_type",
               "notes", "jd_text", "company_scale", "fall_recruit", "deadline"}
    sets, args = [], []
    for k in allowed:
        if k in payload:
            sets.append(f"{k}=?")
            args.append(payload[k])
    if not sets:
        raise HTTPException(400, f"无可更新字段，可选：{sorted(allowed)}")
    if "company_scale" in payload and payload["company_scale"] not in (
            "0-99", "100-499", "500+", "未标记", "", None):
        raise HTTPException(400, "company_scale 只能是：0-99 / 100-499 / 500+ / 未标记")
    if "fall_recruit" in payload and payload["fall_recruit"] not in ("进行中", "未开始", "", None):
        raise HTTPException(400, "fall_recruit 只能是：进行中 / 未开始 / 空")
    args.append(job_id)
    conn = connect()
    try:
        conn.execute(
            f"UPDATE job SET {', '.join(sets)}, updated_at=datetime('now','localtime') WHERE id=?",
            args)
        conn.commit()
        return {"ok": True, "job_id": job_id}
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/shortlist")
def shortlist_api(job_id: int):
    r = fit.shortlist(job_id)
    if r.get("error"):
        raise HTTPException(404, r["error"])
    return r


@app.post("/api/applications/{app_id}/return-to-pool")
def return_to_pool_api(app_id: int):
    """把精投中心的岗位退回岗位池：删除 application 行，job 状态回到 New。"""
    conn = connect()
    try:
        app = conn.execute("SELECT * FROM application WHERE id=?", (app_id,)).fetchone()
        if not app:
            raise HTTPException(404, "application 不存在")
        job_id = app["job_id"]
        conn.execute("DELETE FROM application WHERE id=?", (app_id,))
        conn.execute("UPDATE job SET status='New' WHERE id=? AND status<>'Applied'", (job_id,))
        conn.execute("INSERT INTO job_event (job_id, event_type, detail) VALUES (?,?,?)",
                     (job_id, "returned_to_pool", f"application#{app_id}"))
        conn.commit()
        return {"ok": True, "job_id": job_id}
    finally:
        conn.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        d = dict(r)
        d["match_analysis"] = json.loads(d["match_analysis"] or "null")
        d["evidence"] = json.loads(d["evidence"] or "null")
        d["events"] = [dict(e) for e in conn.execute(
            "SELECT * FROM job_event WHERE job_id=? ORDER BY id", (job_id,))]
        return d
    finally:
        conn.close()


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    """删除岗位，并级联清理其关联记录（因 foreign_keys=ON，必须按依赖顺序先删子表）。"""
    conn = connect()
    try:
        # 1) application 引用 resume_version，先删
        conn.execute("DELETE FROM application WHERE job_id=?", (job_id,))
        # 2) resume_version / greeting_message 仅引用 job
        conn.execute("DELETE FROM resume_version WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM greeting_message WHERE job_id=?", (job_id,))
        # 3) 最后删岗位本身
        conn.execute("DELETE FROM job WHERE id=?", (job_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ================================================================ 岗位来源池 / Job Radar

@app.get("/api/sources")
def list_sources(enabled_only: bool = False):
    return radar.list_sources(enabled_only)


@app.post("/api/sources/import-edge")
def import_edge_sources():
    return radar.import_edge_sources()


@app.post("/api/sources")
def upsert_source(payload: dict):
    if not payload.get("source_name") or not payload.get("url"):
        raise HTTPException(400, "source_name 与 url 必填")
    return radar.upsert_source(
        payload["source_name"], payload["url"],
        payload.get("source_type", "manual"), payload.get("priority", 5),
        payload.get("enabled", 1), payload.get("note", ""))


@app.patch("/api/sources/{sid}")
def patch_source(sid: int, payload: dict):
    return radar.set_source_state(sid, payload.get("enabled"), payload.get("priority"))


@app.delete("/api/sources/{sid}")
def delete_source(sid: int):
    return radar.delete_source(sid)


@app.get("/api/radar/candidates")
def radar_candidates(limit: int = 50):
    return radar.today_candidates(limit)


# ================================================================ 每日管家 / Automation

@app.get("/api/daily/briefing")
def daily_briefing():
    return daily.today_briefing()


@app.get("/api/daily/weekend-review")
def daily_weekend_review():
    return daily.weekend_review()


@app.get("/api/automation/settings")
def get_automation_settings():
    return daily.get_settings()


@app.post("/api/automation/settings")
def save_automation_settings(payload: dict):
    return daily.save_settings(payload)


@app.get("/api/automation/runs")
def list_automation_runs(limit: int = 30):
    return daily.list_runs(limit)


@app.post("/api/radar/fetch-url")
def radar_fetch_url(payload: dict):
    """从岗位 URL 抓取 JD 文本（供预览，不入库）。"""
    url = payload.get("url")
    if not url:
        raise HTTPException(400, "url 必填")
    r = parser.fetch_url(url) if hasattr(parser, "fetch_url") else {"ok": False, "error": "parser 不可用"}
    return radar.extract_jd_from_html(r["text"], url) if r.get("ok") else r


# ================================================================ 精投 / 投递

@app.get("/api/applications")
def list_applications():
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            """SELECT a.*, j.company AS j_company, j.title AS j_title,
                      COALESCE(a.job_url, j.url) AS j_url,
                      j.location AS j_location, j.company_scale,
                      j.fit_score, j.grade, j.match_analysis, j.direction,
                      COALESCE(a.source_url, j.source_url) AS j_source_url, j.source AS j_source,
                      rv.diff_status, rv.pdf_path AS rv_pdf
               FROM application a JOIN job j ON j.id=a.job_id
               LEFT JOIN resume_version rv ON rv.id=a.resume_version_id
               ORDER BY CASE a.status WHEN 'Ready to Apply' THEN 0 WHEN 'Shortlisted' THEN 1
                        WHEN 'Tailoring' THEN 2 ELSE 3 END, a.updated_at DESC""")]
        for r in rows:
            r["match_analysis"] = json.loads(r["match_analysis"] or "null")
        return rows
    finally:
        conn.close()


STATUS_CHAIN = ["New", "Shortlisted", "Tailoring", "Ready to Apply", "Applied",
                "Online Assessment", "Interview", "Offer", "Rejected", "Withdrawn", "Closed"]


@app.post("/api/applications/{app_id}/status")
def set_application_status(app_id: int, payload: dict):
    status = payload.get("status")
    if status not in STATUS_CHAIN:
        raise HTTPException(400, f"非法状态，可选：{STATUS_CHAIN}")
    conn = connect()
    try:
        app = conn.execute("SELECT * FROM application WHERE id=?", (app_id,)).fetchone()
        if not app:
            raise HTTPException(404)
        # 状态守卫：Ready to Apply 必须材料齐备；diff fail 不可 Ready
        if status == "Ready to Apply":
            j = conn.execute("SELECT * FROM job WHERE id=?", (app["job_id"],)).fetchone()
            checks = {}
            checks["jd"] = bool(j and j["jd_text"])
            checks["fit_analysis"] = bool(j and j["match_analysis"])
            # 简历：必须已挂载且 diff 通过；打招呼语改为「可选」，不再阻断投递
            rv_ok = False
            if app["resume_version_id"]:
                rv = conn.execute("SELECT diff_status FROM resume_version WHERE id=?",
                                  (app["resume_version_id"],)).fetchone()
                rv_ok = bool(rv and rv["diff_status"] == "pass")
            checks["resume"] = rv_ok
            # 必须用户显式「满意」才允许投递（见 /approve-resume）
            checks["approved"] = bool(app.get("resume_approved"))
            failed = [k for k, v in checks.items() if not v]
            if failed:
                return JSONResponse({"error": "materials_incomplete", "missing": failed}, 400)
        conn.execute(
            "UPDATE application SET status=?, applied_date=COALESCE(?, applied_date), "
            "next_step=?, notes=?, updated_at=datetime('now','localtime') WHERE id=?",
            (status, payload.get("applied_date") if status == "Applied" else None,
             payload.get("next_step"), payload.get("notes"), app_id))
        conn.execute(
            "INSERT INTO application_event (application_id, from_status, to_status, note) "
            "VALUES (?,?,?,?)", (app_id, app["status"], status, payload.get("note", "")))
        conn.execute("UPDATE job SET status=? WHERE id=?", (status, app["job_id"]))
        conn.commit()
        # 状态变化通知（关键跃迁：Applied→OA/Interview/Offer 等）
        try:
            jrow = conn.execute("SELECT company, title FROM job WHERE id=?", (app["job_id"],)).fetchone()
            if jrow:
                daily.notify_status_change(app["job_id"], jrow["company"], jrow["title"], status)
        except Exception:  # noqa: BLE001
            pass  # 通知失败绝不影响主流程
        return {"ok": True, "status": status}
    finally:
        conn.close()


@app.post("/api/applications/{app_id}/link-materials")
def link_materials(app_id: int, payload: dict):
    """把已生成的 resume_version / greeting 挂到 application 上。"""
    conn = connect()
    try:
        rv_id = payload.get("resume_version_id")
        if rv_id:
            rv = conn.execute("SELECT job_id, diff_status FROM resume_version WHERE id=?", (rv_id,)).fetchone()
            app = conn.execute("SELECT job_id FROM application WHERE id=?", (app_id,)).fetchone()
            if not rv or not app or rv["job_id"] != app["job_id"]:
                raise HTTPException(400, "版本与投递不匹配")
            if rv["diff_status"] != "pass":
                raise HTTPException(400, "该版本 diff 未通过，禁止挂载")
        conn.execute(
            "UPDATE application SET resume_version_id=COALESCE(?, resume_version_id), "
            "greeting_id=COALESCE(?, greeting_id), updated_at=datetime('now','localtime') WHERE id=?",
            (rv_id, payload.get("greeting_id"), app_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/applications/{app_id}/approve-resume")
def approve_resume(app_id: int, payload: dict):
    """简历满意度开关：用户生成定制简历后点「满意」才允许投递；点「不满意」可撤回。"""
    approved = bool(payload.get("approved"))
    rv_id = payload.get("resume_version_id")
    conn = connect()
    try:
        app = conn.execute("SELECT * FROM application WHERE id=?", (app_id,)).fetchone()
        if not app:
            raise HTTPException(404)
        if approved:
            target_rv = rv_id
            if not target_rv:
                # 未指定版本时，自动采用该岗位最新一个 diff 通过的版本（兼容旧数据）
                row = conn.execute(
                    "SELECT id FROM resume_version WHERE job_id=? AND diff_status='pass' "
                    "ORDER BY id DESC LIMIT 1", (app["job_id"],)).fetchone()
                if row:
                    target_rv = row["id"]
            if not target_rv:
                raise HTTPException(400, "没有可采用的简历版本（请先在精投中心生成定制简历）")
            rv = conn.execute("SELECT job_id, diff_status FROM resume_version WHERE id=?",
                              (target_rv,)).fetchone()
            if not rv or rv["job_id"] != app["job_id"]:
                raise HTTPException(400, "版本与投递不匹配")
            if rv["diff_status"] != "pass":
                raise HTTPException(400, "该版本 Diff 未通过，禁止采用（请重新生成）")
            conn.execute(
                "UPDATE application SET resume_version_id=?, resume_approved=1, "
                "updated_at=datetime('now','localtime') WHERE id=?", (target_rv, app_id))
        else:
            conn.execute(
                "UPDATE application SET resume_approved=0, updated_at=datetime('now','localtime') "
                "WHERE id=?", (app_id,))
        conn.commit()
        return {"ok": True, "resume_approved": approved}
    finally:
        conn.close()


# ================================================================ 打招呼语

@app.get("/api/greetings/{job_id}")
def get_greetings(job_id: int):
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM greeting_message WHERE job_id=? ORDER BY kind, version DESC", (job_id,))]
        for r in rows:
            r["evidence"] = json.loads(r["evidence"] or "[]")
        return rows
    finally:
        conn.close()


@app.post("/api/greetings/{job_id}/generate")
def generate_greetings_api(job_id: int):
    r = greeting.generate_greetings(job_id)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


# ================================================================ 面试

@app.get("/api/interviews")
def list_interviews():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT i.*, j.company, j.title AS j_title FROM interview i
               LEFT JOIN application a ON a.id=i.application_id
               LEFT JOIN job j ON j.id=a.job_id
               ORDER BY COALESCE(i.scheduled_at,'') DESC, i.id DESC""")]
    finally:
        conn.close()


@app.post("/api/interviews")
def add_interview(payload: dict):
    rounds = ("OA", "HR Screen", "一面", "二面", "三面", "Final", "HR", "Offer")
    if payload.get("round") not in rounds:
        raise HTTPException(400, f"round 必须是 {rounds}")
    conn = connect()
    try:
        app_id = payload.get("application_id")
        company = payload.get("company", "")
        if app_id:
            row = conn.execute(
                "SELECT j.company, j.title FROM application a JOIN job j ON j.id=a.job_id WHERE a.id=?",
                (app_id,)).fetchone()
            if row:
                company = company or row["company"]
        cur = conn.execute(
            """INSERT INTO interview (application_id, company, position, round, scheduled_at,
               interviewer, format, questions, my_answers, review, performance,
               interviewer_feedback, improvements, next_step, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (app_id, company, payload.get("position", ""), payload["round"],
             payload.get("scheduled_at"), payload.get("interviewer", ""),
             payload.get("format", ""), json.dumps(payload.get("questions", []), ensure_ascii=False),
             payload.get("my_answers", ""), payload.get("review", ""),
             payload.get("performance", ""), payload.get("interviewer_feedback", ""),
             payload.get("improvements", ""), payload.get("next_step", ""),
             payload.get("status", "scheduled")))
        iid = cur.lastrowid
        if app_id:
            conn.execute("UPDATE application SET status='Interview', updated_at=datetime('now','localtime') WHERE id=?",
                         (app_id,))
        conn.commit()
        return {"interview_id": iid}
    finally:
        conn.close()


@app.patch("/api/interviews/{iid}")
def update_interview(iid: int, payload: dict):
    fields = {"scheduled_at", "interviewer", "format", "questions", "my_answers",
              "review", "performance", "interviewer_feedback", "improvements",
              "next_step", "status", "position", "round"}
    sets, args = [], []
    for k in fields:
        if k in payload:
            sets.append(f"{k}=?")
            args.append(json.dumps(payload[k], ensure_ascii=False) if k == "questions" else payload[k])
    if not sets:
        raise HTTPException(400, "无可更新字段")
    args.append(iid)
    conn = connect()
    try:
        conn.execute(f"UPDATE interview SET {', '.join(sets)}, updated_at=datetime('now','localtime') WHERE id=?", args)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/interviews/{iid}/brief")
def interview_brief_api(iid: int):
    r = interview_brief.build_interview_brief(iid)
    if r.get("error"):
        raise HTTPException(404, r["error"])
    return r


# ================================================================ 面试问题库

@app.get("/api/questions")
def list_questions_api(company: str = "", limit: int = 100):
    return interview_brief.list_questions(company, limit)


@app.post("/api/questions")
def add_question_api(payload: dict):
    if not payload.get("question"):
        raise HTTPException(400, "question 必填")
    return {"id": interview_brief.add_question(
        payload.get("interview_id"), payload.get("company", ""), payload.get("position", ""),
        payload.get("round", ""), payload["question"], payload.get("my_answer", ""),
        payload.get("result", ""), payload.get("review", ""), payload.get("tags", []))}


@app.post("/api/questions/similar")
def similar_questions_api(payload: dict):
    return interview_brief.similar_questions(payload.get("question", ""))


# ================================================================ 秋招数据分析

@app.get("/api/analytics")
def analytics_api():
    return analytics.full_analytics()


# ================================================================ Dashboard / 周报 / 通知

@app.get("/api/dashboard")
def dashboard():
    conn = connect()
    try:
        today = "date('now','localtime')"
        today_jobs = conn.execute(f"SELECT count(*) FROM job WHERE discovered_date={today}").fetchone()[0]
        new_s = conn.execute(f"SELECT count(*) FROM job WHERE grade IN ('S','A') AND discovered_date={today}").fetchone()[0]
        today_apps = conn.execute(f"SELECT count(*) FROM application WHERE date(created_at)={today}").fetchone()[0]
        pending = conn.execute("SELECT count(*) FROM application WHERE status IN ('Shortlisted','Tailoring')").fetchone()[0]
        ready = conn.execute("SELECT count(*) FROM application WHERE status='Ready to Apply'").fetchone()[0]
        today_applied = conn.execute(f"SELECT count(*) FROM application WHERE applied_date={today}").fetchone()[0]
        total_applied = conn.execute(
            "SELECT count(*) FROM application WHERE status IN ('Applied','Online Assessment','Interview','Offer') OR applied_date IS NOT NULL").fetchone()[0]
        deadlines = [dict(r) for r in conn.execute(
            "SELECT id, company, title, deadline FROM job WHERE deadline IS NOT NULL AND deadline != '' "
            "AND deadline >= date('now','localtime') ORDER BY deadline LIMIT 5")]
        upcoming_interviews = [dict(r) for r in conn.execute(
            """SELECT i.*, j.title AS j_title FROM interview i
               LEFT JOIN application a ON a.id=i.application_id
               LEFT JOIN job j ON j.id=a.job_id
               WHERE i.scheduled_at >= datetime('now','localtime') ORDER BY i.scheduled_at LIMIT 5""")]
        recent_apps = [dict(r) for r in conn.execute(
            """SELECT a.id, a.status, a.updated_at, j.company, j.title, j.grade, j.fit_score
               FROM application a JOIN job j ON j.id=a.job_id ORDER BY a.updated_at DESC LIMIT 6""")]
        top_jobs = [dict(r) for r in conn.execute(
            f"SELECT id, company, title, fit_score, grade, direction FROM job "
            f"WHERE discovered_date={today} AND fit_score IS NOT NULL ORDER BY fit_score DESC LIMIT 5")]
        offers = conn.execute("SELECT count(*) FROM application WHERE status='Offer'").fetchone()[0]
        # 秋招总览
        total_jobs = conn.execute("SELECT count(*) FROM job").fetchone()[0]
        shortlisted = conn.execute(
            "SELECT count(*) FROM application WHERE status IN ('Shortlisted','Tailoring','Ready to Apply','Applied','Online Assessment','Interview','Offer')").fetchone()[0]
        oa = conn.execute("SELECT count(*) FROM application WHERE status='Online Assessment'").fetchone()[0]
        interviewing = conn.execute("SELECT count(*) FROM application WHERE status='Interview'").fetchone()[0]
        rejected = conn.execute("SELECT count(*) FROM application WHERE status='Rejected'").fetchone()[0]
        # 面试轮次分布
        round_dist = {}
        for r in conn.execute(
                "SELECT round, count(*) c FROM interview GROUP BY round ORDER BY round"):
            round_dist[r["round"]] = r["c"]
        # 14 天投递趋势
        trend = []
        for r in conn.execute(
                "SELECT date(applied_date) d, count(*) c FROM application "
                "WHERE applied_date >= date('now','localtime','-13 day') GROUP BY d ORDER BY d"):
            trend.append({"date": r["d"], "count": r["c"]})
        return {
            "today_jobs": today_jobs, "today_new_s_a": new_s, "today_shortlisted": today_apps,
            "pending": pending, "ready": ready, "today_applied": today_applied,
            "total_applied": total_applied, "offers": offers,
            "overview": {"total_jobs": total_jobs, "shortlisted": shortlisted,
                         "applied": total_applied, "oa": oa, "interviewing": interviewing,
                         "offers": offers, "rejected": rejected},
            "round_dist": round_dist,
            "deadlines": deadlines, "upcoming_interviews": upcoming_interviews,
            "recent_apps": recent_apps, "top_jobs": top_jobs, "trend": trend,
            "master_ready": resume.get_master() is not None,
        }
    finally:
        conn.close()


@app.get("/api/reviews")
def list_reviews_api():
    return review.list_reviews()


@app.post("/api/reviews/generate")
def generate_review_api(week_offset: int = 0):
    return review.generate_weekly_review(week_offset)


@app.get("/api/notifications")
def list_notifications(limit: int = 50):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM notification ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


@app.post("/api/notifications/{nid}/read")
def read_notification(nid: int):
    conn = connect()
    try:
        conn.execute("UPDATE notification SET status='sent', sent_at=datetime('now','localtime') WHERE id=?", (nid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ================================================================ 通知分发 / Webhook

@app.get("/api/notify/prefs")
def get_notify_prefs():
    return notifier.get_prefs()


@app.post("/api/notify/prefs")
def save_notify_prefs(payload: dict):
    return notifier.save_prefs(payload)


@app.get("/api/notify/webhook-status")
def notify_webhook_status():
    return notifier.webhook_status()


@app.post("/api/notify/test")
def notify_test():
    return notifier.send_test_notification()


@app.post("/api/notify/dispatch")
def notify_dispatch():
    return notifier.dispatch_pending()


@app.post("/api/notify/retry")
def notify_retry():
    return notifier.retry_failed()


# ================================================================ LLM 密钥管理（cc switch 式切换/续费）
import re as _re

_PROVIDER_ID_RE = _re.compile(r"^[a-z0-9_]+$")


@app.get("/api/settings/llm")
def llm_settings_get():
    return llm.list_providers()


@app.post("/api/settings/llm")
def llm_settings_set(payload: dict):
    """切换/续费 LLM Key。支持多 Provider 密钥链，切换不改其他 Key。

    body: {provider, api_key?, model?, base_url?}
      - provider: deepseek/openai/moonshot/qwen/custom（必填）
      - api_key: 新 Key（续费/新增）。留空则：若该 Provider 已有 Key → 仅切换；
                 若无 Key → 报错。
      - model/base_url: 仅 custom 或显式覆盖时填。
    """
    provider = (payload.get("provider") or "").strip().lower()
    if not provider or not _PROVIDER_ID_RE.match(provider) or provider not in llm.LLM_PROVIDERS:
        return {"ok": False, "message": f"未知 Provider：{provider}"}
    api_key = (payload.get("api_key") or "").strip()
    env = config.load_env()

    # 切换或新增/续费 Key
    if api_key:
        updates = {f"LLM_KEY_{provider.upper()}": api_key, "LLM_PROVIDER": provider}
    else:
        existing_key = env.get(f"LLM_KEY_{provider.upper()}", "").strip()
        if not existing_key:
            return {"ok": False, "message": f"Provider「{provider}」暂无密钥，请填入 api_key"}
        updates = {"LLM_PROVIDER": provider}

    # 可选覆盖（仅当显式提供）
    base = (payload.get("base_url") or "").strip()
    model = (payload.get("model") or "").strip()
    if base:
        updates["LLM_BASE_URL"] = base
    if model:
        updates["LLM_MODEL"] = model

    config.write_env(updates)
    status = llm.list_providers()
    return {"ok": True, "message": f"已切换到 {provider}" + ("，密钥已更新" if api_key else "（沿用已有密钥）"),
            "status": status}


@app.post("/api/settings/llm/test")
def llm_settings_test():
    return llm.test_connection()


# ================================================================ 系统状态

@app.get("/api/system/status")
def system_status():
    conn = connect()
    try:
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("job", "application", "interview", "resume_version",
                            "greeting_message", "knowledge_item", "document", "weekly_review")}
    finally:
        conn.close()
    try:
        from server.services.parser import ocr_text  # noqa: F401
        ocr = "可用" if parser._OCR is not None else "不可用（rapidocr 未安装）"
    except Exception:  # noqa: BLE001
        ocr = "不可用"
    return {
        "counts": counts, "ocr": ocr,
        "master_source": str(config.MASTER_HTML),
        "master_source_exists": config.MASTER_HTML.exists(),
        "chrome": resume._chrome_path() if any(Path(p).exists() for p in config.CHROME_CANDIDATES) else "未找到",
    }


# ================================================================ 静态前端

# 静态资源不缓存（桌面 App 的 WebView2 有 HTTP 缓存，避免旧版 JS/CSS 残留）
_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
app.mount("/static", NoCacheStaticFiles(directory=str(config.WEB_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(config.WEB_DIR / "index.html"), headers=_NO_CACHE)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="info")
