"""公司官网检索（给 Edge 扩展「来源网站」用）。

为什么需要它：DeepSeek 等标准 chat 接口本身不能联网搜索，只能靠训练知识猜网址
（不可靠）。所以「大模型全网搜索官网」拆成两步：
  1) 这里用一个无需 Key 的搜索引擎（默认 DuckDuckGo 网页版）真实检索；
  2) 把候选结果交给 LLM（在 app.py 的端点里）挑出带招聘入口的官方站。

设计要点：
- 检索在「用户本机」的本地服务里发起，只要本机能联网即可，不依赖任何外部 Key。
- 解析失败时返回空列表，由调用方优雅降级（回退到原招聘站点名）。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import List, Dict

import httpx
from bs4 import BeautifulSoup

_DDG_HTML = "https://html.duckduckgo.com/html/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}


def _real_url(href: str) -> str:
    """DuckDuckGo 把结果包成 /l/?uddg=<encoded> 跳转，解出真实 URL。"""
    if not href:
        return ""
    if href.startswith("/l/?") or "uddg=" in href:
        try:
            q = urllib.parse.urlparse(href).query
            val = urllib.parse.parse_qs(q).get("uddg")
            if val:
                return urllib.parse.unquote(val[0])
        except Exception:
            return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return ""


def ddg_search(query: str, max_results: int = 8, timeout: int = 12) -> List[Dict[str, str]]:
    """用 DuckDuckGo 网页版检索，返回 [{url, title, snippet}, ...]。

    失败（无网/被拦/解析异常）一律返回空列表，不抛错。
    """
    q = urllib.parse.quote_plus(query)
    url = f"{_DDG_HTML}?q={q}"
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out: List[Dict[str, str]] = []
        for a in soup.select("a.result__a"):
            real = _real_url(a.get("href", ""))
            if not real:
                continue
            title = a.get_text(" ", strip=True)
            # snippet：同一结果块内的文案
            snippet = ""
            parent = a.parent
            if parent:
                sib = parent.find_next_sibling()
                if sib:
                    sp = sib.select_one(".result__snippet")
                    if sp:
                        snippet = sp.get_text(" ", strip=True)
            out.append({"url": real, "title": title, "snippet": snippet})
            if len(out) >= max_results:
                break
        return out
    except Exception:
        return []


def search_company_site(company: str, timeout: int = 12) -> List[Dict[str, str]]:
    """针对「公司官网（带招聘入口）」的两轮检索：先带『招聘』，没结果再退纯『官网』。"""
    if not company or not company.strip():
        return []
    c = company.strip()
    results = ddg_search(f"{c} 官网 招聘", max_results=8, timeout=timeout)
    if not results:
        results = ddg_search(f"{c} 官网", max_results=8, timeout=timeout)
    return results
