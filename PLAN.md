# 秋招 OS（Job Search Operating System）— 实施方案 v2.0

版本：v2.0 · 日期：2026-08-30 · 状态：已确认，执行中
取代 v1.0。本版以「最终产品定义与实现约束」为准，后续开发以此为准绳。

---

## 0. 最终定位

**个人秋招 AI 工作台 / 秋招 OS**——围绕整个秋招周期的个人 AI Agent：

工作日岗位雷达 → JD 分析 → 资料库检索匹配 → Job Fit Score → 简历定制（只改自我评价+个人亮点）→ 打招呼语 → Ready to Apply → 人工投递 → 投递跟踪 → 笔试/面试跟踪 → 周末复盘 → 优化下周策略。

- Web 工作台 = 主操作界面
- WorkBuddy Agent = 核心"大脑"
- **SQLite `jobs.db` = 唯一数据源**（Web / Agent / Automation / 未来微信全部读写它）
- 微信 = 未来实时查看/操作入口（V1 不做，预留 Channel Adapter，不阻塞核心开发）

---

## 1. 环境勘察结论（已实测）

| 能力 | 状态 | 说明 |
|---|---|---|
| SQLite + WAL + FTS5 | ✅ | Python 标准库自带，`jobs.db` 单文件 |
| Python 3.13 + venv | ✅ | `~/.workbuddy/binaries/python/envs/default`，pip 网络实测可用 |
| Node 22 | ✅ | v22.22.2 |
| FastAPI + 本地 Web 工作台 | ✅ | localhost 服务 + 浏览器面板预览 |
| **HTML→PDF 浏览器渲染** | ✅ 已确认 | 本机存在 Edge 与 Chrome：`msedge --headless --print-to-pdf`，与 Claude 渲染你的简历是同一引擎级别，排版保真 |
| PDF 文本解析 | ✅ | pypdf（已装）+ pdfplumber |
| 图片 OCR | ⚠️ 待验证 | 无 tesseract；方案 `rapidocr-onnxruntime`（纯 Python 中文识别），装好实测，失败则奖状走人工转录 |
| 网页抓取（作品集/JD） | ✅ | WebFetch/WebSearch + requests |
| WorkBuddy Automations | ✅ | 原生定时调度 |
| 微信消息收发 | ❌ 当前不存在 | V1 不做双向微信；预留 Channel Adapter；不阻塞核心系统 |
| 招聘网站登录/验证码 | ❌ 不做 | 符合 V1 排除清单 |

---

## 2. 整体架构

```
Candidate Knowledge Base (jobs.db 资料域 + candidate/ 文件)
        ↓
WorkBuddy AI Agent（大脑：JD分析/检索/打分/定制/打招呼语/复盘）
        ↓
Job / Application Database (jobs.db 岗位域)
        ↓
Web 工作台 (FastAPI + 静态前端)   ←→   Automations (6个)   ←→   未来微信 Channel
```

**API 分层（微信预留的关键设计）**：所有能力收敛为五组 REST API，Web/Agent/Automation/未来微信都只调 API，不直连数据库逻辑——未来接微信零重构：

- Candidate API（资料上传/解析/搜索/关联）
- Job API（入库/去重/JD分析/打分/精投）
- Application API（投递跟踪/状态机）
- Interview API（面试轮次/复盘）
- Notification API（通知/提醒，channel 字段预留 `session`/`webhook`/`wechat`）

---

## 3. 目录结构

```
秋招实录/
├── jobs.db                      # 唯一数据库（WAL + FTS5）
├── candidate/                   # 资料文件存储（与库分离）
│   ├── resumes/  portfolios/  projects/  awards/
│   ├── certificates/  education/  experience/  other/
├── resume/
│   ├── master/                  # Master Resume HTML/CSS（READ ONLY，永不被 AI 修改）
│   │   ├── index.html  style.css  assets/
│   └── versions/                # 每个岗位一个独立副本目录
│       ├── company_a_ai_pm/
│       └── company_b_product/
├── server/                      # FastAPI 后端
│   ├── db.py                    # schema + 连接
│   ├── main.py
│   ├── routers/                 # candidate / job / application / interview / notification
│   ├── services/                # parse_pdf / ocr / fetch_url / jd_analyze /
│   │                            # fit_score / resume_tailor / html_pdf_render /
│   │                            # diff_verify / greeting / weekly_review
│   └── scripts/                 # init_db / backup_db / rebuild_fts / selftest
├── web/                         # 前端（原生 SPA）
├── backups/                     # 每日数据库快照，保留 14 天
├── .env                         # 密钥（不入库不入代码）
└── PLAN.md
```

