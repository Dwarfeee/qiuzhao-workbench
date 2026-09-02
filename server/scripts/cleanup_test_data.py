"""清理测试阶段残留数据，让系统干净进入真实使用。

清理内容：
1. 知识库去重：每个 (source_type, title) 只保留最早一条，删除重复的实体行 + knowledge_item；
2. 删除测试假岗位（company 含「示例」「测试」「最终验收」）及关联的 application/interview/greeting/resume_version/job_source_url/job_event；
3. 清空测试通知、automation_run 测试记录、interview_question 测试记录、weekly_review。

保留：今天从桌面导入的真实资料（简历 PDF、作品集、按文件名入库的奖项）、真实岗位（广东人才网设计工程师）。

已先备份 jobs.db。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"C:\Users\19600\WorkBuddy\秋招实录")
sys.path.insert(0, str(ROOT))

from server.db import connect  # noqa: E402


def dedupe_entities(conn) -> dict:
    """各实体表按唯一键去重，保留最小 id，删除其余（连带 knowledge_item）。"""
    result = {}
    # (表, 去重列, knowledge_item 的 source_type)
    entities = [
        ("award", "competition", "award"),
        ("experience", "company", "experience"),
        ("project", "name", "project"),
        ("skill", "name", "skill"),
        ("education", "school", "education"),
        ("portfolio_website", "title", "website"),
    ]
    for table, col, stype in entities:
        # 找出重复的 col 值
        dup_rows = conn.execute(
            f"SELECT {col}, min(id) keep_id, group_concat(id) ids, count(*) n "
            f"FROM {table} GROUP BY {col} HAVING n > 1").fetchall()
        removed = 0
        for r in dup_rows:
            ids = [int(x) for x in r["ids"].split(",")]
            drop_ids = [i for i in ids if i != r["keep_id"]]
            for did in drop_ids:
                conn.execute(f"DELETE FROM knowledge_item WHERE source_type=? AND source_id=?", (stype, did))
                conn.execute(f"DELETE FROM {table} WHERE id=?", (did,))
                removed += 1
        result[table] = removed
    return result


def clean_test_jobs(conn) -> dict:
    """删除测试假岗位及其关联数据。"""
    # 测试岗位特征：company 含 示例/测试/最终验收
    jobs = conn.execute(
        "SELECT id FROM job WHERE company LIKE '%示例%' OR company LIKE '%测试%' OR company LIKE '%最终验收%'"
    ).fetchall()
    jids = [r["id"] for r in jobs]
    removed_jobs = len(jids)
    removed_apps = removed_iv = 0
    if jids:
        for jid in jids:
            # 关联 application
            apps = conn.execute("SELECT id FROM application WHERE job_id=?", (jid,)).fetchall()
            for a in apps:
                removed_apps += 1
                conn.execute("DELETE FROM interview WHERE application_id=?", (a["id"],))
                conn.execute("DELETE FROM interview_question WHERE interview_id IN "
                             "(SELECT id FROM interview WHERE application_id=?)", (a["id"],))
                conn.execute("DELETE FROM application_event WHERE application_id=?", (a["id"],))
            removed_iv += len(conn.execute(
                "SELECT id FROM interview WHERE application_id IN (SELECT id FROM application WHERE job_id=?)",
                (jid,)).fetchall())
            # 先清 application.greeting_id 引用，再删 greeting
            conn.execute("UPDATE application SET greeting_id=NULL, resume_version_id=NULL WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM greeting_message WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM resume_version WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM job_source_url WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM job_event WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM application WHERE job_id=?", (jid,))
            conn.execute("DELETE FROM job WHERE id=?", (jid,))
    return {"jobs": removed_jobs, "applications": removed_apps, "interviews": removed_iv}


def clean_misc(conn) -> dict:
    """清空测试通知、运行记录、测试问题、周报（都是测试产生的，真实使用会重新生成）。"""
    n_notif = conn.execute("SELECT count(*) FROM notification").fetchone()[0]
    n_runs = conn.execute("SELECT count(*) FROM automation_run").fetchone()[0]
    n_q = conn.execute("SELECT count(*) FROM interview_question").fetchone()[0]
    n_review = conn.execute("SELECT count(*) FROM weekly_review").fetchone()[0]
    conn.execute("DELETE FROM notification")
    conn.execute("DELETE FROM automation_run")
    conn.execute("DELETE FROM interview_question")
    conn.execute("DELETE FROM weekly_review")
    return {"notifications": n_notif, "runs": n_runs, "questions": n_q, "reviews": n_review}


def main():
    print("=" * 60)
    print("清理测试残留数据")
    print("=" * 60)
    conn = connect()
    try:
        print("\n[1] 知识库去重")
        for k, v in dedupe_entities(conn).items():
            print(f"    {k}: 删除 {v} 条重复")
        print("\n[2] 清理测试岗位/投递/面试")
        for k, v in clean_test_jobs(conn).items():
            print(f"    {k}: {v}")
        print("\n[3] 清理测试通知/运行记录/问题/周报")
        for k, v in clean_misc(conn).items():
            print(f"    {k}: {v}")
        conn.commit()
        print("\n清理完成，已提交。")
    finally:
        conn.close()

    # 验证
    conn = connect()
    try:
        print("\n=== 清理后状态 ===")
        total = conn.execute("SELECT count(*) FROM knowledge_item").fetchone()[0]
        distinct = conn.execute(
            "SELECT count(DISTINCT source_type||'|'||title) FROM knowledge_item").fetchone()[0]
        print(f"knowledge_item: 总数 {total}，唯一 {distinct}（应相等=无重复）")
        for t, col in [('award','competition'),('experience','company'),('project','name'),('skill','name')]:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n}")
        print("job 表:")
        for r in conn.execute("SELECT id, company, title, source FROM job ORDER BY id"):
            print(f"    #{r['id']} {r['company']} · {r['title']} [{r['source']}]")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
