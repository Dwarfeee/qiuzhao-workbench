"""诊断：直接对 Master Resume 跑 measure_layout，打印真实 overflowPx / pageH / maxBottom。
不依赖服务进程，用于判断「简历显示溢出」是真实溢出还是 measure 误报。
"""
import sys, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # 秋招实录/
sys.path.insert(0, str(ROOT))

from server import config
from server.services.resume import measure_layout

def main():
    html_path = config.MASTER_HTML
    if not html_path.exists():
        print("MASTER_HTML 不存在:", html_path)
        return
    print("master html:", html_path)
    vdir = ROOT / "resume" / "_diag"
    vdir.mkdir(parents=True, exist_ok=True)
    try:
        r = measure_layout(html_path, vdir)
        print("measure result:", r)
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    main()
