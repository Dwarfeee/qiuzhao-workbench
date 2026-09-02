"""资料解析：PDF 文本 / 图片 OCR / HTML / URL 抓取。

诚实声明：
- PDF 文本提取：pypdf（可用）
- 网页抓取：httpx + bs4（可用）
- 图片 OCR：rapidocr-onnxruntime，运行时探测，未安装则降级返回 None（前端显示"需人工转录"）
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

_OCR = None
_OCR_TRIED = False


def pdf_text(path: str | Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages):
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            parts.append(f"[第{i+1}页解析失败: {e}]")
    return "\n".join(parts).strip()


def ocr_text(path: str | Path) -> str | None:
    """图片 OCR。rapidocr 未安装时返回 None（调用方需标注'需人工转录'）。"""
    global _OCR, _OCR_TRIED
    if not _OCR_TRIED:
        _OCR_TRIED = True
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            _OCR = RapidOCR()
        except Exception:  # noqa: BLE001
            _OCR = None
    if _OCR is None:
        return None
    try:
        result, _ = _OCR(str(path))
        if not result:
            return ""
        return "\n".join(r[1] for r in result).strip()
    except Exception:  # noqa: BLE001
        return None


def html_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    return re.sub(r"\n{2,}", "\n", text).strip()


def fetch_url(url: str, timeout: float = 20.0) -> dict:
    """抓取网页：返回 {ok, status, title, text, site_name}。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "pdf" in ctype:
            tmp = Path("candidate") / "tmp_fetch.pdf"
            tmp.write_bytes(resp.content)
            return {"ok": True, "status": resp.status_code, "title": "", "site_name": "",
                    "text": pdf_text(tmp), "kind": "pdf"}
        soup = BeautifulSoup(resp.text, "lxml")
        title = (soup.title.string or "").strip() if soup.title else ""
        return {"ok": True, "status": resp.status_code, "title": title,
                "site_name": (soup.find("meta", property="og:site_name") or {}).get("content", "") if soup.find("meta", property="og:site_name") else "",
                "text": html_text(resp.text), "kind": "html"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "text": "", "title": "", "site_name": "", "kind": "none"}


def clean_text(t: str, limit: int = 200000) -> str:
    t = re.sub(r"[ \t]+", " ", t or "")
    return t[:limit].strip()
