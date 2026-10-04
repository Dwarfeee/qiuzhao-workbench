"""端到端验收测试：完整核心闭环。

链路：导入 Master → 种子 KB（真实简历内容）→ 作品集 URL → JD → 分析+Evidence →
精投 → Tailored 版本 → Diff → PDF → PDF 验证 → 打招呼语 → Ready → Applied →
面试 → 周报 → 负向测试（白名单外篡改必须 FAIL）。

所有种子数据均提取自用户真实 Master Resume，不虚构。
运行：python server/scripts/e2e_test.py（需服务已在 8787 端口运行）
"""
from __future__ import annotations

import json
import sys

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
    print("秋招 OS · V1 端到端验收测试")
    print("=" * 70)

    # ---------- 1. Master Resume 只读导入 ----------
    code, m = api("POST", "/api/resume/import-master")
    check("1. Master Resume 导入（只读快照）", code == 200 and m.get("master_resume_id"),
          f"锚点：{m.get('anchors', {}).get('editable_sections')}")
    code, master = api("GET", "/api/resume/master")
    se_items = master.get("self_eval_text") or []
    hl_items = master.get("highlights_text") or []
    check("2. 识别自我评价区域", len(se_items) == 2, f"{[i['cat'] for i in se_items]}")
    check("3. 识别个人亮点区域", len(hl_items) == 3, f"{[i['cat'] for i in hl_items]}")

    # ---------- 2. 种子 KB：真实简历事实 ----------
    api("POST", "/api/kb/personal", json={
        "name": "贺宣锦", "email": "you@example.com", "phone": "138-0000-0000",
        "city": "长沙", "target_cities": ["长沙", "深圳", "杭州", "上海"],
        "target_directions": ["UI/UX", "AI产品", "B端体验设计"],
        "target_positions": ["UI/UX设计师", "AI 产品体验设计师", "交互设计师"],
        "target_industries": ["互联网", "AI", "内容平台"]})
    code, r = api("POST", "/api/kb/entity?kind=education", json={
        "school": "湖南科技大学", "major": "视觉传达设计", "degree": "本科在读",
        "start_date": "2023", "end_date": "2027", "gpa": "3.6/4.0",
        "honors": "专业排名 1/40，一等奖学金、二等奖学金"})
    check("4. 教育经历入库（confirmed）", code == 200)

    code, r = api("POST", "/api/kb/entity?kind=experience", json={
        "company": "芒果数问", "title": "UI/UX Designer 实习",
        "start_date": "2026.6", "end_date": "2026.9",
        "content": "芒果TV-数据管理部（长沙，B端 AI 产品）· 数据分析 Agent。"
        "异动归因：重构「异常识别→维度归因→指标归因→贡献度排序→自然语言解释」信息链路；"
        "AI 输出可信度：设计 AI 输出的可验证信息结构；"
        "洞察分析师智能体：基于 10 位用户访谈定义异常诊断、日报预筛两类核心业务，"
        "完成 1 个 AI Agent 的交互与信息架构设计，覆盖 6 个核心分析任务，"
        "2 轮每轮 6 人的原型测试与用户走查迭代；"
        "PC/Web 适配：完成 40+ 页面设计，推进 10+ 核心页面信息层级与交互，"
        "与产品及 3 位前端协作推进设计落地与 Web 端自适应适配。",
        "tags": ["B端", "AI", "数据分析", "用户研究", "信息架构"]})
    check("5. 实习经历入库（confirmed）", code == 200)

    for p in [
        {"name": "AI 实习猎手", "role": "UX/UI Designer",
         "description": "AI 求职决策辅助工具（概念设计）。针对求职信息过载、能力判断模糊、"
         "投递进度分散，梳理「简历→岗位→面试→投递→Offer」体验链路，设计 AI 简历结构化、"
         "岗位匹配与面试模拟等核心场景。以 Dashboard 聚合求职全流程，设计 Offer 对比与 "
         "AI 推荐理由、风险提示。", "tags": ["AI", "AI Agent 交互", "B端", "信息架构"]},
        {"name": "故宫脊兽 · ADHD 儿童文创小游戏", "role": "UX/UI Designer",
         "description": "面向 ADHD 儿童与家长陪伴场景的概念设计，基于儿童认知特征设计游戏机制、"
         "任务与即时操作反馈。", "tags": ["儿童", "游戏"]},
        {"name": "绵竹木版年画工坊", "role": "UX/UI Designer",
         "description": "非遗数字化体验概念设计。将复杂木版年画工艺流程拆解为儿童可理解的互动步骤，"
         "经原型测试验证。", "tags": ["非遗", "数字化"]},
    ]:
        code, _ = api("POST", "/api/kb/entity?kind=project", json=p)
        check(f"6. 项目入库：{p['name']}", code == 200)

    for a in [
        {"award_name": "中国好创意暨全国数字艺术设计大赛", "competition": "中国好创意（第二十届）暨全国数字艺术设计大赛",
         "award_date": "2026", "level": "国家级三等奖、省级一等奖、省级二等奖", "tags": ["国家级"]},
        {"award_name": "湖南省大学生数字媒体创意设计大赛", "competition": "湖南省大学生数字媒体创意设计大赛",
         "award_date": "2026", "level": "省级二等奖、省级三等奖", "tags": ["省级"]},
        {"award_name": "湖南省大学生服装设计大赛", "award_date": "2026", "level": "省级一等奖", "tags": ["省级"]},
        {"award_name": "湖南省大学生广告艺术大赛", "award_date": "2026", "level": "省级二等奖", "tags": ["省级"]},
        {"award_name": "湖南省大学生数字媒体创意设计大赛（2025）", "award_date": "2025",
         "level": "省级一等奖", "tags": ["省级"]},
        {"award_name": "第六届东方创意之星创新设计大赛", "award_date": "2025", "level": "省级银奖", "tags": ["省级"]},
    ]:
        code, _ = api("POST", "/api/kb/entity?kind=award", json=a)
        check(f"7. 奖项入库：{a['award_name'][:14]}…", code == 200)

    for s in ["Figma", "Adobe 系列", "C4D", "Vibe Coding"]:
        code, _ = api("POST", "/api/kb/entity?kind=skill", json={
            "name": s, "category": "设计工具" if s != "Vibe Coding" else "实现",
            "evidence": [{"source_type": "resume", "quote": "Master Resume 技能栏", "confidence": "confirmed"}]})
        check(f"8. 技能入库：{s}", code == 200)

    # ---------- 3. 网页作品集 ----------
    fd = {"url": "https://hexuanjinhe.netlify.app/", "title": "贺宣锦 Web Portfolio"}
    r = httpx.post(BASE + "/api/kb/url", data=fd, timeout=60)
    pw = r.json()
    check("9. 网页作品集抓取入库", "portfolio_website_id" in pw,
          f"{pw.get('text_chars', 0)} 字" if "portfolio_website_id" in pw else str(pw))

    # ---------- 4. 岗位 + JD 分析 ----------
    jd = """【岗位】AI 产品体验设计师（B端方向）
【职责】
1. 负责 AI 数据分析产品的交互设计与信息架构，将复杂分析结论转化为可理解、可验证的产品体验；
2. 与产品、算法、前端协作，设计 AI Agent 对话与推理结果的呈现方案；
3. 深入 B 端业务场景，开展用户研究与需求分析，输出设计规范；
4. 推动 Web 端设计落地与响应式适配，参与设计走查与可用性测试。
【要求】
1. 本科及以上，设计相关专业，有 UI/UX 实习或项目经验；
2. 熟练使用 Figma、Adobe 等设计工具，具备 prototyping 能力；
3. 对 AI 产品、大模型应用有理解，关注 AI 输出的可信度与可解释性；
4. 具备 B 端复杂业务的信息拆解能力，有数据可视化经验优先；
5. 沟通协作能力强，能跨角色推进方案落地。"""
    code, job = api("POST", "/api/jobs", json={
        "company": "云枢智能（示例）", "title": "AI 产品体验设计师",
        "location": "深圳", "source": "E2E 示例岗位", "url": "https://example.com/job/1",
        "jd_text": jd, "deadline": "2026-09-20"})
    check("10. 岗位入库 + 去重键", code == 200 and job.get("job_id"), f"direction={job.get('direction')}")
    jid = job["job_id"]
    # 去重验证
    code, dup = api("POST", "/api/jobs", json={"company": "云枢智能（示例）", "title": "AI 产品体验设计师", "jd_text": jd})
    check("11. 重复岗位被拦截", dup.get("error") == "duplicate")

    code, analysis = api("POST", f"/api/jobs/{jid}/analyze")
    check("12. JD 分析 + Job Fit Score", code == 200 and analysis.get("score", 0) > 0,
          f"score={analysis.get('score')} grade={analysis.get('grade')} 命中{analysis.get('matched_count')} 缺口{analysis.get('gap_count')}")
    check("13. Evidence 溯源（每个匹配点带资料来源）",
          bool(analysis.get("matched")) and all(
              m.get("evidence") for m in analysis["matched"] if m["dimension"] in (
                  "AI产品/Agent", "B端/复杂业务", "用户研究")))

    # ---------- 5. 精投 → 定制简历 ----------
    code, sl = api("POST", f"/api/jobs/{jid}/shortlist")
    check("14. 加入精投", code == 200 and sl.get("application_id"))
    aid = sl["application_id"]

    # 定制内容：只基于真实资料（芒果数问实习 / 奖项 / 作品集）
    new_eval = [
        {"cat": "优势技能",
         "txt": "熟悉 <b>AI 数据分析产品</b>从用户研究、信息架构、<b>AI Agent 交互</b>到 UI 设计与 Web 端适配的<b>全流程</b>；在芒果数问实习中围绕<b>AI 输出可信度</b>与异动归因设计可验证的分析体验；擅长 <b>B 端复杂信息</b>结构化拆解，熟练使用 Figma、Adobe 系列、C4D。"},
        {"cat": "综合素养",
         "txt": "以<b>用户真实业务任务</b>为设计出发点，具备<b>业务理解与信息拆解能力</b>，能把复杂分析逻辑转化为清晰可操作的产品体验；习惯通过<b>原型测试与用户走查</b>迭代方案（10 位用户访谈、2 轮测试）；与产品及前端跨角色协作推进 40+ 页面落地，持续深耕 <b>AI × UX、B 端数据产品体验</b>方向。"},
    ]
    new_hl = [
        {"cat": "AI 产品体验", "lines": ["AI Agent 交互设计", "AI 输出可信度"]},
        {"cat": "B 端数据分析", "lines": ["异动归因信息链路", "40+ 页面落地"]},
        {"cat": "设计 × 技术", "lines": ["Vibe Coding", "Web 端自适应"]},
    ]
    code, rv = api("POST", f"/api/jobs/{jid}/resume-version", json={
        "self_eval_items": new_eval, "highlight_items": new_hl,
        "reason": "示例：按 JD 强调 AI 输出可信度、B 端数据分析与 Web 落地能力（均来自芒果数问实习真实经历）"})
    check("15. Tailored 版本生成", code == 200 and rv.get("resume_version_id"))
    check("16. 程序级 Diff（白名单外逐字节一致）", rv.get("diff_status") == "pass",
          f"diff={rv.get('diff', {}).get('status')}")
    pc = rv.get("pdf_check") or {}
    checks = pc.get("checks", {})
    check("17. PDF 渲染成功（Chrome headless）", bool(checks.get("pdf_exists")))
    check("18. PDF 页数一致（A4 单页）", bool(checks.get("pages_equal")),
          f"master={checks.get('pages', {}).get('master')} tailored={checks.get('pages', {}).get('tailored')}")
    check("19. PDF 非目标区域文本一致", bool(checks.get("text_outside_regions_equal")),
          str(checks.get("text_diff_hint", "")))
    layout = pc.get("layout") or {}
    check("20. measure.js 溢出检测", layout.get("ok") and (layout.get("overflowPx") or 0) <= 0,
          f"overflowPx={layout.get('overflowPx')} 孤行={len(layout.get('orphans') or [])}")
    check("21. 图片/二维码正常（渲染流程与原版一致）", bool(checks.get("images_ok")))
    rv_id = rv.get("resume_version_id")

    # ---------- 负向测试：白名单外篡改必须 FAIL ----------
    sys.path.insert(0, r"C:\Users\<你的用户名>\WorkBuddy\秋招实录")
    from server.services.resume import diff_html
    master_html = open(r"C:\Users\<你的用户名>\Desktop\resume_build\resume.html", encoding="utf-8").read()
    from server.services import resume as R
    tampered = R.apply_tailoring(master_html, new_eval, new_hl)
    tampered = tampered.replace("湖南科技大学", "清华大学")  # 篡改教育经历
    neg = diff_html(master_html, tampered)
    check("22. 负向测试：篡改教育经历 → Diff FAIL", neg["status"] == "fail",
          f"检出 {len(neg['structural_changes']) + len(neg['content_changes'])} 处白名单外变化")
    tampered2 = tampered.replace("清华", "湖南科技")
    tampered2 = tampered2.replace("--accent:#3a5a80", "--accent:#ff0000")  # 篡改 CSS
    neg2 = diff_html(master_html, tampered2)
    check("23. 负向测试：篡改 CSS 颜色 → Diff FAIL", neg2["status"] == "fail")

    # ---------- 6. 打招呼语 ----------
    code, g = api("POST", f"/api/greetings/{jid}/generate")
    check("24. JD 定制打招呼语（4 版）", code == 200 and len(g.get("greetings", {})) == 4)
    check("25. 打招呼语携带 confirmed 证据", bool(g.get("evidence")),
          f"{len(g.get('evidence', []))} 条")
    check("26. 打招呼语无虚构占位（所有槽位有真实值）",
          "【需补充" not in json.dumps(g.get("greetings", {}), ensure_ascii=False))

    # ---------- 7. Ready → Applied ----------
    code, r = api("POST", f"/api/applications/{aid}/link-materials", json={"resume_version_id": rv_id})
    check("27. 简历版本挂载（Diff pass 才允许）", code == 200)
    code, r = api("POST", f"/api/applications/{aid}/status", json={"status": "Ready to Apply"})
    check("28. Ready to Apply（材料齐备校验通过）", code == 200)
    code, r = api("POST", f"/api/applications/{aid}/status", json={
        "status": "Applied", "applied_date": "2026-08-31"})
    check("29. 标记已投递（人工投递后记录）", code == 200)

    # ---------- 8. 面试 ----------
    code, iv = api("POST", "/api/interviews", json={
        "application_id": aid, "round": "一面", "scheduled_at": "2026-09-04 15:00",
        "format": "视频", "questions": ["介绍芒果数问的异动归因设计", "AI 输出可信度如何落地"],
        "review": "", "next_step": "等结果"})
    check("30. 面试记录（自动推进状态到 Interview）", code == 200 and iv.get("interview_id"))

    # ---------- 9. 周报 ----------
    code, wr = api("POST", "/api/reviews/generate")
    check("31. Weekly Review 生成", code == 200 and wr.get("week"),
          f"{wr.get('week')}：发现{wr.get('stats', {}).get('discovered')}·精投{wr.get('stats', {}).get('shortlisted')}·投递{wr.get('stats', {}).get('applied')}")

    # ---------- 10. 检索与 Dashboard ----------
    code, s = api("GET", "/api/kb/search?q=" + "AI Agent")
    check("32. KB 全文检索（AI Agent）", code == 200 and len(s.get("items", [])) >= 2,
          f"{len(s.get('items', []))} 条命中")
    code, d = api("GET", "/api/dashboard")
    check("33. Dashboard 数据聚合", code == 200 and d.get("total_applied", 0) >= 1,
          f"今日发现{d.get('today_jobs')}·待投{d.get('ready')}·累计投递{d.get('total_applied')}·面试{len(d.get('upcoming_interviews', []))}")

    print("\n" + "=" * 70)
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("全部通过 ✅")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
