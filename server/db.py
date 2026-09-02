"""jobs.db schema — 秋招 OS 唯一数据源。

三个逻辑域：资料域（Candidate KB）/ 岗位域（Job & Application）/ 运营域。
所有表公共列：id, created_at, updated_at。
可信度枚举贯穿全库：confirmed / possible / unverified。
"""
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"

_CJK = re.compile(r"[\u4e00-\u9fff]+")


def _bigram(text: str) -> str:
    """中文按重叠二元切分（'产品经理'→'产品 品经 经理'），拉丁词整体小写。
    使 FTS5 unicode61 分词器可对任意中文词命中（含双字词）。"""
    if not text:
        return ""
    tokens = []
    for run in _CJK.split(text):        # 拉丁/数字段
        for w in re.findall(r"[A-Za-z0-9]+", run):
            tokens.append(w.lower())
    for run in _CJK.findall(text):      # 中文段 → 重叠 bigram
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return " ".join(tokens)


def fts_tokenize(query: str) -> str:
    """把用户查询转成 FTS MATCH 语法（各词 AND）。"""
    toks = _bigram(query).split()
    return " ".join(toks)


def search_kb(query: str, limit: int = 50):
    """知识库全文检索：返回命中的 knowledge_item 行。"""
    q = fts_tokenize(query)
    if not q:
        return []
    conn = connect()
    try:
        return conn.execute(
            "SELECT ki.* FROM knowledge_fts f JOIN knowledge_item ki ON ki.id = f.rowid "
            "WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?",
            (q, limit)).fetchall()
    finally:
        conn.close()

CONFIDENCE = ("confirmed", "possible", "unverified")
APP_STATUSES = (
    "New", "Shortlisted", "Tailoring", "Ready to Apply", "Applied",
    "Online Assessment", "Interview", "Offer", "Rejected", "Withdrawn", "Closed",
)
INTERVIEW_ROUNDS = ("OA", "HR Screen", "一面", "二面", "三面", "Final", "HR", "Offer")
GRADES = ("S", "A", "B", "C", "D")
GREETING_KINDS = ("platform_short", "hr_chat", "apply_note", "portfolio_intro")
DOC_CATEGORIES = (
    "resume", "portfolio", "project", "experience", "education",
    "award", "certificate", "skill", "personal", "other",
)

PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=5000;",
)


class _Row(sqlite3.Row):
    """sqlite3.Row 子类：同时支持位置索引 row[1] 与 row.get('key', default)。"""

    def get(self, key, default=None):
        try:
            return self[key]
        except (IndexError, KeyError):
            return default


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = _Row
    conn.create_function("bigram", 1, _bigram, deterministic=True)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