---

## 4. Master Resume 处理方案（最高优先级约束）

### 4.1 Source of Truth

**HTML/CSS 源代码是 Master Resume 的 Source of Truth**，PDF 只是渲染结果。不采用 PDF 直接编辑（PyMuPDF 路线废弃）。

### 4.2 处理流程

```
resume/master/index.html (READ ONLY)
    ↓ 复制到 resume/versions/<company>/
只修改：自我评价 DOM + 个人亮点 DOM（由锚点配置指定）
    ↓
Tailored HTML
    ↓ 浏览器渲染（Edge headless --print-to-pdf）
Tailored PDF
    ↓ 自动验证（见 4.3）
通过 → Ready to Apply；失败 → 自动回滚删除
```

### 4.3 程序级 Diff 验证（不依赖 AI 自觉）

每生成一个 Tailored Resume，自动执行：

1. **HTML 结构 diff**：BeautifulSoup 解析 Master 与 Tailored，DOM 树逐节点对比——除两个白名单区域外，任何结构/属性变化 → FAIL
2. **CSS diff**：style.css 与内联样式逐字符对比，必须 0 差异
3. **全文文本 diff**：除两段外逐字符一致
4. **PDF 检查**：文本层 diff + 页数一致 +（可选）页面截图视觉对比
5. FAIL → **自动回滚**（删除该 version 目录 + 数据库记录标记 failed），**禁止进入 Ready to Apply**

### 4.4 接入流程（拿到你的简历代码后）

第一步**只 READ/ANALYZE，不修改**：分析 HTML 结构、CSS 结构、自我评价所在 DOM、个人亮点所在 DOM、页面尺寸、渲染方式、assets、浏览器环境 → 向你报告"识别到的自我评价区域/个人亮点区域" → **你确认锚点后**才进入定制流程。Master 文件永远只读。

---

## 5. Candidate Knowledge Base 设计

### 5.1 知识库流水线

```
上传 → 文件保存 → 类型识别 → 文本解析(pypdf/pdfplumber)
→ OCR(需要时, rapidocr) → AI 结构化 → Knowledge Item
→ 建立关联(KnowledgeRelation) → FTS5 全文索引 → 供 AI 检索
```

### 5.2 每份资料的通用字段

名称、类型、来源、上传时间、文件路径/URL、解析状态、摘要、标签、相关项目、相关经历、相关技能、相关岗位、**可信度（confirmed / possible / unverified）**。

### 5.3 可信度铁律

- `confirmed`：来自你上传的真实资料 → 可用于简历/打招呼语/求职材料
- `possible`：AI 推断、无直接证据 → 仅用于匹配建议展示
- `unverified`：无法确认
- **服务端硬校验**：生成简历/打招呼语时引用非 confirmed 证据 → 直接拒绝
- AI 不允许把 possible/unverified 当事实，不允许编造经历/数据/奖项/技能

### 5.4 Evidence 证据溯源（贯穿全系统）

Job Fit Score、简历定制、打招呼语、面试准备的所有结论都记录 `evidence`：`[{source_type, source_id, title, quote, confidence}]`。Web 端可回答："为什么 AI 认为我适合这个岗位？这个结论来自哪份资料？"

---

## 6. 岗位系统设计

### 6.1 Job 表关键字段

公司、岗位名称、地点、URL、来源、JD 原文(FTS)、抓取时间、发布时间、截止时间、岗位方向、Job Fit Score、推荐等级(S/A/B/C/D)、状态、相关资料(evidence json)、相关简历版本、相关打招呼语。

### 6.2 去重

入库前按 `公司+岗位名`、`URL`、`岗位ID`、`JD 相似度(文本指纹)` 四重判重；已 查看/收藏/精投/投递 的岗位不再推荐。

### 6.3 Job Fit Score（0-100）

加权：方向匹配 30 / 硬性条件满足 30 / 证据强度(confirmed 数量) 20 / 城市·行业 10 / 经验匹配 10。
必须输出：分数理由 + 匹配点 + 缺口 + 证据来源。找不到证据的要求明确标注"资料库中无对应经历"。

### 6.4 岗位雷达

周一至五每日目标 ~20 个，**宁缺毋滥**：只有 13 个够格就返回 13 个。来源：招聘网站公开页、公司官网、你粘贴的 JD/URL。

### 6.5 精投中心（Precision Applications）

