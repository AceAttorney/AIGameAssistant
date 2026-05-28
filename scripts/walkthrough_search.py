# -*- coding: utf-8 -*-
"""
walkthrough_search.py - 游戏攻略搜索脚本

职责：接收游戏名+关键词，搜索配置的攻略站，返回内容。
名称补全、多名称变体尝试由 AI 代理在调用前完成。

用法:
    python scripts/walkthrough_search.py --game "游戏名" --keyword "攻略关键词"
    python scripts/walkthrough_search.py --json '{"game":"...","keyword":"..."}'

输出 JSON 到 stdout，日志到 stderr。
"""

import argparse
import concurrent.futures
import json
import sys
import os
import re
import time
import random
import logging
from urllib.parse import urlencode, urljoin, urlparse, quote_plus

# ── Vendor fallback: 沙箱环境下自带依赖，无需 pip install ──
_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
if os.path.isdir(_vendor_dir):
    sys.path.insert(0, _vendor_dir)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import requests
except ImportError:
    sys.stderr.write("[ERROR] 缺少依赖库 requests，请执行: pip install requests\n")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.stderr.write("[ERROR] 缺少依赖库 beautifulsoup4，请执行: pip install beautifulsoup4\n")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "sources.json")
PARSERS_DIR = os.path.join(PROJECT_ROOT, "config", "parsers")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

TIMEOUT = 10
MAX_CONTENT_LENGTH = 8000

logger = logging.getLogger("walkthrough_search")

