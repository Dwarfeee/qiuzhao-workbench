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
from server.services import daily, fit, greeting, interview_brief, kb, llm, notifier, parser, radar, resume, review, analytics  # noqa: E402

# 启动时执行幂等迁移（补新表/新列）
init_db()

app = FastAPI(title="秋招 OS", version="1.0")


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
            """SELECT a.*, j.company AS j_company, j.title AS j_title, j.url AS j_url,
                      j.location AS j_location, j.company_scale,
                      j.fit_score, j.grade, j.match_analysis, j.direction,
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
