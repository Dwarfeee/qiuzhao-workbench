"""每日备份 jobs.db → backups/，保留最近 14 天。用 SQLite backup API 保证 WAL 一致性。"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import connect, DB_PATH, ROOT  # noqa: E402

BACKUP_DIR = ROOT / "backups"
KEEP_DAYS = 14


def backup() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"jobs_{ts}.db"
    src = connect(DB_PATH)
    dst = connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    prune()
    return dest


def prune():
    cutoff = time.time() - KEEP_DAYS * 86400
    removed = 0
    for f in BACKUP_DIR.glob("jobs_*.db"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        print(f"已清理 {removed} 个过期备份")


if __name__ == "__main__":
    p = backup()
    print(f"备份完成: {p} ({p.stat().st_size} bytes)")
