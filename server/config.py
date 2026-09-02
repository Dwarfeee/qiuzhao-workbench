"""全局路径与常量配置。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"

# ---- 本地服务 ----
HOST = "127.0.0.1"
PORT = 8787
BASE_URL = f"http://{HOST}:{PORT}"

# ---- Master Resume（只读源，永不写入）----
MASTER_SOURCE_DIR = Path(r"C:\Users\19600\Desktop\resume_build")   # 原始环境，只读
MASTER_HTML = MASTER_SOURCE_DIR / "resume.html"
MASTER_SNAPSHOT_DIR = ROOT / "resume" / "master"                   # 系统内只读快照
VERSIONS_DIR = ROOT / "resume" / "versions"                        # Tailored 版本目录

# ---- 浏览器渲染 ----
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# ---- Candidate 文件存储 ----
CANDIDATE_DIR = ROOT / "candidate"

# ---- 网申信息总汇：上传的附件（PDF/Word/图片/等）存储目录 ----
UPLOAD_DIR = ROOT / "uploads" / "netapply"

# ---- 允许修改的简历区域（白名单，除此之外全部锁定）----
EDITABLE_REGIONS = ("self_eval", "highlights")

# ---- Web 资源 ----
WEB_DIR = ROOT / "web"

# 岗位方向关键词（用于 job.direction 归类）
DIRECTIONS = {
    "UI/UX": ["ui", "ux", "交互设计", "用户体验", "界面设计", "视觉设计", "设计师", "designer", "体验设计"],
    "AI产品": ["ai产品", "ai agent", "agent", "大模型", "llm", "智能体", "aigc", "ai应用", "ai 设计"],
    "产品设计": ["产品经理", "产品设计", "产品助理", "product"],
    "数据分析": ["数据分析", "数据产品", "bi", "数据可视化"],
    "前端/创意开发": ["前端", "web", "创意开发", "creative developer", "html"],
}

import json
from pathlib import Path


def load_env() -> dict:
    """读取 .env（若有），返回键值 dict。Secret 不进代码。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return {}
    out = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def write_env(updates: dict) -> None:
    """安全更新 .env：只写入 updates 中的键，保留其他键值；值为空串则删除该键。

    绝不在日志/返回值中暴露明文。Secret 只落 .env。
    """
    env_path = ROOT / ".env"
    existing: dict = {}
    order: list[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            k = k.strip()
            if k not in existing:
                order.append(k)
            existing[k] = v.strip()
    for k, v in updates.items():
        v = (v or "").strip()
        if v == "":
            existing.pop(k, None)
            if k in order:
                order.remove(k)
        else:
            if k not in existing:
                order.append(k)
            existing[k] = v
    lines = [f"{k}={existing[k]}" for k in order]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