加入精投 → 匹配分析 → 资料检索 → 简历定制 → 打招呼语 → 作品集 → Ready to Apply 检查清单（JD/Fit/简历/PDF/作品集/打招呼语/备注/链接 全齐才亮灯）。

### 6.6 打招呼语（4 类）

招聘平台短消息 / HR 私聊版 / 投递备注 / 作品集配套介绍。基于真实经历+匹配点+evidence 生成；硬校验：每个事实断言必须挂 confirmed 证据，否则拒绝输出。风格：自然、具体、简洁、真人感。

---

## 7. 数据库 Schema（jobs.db）

三个逻辑域，公共列 `id, created_at, updated_at`。

**资料域**：`personal_info` · `master_resume`（master 目录路径、结构锚点、两段原文、只读标记）· `resume_version`（job_id、原/新自我评价、原/新个人亮点、修改原因、diff_json、diff_status、pdf 检查结果、目录路径）· `resume_modification`（逐字段原/新/理由/evidence）· `portfolio`（文件型）· `portfolio_website`（URL 型：URL、标题、网站名、抓取时间、摘要、相关项目/技能）· `project` · `experience` · `education` · `award` · `certificate` · `skill`（证据来源必填）· `document`（原始文件元数据）· `knowledge_item`（扁平知识索引 + FTS5 虚表 `knowledge_fts`）· `knowledge_relation`（from/to、relation、confidence、reason）

**岗位域**：`job` · `job_event` · `application` · `application_event` · `interview` · `greeting_message`

**运营域**：`weekly_review` · `automation_run` · `notification`（channel 预留 session/webhook/wechat）· `wechat_command`（未来微信指令预留）

状态机：`New → Shortlisted → Tailoring → Ready to Apply → Applied → Online Assessment → Interview → Offer | Rejected | Withdrawn | Closed`

完整 DDL 见 `server/db.py`（M1 交付物）。

---

## 8. 页面（Sidebar 14 项）

Dashboard / 今日岗位 / 岗位池 / 精投中心 / 投递管理 / 面试管理 / 我的资料库 / 简历中心 / 作品集 / 数据分析 / 周度复盘 / 微信助手（V1 显示通道状态与指令说明）/ 自动化 / 设置。

我的资料库页：上传、查看、搜索、分类、编辑、删除、预览、重新解析、建立关联 + 首页统计卡 + **"AI 最近发现的高价值资料"**。

---

## 9. Automation（6 个）

| # | 名称 | 计划 | 内容 |
|---|---|---|---|
| 1 | 工作日岗位雷达 | 周一至五 | 搜索/入库/去重/打分/Top 精投清单（目标20，宁缺毋滥） |
| 2 | 每日秋招管家 | 每日 | Ready未投、材料缺失、当日待办 |
| 3 | 截止日期提醒 | 每日 | 3 天内截止 + 逾期 |
| 4 | 面试提醒 | 每日 | 次日面试 + 准备项 |
| 5 | 周六周报 | 周六 | 本周数据汇总 → 方向分析 → 下周策略 |
| 6 | 重要状态变化提醒 | 事件触发（由管家扫描） | 面试邀约/Offer/Reject 等状态跃迁 |

全部共享 `jobs.db`；每次运行写 `automation_run`。

---

## 10. 微信（V1 不做，不阻塞）

- 当前环境无微信消息收发通道 → **V1 不做双向微信机器人**
- 架构预留：Notification API 的 channel 字段 + `wechat_command` 表 + 指令解析层设计文档
- 未来接入时：Web/WorkBuddy/微信调用同一套 API，零重构
- 可选过渡：Server酱/PushPlus webhook 单向推送（需 key，存 `.env`，V1 不依赖）

---

## 11. 隐私与安全

私人数据不出本机；不上传公开仓库；`.env` 管理密钥；代码与数据库不落明文密钥；`backups/` 每日快照保留 14 天。

---

## 12. 开发顺序（严格按确认的 PHASE 1-16）

| PHASE | 内容 | 状态 |
|---|---|---|
| 1 | 更新 PLAN.md | ✅ 本文件 |
| 2 | 数据库 schema（**= M1**） | ✅ 2026-08-30 完成：22 表 + FTS5（中文 bigram）+ 自测 7/7 + 备份机制 |
| 3 | Candidate Knowledge Base（上传/解析/OCR/URL/结构化/搜索/关联） | |
| 4 | 接入 Master Resume HTML/CSS（READ/ANALYZE → 锚点确认） | 待你上传源码 |
| 5 | JD → 资料库检索 | |
| 6 | Job Fit Score | |
| 7 | Precision Applications | |
| 8 | Resume Version（只改两段） | |
| 9 | HTML → PDF（Edge headless） | |
| 10 | 自动 Diff 验证（FAIL→回滚） | |
| 11 | JD 定制打招呼语 | |
| 12 | Application Tracker | |
| 13 | Interview Tracker | |
| 14 | Weekly Review | |
| 15 | Automation ×6 | |
| 16 | 微信 Channel（最后） | |