TAG_CLEANUP_RE = re.compile(r"<(script|style|nav|footer|header|aside|noscript|iframe)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

PREFERRED_DOMAINS = [
    "gamersky.com", "ali213.net", "gl.ali213.net",
    "yxdown.com", "pc6.com", "doyo.cn",
    "bilibili.com/read",
    "gamefaqs.gamespot.com", "neoseeker.com",
    "ign.com/wikis", "strategywiki.org",
    "walkthrough.freeola.com", "archive.org",
]
BLACKLIST_DOMAINS = [
    "baike.baidu.com", "zhidao.baidu.com", "baijiahao.baidu.com", "zhihu.com",
    "zdic.net", "zh.wikipedia.org",
    "hanzi.", "cidian.", "hanyu", "ditu.", "tianditu",
]

REDDIT_SUBS = [
    "retrogaming", "Gameboy", "nds", "3DS", "PSP",
    "emulation", "patientgamers",
]


def parse_args():
    parser = argparse.ArgumentParser(description="游戏攻略搜索脚本")
    parser.add_argument("--game", type=str, default=None, help="游戏名称")
    parser.add_argument("--keyword", type=str, default=None, help="攻略关键词")
    parser.add_argument("--json", type=str, default=None, dest="json_input", help='JSON 输入')
    parser.add_argument("--debug", action="store_true", default=False, help="调试日志")
    parser.add_argument("--source", type=str, default="all",
        choices=["all", "gamersky", "archive", "reddit", "search"],
        help="指定搜索源（all=全链路, 配合记忆体系单源直达）")
    return parser.parse_args()


def setup_logging(debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False


def load_config():
    if not os.path.exists(CONFIG_PATH):
        logger.error("配置文件缺失: %s", CONFIG_PATH)
        sys.stderr.write("[ERROR] 配置文件缺失: %s\n" % CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import make_session, fetch_html, fetch_json, source_needs_proxy, search_engine_results


def safe_select_one(soup, selector):
    return soup.select_one(selector)


def safe_select_all(soup, selector):
    return soup.select(selector)


def extract_text(el):
    return el.get_text(strip=True) if el else ""


def extract_attr(el, attr, default=""):
    return el.get(attr, default).strip() if el else default


def make_absolute_url(base_url, url):
    if not url or url.startswith("data:"):
        return url
    return urljoin(base_url, url)


def extract_domain_key(url):
    parsed = urlparse(url)
    netloc = (parsed.netloc or parsed.path).split(":")[0]
    parts = netloc.split(".")
    return parts[-2] if len(parts) >= 2 else netloc


# ── 搜索 ──

def build_search_url(url_pattern, game, keyword):
    query = "%s" % (game,)
    return url_pattern.replace("{keyword}", quote_plus(query, safe=""))


def filter_results_by_keyword(results, keyword):
    """用 keyword 筛选搜索结果"""
    if not keyword:
        return results

    filtered = []
    keyword_lower = keyword.lower()

    for r in results:
        title = r.get("title", "").lower()
        if keyword_lower in title:
            filtered.insert(0, r)
        else:
            filtered.append(r)

    return filtered


def search_source(session, source, game, keyword):
    """在单个攻略站内搜索。返回搜索结果列表 [{title, link, summary}]"""
    name = source["name"]
    source_url = source["url"]
    domain_key = extract_domain_key(source_url)

    parser_path = os.path.join(PARSERS_DIR, "%s.json" % domain_key)
    if not os.path.exists(parser_path):
        return None, "no_parser"

    with open(parser_path, "r", encoding="utf-8") as f:
        parser_config = json.load(f)

    search_cfg = parser_config.get("search", {})
    url_pattern = search_cfg.get("url_pattern", "")
    if not url_pattern:
        return None, "no_url_pattern"

    search_url = build_search_url(url_pattern, game, keyword)
    logger.info("[%s] 搜索: %s", name, search_url)

    try:
        html = fetch_html(session, search_url)
    except Exception as e:
        logger.warning("[%s] 搜索失败: %s", name, e)
        return None, "http_error"

    soup = BeautifulSoup(html, "html.parser")
    result_els = safe_select_all(soup, search_cfg.get("result_selector", ""))
    results = []
    for el in result_els:
        title_el = safe_select_one(el, search_cfg.get("title_selector", ""))
        link_el = safe_select_one(el, search_cfg.get("link_selector", ""))
        title = extract_text(title_el)
        link = extract_attr(link_el, "href")
        if title and link:
            results.append({"title": title, "link": make_absolute_url(source_url, link)})

    results = filter_results_by_keyword(results, keyword)

    return results, "ok"


# ── 内容提取 ──

def extract_title(soup, parser_config):
    sel = parser_config.get("content", {}).get("title_selector", "h1")
    el = safe_select_one(soup, sel)
    if el:
        return extract_text(el)
    tag = soup.find("title")
    return extract_text(tag) if tag else ""


def strip_ads_and_nav(soup):
    bad = [
        ".ad", ".ads", ".advertisement", "[class*='ad-']",
        ".nav", ".navigation", ".navbar", ".menu", ".sidebar",
        ".comment", ".comments", "#comments",
        ".footer", ".header", ".share", ".social", ".related",
        ".recommend", ".breadcrumb",
        "script", "style", "noscript", "iframe", "ins",
    ]
    for sel in bad:
        for el in soup.select(sel):
            el.decompose()


def clean_html(html):
    html = COMMENT_RE.sub("", html)
    return TAG_CLEANUP_RE.sub("", html)


def html_to_markdown(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    result = _walk(body, page_url)
    lines = result.split("\n")
    cleaned = []
    prev_empty = False
    for line in lines:
        s = line.strip()
        if not s:
            if not prev_empty:
                cleaned.append("")
            prev_empty = True
        else:
            cleaned.append(s)
            prev_empty = False
    return "\n".join(cleaned).strip()


def _walk(el, page_url):
    parts = []
    for child in el.children:
        if child.name is None:
            parts.append(str(child))
            continue
        tag = child.name.lower()
        if tag in ("script", "style", "noscript", "nav", "footer", "header", "aside"):
            continue
        parts.append(_convert(child, tag, page_url) or "")
    return "".join(parts)


def _convert(el, tag, page_url):
    text = _walk(el, page_url).strip()
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return "\n\n%s %s\n\n" % ("#" * int(tag[1]), text) if text else "\n\n"
    if tag == "p":
        return "\n\n%s\n\n" % (text,) if text else "\n\n"
    if tag in ("ul", "ol"):
        items = []
        for li in el.find_all("li", recursive=False):
            t = _walk(li, page_url).strip()
            if t:
                items.append(t)
        if not items:
            return ""
        lines = []
        for i, item in enumerate(items):
            if tag == "ul":
                lines.append("- " + item)
            else:
                lines.append("%d. %s" % (i + 1, item))
        return "\n" + "\n".join(lines) + "\n"
    if tag == "li":
        return _walk(el, page_url)
    if tag == "img":
        # 懒加载：data-src > data-original > src
        src = (
            extract_attr(el, "data-src") or
            extract_attr(el, "data-original") or
            extract_attr(el, "src")
        )
        # 跳过占位符
        if src and "gamersky.com/webimg" in src and "common/blank.png" in src:
            # 检查父级 <a> 的 href
            parent = el.parent
            if parent and parent.name == "a":
                a_href = extract_attr(parent, "href")
                match = re.search(r'[?&](https?://[^\s&]+\.(?:jpg|png|gif|webp|jpeg))', a_href, re.IGNORECASE)
                if match:
                    src = match.group(1)
                else:
                    src = ""
            else:
                src = ""
        alt = extract_attr(el, "alt")
        return "\n\n![%s](%s)\n\n" % (alt, make_absolute_url(page_url, src)) if src else ""
    if tag == "a":
        href = extract_attr(el, "href")
        return "[%s](%s)" % (text, make_absolute_url(page_url, href)) if text and href else text
    if tag == "br":
        return "\n"
    if tag in ("strong", "b"):
        return "**%s**" % text if text else ""
    if tag in ("em", "i"):
        return "*%s*" % text if text else ""
    if tag == "blockquote":
        return "\n\n> %s\n\n" % text if text else ""
    if tag == "hr":
        return "\n\n---\n\n"
    if tag in ("div", "section", "article", "main", "figure", "figcaption", "span", "pre", "code", "table", "tr", "td", "th", "thead", "tbody"):
        return _walk(el, page_url)
    return _walk(el, page_url)


def extract_images(soup, parser_config, page_url):
    content_cfg = parser_config.get("content", {})
    sel = content_cfg.get("image_selector", "img")
    containers = content_cfg.get("image_containers", [])
    images = []
    seen = set()

    for img in safe_select_all(soup, sel):
        # 懒加载图片：data-src > data-original > src
        src = (
            extract_attr(img, "data-src") or
            extract_attr(img, "data-original") or
            extract_attr(img, "src")
        )
        if not src:
            continue

        # 跳过游民星空占位符
        if "gamersky.com/webimg" in src and "common/blank.png" in src:
            # 尝试从父级 <a> 标签的 href 中提取真实图片 URL
            parent = img.parent
            if parent and parent.name == "a":
                a_href = extract_attr(parent, "href")
                if "showimage" in a_href:
                    # 格式: ...?http://img1.gamersky.com/real-image.jpg
                    match = re.search(r'[?&](https?://[^\s&]+\.(?:jpg|png|gif|webp|jpeg))', a_href, re.IGNORECASE)
                    if match:
                        src = match.group(1)
            # 还是占位符就跳过
            if "common/blank.png" in src:
                continue

        abs_src = make_absolute_url(page_url, src)
        if abs_src in seen:
            continue
        seen.add(abs_src)

        alt = extract_attr(img, "alt")
        caption = alt
        parent = img.parent
        for _ in range(4):
            if not parent:
                break
            fc = parent.select_one("figcaption")
            if fc:
                caption = extract_text(fc)
                break
            parent = parent.parent
        images.append({"url": abs_src, "alt": alt, "caption": caption})
    return images


def extract_content(html, parser_config, page_url):
    soup = BeautifulSoup(html, "html.parser")
    strip_ads_and_nav(soup)
    content_sel = parser_config.get("content", {}).get("content_selector", "")
    if content_sel:
        el = safe_select_one(soup, content_sel)
        content_html = str(el) if el else html
    else:
        content_html = html
    content_html = clean_html(content_html)
    md = html_to_markdown(content_html, page_url)
    md = clean_noise(md)  # 清洗页脚噪音
    imgs = extract_images(BeautifulSoup(content_html, "html.parser"), parser_config, page_url)
    return md, imgs


def truncate_content(content, max_length, source_url):
    if len(content) <= max_length:
        return content
    truncated = content[:max_length].rsplit("\n\n", 1)[0]
    return truncated + "\n\n> 内容已截断，[查看完整攻略](%s)" % source_url


# ── 内容清洗 ──

def clean_noise(md):
    """清洗攻略页面中的噪音内容（页脚导航、无关链接、投票等）"""
    lines = md.split("\n")
    cleaned = []
    skip_until_blank = False

    for line in lines:
        stripped = line.strip()

        # 噪音行模式 — 命中则跳过
        if any(kw in stripped for kw in [
            "更多相关内容请关注",
            "责任编辑：",
            "友情提示：支持键盘",
            "本文是否解决了您的问题",
            "已解决",
            "未解决",
            "文章内容导航",
            "展开",
            "下一页",
            "上一页",
            "拒绝访问",
            "上网策略",
            "请联系网络管理员",
        ]):
            continue

        # 纯数字页码链接（如 "[1](...)"  "[2](...)"）
        if re.match(r'^(\*\*)?\[?\d+\]?\(?.*shtml.*\)?(\*\*)?$', stripped):
            continue

        # 连续多个页码链接行
        if re.match(r'^(\[\d+\]\([^)]+\)\s*)+$', stripped):
            continue

        # 文章内容导航条目: "- [第N页：...](url)" or "- **第N页：...**" or "- [第N页：...]"
        if re.match(r'^-\s+(\*\*)?\[?第\d+页[：:].+', stripped):
            continue

        # 页面导航中的当前页标记（纯数字的粗体链接）
        if re.match(r'^\*\*\[?\d+\]?\(?.*shtml.*\)?\*\*$', stripped):
            continue

        # 纯图占位符
        if stripped.startswith("[![游民星空](http://image.gamersky.com/webimg13/zhuanti/common/blank.png)]"):
            continue

        # 空行压缩 — 最多保留一个连续空行
        if not stripped:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue

        # 展开收起按钮
        if stripped == "展开":
            continue

        cleaned.append(line)

    # 去尾部空行
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    return "\n".join(cleaned)


# ── 翻页支持 ──

MAX_PAGES = 5  # 最多抓取页数

def detect_page_urls(soup, base_url, parser_config):
    """从页面中检测翻页链接，返回按序号排列的 URL 列表（去重）"""
    page_urls = []
    seen = {base_url}

    # 常见翻页选择器
    nav_selectors = [
        ".page_link a", ".pagelist a", ".page a", ".pagination a",
        ".page_nav a", ".pagenavi a", "[class*='page'] a",
        ".Mid2L_con a[href*='_']",  # 游民星空风格
    ]
    for sel in nav_selectors:
        for a in soup.select(sel):
            href = a.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript"):
                continue
            full_url = make_absolute_url(base_url, href)
            if full_url in seen:
                continue
            # 过滤掉明显不是同一篇文章的链接
            base_path = urlparse(base_url).path.rsplit(".", 1)[0]  # 去掉 .shtml
            target_path = urlparse(full_url).path.rsplit(".", 1)[0]
            if base_path in target_path or target_path in base_path:
                seen.add(full_url)
                page_urls.append(full_url)

    if page_urls:
        logger.info("[翻页] 检测到 %d 个分页链接", len(page_urls))
    return page_urls[:MAX_PAGES]


def fetch_and_extract(session, url, source_name, parser_config):
    """抓取攻略详情页（含翻页），返回 result dict 或 None"""
    try:
        html = fetch_html(session, url)
    except Exception as e:
        logger.warning("[%s] 详情页抓取失败: %s", source_name, e)
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        title = extract_title(soup, parser_config)
        md, imgs = extract_content(html, parser_config, url)
    except Exception as e:
        logger.warning("[%s] 内容提取失败: %s", source_name, e)
        return None
    if not md or len(md.strip()) < 50:
        return None

    # 跳过视频攻略（标题含"视频"）
    if "视频" in title:
        logger.info("[%s] 跳过视频攻略: %s", source_name, title)
        return None

    # ── 翻页 ──
    page_urls = detect_page_urls(soup, url, parser_config)
    all_images = list(imgs)
    pages_content = [md]

    for pi, page_url in enumerate(page_urls):
        logger.info("[翻页] 抓取第%d/%d页: %s", pi + 2, len(page_urls) + 1, page_url)
        try:
            page_html = fetch_html(session, page_url)
        except Exception:
            logger.warning("[翻页] 第%d页抓取失败", pi + 2)
            continue
        try:
            page_md, page_imgs = extract_content(page_html, parser_config, page_url)
        except Exception:
            continue
        if page_md and len(page_md.strip()) > 50:
            # 去掉重复的标题行
            page_md = re.sub(r'^#+\s+.+\n', '', page_md.strip(), count=1)
            pages_content.append(page_md)
            all_images.extend(page_imgs)

    # 合并所有页
    if len(pages_content) > 1:
        merged = ("\n\n---\n\n").join(pages_content)
        merged = truncate_content(merged, MAX_CONTENT_LENGTH * 3, url)  # 多页给更多空间
        logger.info("[翻页] 合并 %d 页，共 %d 字符", len(pages_content), len(merged))
    else:
        merged = md

    merged = clean_noise(merged)  # 最终清洗
    merged = truncate_content(merged, MAX_CONTENT_LENGTH, url) if len(pages_content) == 1 else merged

    logger.info("[SUCCESS] %s: %s (清洗后 %d 字符)", source_name, title, len(merged))
    return {
        "success": True,
        "source": source_name,
        "url": url,
        "title": title,
        "content": merged,
        "images": all_images,
        "is_search_engine": False,
    }


# ── Archive.org 攻略书搜索 ──

ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
ARCHIVE_DETAIL_URL = "https://archive.org/details/%s"

def search_archive_org(session, game, keyword):
    """搜索 Internet Archive 中的游戏攻略书"""
    query = 'title:(%s) AND (strategy guide OR walkthrough OR official guide OR prima OR bradygames)' % game
    try:
        resp = session.get(ARCHIVE_SEARCH_URL, params={
            "q": query,
            "fl[]": "identifier,title,downloads,description,subject,collection",
            "sort[]": "downloads desc",
            "rows": 3,
            "output": "json",
        }, timeout=TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as e:
        logger.info("[Archive.org] 搜索失败: %s", str(e)[:40])
        return None

    docs = data.get("response", {}).get("docs", [])
    if not docs:
        logger.info("[Archive.org] 无攻略书结果")
        return None

    best = docs[0]
    ident = best.get("identifier", "")
    title = best.get("title", game)
    description = best.get("description", "")
    downloads = best.get("downloads", 0)
    subject = best.get("subject", [])
    collection = best.get("collection", [])

    logger.info("[Archive.org] 找到攻略书: %s (下载 %d 次)", title[:50], downloads)

    content = "**%s**\n\n" % title
    if description:
        content += "%s\n\n" % description[:1000]
    content += "📥 下载: https://archive.org/download/%s/\n" % ident
    content += "🔗 在线查看: %s" % (ARCHIVE_DETAIL_URL % ident)

    return {
        "success": True,
        "source": "Internet Archive",
        "url": ARCHIVE_DETAIL_URL % ident,
        "title": title,
        "content": content[:MAX_CONTENT_LENGTH],
        "images": [],
        "is_search_engine": False,
        "archive_id": ident,
        "subject": subject,
        "collection": collection,
    }


# ── Reddit 社区搜索 ──

def search_reddit(session, game, keyword, subreddits=None):
    """搜索 Reddit 怀旧游戏子板，提取帖子和高赞评论"""
    if subreddits is None:
        subreddits = REDDIT_SUBS

    query = "%s %s" % (game, keyword)
    all_posts = []
    for sub in subreddits[:6]:
        try:
            url = "https://www.reddit.com/r/%s/search.json" % sub
            resp = session.get(url, params={
                "q": query, "restrict_sr": "on",
                "limit": 3, "sort": "relevance", "raw_json": "1"
            }, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for child in data.get("data", {}).get("children", []):
                post = child["data"]
                all_posts.append({
                    "title": post.get("title", ""),
                    "selftext": post.get("selftext", ""),
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "subreddit": post.get("subreddit", sub),
                    "permalink": "https://www.reddit.com" + post.get("permalink", ""),
                })
        except Exception as e:
            logger.info("[Reddit] r/%s 搜索失败: %s", sub, str(e)[:40])
            continue

    if not all_posts:
        return None

    all_posts.sort(key=lambda p: p["score"], reverse=True)
    best = all_posts[0]
    logger.info("[Reddit] %d 条帖子, 最佳: r/%s (↑%d)", len(all_posts), best["subreddit"], best["score"])

    # 获取评论
    comments = []
    try:
        comment_url = best["permalink"].rstrip("/") + ".json"
        resp = session.get(comment_url, params={"raw_json": "1"}, timeout=TIMEOUT)
        if resp.status_code == 200:
            comment_data = resp.json()
            if len(comment_data) > 1:
                for c in comment_data[1]["data"]["children"][:5]:
                    if c["kind"] == "t1":
                        body = c["data"].get("body", "")
                        sc = c["data"].get("score", 0)
                        if body and sc > 0 and len(body) > 20:
                            comments.append("- (↑%d) %s" % (sc, re.sub(r'\n+', ' ', body)[:500]))
    except Exception:
        pass

    content = "**%s**\nr/%s | ↑%d | Reddit\n\n%s\n\n[查看原文](%s)" % (
        best["title"], best["subreddit"],
        best["score"],
        best["selftext"][:4000] if best["selftext"] else "(链接帖子，内容见原文)",
        best["permalink"],
    )
    if comments:
        content += "\n\n---\n**热门评论：**\n" + "\n\n".join(comments)

    return {
        "success": True,
        "source": "Reddit r/%s" % best["subreddit"],
        "url": best["permalink"],
        "title": best["title"],
        "content": content[:MAX_CONTENT_LENGTH],
        "images": [],
        "is_search_engine": False,
    }


# ── 搜索引擎回退 ──

SEARCH_ENGINE = "duckduckgo"  # duckduckgo | bing

def search_via_engine(session, game, keyword):
    """搜索引擎兜底（默认 DuckDuckGo，备选 Bing）"""
    query = '"%s" %s' % (game, keyword)

    if SEARCH_ENGINE == "duckduckgo":
        # Use ddgs library instead of HTML scraping
        engine_results = search_engine_results(session, query, max_results=5)
        results = []
        for r in engine_results:
            results.append({"title": r["title"], "link": r["url"], "snippet": r["snippet"]})
    else:
        if SEARCH_ENGINE == "google":
            url = "https://www.google.com/search?%s" % urlencode({"q": query, "hl": "zh-CN"})
        else:
            url = "https://www.bing.com/search?%s" % urlencode({"q": query})

        logger.info("[%s] 搜索: %s", SEARCH_ENGINE, url)
        try:
            html = fetch_html(session, url)
        except Exception as e:
            logger.warning("[%s] 请求失败: %s", SEARCH_ENGINE, e)
            return None

        soup = BeautifulSoup(html, "html.parser")
        results = []

        if SEARCH_ENGINE == "google":
            for div in soup.select("div.g"):
                h3 = div.select_one("h3")
                a = h3.select_one("a") if h3 else None
                if not a:
                    continue
                title = extract_text(a)
                link = extract_attr(a, "href")
                if link.startswith("/url?"):
                    import urllib.parse as ulp
                    qs = ulp.parse_qs(ulp.urlparse(link).query)
                    link = qs.get("q", [link])[0]
                snippet_el = div.select_one(".VwiC3b, span.aCOpRe, .lEBKkf")
                snippet = extract_text(snippet_el) if snippet_el else ""
                if title and link:
                    results.append({"title": title, "link": link, "snippet": snippet})
        else:
            for li in soup.select("li.b_algo"):
                h2 = li.select_one("h2 a")
                if not h2:
                    continue
                title = extract_text(h2)
                link = extract_attr(h2, "href")
                if "bing.com/ck/a" in link:
                    import base64
                    match = re.search(r'[?&]u=([^&]+)', link)
                    if match:
                        try:
                            link = base64.urlsafe_b64decode(match.group(1) + "===").decode("utf-8")
                        except Exception:
                            pass
                snippet_el = li.select_one(".b_caption p, .b_lineclamp2")
                snippet = extract_text(snippet_el) if snippet_el else ""
                if title and link:
                    results.append({"title": title, "link": link, "snippet": snippet})

    if not results:
        logger.warning("[%s] 未找到结果", SEARCH_ENGINE)
        return None

    # 黑名单过滤
    results = [r for r in results if not any(d in r["link"] for d in BLACKLIST_DOMAINS)]
    if not results:
        logger.warning("[%s] 所有结果都在黑名单中", SEARCH_ENGINE)
        return None

    # 优先攻略站
    preferred = [r for r in results if any(d in r["link"] for d in PREFERRED_DOMAINS)]
    rest = [r for r in results if r not in preferred]
    results = preferred + rest

    logger.info("[%s] 找到 %d 条 (优先 %d)", SEARCH_ENGINE, len(results), len(preferred))

    generic_parser = {
        "content": {"title_selector": "h1", "content_selector": "", "image_selector": "img", "image_containers": ["figure"]}
    }
    for rank, r in enumerate(results[:3]):
        logger.info("[%s] 尝试 #%d: %s", SEARCH_ENGINE, rank + 1, r["link"])
        extracted = fetch_and_extract(session, r["link"], "搜索引擎", generic_parser)
        if extracted and len(extracted.get("content", "")) > 200:
            extracted["is_search_engine"] = True
            return extracted
        if r.get("snippet") and len(r["snippet"]) > 80:
            logger.info("[%s] 抓取失败，使用摘要降级", SEARCH_ENGINE)
            return {
                "success": True,
                "source": "搜索引擎",
                "url": r["link"],
                "title": r["title"],
                "content": r["snippet"] + "\n\n> [查看原文](%s)" % r["link"],
                "images": [],
                "is_search_engine": True,
            }
    return None


# ── 并行搜索 ──

def _run_all_parallel(game, keyword, sources, config, session_direct, session_proxy, use_engine):
    """并行运行所有搜索源，返回结果列表（每个结果含 source 字段）"""

    def _run_walkthrough():
        """搜索所有 walkthrough 源，返回第一条有效结果"""
        for source in sources:
            sess = session_proxy if source_needs_proxy(source) else session_direct
            name = source["name"]
            try:
                results, status = search_source(sess, source, game, keyword)
            except Exception as e:
                logger.warning("[%s] 搜索异常: %s", name, str(e)[:50])
                continue
            if status != "ok" or not results:
                continue
            domain_key = extract_domain_key(source["url"])
            parser_path = os.path.join(PARSERS_DIR, "%s.json" % domain_key)
            with open(parser_path, "r", encoding="utf-8") as f:
                parser_config = json.load(f)
            for rank, r in enumerate(results[:3]):
                try:
                    extracted = fetch_and_extract(sess, r["link"], name, parser_config)
                except Exception as e:
                    logger.warning("[%s] 提取异常: %s", name, str(e)[:50])
                    continue
                if extracted:
                    return extracted
        return {"success": False, "error": "no_results", "source": "walkthrough"}

    def _run_archive():
        try:
            result = search_archive_org(session_proxy, game, keyword)
        except Exception as e:
            logger.warning("[Archive] 异常: %s", str(e)[:50])
            result = None
        if result:
            return result
        return {"success": False, "error": "no_results", "source": "archive"}

    def _run_reddit():
        try:
            result = search_reddit(session_proxy, game, keyword)
        except Exception as e:
            logger.warning("[Reddit] 异常: %s", str(e)[:50])
            result = None
        if result:
            return result
        return {"success": False, "error": "no_results", "source": "reddit"}

    def _run_search_engine():
        if not use_engine:
            return {"success": False, "error": "disabled", "source": "search"}
        try:
            result = search_via_engine(session_proxy, game, keyword)
        except Exception as e:
            logger.warning("[Search] 异常: %s", str(e)[:50])
            result = None
        if result:
            return result
        return {"success": False, "error": "no_results", "source": "search"}

    tasks = [
        ("walkthrough", _run_walkthrough),
        ("archive", _run_archive),
        ("reddit", _run_reddit),
        ("search", _run_search_engine),
    ]

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {executor.submit(fn): name for name, fn in tasks}
        for future in concurrent.futures.as_completed(future_map):
            name = future_map[future]
            try:
                result = future.result(timeout=20)
            except concurrent.futures.TimeoutError:
                result = {"success": False, "error": "timeout", "source": name}
                logger.warning("[%s] 超时", name)
            except Exception as e:
                result = {"success": False, "error": str(e)[:100], "source": name}
                logger.warning("[%s] 异常: %s", name, str(e)[:50])
            if result:
                # 确保 source 字段存在
                if "source" not in result:
                    result["source"] = name
                results.append(result)

    # 保持输出顺序：walkthrough, archive, reddit, search
    order = {"walkthrough": 0, "archive": 1, "reddit": 2, "search": 3}
    results.sort(key=lambda r: order.get(_task_name_from_result(r), 99))
    return results


def _task_name_from_result(result):
    """从结果推断任务名"""
    src = result.get("source", "").lower()
    if any(kw in src for kw in ["游民", "gamersky", "walkthrough"]):
        return "walkthrough"
    if "archive" in src or "internet archive" in src:
        return "archive"
    if "reddit" in src:
        return "reddit"
    if any(kw in src for kw in ["搜索", "search", "引擎", "engine"]):
        return "search"
    return src


# ── 主流程 ──

def main():
    args = parse_args()
    game = args.game
    keyword = args.keyword

    if args.json_input:
        try:
            data = json.loads(args.json_input)
            game = data.get("game", game)
            keyword = data.get("keyword", keyword)
        except json.JSONDecodeError as e:
            sys.stderr.write("[ERROR] JSON 解析失败: %s\n" % e)
            sys.exit(1)

    if not game or not keyword:
        sys.stderr.write("[ERROR] 需要 --game 和 --keyword 参数\n")
        sys.exit(1)

    setup_logging(args.debug)

    config = load_config()
    sources = [s for s in config.get("walkthrough_sources", []) if s.get("enabled")]
    features = config.get("features", {})
    use_engine = features.get("use_search_engine", False)

    source_filter = args.source

    logger.info("数据源: %d 个 (筛选: %s)", len(sources), source_filter)

    proxy_cfg = config.get("proxy", {})
    session_direct = make_session(USER_AGENT, proxy_cfg, use_proxy=False, log_name="direct")
    session_proxy = make_session(USER_AGENT, proxy_cfg, use_proxy=True, log_name="proxy")

    if source_filter == "all":
        # ── 并行模式：同时运行所有源 ──
        results = _run_all_parallel(
            game, keyword, sources, config,
            session_direct, session_proxy, use_engine
        )
        if not results:
            results = [{"success": False, "error": "no_results"}]
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
        return

    # ── 单源模式：保持原有顺序行为 ──
    def _run_source(name):
        """检查 source_filter 是否允许运行该源"""
        return source_filter == name

    # 1) 遍历已配置的攻略站
    if _run_source("gamersky"):
        for source in sources:
            sess = session_proxy if source_needs_proxy(source) else session_direct
            name = source["name"]

            results, status = search_source(sess, source, game, keyword)
            if status != "ok" or not results:
                logger.warning("[%s] 无结果 (%s)", name, status)
                continue

            logger.info("[%s] %d 条结果", name, len(results))
            domain_key = extract_domain_key(source["url"])
            parser_path = os.path.join(PARSERS_DIR, "%s.json" % domain_key)
            with open(parser_path, "r", encoding="utf-8") as f:
                parser_config = json.load(f)

            for rank, r in enumerate(results[:3]):
                logger.info("[%s] 尝试 #%d: %s", name, rank + 1, r["link"])
                extracted = fetch_and_extract(sess, r["link"], name, parser_config)
                if extracted:
                    print(json.dumps(extracted, ensure_ascii=False, indent=2))
                    return
    else:
        logger.info("跳过攻略站搜索 (source=%s)", source_filter)

    # 2) Archive.org 攻略书搜索（需要代理）
    if _run_source("archive"):
        logger.info("搜索Archive.org攻略书")
        archive_result = search_archive_org(session_proxy, game, keyword)
        if archive_result:
            print(json.dumps(archive_result, ensure_ascii=False, indent=2))
            return

    # 3) Reddit 社区搜索（需要代理）
    if _run_source("reddit"):
        logger.info("搜索Reddit社区")
        reddit_result = search_reddit(session_proxy, game, keyword)
        if reddit_result:
            print(json.dumps(reddit_result, ensure_ascii=False, indent=2))
            return

    # 4) 搜索引擎兜底
    if _run_source("search") and use_engine:
        logger.info("启用搜索引擎兜底 (%s)", SEARCH_ENGINE)
        result = search_via_engine(session_proxy, game, keyword)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

    # 5) 彻底无结果
    print(json.dumps({"success": False, "error": "no_results"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