SCHEMA = """
-- ============ 资料域 Candidate Knowledge Base ============

CREATE TABLE IF NOT EXISTS personal_info (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT, email TEXT, phone TEXT, city TEXT,
    target_cities TEXT,          -- json array
    target_directions TEXT,      -- json array
    target_positions TEXT,       -- json array
    target_industries TEXT,      -- json array
    work_preference TEXT,
    salary_expectation TEXT,
    available_date TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS net_apply_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,                       -- 分组：基本信息 / 教育 / 其他
    label TEXT NOT NULL,                 -- 字段名，如 姓名 / 邮箱 / 政治面貌
    value TEXT,
    hint TEXT,                           -- 占位提示 / 备注
    order_no INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS master_resume (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    master_dir TEXT NOT NULL,          -- resume/master 绝对路径（READ ONLY）
    html_path TEXT NOT NULL,
    css_paths TEXT,                    -- json array
    assets_dir TEXT,
    pdf_path TEXT,                     -- 渲染参照版 PDF
    self_eval_text TEXT,               -- 自我评价原文
    highlights_text TEXT,              -- 个人亮点原文
    anchors TEXT,                      -- json: {self_eval:{...}, highlights:{...}} DOM 锚点
    page_size TEXT,
    render_command TEXT,               -- Edge headless 命令模板
    text_extract TEXT,                 -- 全文（供检索）
    is_readonly INTEGER DEFAULT 1 CHECK (is_readonly = 1),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS resume_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES job(id),
    master_resume_id INTEGER NOT NULL REFERENCES master_resume(id),
    company TEXT NOT NULL,
    position TEXT,
    dir_path TEXT,                     -- resume/versions/<slug>/
    html_path TEXT,
    pdf_path TEXT,
    orig_self_eval TEXT,
    new_self_eval TEXT,
    orig_highlights TEXT,
    new_highlights TEXT,
    reason TEXT,                       -- 修改原因
    diff_json TEXT,                    -- 结构化 diff 结果
    diff_status TEXT DEFAULT 'pending' CHECK (diff_status IN ('pending','pass','fail')),
    pdf_check_json TEXT,               -- 文本/页数/视觉检查结果
    status TEXT DEFAULT 'draft',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS resume_modification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_version_id INTEGER NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('self_eval','highlights')),
    before_text TEXT,
    after_text TEXT,
    reason TEXT,
    evidence TEXT,                     -- json: [{source_type, source_id, quote, confidence}]
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('pdf','file','html')),
    file_path TEXT,
    summary TEXT,
    text_content TEXT,
    related_projects TEXT,             -- json
    related_skills TEXT,               -- json
    tags TEXT,                         -- json
    parse_status TEXT DEFAULT 'unparsed' CHECK (parse_status IN ('unparsed','parsed','failed')),
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS portfolio_website (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    site_name TEXT,
    url TEXT NOT NULL,
    fetch_time TEXT,
    content_summary TEXT,
    extracted_text TEXT,
    related_projects TEXT,             -- json
    related_skills TEXT,               -- json
    tags TEXT,                         -- json
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS project (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    role TEXT,
    outcome TEXT,
    metrics TEXT,                      -- json: 关键数据
    github_url TEXT,
    web_url TEXT,
    tags TEXT,
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS experience (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    title TEXT,
    start_date TEXT,
    end_date TEXT,
    content TEXT,
    projects TEXT,                     -- json
    outcome TEXT,
    metrics TEXT,                      -- json
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS education (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school TEXT NOT NULL,
    major TEXT,
    degree TEXT,
    start_date TEXT,
    end_date TEXT,
    courses TEXT,                      -- json
    gpa TEXT,
    honors TEXT,
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS award (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    award_name TEXT NOT NULL,
    competition TEXT,
    issuer TEXT,
    award_date TEXT,
    level TEXT,                        -- 国家级/省级/校级 等
    rank TEXT,
    count INTEGER NOT NULL DEFAULT 1,  -- 同类奖项的项数（如 xxx奖-1/-2 → 2项）
    related_project TEXT,
    abilities TEXT,                    -- json: 证明的能力
    evidence_doc_id INTEGER REFERENCES document(id),
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS certificate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    issuer TEXT,
    cert_date TEXT,
    level TEXT,
    evidence_doc_id INTEGER REFERENCES document(id),
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS skill (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT,
    proficiency TEXT,
    evidence TEXT NOT NULL,            -- json: [{source_type, source_id, quote, confidence}] 必填
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS document (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    file_type TEXT,                    -- pdf/png/jpg/docx/html/url...
    file_path TEXT,
    url TEXT,
    category TEXT CHECK (category IN ('resume','portfolio','project','experience','education','award','certificate','skill','personal','other')),
    source TEXT,                       -- 来源说明
    tags TEXT,                         -- json
    parse_status TEXT DEFAULT 'unparsed' CHECK (parse_status IN ('unparsed','parsed','failed')),
    summary TEXT,
    related_projects TEXT,             -- json
    related_jobs TEXT,                 -- json
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS knowledge_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,         -- resume/portfolio/project/experience/education/award/certificate/skill/personal_info/website
    source_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    text TEXT,
    summary TEXT,
    tags TEXT,                         -- json
    confidence TEXT DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','possible','unverified')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (source_type, source_id)
);

-- 全文索引（触发器内经 bigram() 中文二元切分，unicode61 按空格分词）
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    title, text, tags,
    content='knowledge_item', content_rowid='id', tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS knowledge_fts_ai AFTER INSERT ON knowledge_item BEGIN
    INSERT INTO knowledge_fts(rowid, title, text, tags)
        VALUES (new.id, bigram(new.title), bigram(new.text), bigram(new.tags));
END;
CREATE TRIGGER IF NOT EXISTS knowledge_fts_ad AFTER DELETE ON knowledge_item BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, text, tags)
        VALUES ('delete', old.id, bigram(old.title), bigram(old.text), bigram(old.tags));
END;
CREATE TRIGGER IF NOT EXISTS knowledge_fts_au AFTER UPDATE ON knowledge_item BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, text, tags)
        VALUES ('delete', old.id, bigram(old.title), bigram(old.text), bigram(old.tags));
    INSERT INTO knowledge_fts(rowid, title, text, tags)
        VALUES (new.id, bigram(new.title), bigram(new.text), bigram(new.tags));
END;

CREATE TABLE IF NOT EXISTS knowledge_relation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_type TEXT NOT NULL,
    from_id INTEGER NOT NULL,
    to_type TEXT NOT NULL,
    to_id INTEGER NOT NULL,
    relation TEXT,                     -- supports/mentions/related_to/...
    confidence TEXT NOT NULL CHECK (confidence IN ('confirmed','possible','unverified')),
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (from_type, from_id, to_type, to_id, relation)
);

-- ============ 岗位域 Job & Application ============

CREATE TABLE IF NOT EXISTS job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    url TEXT,
    source TEXT,                       -- 来源名（如 BOSS直聘）
    source_url TEXT,                   -- 来源站 URL（来源池 url，可追溯）
    job_type TEXT,                     -- 校招/社招/实习
    jd_text TEXT,
    fetch_time TEXT,
    publish_date TEXT,
    deadline TEXT,
    direction TEXT,                    -- 岗位方向
    fit_score REAL CHECK (fit_score IS NULL OR (fit_score >= 0 AND fit_score <= 100)),
    grade TEXT CHECK (grade IS NULL OR grade IN ('S','A','B','C','D')),
    status TEXT DEFAULT 'New',
    dedupe_key TEXT UNIQUE,            -- 公司+岗位 规范化指纹
    match_analysis TEXT,               -- json: [{requirement, evidence[], gap, relevance}]
    evidence TEXT,                     -- json: 汇总证据
    discovered_date TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS job_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,          -- discovered/scored/shortlisted/...
    detail TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS job_source (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,         -- 如 BOSS直聘、腾讯校招
    source_type TEXT NOT NULL,         -- edge_bookmark/platform/official/manual
    url TEXT,
    priority INTEGER DEFAULT 5,        -- 1-10，越高越优先
    enabled INTEGER DEFAULT 1,         -- 1 启用 / 0 暂停
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS job_source_url (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    source TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS application (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES job(id),
    resume_version_id INTEGER REFERENCES resume_version(id),
    portfolio_id INTEGER REFERENCES portfolio(id),
    greeting_id INTEGER REFERENCES greeting_message(id),
    status TEXT DEFAULT 'New' CHECK (status IN (
        'New','Shortlisted','Tailoring','Ready to Apply','Applied',
        'Online Assessment','Interview','Offer','Rejected','Withdrawn','Closed')),
    applied_date TEXT,
    source TEXT,
    next_step TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS application_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS interview (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES application(id),
    company TEXT NOT NULL,
    position TEXT,
    round TEXT NOT NULL CHECK (round IN ('OA','HR Screen','一面','二面','三面','Final','HR','Offer')),
    scheduled_at TEXT,
    interviewer TEXT,
    format TEXT,                       -- 现场/视频/电话/笔试
    questions TEXT,                    -- json
    my_answers TEXT,
    review TEXT,                       -- 复盘
    performance TEXT,                  -- 面试表现（好/差/待改进）
    interviewer_feedback TEXT,         -- 面试官反馈
    improvements TEXT,                 -- 需要改进的地方
    next_step TEXT,
    status TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS interview_question (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id INTEGER REFERENCES interview(id) ON DELETE CASCADE,
    company TEXT,
    position TEXT,
    round TEXT,
    question TEXT NOT NULL,
    my_answer TEXT,
    result TEXT,                       -- 通过/未通过/待定
    review TEXT,                       -- 复盘
    tags TEXT,                         -- json: 问题类型标签
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS greeting_message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES job(id),
    kind TEXT NOT NULL CHECK (kind IN ('platform_short','hr_chat','apply_note','portfolio_intro')),
    text TEXT NOT NULL,
    evidence TEXT,                     -- json: 每个事实断言的证据
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- ============ 运营域 ============

CREATE TABLE IF NOT EXISTS weekly_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week TEXT NOT NULL,                -- ISO 周次 e.g. 2026-W36
    stats TEXT,                        -- json: 发现/精投/投递/笔试/面试/Offer/Reject
    insights TEXT,                     -- 方向分析
    strategy_next_week TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS automation_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_name TEXT NOT NULL,
    run_at TEXT,
    status TEXT,
    output_summary TEXT
);

CREATE TABLE IF NOT EXISTS automation_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS notification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,                         -- new_s_grade/deadline/interview/daily_summary/weekly_review/status_change
    title TEXT,
    content TEXT,
    channel TEXT DEFAULT 'session' CHECK (channel IN ('session','webhook','wechat')),
    event_key TEXT,                    -- 去重键 e.g. deadline:{job_id}:{date}
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS wechat_command (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_text TEXT NOT NULL,
    intent TEXT,
    params TEXT,                       -- json
    confirm_status TEXT DEFAULT 'pending' CHECK (confirm_status IN ('pending','confirmed','rejected','executed')),
    result TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_job_company ON job(company);
CREATE INDEX IF NOT EXISTS idx_job_grade ON job(grade);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status);
CREATE INDEX IF NOT EXISTS idx_job_source_enabled ON job_source(enabled);
CREATE INDEX IF NOT EXISTS idx_application_status ON application(status);
CREATE INDEX IF NOT EXISTS idx_interview_app ON interview(application_id);
CREATE INDEX IF NOT EXISTS idx_greeting_job ON greeting_message(job_id);
CREATE INDEX IF NOT EXISTS idx_ki_source ON knowledge_item(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_rv_job ON resume_version(job_id);
"""

