"""
common.py — walkthrough_search 和 wiki_search 的共享工具模块
"""

import sys
import os
import re
import time
import random
import logging
# (no urllib.parse imports needed — ddgs handles URL construction)

# ── Vendor fallback: 沙箱环境下自带依赖，无需 pip install ──
_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
if os.path.isdir(_vendor_dir):
    sys.path.insert(0, _vendor_dir)

try:
    import requests
except ImportError:
    pass

try:
    import urllib3
except ImportError:
    pass

logger = logging.getLogger("aiga_common")

# ── 反爬配置 ──

RETRY_MAX = 3
RETRY_BASE_DELAY = 2
DELAY_MIN = 0.3
DELAY_MAX = 1.5

# ── 代理 ──

def build_proxy_url(proxy_cfg):
    """从代理配置构建 URL，未启用返回 None"""
    if not proxy_cfg or not proxy_cfg.get("enabled", False):
        return None
    ptype = proxy_cfg.get("type", "http")
    host = proxy_cfg.get("host", "127.0.0.1")
    port = proxy_cfg.get("port", 7890)
    username = proxy_cfg.get("username", "")
    password = proxy_cfg.get("password", "")
    auth = "%s:%s@" % (username, password) if username else ""
    if ptype == "socks5":
        return "socks5://%s%s:%d" % (auth, host, port)
    return "http://%s%s:%d" % (auth, host, port)


# ── Session ──

def make_session(user_agent, proxy_cfg=None, use_proxy=False, log_name=None):
    """创建 requests Session，含标准头、SSL宽松、可选代理"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    })
    session.verify = False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if use_proxy and proxy_cfg:
        proxy_url = build_proxy_url(proxy_cfg)
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}
            logger.info("[Proxy] %s 使用代理", log_name or "session")

    session._last_referer = None
    return session


# ── 反爬 ──

def _jitter():
    delay = random.uniform(DELAY_MIN, DELAY_MAX)
    time.sleep(delay)


def fetch_html(session, url, timeout=15, referer=True):
    """带重试+退避+Referer的 HTML 抓取"""
    last_err = None
    for attempt in range(RETRY_MAX):
        try:
            if attempt > 0:
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.info("[重试] 第%d次 (%ds后)", attempt + 1, wait)
                time.sleep(wait)
            _jitter()
            headers = {}
            if referer and getattr(session, '_last_referer', None):
                headers["Referer"] = session._last_referer
            resp = session.get(url, timeout=timeout, headers=headers)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 10))
                logger.warning("[限流] 429，等待%ds", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                logger.warning("[服务端错误] %d，重试", resp.status_code)
                continue
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            session._last_referer = url
            return resp.text
        except Exception as e:
            last_err = str(e)[:50]
            msg = "[超时]" if "timeout" in str(e).lower() else "[连接错误]"
            logger.warning("%s 第%d次: %s", msg, attempt + 1, last_err)
    raise Exception("请求失败 (%d次): %s" % (RETRY_MAX, last_err))


def fetch_json(session, url, timeout=15):
    """带重试+退避的 JSON 抓取"""
    _jitter()
    for attempt in range(RETRY_MAX):
        try:
            if attempt > 0:
                time.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = str(e)[:50]
    raise Exception("JSON请求失败: %s" % last_err)


# ── 代理判断 ──

def source_needs_proxy(source):
    """判断源是否需要代理（按配置字段或域名推断）"""
    if "uses_proxy" in source:
        return source["uses_proxy"]
    url = source.get("url", "") or source.get("base_url", "")
    overseas = ["fandom.com", "wikipedia.org", "wikibooks.org", "gamefaqs",
                 "fextralife", "wiki.gg", "zeldawiki", "reddit.com"]
    return any(d in url for d in overseas)


# ── 搜索引擎（通过 ddgs 库搜索，返回结果列表）──

def search_engine_results(session, query, max_results=10):
    """通过 ddgs 库搜索，返回 [{title, url, snippet}] 列表"""
    from ddgs import DDGS

    # Get proxy from session if configured
    proxy = None
    if session.proxies:
        proxy_url = session.proxies.get("http") or session.proxies.get("https")
        if proxy_url:
            proxy = proxy_url

    try:
        results = DDGS(proxy=proxy, timeout=10).text(query, max_results=max_results, backend="auto")
    except Exception as e:
        logger.warning("[DDG] 搜索失败: %s", e)
        return []

    # Convert to our standard format
    output = []
    for r in results:
        output.append({
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", "")
        })

    logger.info("[DDG] 找到 %d 条结果", len(output))
    return output