**V1 优先级**：核心闭环（资料→JD→匹配→精投→定制→Diff→PDF→打招呼→Ready→投递→跟踪→面试→复盘）优先于复杂 Dashboard / 微信 / 高级分析。

---

## 13. 待你提供

1. Master Resume HTML/CSS 源码（+PDF 参照版）→ 我先 READ/ANALYZE 报告锚点，确认后才定制
2. PDF 作品集、网页作品集 URL、奖状、项目资料、个人信息 → 逐项解析入库
3. （可选）Server酱/PushPlus key

**原始资料我只解析、绝不修改。**

---

## 14. 产品范围精简决策（2026-09-03）

> 性质：**范围缩减（scope cut）**，非破坏性数据改动；回归 v2.0 原始定位「人工投递 → 投递跟踪」。
> 对应提交 `41432fa`，里程碑标签 **`v0.3.0`**。

### 14.1 决策内容

经多轮实测后，用户决定砍掉两块功能、只保留核心捕捉能力：

1. **移除工作台「网申信息总汇」板块**（侧边栏导航 + `#page-netapply` 整页）。
2. **删除 Edge 插件「自动填网申」功能**，插件只剩「一键捕捉岗位」。

### 14.2 决策理由

- 自动填网申投入产出比低：
  - 各家 ATS 表单结构差异极大，规则填充覆盖率有限，缺口字段仍需人工补；
  - 实测出现过「实习经历误填为工作经历」「多值字段（如 6 个奖项）挤进同一栏、无法自动加行」等智能度问题，需逐站补识别规则，维护成本高；
  - 填表依赖「网申信息总汇」维护大量结构化字段（姓名/邮箱/手机/政治面貌/英语等级…），又需配套附件上传与 LLM 文档识别，链路重。
- 用户定位回归本系统原始职责：**AI 负责捕捉岗位、分析 JD、定制简历与打招呼语；网申表单填写回归人工**（人力投入更直接、更可控）。

### 14.3 影响（已删除的代码 / 文件）

| 位置 | 删除内容 |
|---|---|
| `web/index.html` | 「网申信息总汇」侧边栏项 + `#page-netapply` 区块 |
| `web/js/app.js` | `renderNetApply` 及 `netapplyForm` / `extractNetapply` / `editNetapply` / `delNetapply` / `wireNetApply` + `loadPage` 映射 |
| `edge-extension/popup.html` | 「填网申」按钮 + `fill-box`（含 `ats-opt` / `rec-resume`） |
| `edge-extension/popup.js` | `fill` / `fill-go` / `rec-resume` 处理器（保留 `getStoredTargetTab`，捕捉仍用） |
| `edge-extension/background.js` | `form-data` / `fill-netapply` / `fill-ats` / `fill-enhance` / `recommend-resume` / `smart-fill-map` 六个中继 |
| `edge-extension/content.js` | 删除（仅为填表注入脚本）；`manifest.json` 移除 `content_scripts` 条目 |
| `server/app.py` | 网申信息总汇 CRUD、附件上传/下载/识别、`_llm_refine_doc`、全部 extension 填表接口（`form-data` / `smart-fill-map` / `fill-netapply` / `fill-ats` / `fill-enhance` / `recommend-resume`）及辅助函数 |
| `server/db.py` | 不再创建 `net_apply_info` 表与迁移（`jobs.db` 中旧表保留为孤立表，未 DROP，防数据丢失） |
| `server/config.py` | 移除无引用的 `UPLOAD_DIR` |
| `edge-extension/README.md` | 同步去掉填网申说明 |

### 14.4 保留并验证可用

- Edge 插件「一键捕捉岗位」全链路正常：`capture` / `smart-capture`（DeepSeek 解析）/ `find-company-site`。
- 删除后 `py_compile` 通过、全仓库无 `netapply` / `fill` / `content.js` 残留引用；`/api/netapply` 返回 404，捕捉接口均 200。
- 运维提示：改 `manifest.json` / 插件文件后须到 `edge://extensions` 重新加载扩展；`app.py` 改动需重启本地服务（或走 `start-server.bat` 自带 `--reload`）。
