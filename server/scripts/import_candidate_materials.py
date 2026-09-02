"""把桌面上的真实求职资料导入 Candidate KB（只读原文件，复制入库，绝不移动/删除原文件）。

用户已确认方案：
- 简历：resume.html 保持 Source of Truth（不碰），单栏 PDF 作为参照件入库；
- 作品集：合并版-10M + 压缩版 20MB 两份都入；
- 奖项：按文件名结构化入库（不 OCR，文件名已含 年份/比赛/级别/名次）。

导入原则：全部 confidence='confirmed'（来自用户桌面真实正式资料）。
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\19600\WorkBuddy\秋招实录")
sys.path.insert(0, str(ROOT))

from server import config  # noqa: E402
from server.db import connect  # noqa: E402
from server.services import kb, parser  # noqa: E402

DESKTOP = Path(r"C:\Users\19600\Desktop")

# 奖项级别关键词映射（文件名里的级别 → 标准化）
LEVEL_MAP = {"国家级": "国家级", "省级": "省级", "市级": "市级", "校级": "校级"}


def import_resume_pdf():
    f = DESKTOP / "贺宣锦-UIUX-简历-单栏.pdf"
    if not f.exists():
        return {"skip": "单栏简历 PDF 不存在"}
    content = f.read_bytes()
    r = kb.ingest_file("贺宣锦-UIUX-简历-单栏.pdf", content, "resume",
                       source="桌面导入", tags=["简历", "单栏", "master参照"])
    return r


def import_portfolios():
    results = []
    for name in ("贺宣锦-UIUX-视觉传达设计-简历作品集合并版-10M以内.pdf",
                 "贺宣锦-UIUX-视觉传达设计-压缩版作品集.pdf"):
        f = DESKTOP / name
        if not f.exists():
            results.append({"name": name, "skip": "不存在"})
            continue
        r = kb.ingest_file(name, f.read_bytes(), "portfolio",
                           source="桌面导入", tags=["作品集", "PDF"])
        results.append({"name": name, "result": r})
    return results


def import_awards():
    """按文件名结构化导入奖项（不 OCR）。文件名格式：{年份}-{比赛}-{级别}-{名次}[-序号].ext

    同类奖项（同 年份+比赛+级别+名次）的多张证书（如 xxx奖-1/-2/-3）合并为一条记录，
    用 count 字段累计项数。总项数 = 证书张数。
    """
    base = DESKTOP / "奖项"
    if not base.exists():
        return {"skip": "奖项目录不存在"}

    # 第一遍：扫描并聚合（key = 年份+比赛+级别+名次，value = 证书文件列表）
    grouped: dict[tuple, list[Path]] = {}
    for level_dir in ("国家级", "省级", "市级", "校级"):
        d = base / level_dir
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            stem = re.sub(r"-\d+$", "", f.stem)   # 去掉尾部 -序号
            parts = stem.split("-")
            if len(parts) < 3:
                continue
            year = parts[0]
            level = LEVEL_MAP.get(parts[-2], parts[-2])
            rank = parts[-1]
            competition = "-".join(parts[1:-2])
            grouped.setdefault((competition, year, level, rank), []).append(f)

    conn = connect()
    try:
        imported, skipped_dup = 0, 0
        for (competition, year, level, rank), files in sorted(grouped.items()):
            count = len(files)
            exist = conn.execute(
                "SELECT id FROM award WHERE competition=? AND award_date=? AND level=? AND rank=?",
                (competition, year, level, rank)).fetchone()
            if exist:
                skipped_dup += 1
                continue
            # 证书图片全部留档到 candidate/awards（保留原文件名，含 -序号）
            dest_dir = config.CANDIDATE_DIR / "awards"
            dest_dir.mkdir(parents=True, exist_ok=True)
            for f in files:
                dest = dest_dir / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
            suffix = f"{rank}" if count == 1 else f"{rank} {count}项"
            cur = conn.execute(
                """INSERT INTO award (award_name, competition, award_date, level, rank, count,
                   related_project, abilities, confidence)
                   VALUES (?,?,?,?,?,?,?,?, 'confirmed')""",
                (f"{competition}·{suffix}", competition, year, level, rank, count, None, None))
            aid = cur.lastrowid
            # 入知识库（FTS 索引）
            conn.execute(
                """INSERT INTO knowledge_item (source_type, source_id, title, text, summary,
                   tags, confidence)
                   VALUES ('award', ?, ?, ?, ?, ?, 'confirmed')
                   ON CONFLICT(source_type, source_id) DO UPDATE SET
                     text=excluded.text, updated_at=datetime('now','localtime')""",
                (aid, f"{competition}·{level}·{suffix}",
                 f"{year} {competition} {level} {suffix}",
                 f"{level}·{competition}",
                 json_tags(["奖项", level])))
            imported += 1
        conn.commit()
        total_count = sum(len(v) for v in grouped.values())
        return {"imported": imported, "skipped_duplicate": skipped_dup,
                "total_certificates": total_count}
    finally:
        conn.close()


def json_tags(tags):
    import json as _json
    return _json.dumps(tags, ensure_ascii=False)


def main():
    print("=" * 60)
    print("导入桌面真实资料 → Candidate KB")
    print("=" * 60)
    print("\n[1] 简历 PDF（参照件）")
    print("   ", import_resume_pdf())
    print("\n[2] 作品集 PDF")
    for r in import_portfolios():
        print("   ", r)
    print("\n[3] 奖项（按文件名结构化）")
    print("   ", import_awards())
    print("\n完成。")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