TRIGRAM_FALLBACK = ""  # 保留占位以兼容旧引用；已改用 bigram + unicode61


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：给已存在的表补齐新列（幂等）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job)")}
    if "source_url" not in cols:
        conn.execute("ALTER TABLE job ADD COLUMN source_url TEXT")
    if "job_type" not in cols:
        conn.execute("ALTER TABLE job ADD COLUMN job_type TEXT")
    icols = {r[1] for r in conn.execute("PRAGMA table_info(interview)")}
    for col in ("performance", "interviewer_feedback", "improvements"):
        if col not in icols:
            conn.execute(f"ALTER TABLE interview ADD COLUMN {col} TEXT")
    ncols = {r[1] for r in conn.execute("PRAGMA table_info(notification)")}
    for col in ("event_key", "error_message"):
        if col not in ncols:
            conn.execute(f"ALTER TABLE notification ADD COLUMN {col} TEXT")
    # 网申信息总汇：支持「文件(PDF/Word/图片) / 网络链接」类型
    nacols = {r[1] for r in conn.execute("PRAGMA table_info(net_apply_info)")}
    for col, typ in (("kind", "TEXT DEFAULT 'text'"), ("file_path", "TEXT"),
                     ("file_name", "TEXT"), ("file_ext", "TEXT"), ("link_url", "TEXT")):
        if col not in nacols:
            conn.execute(f"ALTER TABLE net_apply_info ADD COLUMN {col} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_event_key ON notification(event_key)")
    acols = {r[1] for r in conn.execute("PRAGMA table_info(award)")}
    if "count" not in acols:
        conn.execute("ALTER TABLE award ADD COLUMN count INTEGER NOT NULL DEFAULT 1")
    # 公司规模（用户手动标记：人数区间 0-99 / 100-499 / 500+ / 未标记），用于练手/投递策略
    jcols = {r[1] for r in conn.execute("PRAGMA table_info(job)")}
    if "company_scale" not in jcols:
        conn.execute("ALTER TABLE job ADD COLUMN company_scale TEXT")
    else:
        # 旧版「大厂/中厂/小厂」语义 → 新「人数区间」语义（幂等：只命中旧值）
        conn.execute("UPDATE job SET company_scale='0-99' WHERE company_scale='小厂'")
        conn.execute("UPDATE job SET company_scale='100-499' WHERE company_scale='中厂'")
        conn.execute("UPDATE job SET company_scale='500+' WHERE company_scale='大厂'")
    if "notes" not in jcols:
        conn.execute("ALTER TABLE job ADD COLUMN notes TEXT")
    # 薪资（文本 + 可排序的月薪下限 K），用于「薪资好的优先」排期
    if "salary" not in jcols:
        conn.execute("ALTER TABLE job ADD COLUMN salary TEXT")
    if "salary_min" not in jcols:
        conn.execute("ALTER TABLE job ADD COLUMN salary_min REAL")
    # 计划投递日期（周日排期时分配到周一~周五，YYYY-MM-DD）
    if "planned_date" not in jcols:
        conn.execute("ALTER TABLE job ADD COLUMN planned_date TEXT")
    # 简历满意度：用户生成定制简历后显式「满意」才允许投递（投递守卫依赖此列）
    acols = {r[1] for r in conn.execute("PRAGMA table_info(application)")}
    if "resume_approved" not in acols:
        conn.execute("ALTER TABLE application ADD COLUMN resume_approved INTEGER NOT NULL DEFAULT 0")
    # 精投中心「保留原岗位链接 + 来源链接」：加入精投时从 job 复制，避免 job 行被改/删除后丢失
    if "source_url" not in acols:
        conn.execute("ALTER TABLE application ADD COLUMN source_url TEXT")
    if "job_url" not in acols:
        conn.execute("ALTER TABLE application ADD COLUMN job_url TEXT")
    # 秋招状态（用户手动标记：进行中 / 未开始 / 未标记），用于岗位池一眼区分是否已开秋招
    if "fall_recruit" not in jcols:
        conn.execute("ALTER TABLE job ADD COLUMN fall_recruit TEXT NOT NULL DEFAULT ''")
    # 个人真名（网申/简历命名用）：缺省填 贺宣锦
    conn.execute("INSERT OR IGNORE INTO personal_info (id) VALUES (1)")
    pname = conn.execute("SELECT name FROM personal_info WHERE id=1").fetchone()
    if not pname or not pname["name"]:
        conn.execute("UPDATE personal_info SET name='贺宣锦' WHERE id=1")


def get_setting(key: str, default=None):
    """读 automation_settings（key/value 文本表），不存在返回 default。"""
    conn = connect()
    try:
        r = conn.execute("SELECT value FROM automation_settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    finally:
        conn.close()


def set_setting(key: str, value) -> None:
    """写 automation_settings（UPSERT）。value 转字符串存储。"""
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO automation_settings (key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
            (key, str(value)))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    conn = init_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print(f"DB: {DB_PATH}")
    print(f"SQLite {sqlite3.sqlite_version}, tables ({len(tables)}):")
    for t in tables:
        print(f"  - {t}")
    conn.close()
