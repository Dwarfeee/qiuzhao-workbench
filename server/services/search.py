"""公司官网/招聘站检索（给 Edge 扩展「来源网站」用）。

为什么需要它：DeepSeek 等标准 chat 接口本身不能联网搜索，只能靠训练知识猜网址
（不可靠）。所以「大模型全网搜索官网」拆成两步：
  1) 这里用搜索引擎 + 站点探测真实定位招聘入口；
  2) 把候选结果交给 LLM（在 app.py 的端点里）做最终判定。

实现策略（按可靠性排序）：
  1. 常见公司招聘站知识库直接命中；
  2. 公司名 → 主域名映射 + 常见招聘子域探测；
  3. Bing 搜索公司官网 → 访问首页找招聘链接；
  4. Bing 搜索「公司 招聘」兜底；
  5. DuckDuckGo 最后兜底。
设计要点：
- 检索在「用户本机」的本地服务里发起，只要本机能联网即可，不依赖任何外部 Key。
- 解析/探测失败时返回空列表，由调用方优雅降级（提示用户手动填写官网）。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import List, Dict, Optional

import httpx
from bs4 import BeautifulSoup

_BING = "https://www.bing.com/search"
_DDG_HTML = "https://html.duckduckgo.com/html/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}

# 常见公司 → 主域名（用于生成招聘子域做探测）
_COMPANY_DOMAIN_MAP: Dict[str, str] = {
    "网易": "163.com",
    "网易游戏": "game.163.com",
    "网易互娱": "game.163.com",
    "腾讯": "tencent.com",
    "字节跳动": "bytedance.com",
    "字节": "bytedance.com",
    "抖音": "bytedance.com",
    "阿里巴巴": "alibaba.com",
    "阿里": "alibaba.com",
    "蚂蚁集团": "antgroup.com",
    "蚂蚁金服": "antgroup.com",
    "美团": "meituan.com",
    "百度": "baidu.com",
    "京东": "jd.com",
    "拼多多": "pinduoduo.com",
    "华为": "huawei.com",
    "小米": "mi.com",
    "快手": "kuaishou.com",
    "小红书": "xiaohongshu.com",
    "哔哩哔哩": "bilibili.com",
    "b站": "bilibili.com",
    "携程": "trip.com",
    "滴滴": "didiglobal.com",
    "蔚来": "nio.com",
    "理想汽车": "lixiang.com",
    "小鹏汽车": "xiaopeng.com",
    "米哈游": "mihoyo.com",
    "莉莉丝": "lilith.com",
    "叠纸": "papergames.cn",
    "鹰角": "hypergryph.com",
}

# 招聘子域/路径候选（按优先级）
_RECRUIT_SUBDOMAINS = [
    "hr", "jobs", "careers", "career", "campus", "join", "recruit", "zhaopin",
    "job", "talent", "people", "hrms", "personnel",
]
_RECRUIT_PATHS = [
    "/jobs", "/careers", "/join", "/recruit", "/campus", "/zhaopin",
    "/about/jobs", "/about/careers", "/hr", "/job",
]

# 已知公司招聘站知识库（直接命中，避免探测）
_KNOWN_RECRUIT_URLS: Dict[str, List[str]] = {
    "网易": ["https://hr.163.com/"],
    "网易游戏": ["https://hr.game.163.com/"],
    "网易互娱": ["https://hr.game.163.com/"],
    "腾讯": ["https://careers.tencent.com/"],
    "字节跳动": ["https://jobs.bytedance.com/"],
    "字节": ["https://jobs.bytedance.com/"],
    "抖音": ["https://jobs.bytedance.com/"],
    "阿里巴巴": ["https://talent.alibaba.com/"],
    "阿里": ["https://talent.alibaba.com/"],
    "蚂蚁集团": ["https://talent.antgroup.com/"],
    "蚂蚁金服": ["https://talent.antgroup.com/"],
    "美团": ["https://zhaopin.meituan.com/"],
    "百度": ["https://talent.baidu.com/"],
    "京东": ["https://campus.jd.com/"],
    "拼多多": ["https://careers.pinduoduo.com/"],
    "华为": ["https://career.huawei.com/"],
    "小米": ["https://hr.xiaomi.com/"],
    "快手": ["https://campus.kuaishou.com/"],
    "小红书": ["https://job.xiaohongshu.com/"],
    "哔哩哔哩": ["https://jobs.bilibili.com/"],
    "b站": ["https://jobs.bilibili.com/"],
    "携程": ["https://careers.trip.com/"],
    "滴滴": ["https://talent.didiglobal.com/"],
    "米哈游": ["https://jobs.mihoyo.com/"],
    "莉莉丝": ["https://jobs.lilith.com/"],
    "叠纸": ["https://jobs.papergames.cn/"],
    "鹰角": ["https://jobs.hypergryph.com/"],
}

# 第三方招聘系统域名（公司名直接作为子域）
_THIRD_PARTY_RECRUIT_DOMAINS = [
    "mokahr.com", "zhiye.com", "dayee.com", "hrxincai.com",
]


def _normalize_company(name: str) -> str:
    """去掉常见后缀，方便知识库命中。"""
    n = name.strip().lower()
    for suffix in ["有限公司", "有限责任公司", "公司", "集团", "股份有限公司"]:
        if n.endswith(suffix.lower()):
            n = n[:-len(suffix)].strip()
    return n


def _resolve_url(href: str, timeout: int = 8) -> str:
    """把百度 / DuckDuckGo 的跳转/加密链接解成真实 URL。"""
    if not href:
        return ""
    if href.startswith("/l/?") or "uddg=" in href:
        try:
            q = urllib.parse.urlparse(href).query
            val = urllib.parse.parse_qs(q).get("uddg")
            if val:
                return urllib.parse.unquote(val[0])
        except Exception:
            pass
    if "baidu.com/link" in href or href.startswith("https://www.baidu.com/baidu.php"):
        try:
            r = httpx.head(href, headers=_HEADERS, timeout=timeout, follow_redirects=True)
            final = str(r.url)
            if final.startswith("http") and "baidu.com" not in urllib.parse.urlparse(final).netloc:
                return final
        except Exception:
            pass
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return ""


def _url_looks_like_homepage(url: str, company: str) -> bool:
    """粗略判断 URL 是否像公司官网首页（排除百科、邮箱、新闻子站等）。"""
    if not url.startswith("http"):
        return False
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path.lower()
    excluded = [
        "baike.baidu.com", "zh.wikipedia.org", "wiki", "mail.", "email.",
        "news.", "music.", "video.", "map.", "image.", "zhihu.com",
    ]
    if any(e in host or e in path for e in excluded):
        return False
    return True


def _url_looks_like_recruitment(url: str) -> bool:
    """粗略判断 URL 是否像招聘站。"""
    if not url.startswith("http"):
        return False
    full = url.lower()
    host = urllib.parse.urlparse(url).netloc.lower()
    # 排除招聘平台
    excluded_platforms = ["zhipin.com", "boss", "lagou.com", "liepin.com", "nowcoder.com",
                          "linkedin.com", "51job.com", "智联招聘", "前程无忧", "猎聘"]
    if any(p in host for p in excluded_platforms):
        return False
    signals = ["hr", "jobs", "career", "campus", "join", "recruit", "zhaopin", "talent"]
    return any(s in host or s in full for s in signals)


def _head_check(url: str, timeout: int = 8) -> Optional[str]:
    """HEAD 探测 URL，返回最终 URL（若看起来像招聘站）。"""
    try:
        r = httpx.head(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code >= 400:
            return None
        final = str(r.url)
        # 只要 2xx/3xx 且不是 404 就接受；后续 LLM/启发式再挑
        return final
    except Exception:
        return None


def _probe_subdomain_candidates(domain: str) -> List[Dict[str, str]]:
    """根据主域名生成常见招聘子域并探测。"""
    found: List[Dict[str, str]] = []
    # 子域：hr.xxx.com, jobs.xxx.com, ...
    for sub in _RECRUIT_SUBDOMAINS:
        url = f"https://{sub}.{domain}/"
        final = _head_check(url)
        if final and _url_looks_like_recruitment(final):
            found.append({"url": final, "title": f"{sub}.{domain} 招聘站", "snippet": ""})
    # 主域 + 招聘路径
    for path in _RECRUIT_PATHS:
        url = f"https://www.{domain}{path}"
        final = _head_check(url)
        if final and _url_looks_like_recruitment(final):
            found.append({"url": final, "title": f"{domain}{path} 招聘页", "snippet": ""})
    # 第三方招聘系统：xxx.mokahr.com, xxx.zhiye.com
    for third in _THIRD_PARTY_RECRUIT_DOMAINS:
        # 去掉 .com 等后缀生成子域前缀
        prefix = domain.replace(".com", "").replace(".cn", "").replace(".net", "").split(".")[0]
        url = f"https://{prefix}.{third}/"
        final = _head_check(url)
        if final:
            found.append({"url": final, "title": f"{prefix}.{third} 招聘系统", "snippet": ""})
    return found


def _extract_meta_refresh(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """解析 HTML 里的 meta refresh，返回跳转 URL。"""
    for meta in soup.find_all("meta"):
        equiv = meta.get("http-equiv", "").lower()
        content = meta.get("content", "")
        if equiv == "refresh":
            m = re.search(r"url\s*=\s*['\"]?([^'\";>]+)", content, re.I)
            if m:
                return urllib.parse.urljoin(base_url, m.group(1).strip())
    return None


def _probe_homepage_recruitment(homepage: str) -> List[Dict[str, str]]:
    """访问公司官网首页，找招聘/加入我们链接。"""
    found: List[Dict[str, str]] = []
    try:
        r = httpx.get(homepage, headers=_HEADERS, timeout=12, follow_redirects=True)
        if r.status_code >= 400:
            return found
        final_base = str(r.url)
        soup = BeautifulSoup(r.text, "html.parser")

        # 先处理 meta refresh
        refresh = _extract_meta_refresh(soup, final_base)
        if refresh:
            r2 = httpx.get(refresh, headers=_HEADERS, timeout=12, follow_redirects=True)
            if r2.status_code < 400:
                final_base = str(r2.url)
                soup = BeautifulSoup(r2.text, "html.parser")

        kw = re.compile(r"招聘|招贤|加入我们|careers|jobs|join us|校园招聘|社会招聘|人才招聘", re.I)
        seen = set()
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            href = a["href"]
            if kw.search(text) or kw.search(href):
                full = urllib.parse.urljoin(final_base, href)
                if full in seen:
                    continue
                seen.add(full)
                found.append({"url": full, "title": text or "招聘入口", "snippet": ""})
        return found
    except Exception:
        return found


def _ddg_search(query: str, max_results: int = 8, timeout: int = 12) -> List[Dict[str, str]]:
    """DuckDuckGo 网页版检索。失败一律返回空列表。"""
    q = urllib.parse.quote_plus(query)
    url = f"{_DDG_HTML}?q={q}"
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out: List[Dict[str, str]] = []
        for a in soup.select("a.result__a"):
            real = _resolve_url(a.get("href", ""))
            if not real:
                continue
            title = a.get_text(" ", strip=True)
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


def _bing_search(query: str, max_results: int = 8, timeout: int = 12) -> List[Dict[str, str]]:
    """Bing 网页版检索。失败一律返回空列表。"""
    q = urllib.parse.quote_plus(query)
    url = f"{_BING}?q={q}&count={max_results}&setlang=zh-CN&cc=CN&ensearch=0"
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out: List[Dict[str, str]] = []
        for li in soup.select("li.b_algo"):
            h2 = li.select_one("h2")
            a = h2.select_one("a") if h2 else None
            if not a:
                continue
            href = a.get("href", "")
            if not href or not href.startswith("http"):
                continue
            title = a.get_text(" ", strip=True)
            p = li.select_one("p")
            snippet = p.get_text(" ", strip=True) if p else ""
            out.append({"url": _resolve_url(href), "title": title, "snippet": snippet})
            if len(out) >= max_results:
                break
        if not out:
            for a in soup.select("#b_results h2 a"):
                href = a.get("href", "")
                if not href or not href.startswith("http"):
                    continue
                out.append({"url": _resolve_url(href), "title": a.get_text(" ", strip=True), "snippet": ""})
                if len(out) >= max_results:
                    break
        return out
    except Exception:
        return []


def _guess_homepage_from_bing(company: str) -> Optional[str]:
    """用 Bing 搜公司官网，挑一个最像官网的 URL。"""
    for q in (f"{company} 官方网站", f"{company} 官网"):
        results = _bing_search(q, max_results=8)
        for r in results:
            url = r["url"]
            if _url_looks_like_homepage(url, company):
                return url
    return None


def search_company_site(company: str, timeout: int = 12) -> List[Dict[str, str]]:
    """针对「公司招聘官网」的多层检索。"""
    if not company or not company.strip():
        return []
    c = company.strip()
    norm = _normalize_company(c)

    # 1) 知识库直接命中
    if norm in _KNOWN_RECRUIT_URLS:
        return [{"url": u, "title": f"{c} 招聘官网", "snippet": "知识库命中"} for u in _KNOWN_RECRUIT_URLS[norm]]

    # 2) 公司名 → 主域名映射，探测招聘子域
    domain = _COMPANY_DOMAIN_MAP.get(norm)
    if domain:
        candidates = _probe_subdomain_candidates(domain)
        if candidates:
            return candidates

    # 3) Bing 搜官网 → 首页探测招聘链接
    homepage = _guess_homepage_from_bing(c)
    if homepage:
        recruits = _probe_homepage_recruitment(homepage)
        if recruits:
            return recruits
        # 首页没找到，用首页域名探测子域
        parsed = urllib.parse.urlparse(homepage)
        root_domain = parsed.netloc.replace("www.", "")
        if root_domain:
            candidates = _probe_subdomain_candidates(root_domain)
            if candidates:
                return candidates

    # 4) Bing 直接搜「公司 招聘」
    for q in (f"{c} 招聘官网", f"{c} 招聘"):
        results = _bing_search(q, max_results=8)
        recruits = [r for r in results if _url_looks_like_recruitment(r["url"])]
        if recruits:
            return recruits
        if results:
            return results

    # 5) DuckDuckGo 最后兜底
    for q in (f"{c} 招聘官网", f"{c} 招聘"):
        results = _ddg_search(q, max_results=8)
        recruits = [r for r in results if _url_looks_like_recruitment(r["url"])]
        if recruits:
            return recruits
        if results:
            return results

    return []
