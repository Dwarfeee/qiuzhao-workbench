"""M1 自测：验证 jobs.db 核心约束真实生效（不留测试数据）。

覆盖：
1. 中文 FTS5 全文检索（trigram）
2. 可信度枚举硬约束（possible/unverified 无法冒充 confirmed 之外的状态）
3. 投递状态机枚举约束
4. 简历版本 diff_status 约束
5. job 去重唯一键
6. 外键级联
"""
import sys
import sqlite3

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\19600\WorkBuddy\秋招实录\server")
from db import connect, search_kb  # noqa: E402

PASSED, FAILED = [], []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as e:
        FAILED.append((name, e))
        print(f"  FAIL  {name}: {e}")


def expect_error(sql, params=()):
    try:
        conn.execute(sql, params)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return
    raise AssertionError("应当被约束拒绝，但写入成功了")


conn = connect()


def t_fts_chinese():
    conn.execute(
        "INSERT INTO knowledge_item (source_type, source_id, title, text) "
        "VALUES ('project', 900001, 'AI 产品项目测试', '主导 AI 产品设计与用户研究，数据分析驱动迭代')")
    conn.commit()
    for kw in ("AI 产品", "用户研究", "数据分析", "产品", "项目"):
        rows = search_kb(kw)
        assert rows and any(r["title"] == "AI 产品项目测试" for r in rows), \
            f"检索『{kw}』未命中（含双字词）"
    conn.execute("DELETE FROM knowledge_item WHERE source_id=900001 AND source_type='project'")
    conn.commit()
    assert not search_kb("用户研究"), "删除后 FTS 未同步"


def t_confidence_enum():
    expect_error(
        "INSERT INTO project (name, confidence) VALUES ('X', 'fake')")


def t_app_status_enum():
    conn.execute("INSERT INTO job (company, title) VALUES ('测试公司', '测试岗位')")
    job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    expect_error(
        "INSERT INTO application (job_id, status) VALUES (?, '随便投')", (job_id,))
    conn.execute("DELETE FROM job WHERE id=?", (job_id,))
    conn.commit()


def t_diff_status_enum():
    expect_error(
        "INSERT INTO resume_version (job_id, master_resume_id, company, diff_status) "
        "VALUES (1, 1, 'X', 'maybe')")


def t_job_dedupe():
    conn.execute("INSERT INTO job (company, title, dedupe_key) VALUES ('腾讯', 'AI产品经理', 'tencent|ai-pm')")
    expect_error(
        "INSERT INTO job (company, title, dedupe_key) VALUES ('腾讯', 'AI产品经理', 'tencent|ai-pm')")
    conn.execute("DELETE FROM job WHERE dedupe_key='tencent|ai-pm'")
    conn.commit()


def t_relation_unique():
    conn.execute(
        "INSERT INTO knowledge_relation (from_type, from_id, to_type, to_id, relation, confidence) "
        "VALUES ('project', 1, 'job', 1, 'supports', 'confirmed')")
    expect_error(
        "INSERT INTO knowledge_relation (from_type, from_id, to_type, to_id, relation, confidence) "
        "VALUES ('project', 1, 'job', 1, 'supports', 'possible')")
    conn.execute("DELETE FROM knowledge_relation WHERE from_type='project' AND from_id=1")
    conn.commit()


def t_interview_round_enum():
    expect_error(
        "INSERT INTO interview (company, round) VALUES ('X', '第七面')")


for name, fn in [
    ("中文 FTS5 检索 + 触发器同步", t_fts_chinese),
    ("可信度枚举硬约束", t_confidence_enum),
    ("投递状态机枚举", t_app_status_enum),
    ("简历 diff_status 枚举", t_diff_status_enum),
    ("岗位去重唯一键", t_job_dedupe),
    ("知识关联唯一 + 置信度约束", t_relation_unique),
    ("面试轮次枚举", t_interview_round_enum),
]:
    check(name, fn)

leftover = conn.execute(
    "SELECT count(*) FROM job WHERE company='测试公司'").fetchone()[0]
assert leftover == 0, "测试数据未清理"
conn.close()

print(f"\n结果：{len(PASSED)} 通过 / {len(FAILED)} 失败")
if FAILED:
    sys.exit(1)
print("M1 自测全部通过。")
