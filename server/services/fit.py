"""JD 分析 + Job Fit Score + Evidence 检索（规则+检索引擎版）。

诚实边界：本模块是确定性的关键词维度 + KB 全文检索引擎，不是 LLM。
- 优点：结果可复现、每条结论都有 evidence、绝不编造；
- 深度语义定制（自我评价/亮点文案、打招呼语润色）由 WorkBuddy Agent 会话内完成，
  通过 API 写回，同样受 Diff 验证约束。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from server.db import connect, search_kb, get_setting

# JD 要求维度 → 关键词（命中即认为 JD 在要求该维度）
DIMENSIONS = {
    "AI产品/Agent": {"kw": ["ai产品", "ai agent", "agent", "大模型", "llm", "智能体", "aigc",
                             "ai应用", "生成式", "copilot", "ai设计", "ai 交互"], "weight": 12},
    "B端/复杂业务": {"kw": ["b端", "b 端", "企业级", "saas", "后台", "中台", "erp", "crm",
                             "数据产品", "工作台", "管理系统"], "weight": 10},
    "用户研究": {"kw": ["用户研究", "用户访谈", "可用性", "走查", "问卷", "用户调研", "ux research",
                         "需求分析"], "weight": 8},
    "交互/信息架构": {"kw": ["交互设计", "信息架构", "原型", "线框", "流程图", "交互规范",
                              "interaction", "设计规范"], "weight": 8},
    "视觉/UI": {"kw": ["ui设计", "ui/ux", "界面设计", "视觉设计", "ui", "ux", "设计师",
                        "designer", "体验设计", "design"], "weight": 10},
    "数据/可视化": {"kw": ["数据分析", "数据可视化", "图表", "dashboard", "bi", "报表",
                             "指标"], "weight": 6},
    "Web/前端实现": {"kw": ["web", "网页", "响应式", "前端", "html", "css", "落地页",
                              "创意开发", "vibe coding", "低代码"], "weight": 6},
    "设计工具": {"kw": ["figma", "sketch", "adobe", "photoshop", " illustrator", "ae",
                          "c4d", "即时设计", "mastergo"], "weight": 5},
    "三维/动效": {"kw": ["动效", "c4d", "三维", "3d", "blender", "motion"], "weight": 4},
}

# 我的 KB 证据关键词（每个维度对应「我有什么」的检索词）
EVIDENCE_QUERIES = {
    "AI产品/Agent": ["AI Agent 交互", "AI 输出可信度", "大模型", "芒果数问", "异动归因", "洞察分析师"],
    "B端/复杂业务": ["B端", "复杂业务", "数据管理部", "40+ 页面", "中台", "数据分析"],
    "用户研究": ["用户访谈", "原型测试", "用户走查", "10 位用户"],
    "交互/信息架构": ["信息架构", "信息链路", "交互设计", "信息层级"],
    "视觉/UI": ["UI/UX", "视觉传达", "界面设计", "体验设计", "Figma", "Adobe"],
    "数据/可视化": ["数据分析", "数据产品", "指标", "归因"],
    "Web/前端实现": ["Vibe Coding", "Web 端", "网页作品集", "netlify"],
    "设计工具": ["Figma", "Adobe", "C4D"],
    "三维/动效": ["C4D", "三维", "文创"],
}

DIRECTION_RULES = {
    "UI/UX": ["ui", "ux", "交互设计", "用户体验", "界面设计", "视觉设计", "设计师", "designer", "体验设计"],
    "AI产品": ["ai产品", "ai agent", "agent", "大模型", "llm", "智能体", "aigc", "ai 应用", "ai设计"],
    "产品设计": ["产品经理", "产品设计", "产品助理", "product manager"],
    "数据分析": ["数据分析", "数据产品", "bi", "数据可视化"],
}

# 我的目标方向白名单（用户明确：只想投 UI/UX 相关岗位，其他方向不进岗位池）
# UI/UX 为主方向；AI产品 里的「AI 产品设计/体验设计」也属设计岗，保留。
TARGET_DIRECTIONS = ("UI/UX", "AI产品")


def _norm(t: str) -> str:
    return (t or "").lower().replace("　", " ")


def guess_direction(title: str, jd: str) -> str:
    """岗位方向判断：**只看标题**，不看 JD 全文。

    原因：JD 里出现一两个「数据分析/后台」等词不代表这是 UI/UX 岗，
    否则「信息技术工程师」这类岗会被误判成 UI/UX 而虚高打分。
    标题判不出 → 用 JD 前 200 字（岗位职责开头，最贴近岗位本质）辅助。
    """
    blob = _norm(title or "")
    best, best_hit = _dir_match(blob)
    if best == "其他" and jd:
        blob2 = _norm((jd or "")[:200])
        best, best_hit = _dir_match(blob2)
    return best


def _dir_match(blob: str) -> tuple[str, int]:
    best, best_hit = "其他", 0
    for d, kws in DIRECTION_RULES.items():
        hit = sum(1 for k in kws if k in blob)
        if hit > best_hit:
            best, best_hit = d, hit
    return best, best_hit


def is_target_direction(direction: str) -> bool:
    """是否我的目标方向（UI/UX 相关）。非目标方向的岗位不进岗位池。"""
    return (direction or "") in TARGET_DIRECTIONS


# ---------------- 薪资解析（用于「薪资好的优先」排期）----------------
def parse_salary(text: str) -> tuple[str, float | None]:
    """从 JD 文本提取薪资。

    支持：「5K-10K/月」「8k-12k」「面议」「年薪16万-24万」「6000-8000元」等。
    返回 (薪资原文, 月薪下限单位 K 的数值)；识别不出返回 ("", None)。
    """
    if not text:
        return "", None
    t = text.replace("／", "/").replace("元", "").replace(" ", "")
    # 年薪：16万-24万/年 → 换算月薪(K)
    m = re.search(r"年薪?\s*(\d+(?:\.\d+)?)\s*万\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*万", t)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*万\s*/\s*年", t)
    if m:
        lo = float(m.group(1)) * 10 / 12   # 万/年 → K/月
        return f"年薪{m.group(1)}-{m.group(2)}万", round(lo, 1)
    # K 为单位：5K-10K / 5k-10k
    m = re.search(r"(\d+(?:\.\d+)?)\s*[kK]\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*[kK]", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}K/月", float(m.group(1))
    # 纯数字月薪：6000-8000
    m = re.search(r"(\d{4,5})\s*[-~到至]\s*(\d{4,5})", t)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 2000 <= lo <= 60000:
            return f"{lo}-{hi}元/月", round(lo / 1000, 1)
    # 单个数字 + K：8K以上
    m = re.search(r"(\d+(?:\.\d+)?)\s*[kK]\s*以上", t)
    if m:
        return f"{m.group(1)}K以上", float(m.group(1))
    if "面议" in t:
        return "面议", None
    return "", None


def parse_job_block(block: str) -> dict:
    """从一段 JD 文本块解析出公司/岗位/薪资/地点/截止。

    兼容两种常见格式：
      1) 首行形如「岗位-公司-来源」或「公司｜岗位」；
      2) 直接是 JD 正文（首行含岗位名，正文里含公司信息）。
    解析不出的字段留空，由用户在工作台补。
    """
    lines = [l.strip() for l in (block or "").splitlines() if l.strip()]
    company, title, location, deadline = "", "", "", ""
    if not lines:
        return {"company": "", "title": "", "salary": "", "location": "", "deadline": ""}
    head = lines[0]
    # 「岗位-公司-来源」或「公司-岗位」
    if head.count("-") >= 2:
        parts = [p.strip() for p in head.split("-") if p.strip()]
        title, company = parts[0], "-".join(parts[1:-1])
    elif "-" in head or "｜" in head or "|" in head:
        sep = "-" if "-" in head else ("｜" if "｜" in head else "|")
        parts = [p.strip() for p in head.split(sep) if p.strip()]
        if len(parts) >= 2:
            company, title = parts[0], parts[1]
        elif parts:
            title = parts[0]
    else:
        title = head
    body = "\n".join(lines)
    # 地点
    m = re.search(r"(?:工作地点|工作地|城市|地点)\s*[:：]?\s*([^\s|｜\n]{2,20})", body)
    if m:
        location = m.group(1).strip()
    # 截止时间
    m = re.search(r"(?:截止|投递截止|报名截止|有效期至)\s*[:：]?\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})", body)
    if m:
        deadline = m.group(1).replace("年", "-").replace("月", "-").replace("/", "-")
    # 公司（若首行没解析出，从正文找）
    if not company:
        m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,30}(?:有限公司|股份有限公司|集团|科技|公司))", body)
        if m:
            company = m.group(1)
    salary, _ = parse_salary(body)
    return {"company": company, "title": title, "salary": salary,
            "location": location, "deadline": deadline}


def make_dedupe_key(company: str, title: str) -> str:
    def n(s):
        s = re.sub(r"[\s·•（）()\-—_/\\]+", "", (s or "").lower())
        return s
    return f"{n(company)}|{n(title)}"


def add_job(company: str, title: str, location="", url="", source="手动添加",
            jd_text="", publish_date="", deadline="", source_url="", job_type="") -> dict:
    dk = make_dedupe_key(company, title)
    conn = connect()
    try:
        exist = conn.execute("SELECT id, company, title FROM job WHERE dedupe_key=?", (dk,)).fetchone()
        if exist:
            # 去重命中：若带了新的来源 URL，追加到 job_source_url 以便追溯多来源
            if url:
                dup_url = conn.execute(
                    "SELECT id FROM job_source_url WHERE job_id=? AND url=?",
                    (exist["id"], url)).fetchone()
                if not dup_url:
                    conn.execute("INSERT INTO job_source_url (job_id, url, source) VALUES (?,?,?)",
                                 (exist["id"], url, source))
                    conn.commit()
            return {"error": "duplicate", "job_id": exist["id"],
                    "msg": f"岗位已存在：{exist['company']} · {exist['title']}"}
        direction = guess_direction(title, jd_text)
        cur = conn.execute(
            """INSERT INTO job (company, title, location, url, source, source_url, job_type,
               jd_text, fetch_time, publish_date, deadline, direction, status, dedupe_key, discovered_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'New', ?, date('now','localtime'))""",
            (company.strip(), title.strip(), location, url, source, source_url, job_type,
             jd_text, datetime.now().strftime("%Y-%m-%d %H:%M"), publish_date, deadline,
             direction, dk))
        jid = cur.lastrowid
        if url:
            conn.execute("INSERT INTO job_source_url (job_id, url, source) VALUES (?,?,?)",
                         (jid, url, source))
        conn.execute("INSERT INTO job_event (job_id, event_type, detail) VALUES (?,?,?)",
                     (jid, "discovered", f"来源 {source}"))
        conn.commit()
        return {"job_id": jid, "dedupe_key": dk, "direction": direction}
    finally:
        conn.close()


def add_jobs_bulk(text: str, source: str = "批量粘贴 JD",
                  splitter: str = r"\n\s*-{3,}\s*\n") -> dict:
    """批量粘贴 JD：一段文本按分隔符拆成多条，逐条解析入库并自动分析评分。

    分隔符默认 `---`（单独一行，3 个以上连字符）。也兼容「===」「###」。
    返回 {created, skipped, filtered_non_uiux, items}
    """
    raw = (text or "").strip()
    if not raw:
        return {"error": "empty", "msg": "内容为空"}
    # 统一分隔符后拆分
    blocks = re.split(splitter, raw)
    if len(blocks) <= 1:
        blocks = re.split(r"\n\s*={3,}\s*\n|\n\s*#{3,}\s*\n", raw)
    if len(blocks) <= 1:
        blocks = re.split(r"\n\s*\n\s*\n+", raw)   # 多个空行也算分隔

    created, skipped, filtered, items = 0, 0, 0, []
    for b in blocks:
        b = b.strip()
        if len(b) < 15:      # 太短，视为无效片段
            continue
        info = parse_job_block(b)
        company = (info["company"] or "").strip()
        title = (info["title"] or "").strip()
        if not title:
            title = b.splitlines()[0].strip()[:40]
        if not company:
            company = "待补充公司"
        # 方向过滤（非 UI/UX 不入库）
        if not is_target_direction(guess_direction(title, b)):
            filtered += 1
            continue
        r = add_job(company=company, title=title, location=info["location"],
                    url="", source=source, jd_text=b,
                    deadline=info["deadline"], job_type="校招")
        if r.get("error") == "duplicate":
            skipped += 1
            items.append({"company": company, "title": title, "status": "重复跳过"})
            continue
        jid = r.get("job_id")
        if not jid:
            continue
        # 薪资入库
        sal, sal_min = parse_salary(b)
        if sal:
            conn = connect()
            try:
                conn.execute(
                    "UPDATE job SET salary=?, salary_min=? WHERE id=?",
                    (sal, sal_min, jid))
                conn.commit()
            finally:
                conn.close()
        analyze_job(jid, use_llm=False)      # 批量入库：省额度，先用规则版
        row = None
        conn = connect()
        try:
            row = conn.execute(
                "SELECT fit_score, grade, direction FROM job WHERE id=?", (jid,)).fetchone()
        finally:
            conn.close()
        created += 1
        items.append({
            "job_id": jid, "company": company, "title": title,
            "salary": sal, "status": "已入库",
            "fit_score": row["fit_score"] if row else None,
            "grade": row["grade"] if row else None,
            "direction": row["direction"] if row else None,
        })
    return {"created": created, "skipped_duplicate": skipped,
            "filtered_non_uiux": filtered, "items": items}


def analyze_job(job_id: int, use_llm: bool = None) -> dict:
    """JD → 维度要求 → KB evidence 检索 → fit score + 匹配点 + 缺口。

    use_llm=None 时按设置 default_llm_analysis 决定；为 True 优先走 LLM，
    LLM 失败/未配置时自动回退规则版，保证一定有分析结果。
    """
    if use_llm is None:
        use_llm = get_setting("default_llm_analysis", "1") == "1"
    if use_llm:
        try:
            return _analyze_job_llm(job_id)
        except Exception:
            return analyze_job_rule(job_id)
    return analyze_job_rule(job_id)


def analyze_job_rule(job_id: int) -> dict:
    """规则版（确定性关键词维度 + KB 检索），不调大模型。"""
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        if not job:
            return {"error": "job not found"}
        jd = job["jd_text"] or ""
    finally:
        conn.close()
    blob = _norm(job["title"] + "\n" + jd)

    matched, gaps, evidence_all = [], [], []
    for dim, cfg in DIMENSIONS.items():
        hit_kw = [k for k in cfg["kw"] if k in blob]
        if not hit_kw:
            continue  # JD 未要求该维度，不计入
        # 检索我的真实资料
        ev_items = []
        seen = set()
        for q in EVIDENCE_QUERIES.get(dim, []):
            for r in search_kb(q, 5):
                key = (r["source_type"], r["source_id"])
                if key in seen:
                    continue
                seen.add(key)
                ev_items.append({
                    "source_type": r["source_type"], "source_id": r["source_id"],
                    "title": r["title"], "quote": (r["text"] or "")[:100],
                    "confidence": r["confidence"]})
        if ev_items:
            matched.append({"dimension": dim, "jd_keywords": hit_kw[:4],
                            "weight": cfg["weight"], "evidence": ev_items[:4]})
            evidence_all.extend(ev_items[:4])
        else:
            gaps.append({"dimension": dim, "jd_keywords": hit_kw[:4],
                         "note": "资料库中未检索到该维度的真实证据"})

    # ---- 打分 ----
    total_w = sum(m["weight"] for m in matched) + sum(
        DIMENSIONS[g["dimension"]]["weight"] for g in gaps)
    score = 0.0
    if total_w:
        score = round(sum(m["weight"] for m in matched) / total_w * 88, 1)  # 封顶 88

    # 方向相关性：岗位方向必须是「我的目标方向」之一，否则强降分
    # 我的目标方向 = UI/UX / AI产品 / 产品设计 / 数据分析（见 DIRECTION_RULES）
    direction = job["direction"] or guess_direction(job["title"], jd)
    if direction in ("其他", ""):
        score = round(score * 0.4, 1)   # 方向不相关 → 腰斩式降分
    elif direction == "数据分析":
        score = round(score * 0.7, 1)   # 数据分析是我的次要方向，适当降权

    # 教育背景加分：仅当方向相关时才加（方向无关不加分，避免虚高）
    if direction not in ("其他", ""):
        edu_ev = search_kb("湖南科技大学 视觉传达", 3)
        if edu_ev:
            score = round(min(score + 5, 100), 1)
            evidence_all.append({"source_type": "education", "source_id": edu_ev[0]["source_id"],
                                 "title": edu_ev[0]["title"], "quote": (edu_ev[0]["text"] or "")[:100],
                                 "confidence": "confirmed"})

    # 缺口惩罚：存在明显缺口时额外扣分（每缺口 -5，最多 -20）
    if gaps:
        score = round(max(score - min(len(gaps) * 5, 20), 0), 1)

    grade = "S" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 60 else "D"

    analysis = {"matched": matched, "gaps": gaps, "score": score, "grade": grade,
                "matched_count": len(matched), "gap_count": len(gaps),
                "evidence_total": len(evidence_all), "direction": direction}
    conn = connect()
    try:
        conn.execute(
            "UPDATE job SET fit_score=?, grade=?, match_analysis=?, evidence=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (score, grade, json.dumps(analysis, ensure_ascii=False),
             json.dumps(evidence_all[:20], ensure_ascii=False), job_id))
        conn.execute("INSERT INTO job_event (job_id, event_type, detail) VALUES (?,?,?)",
                     (job_id, "scored", f"fit={score} grade={grade}"))
        # S/A 级岗位自动产生通知（Notification API，channel 预留 webhook/wechat）
        if grade in ("S", "A"):
            conn.execute(
                "INSERT INTO notification (kind, title, content, channel) VALUES (?,?,?,?)",
                ("new_s_grade", f"[{grade}] {job['company']}·{job['title']}",
                 f"匹配度 {score}（{grade} 级）。命中 {len(matched)} 个维度，缺口 {len(gaps)} 个。",
                 "session"))
        conn.commit()
    finally:
        conn.close()
    return analysis


def shortlist(job_id: int) -> dict:
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        if not job:
            return {"error": "job not found"}
        exist = conn.execute("SELECT id, status FROM application WHERE job_id=?", (job_id,)).fetchone()
        if exist:
            return {"application_id": exist["id"], "status": exist["status"], "msg": "已在流程中"}
        cur = conn.execute(
            "INSERT INTO application (job_id, status, source, source_url, job_url) "
            "VALUES (?, 'Shortlisted', ?, ?, ?)",
            (job_id, job["source"], job.get("source_url") or "", job.get("url") or ""))
        aid = cur.lastrowid
        conn.execute("UPDATE job SET status='Shortlisted' WHERE id=?", (job_id,))
        conn.execute("INSERT INTO job_event (job_id, event_type, detail) VALUES (?,?,?)",
                     (job_id, "shortlisted", f"application#{aid}"))
        conn.execute(
            "INSERT INTO application_event (application_id, from_status, to_status, note) "
            "VALUES (?,?,?,?)", (aid, None, "Shortlisted", "加入精投"))
        conn.commit()
        return {"application_id": aid, "status": "Shortlisted"}
    finally:
        conn.close()


def _candidate_profile(conn) -> str:
    """拼一份精简的候选人画像（姓名/目标方向/核心技能/代表项目），供 LLM 判断匹配。"""
    pi = conn.execute("SELECT * FROM personal_info WHERE id=1").fetchone()
    if not pi:
        return "（未录入个人信息）"
    name = pi.get("name") or ""
    targets = []
    for k in ("target_positions", "target_directions", "target_industries"):
        try:
            v = json.loads(pi.get(k) or "[]")
        except Exception:
            v = []
        if isinstance(v, list):
            targets += [str(x) for x in v]
    skills = [r["name"] for r in conn.execute(
        "SELECT name FROM skill ORDER BY rowid DESC LIMIT 15")]
    projects = [r["name"] for r in conn.execute(
        "SELECT name FROM project ORDER BY rowid DESC LIMIT 10")]
    edu = (pi.get("notes") or "")
    lines = [f"姓名：{name}", f"目标方向/岗位：{', '.join(targets) if targets else '未填'}"]
    if skills:
        lines.append("核心技能：" + "、".join(skills))
    if projects:
        lines.append("代表项目：" + "、".join(projects))
    if edu:
        lines.append(f"补充：{edu}")
    return "\n".join(lines)


def _analyze_job_llm(job_id: int) -> dict:
    """LLM 版 JD 分析：让大模型理解 JD 语义并给出匹配维度 + 评分，证据仍从 KB 真实检索回填。"""
    from server.services import llm
    if not llm.is_configured():
        raise RuntimeError("LLM 未配置，回退规则版")
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
        if not job:
            return {"error": "job not found"}
        jd = (job["title"] or "") + "\n" + (job["jd_text"] or "")
        profile = _candidate_profile(conn)
    finally:
        conn.close()

    system = (
        "你是一名资深校招匹配评估专家。根据【岗位 JD】与【候选人画像】，"
        "判断候选人与岗位的匹配度。必须只输出严格 JSON，不要任何解释或 markdown 代码块。\n"
        "JSON 字段：\n"
        "  direction: 单一字符串，从 [UI/UX, AI产品, 产品设计, 数据分析, 其他] 中选\n"
        "  requirements: 数组，JD 中明确要求的胜任维度，每项 {dimension(中文维度名), jd_keywords:[命中原文关键词], has_evidence(布尔，候选人画像是否具备该能力), note(一句话说明)}\n"
        "  gaps: 数组，候选人明显不满足或画像中无证据的维度，每项 {dimension, reason}\n"
        "  fit_score: 整数 0-100（方向不相关请给 <=40）\n"
        "  grade: 单字符 S/A/B/C/D\n"
        "  summary: 1-2 句中文总结匹配结论"
    )
    user = f"【候选人画像】\n{profile}\n\n【岗位 JD】\n{jd}"

    raw = llm.chat(system, user, temperature=0.3, json_mode=True)
    # 容错：抽取第一个 {...}
    try:
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
    except Exception as e:
        raise RuntimeError(f"LLM 返回非 JSON：{raw[:120]}") from e

    direction = data.get("direction") or "其他"
    reqs = data.get("requirements") or []
    gaps = data.get("gaps") or []
    score = float(data.get("fit_score") or 0)
    score = max(0.0, min(100.0, score))
    grade = (data.get("grade") or "D").upper()
    if grade not in ("S", "A", "B", "C", "D"):
        grade = "S" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 60 else "D"

    # 证据从 KB 真实检索回填（保持与规则版一致的「真实证据」结构）
    matched, evidence_all = [], []
    for r in reqs:
        dim = r.get("dimension", "其他")
        kws = r.get("jd_keywords") or []
        ev_items = []
        seen = set()
        for q in (kws or [dim]):
            for er in search_kb(q, 5):
                key = (er["source_type"], er["source_id"])
                if key in seen:
                    continue
                seen.add(key)
                ev_items.append({"source_type": er["source_type"], "source_id": er["source_id"],
                                 "title": er["title"], "quote": (er["text"] or "")[:100],
                                 "confidence": er["confidence"]})
        ev_items = ev_items[:4]
        matched.append({"dimension": dim, "jd_keywords": kws[:4], "weight": 8,
                        "evidence": ev_items, "has_evidence": bool(ev_items) and bool(r.get("has_evidence"))})
        evidence_all.extend(ev_items[:4])
    gap_list = [{"dimension": g.get("dimension", "其他"),
                 "jd_keywords": g.get("jd_keywords") or [],
                 "note": g.get("reason") or "候选人画像中无对应证据"} for g in gaps]

    analysis = {"matched": matched, "gaps": gap_list, "score": round(score, 1),
                "grade": grade, "matched_count": len(matched), "gap_count": len(gap_list),
                "evidence_total": len(evidence_all), "direction": direction,
                "summary": data.get("summary", ""), "llm": True}
    conn = connect()
    try:
        conn.execute(
            "UPDATE job SET fit_score=?, grade=?, match_analysis=?, evidence=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (round(score, 1), grade, json.dumps(analysis, ensure_ascii=False),
             json.dumps(evidence_all[:20], ensure_ascii=False), job_id))
        conn.execute("INSERT INTO job_event (job_id, event_type, detail) VALUES (?,?,?)",
                     (job_id, "scored_llm", f"fit={round(score,1)} grade={grade}"))
        if grade in ("S", "A"):
            conn.execute(
                "INSERT INTO notification (kind, title, content, channel) VALUES (?,?,?,?)",
                ("new_s_grade", f"[{grade}] {job['company']}·{job['title']}",
                 f"LLM 匹配度 {round(score,1)}（{grade} 级）。命中 {len(matched)} 个维度，缺口 {len(gap_list)} 个。",
                 "session"))
        conn.commit()
    finally:
        conn.close()
    return analysis
