# -*- coding: utf-8 -*-
"""
wiki_search.py - 游戏百科知识查询脚本

职责：
  接收游戏名+知识主题，按方案 C 混合路由策略查询：
  1. 查 wiki_routes.json → 走专有Wiki（原神BWIKI、神奇宝贝百科等）
  2. 没命中 → 通用MediaWiki源并行搜（Wikipedia + 萌娘百科）
  3. 全失败 → 搜索引擎兜底（DuckDuckGo）

用法:
  python scripts/wiki_search.py --game "宝可梦" --topic "宝石海星 技能"
  python scripts/wiki_search.py --json '{"game":"...","topic":"..."}'

输出 JSON 到 stdout，日志到 stderr。
"""

import argparse
import json
import sys
import os
import re
import logging
import time
from urllib.parse import urlencode, urljoin, quote_plus, urlparse

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
ROUTES_PATH = os.path.join(PROJECT_ROOT, "config", "wiki_routes.json")
PARSERS_DIR = os.path.join(PROJECT_ROOT, "config", "parsers")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

TIMEOUT = 15
BLOCKED_TIMEOUT = 5  # 可能被墙的源用短超时，快速跳过
MAX_CONTENT_LENGTH = 8000

BLOCKED_DOMAINS = [
    "zh.wikipedia.org", "en.wikipedia.org",
]

# Reddit 怀旧游戏相关子板
REDDIT_SUBS = [
    "retrogaming", "Gameboy", "nds", "3DS", "PSP",
    "emulation", "patientgamers", "castlevania", "truegaming",
]

logger = logging.getLogger("wiki_search")

# ── 通用工具 ──

def setup_logging(debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False


def parse_args():
    parser = argparse.ArgumentParser(description="游戏百科知识查询脚本")
    parser.add_argument("--game", type=str, default=None, help="游戏名称")
    parser.add_argument("--topic", type=str, default=None, help="知识主题")
    parser.add_argument("--json", type=str, default=None, dest="json_input", help="JSON 输入")
    parser.add_argument("--debug", action="store_true", default=False, help="调试日志")
    return parser.parse_args()


def load_config():
    if not os.path.exists(CONFIG_PATH):
        logger.error("配置文件缺失: %s", CONFIG_PATH)
        sys.stderr.write("[ERROR] 配置文件缺失: %s\n" % CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_routes():
    if not os.path.exists(ROUTES_PATH):
        logger.warning("路由表缺失: %s，跳过专有Wiki匹配", ROUTES_PATH)
        return {"routes": []}
    with open(ROUTES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import make_session, fetch_html, fetch_json, source_needs_proxy


def make_absolute_url(base_url, url):
    if not url or url.startswith("data:"):
        return url
    return urljoin(base_url, url)


# ── 第1步：游戏名称匹配专有Wiki路由 ──

def match_game_to_routes(game, routes_config):
    """通过游戏名匹配路由表，返回匹配到的 [{name, type, domain, url}]"""
    game_lower = game.lower().strip()

    for route in routes_config.get("routes", []):
        keywords = [kw.lower().strip() for kw in route.get("match", [])]
        for kw in keywords:
            if kw in game_lower or game_lower in kw:
                logger.info("[路由匹配] %s → %s", game, route["match"][0])
                return route["sources"]

    logger.info("[路由匹配] %s 未匹配到专有路由", game)
    return None


# ── MediaWiki API 通用搜索 ──

def get_mediawiki_api_url(source_url):
    """从 MediaWiki 源配置获取 API URL"""
    if isinstance(source_url, dict):
        return source_url.get("api_url")
    return None


def search_mediawiki_api(session, api_url, query, limit=5):
    """通过 MediaWiki API 搜索页面"""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": limit,
        "srprop": "snippet|titlesnippet"
    }

    try:
        full_url = api_url.rstrip("/") + "?" + urlencode(params)
        data = fetch_json(session, full_url, timeout=TIMEOUT)
    except Exception as e:
        logger.warning("[MediaWiki API] 搜索失败: %s", e)
        return []

    results = data.get("query", {}).get("search", [])
    logger.info("[MediaWiki API] 搜索 '%s' → %d 条结果", query, len(results))
    return results


def fetch_mediawiki_page(session, api_url, page_title):
    """通过 MediaWiki API 获取页面内容。
    优先用 extracts API（纯文本），失败则降级到 revisions + wikitext 解析。"""
    # 方法1：extracts API（Wikipedia/Wikia 等支持）
    try:
        params = {
            "action": "query",
            "prop": "extracts",
            "exlimit": 1,
            "explaintext": 1,
            "format": "json",
            "titles": page_title
        }
        full_url = api_url.rstrip("/") + "?" + urlencode(params)
        data = fetch_json(session, full_url, timeout=TIMEOUT)
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_info in pages.items():
            if page_id == "-1":
                continue
            content = page_info.get("extract", "")
            if content and len(content) > 20:
                return page_info.get("title", page_title), content
    except Exception as e:
        logger.info("[MediaWiki] extracts API 失败，降级到 revisions: %s", e)

    # 方法2：revisions + wikitext 解析（52poke/萌百 等没装 extracts 扩展的）
    try:
        params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
            "titles": page_title
        }
        full_url = api_url.rstrip("/") + "?" + urlencode(params)
        data = fetch_json(session, full_url, timeout=TIMEOUT)
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_info in pages.items():
            if page_id == "-1":
                continue
            revisions = page_info.get("revisions", [])
            if revisions:
                raw = revisions[0].get("slots", {}).get("main", {}).get("*", "")
                if raw and len(raw) > 50:
                    title = page_info.get("title", page_title)
                    text = wikitext_to_plain(raw)
                    if text and len(text) > 20:
                        return title, text
    except Exception as e:
        logger.warning("[MediaWiki] revisions API 失败: %s", e)

    return None, None


def wikitext_to_plain(wikitext):
    """将 MediaWiki 原始 wikitext 转为可读纯文本"""
    # 移除模板 {{...}}
    depth = 0
    result = []
    i = 0
    while i < len(wikitext):
        c = wikitext[i]
        if c == '{' and i + 1 < len(wikitext) and wikitext[i + 1] == '{':
            depth += 1
            i += 2
            continue
        if c == '}' and i + 1 < len(wikitext) and wikitext[i + 1] == '}':
            if depth > 0:
                depth -= 1
                i += 2
                continue
        if depth == 0:
            result.append(c)
        i += 1
    text = ''.join(result)

    # Wiki链接 [[target|label]] or [[target]]
    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+?)\]\]', r'\1', text)
    # 外部链接 [url label]
    text = re.sub(r'\[https?://[^\s\]]+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://[^\s\]]+\]', '', text)
    # 粗体斜体
    text = re.sub(r"'''(.+?)'''", r'\1', text)
    text = re.sub(r"''(.+?)''", r'\1', text)
    # HTML标签（先处理br）
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    # 标题 == Title ==
    text = re.sub(r'==+\s*(.+?)\s*==+', r'\n## \1\n', text)
    # 列表
    text = re.sub(r'^\*+', '-', text, flags=re.MULTILINE)
    text = re.sub(r'^#+', '', text, flags=re.MULTILINE)
    # 表格语法残留
    text = re.sub(r'^\{\|.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\|\}', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\|[-]+\|?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\|', '', text, flags=re.MULTILINE)
    # HTML实体
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
    # 压缩空白行
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    # 过滤纯空白行和残留模板行
    lines = [l.strip() for l in text.split('\n') if l.strip() and '}}' not in l and '{{' not in l and not l.startswith('|')]
    return '\n\n'.join(lines) if lines else ""


def result_contains_game(result_title, result_content, game):
    """校验搜索结果是否确实与目标游戏相关。
    在标题或内容前500字符中查找游戏名（支持部分匹配）。"""
    if not game:
        return True

    # 将游戏名拆成关键词，逐个检查
    keywords = re.split(r'[\s/:：·]+', game)
    keywords = [kw for kw in keywords if len(kw) >= 2]  # 过滤单字

    title_lower = result_title.lower()
    content_head = (result_content or "")[:500].lower()

    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in title_lower or kw_lower in content_head:
            return True

    # 如果游戏名没有命中，但 topic 词命中了标题（说明可能是角色/道具）
    # 注意：不无条件放行，避免把宝可梦内容当成恶魔城内容
    return False


def extract_mediawiki_result(session, api_url, search_results, base_url, game="", max_len=MAX_CONTENT_LENGTH):
    """从搜索结果中提取第一个有内容且相关的的结果"""
    for result in search_results[:5]:  # 最多试5条
        page_title = result.get("title", "")
        snippet = result.get("snippet", "")
        title, content = fetch_mediawiki_page(session, api_url, page_title)
        if content:
            # 检查相关性
            if game and not result_contains_game(title, content, game):
                logger.info("[相关性校验] 跳过不相关结果: %s", title)
                continue

            # 截断但保留完整段落
            if len(content) > max_len:
                content = content[:max_len].rsplit("\n\n", 1)[0] + "\n\n> 内容已截断，[查看完整页面](%s)" % make_absolute_url(base_url, "w/" + quote_plus(page_title))

            # 清理 HTML 残留标签
            content = re.sub(r"<[^>]+>", "", content)
            snippet_clean = re.sub(r"<[^>]+>", "", snippet) if snippet else ""

            return {
                "success": True,
                "source": base_url,
                "url": make_absolute_url(base_url, "w/" + quote_plus(page_title)),
                "title": page_title,
                "content": content,
                "snippet": snippet_clean,
                "is_search_engine": False
            }

    return None


def search_mediawiki_source(session, source, game, topic):
    """搜索单个 MediaWiki 源（维基百科、萌娘百科等）"""
    api_url = source.get("api_url")
    base_url = source.get("base_url", "")
    source_name = source.get("name", "未知")

    if not api_url:
        logger.warning("[%s] 缺少 api_url", source_name)
        return None

    # 被墙的域用短超时快速跳过
    is_blocked = any(domain in api_url for domain in BLOCKED_DOMAINS)
    if is_blocked:
        # 先探测一下是否可达
        try:
            resp = session.get(api_url, timeout=BLOCKED_TIMEOUT)
            if resp.status_code >= 500:
                logger.warning("[%s] 服务器不可达 (HTTP %d)，跳过", source_name, resp.status_code)
                return None
        except Exception:
            logger.warning("[%s] 连接超时，可能被墙，跳过", source_name)
            return None

    # 多查询策略：先搜 "{game} {topic}"，无结果则只搜 "{game}"
    queries = []
    if topic:
        queries.append("%s %s" % (game, topic))
    queries.append(game)

    for qi, query in enumerate(queries):
        logger.info("[%s] 搜索 (query%d): %s", source_name, qi + 1, query)

        try:
            results = search_mediawiki_api(session, api_url, query)
        except Exception as e:
            logger.warning("[%s] 搜索异常: %s", source_name, e)
            if qi < len(queries) - 1:
                continue
            return None

        if not results:
            logger.warning("[%s] query%d 无搜索结果", source_name, qi + 1)
            continue

        result = extract_mediawiki_result(session, api_url, results, base_url, game=game)
        if result:
            result["source"] = source_name
            logger.info("[SUCCESS] %s: %s", source_name, result.get("title", ""))
            return result

        logger.warning("[%s] query%d 有结果但都无法提取或未通过相关性校验", source_name, qi + 1)

    logger.warning("[%s] 所有query均无结果", source_name)
    return None


# ── 专有Wiki（非MediaWiki类型）搜索 ──

def search_fextralife(session, game, topic):
    """搜索 Fextralife Wiki（HTML 解析）"""
    query = "%s %s" % (game, topic)
    search_url = "https://eldenring.wiki.fextralife.com/search?q=%s" % quote_plus(query)
    logger.info("[Fextralife] 搜索: %s", search_url)

    try:
        html = fetch_html(session, search_url)
    except Exception as e:
        logger.warning("[Fextralife] 请求失败: %s", e)
        return None

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for link in soup.select("a.search-result-link") or soup.select("a[href*='/wiki/']"):
        href = link.get("href", "")
        text = link.get_text(strip=True)
        if href and text and len(text) > 5:
            results.append({"title": text, "url": make_absolute_url("https://eldenring.wiki.fextralife.com", href)})

    if not results:
        logger.warning("[Fextralife] 无搜索结果")
        return None

    # 取第一个结果
    target = results[0]
    logger.info("[Fextralife] 尝试: %s", target["url"])

    try:
        html = fetch_html(session, target["url"])
    except Exception as e:
        logger.warning("[Fextralife] 详情页失败: %s", e)
        return None

    soup = BeautifulSoup(html, "html.parser")
    # 移除干扰元素
    for tag in soup.select("script, style, nav, footer, header, .sidebar, .ads"):
        tag.decompose()

    content_el = soup.select_one(".wiki-content, .col-sm-9, .col-md-9, main article")
    if not content_el:
        content_el = soup.find("article") or soup.find("main")

    content = content_el.get_text(strip=True) if content_el else soup.get_text(strip=True)
    content = re.sub(r"\s+", " ", content)[:MAX_CONTENT_LENGTH]

    return {
        "success": True,
        "source": "Fextralife",
        "url": target["url"],
        "title": target["title"],
        "content": content,
        "is_search_engine": False
    }


# ── 专用Wiki搜索（通过路由表匹配的源） ──

def search_routed_source(session, source, game, topic):
    """搜索路由表中匹配的专有Wiki源"""
    source_type = source.get("type", "")
    source_url = source.get("url", "")
    source_name = source.get("name", "未知")

    # 这些类型都是 MediaWiki，用同类处理
    if source_type in ("biligame", "fandom", "moegirl", "minecraftwiki", "terrariawiki", "bulbapedia"):
        # 构建 MediaWiki API URL
        if source_type == "biligame":
            domain = source.get("domain", "")
            api_url = "https://wiki.biligame.com/%s/api.php" % domain
        elif source_type == "fandom":
            domain = source.get("domain", "")
            api_url = "https://%s.fandom.com/api.php" % domain
        elif source_type == "moegirl":
            api_url = "https://zh.moegirl.org.cn/api.php"
        elif source_type == "bulbapedia":
            api_url = "https://wiki.52poke.com/api.php"
        else:
            api_url = source_url.rstrip("/") + "/api.php"

        return search_mediawiki_api_source(session, source_name, api_url, source_url, game, topic)

    elif source_type == "fextralife":
        return search_fextralife(session, game, topic)

    elif source_type == "huiji":
        # 灰机Wiki 暂不支持，跳过
        logger.warning("[%s] 灰机Wiki暂不支持API查询", source_name)
        return None

    else:
        logger.warning("[%s] 未知源类型: %s", source_name, source_type)
        return None


def search_mediawiki_api_source(session, source_name, api_url, base_url, game, topic):
    """封装好的 MediaWiki API 单源搜索
    对于专有Wiki（52poke/BWiki等），直接搜 topic 即可，因为 wiki 本身就限定在游戏范围内"""
    # 专有Wiki不需要加游戏名，topic本身已经足够
    query = topic if topic else game
    logger.info("[专有Wiki/%s] 搜索: %s", source_name, query)

    results = search_mediawiki_api(session, api_url, query)
    if not results:
        logger.warning("[专有Wiki/%s] 无搜索结果", source_name)
        return None

    # 专有Wiki 不传 game 参数，跳过相关性校验（wiki本身已限定范围）
    result = extract_mediawiki_result(session, api_url, results, base_url)
    if result:
        result["source"] = source_name
        logger.info("[SUCCESS] 专有Wiki/%s: %s", source_name, result.get("title", ""))
        return result

    return None


# ── Reddit 怀旧游戏社区搜索 ──

def search_reddit(session, game, topic, subreddits=None):
    """搜索 Reddit 怀旧游戏子板，提取帖子和高赞评论"""
    if subreddits is None:
        subreddits = REDDIT_SUBS

    query = "%s %s" % (game, topic) if topic else game

    # 按相关性在子板内搜索
    all_posts = []
    for sub in subreddits[:6]:  # 限6个子板避免太多请求
        try:
            url = "https://www.reddit.com/r/%s/search.json" % sub
            resp = session.get(url, params={
                "q": query, "restrict_sr": "on",
                "limit": 3, "sort": "relevance", "raw_json": "1"
            }, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            for child in children:
                post = child["data"]
                all_posts.append({
                    "title": post.get("title", ""),
                    "selftext": post.get("selftext", ""),
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "subreddit": post.get("subreddit", sub),
                    "permalink": "https://www.reddit.com" + post.get("permalink", ""),
                    "url": post.get("url", ""),
                })
        except Exception as e:
            logger.info("[Reddit] r/%s 搜索失败: %s", sub, str(e)[:40])
            continue

    if not all_posts:
        logger.info("[Reddit] 所有子板无结果")
        return None

    # 按分数排序，选最佳帖子
    all_posts.sort(key=lambda p: p["score"], reverse=True)
    best = all_posts[0]

    logger.info("[Reddit] 找到 %d 条帖子, 最佳: r/%s (%d upvotes)", len(all_posts), best["subreddit"], best["score"])

    # 获取帖子评论
    comments_text = ""
    try:
        comment_url = best["permalink"].rstrip("/") + ".json"
        resp = session.get(comment_url, params={"raw_json": "1"}, timeout=TIMEOUT)
        if resp.status_code == 200:
            comment_data = resp.json()
            # 取前5条顶级评论
            if len(comment_data) > 1:
                top_comments = comment_data[1]["data"]["children"][:5]
                lines = []
                for c in top_comments:
                    if c["kind"] != "t1":
                        continue
                    body = c["data"].get("body", "")
                    score = c["data"].get("score", 0)
                    if body and score > 0 and len(body) > 20:
                        # 清理 Markdown
                        body = re.sub(r'\n+', '\n', body)[:500]
                        lines.append("- (↑%d) %s" % (score, body))
                if lines:
                    comments_text = "\n\n---\n**热门评论：**\n" + "\n\n".join(lines)
    except Exception as e:
        logger.info("[Reddit] 评论获取失败: %s", str(e)[:40])

    # 组装内容
    content = "**%s** (r/%s | ↑%d | 💬%d)\n\n%s\n\n[查看原文](%s)" % (
        best["title"],
        best["subreddit"],
        best["score"],
        best["num_comments"],
        best["selftext"][:MAX_CONTENT_LENGTH // 2] if best["selftext"] else "(纯链接或图片帖子，内容见原文)",
        best["permalink"],
    )
    if comments_text:
        content += comments_text

    return {
        "success": True,
        "source": "Reddit r/%s" % best["subreddit"],
        "url": best["permalink"],
        "title": best["title"],
        "content": content[:MAX_CONTENT_LENGTH],
        "snippet": "",
        "is_search_engine": False,
    }


# ── 搜索引擎兜底 ──

SEARCH_ENGINE = "duckduckgo"

def search_via_engine(session, game, topic):
    """搜索引擎兜底（默认 DuckDuckGo）"""
    query = '"%s" %s' % (game, topic)
    if SEARCH_ENGINE == "duckduckgo":
        url = "https://html.duckduckgo.com/html/?%s" % urlencode({"q": query})
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

    if SEARCH_ENGINE == "duckduckgo":
        for item in soup.select(".result, .web-result"):
            a = item.select_one(".result__title a, .result__a")
            if not a:
                continue
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if "duckduckgo.com/l/" in link:
                import urllib.parse as ulp
                qs = ulp.parse_qs(ulp.urlparse(link).query)
                real = qs.get("uddg", [""])[0]
                if real:
                    link = ulp.unquote(real)
            snippet_el = item.select_one(".result__snippet, .snippet")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and link:
                results.append({"title": title, "link": link, "snippet": snippet})
    else:
        for li in soup.select("li.b_algo"):
            h2 = li.select_one("h2 a")
            if not h2:
                continue
            title = h2.get_text(strip=True)
            link = h2.get("href", "")
            if "bing.com/ck/a" in link:
                import base64
                match = re.search(r'[?&]u=([^&]+)', link)
                if match:
                    try:
                        link = base64.urlsafe_b64decode(match.group(1) + "===").decode("utf-8")
                    except Exception:
                        pass
            snippet_el = li.select_one(".b_caption p")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and link:
                results.append({"title": title, "link": link, "snippet": snippet})

    if not results:
        logger.warning("[%s] 未找到结果", SEARCH_ENGINE)
        return None

    # 黑名单：过滤不相关的结果（百度百科等泛化条目）
    blacklist = ["baike.baidu.com", "zhidao.baidu.com", "baijiahao.baidu.com", "zhihu.com"]
    results = [r for r in results if not any(d in r["link"].lower() for d in blacklist)]
    if not results:
        logger.warning("[%s] 所有结果都在黑名单中", SEARCH_ENGINE)
        return None

    # 优先wiki类站点
    wiki_domains = ["wikipedia.org", "moegirl.org", "fandom.com", "biligame.com", "52poke.com", "wiki.gg", "poe2db.tw"]
    preferred = [r for r in results if any(d in r["link"].lower() for d in wiki_domains)]
    rest = [r for r in results if r not in preferred]
    results = preferred + rest

    logger.info("[%s] 找到 %d 条 (优先 %d)", SEARCH_ENGINE, len(results), len(preferred))

    for rank, r in enumerate(results[:3]):
        logger.info("[%s] 尝试 #%d: %s", SEARCH_ENGINE, rank + 1, r["link"])
        try:
            html = fetch_html(session, r["link"])
        except Exception as e:
            logger.warning("[%s] 抓取失败: %s", SEARCH_ENGINE, e)
            continue

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.select("script, style, nav, footer, header, .sidebar, .ads, .comment"):
            tag.decompose()

        # 尝试提取正文
        content_el = (
            soup.select_one("article") or
            soup.select_one("main") or
            soup.select_one(".mw-parser-output") or
            soup.select_one(".content") or
            soup.select_one("#content")
        )

        content = content_el.get_text(strip=True) if content_el else soup.get_text(strip=True)
        content = re.sub(r"\s+", " ", content).strip()[:MAX_CONTENT_LENGTH]

        if len(content) > 100:
            return {
                "success": True,
                "source": "搜索引擎",
                "url": r["link"],
                "title": r["title"],
                "content": content,
                "snippet": r.get("snippet", ""),
                "is_search_engine": True
            }

    return None


# ── 主流程 ──

def main():
    args = parse_args()
    game = args.game
    topic = args.topic

    if args.json_input:
        try:
            data = json.loads(args.json_input)
            game = data.get("game", game)
            topic = data.get("topic", topic)
        except json.JSONDecodeError as e:
            sys.stderr.write("[ERROR] JSON 解析失败: %s\n" % e)
            sys.exit(1)

    if not game:
        sys.stderr.write("[ERROR] 需要 --game 参数\n")
        sys.exit(1)

    setup_logging(args.debug)

    config = load_config()
    routes_config = load_routes()
    wiki_sources = [s for s in config.get("wiki_sources", []) if s.get("enabled", True)]
    features = config.get("features", {})
    use_engine = features.get("use_search_engine", False)

    proxy_cfg = config.get("proxy", {})
    session_direct = make_session(USER_AGENT, proxy_cfg, use_proxy=False, log_name="direct")
    session_proxy = make_session(USER_AGENT, proxy_cfg, use_proxy=True, log_name="proxy")

    # ── 第1步：匹配专有Wiki路由 ──
    matched_sources = match_game_to_routes(game, routes_config)
    if matched_sources:
        logger.info("[Step 1] 命中专有Wiki路由，%d 个源", len(matched_sources))
        for source in matched_sources:
            sess = session_proxy if source_needs_proxy(source) else session_direct
            result = search_routed_source(sess, source, game, topic or "")
            if result:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
        logger.info("[Step 1] 专有Wiki路由均无结果，进入Step 2")

    # ── 第2步：通用MediaWiki源并行搜 ──
    logger.info("[Step 2] 通用百科搜索，%d 个源", len(wiki_sources))
    for source in wiki_sources:
        source_type = source.get("type", "")
        sess = session_proxy if source_needs_proxy(source) else session_direct
        if source_type == "mediawiki":
            result = search_mediawiki_source(sess, source, game, topic or "")
            if result:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
        elif source_type == "gamefaqs":
            logger.info("[GameFAQs] 暂不由API搜索，由搜索引擎兜底覆盖")
            continue

    # ── 第3步：Reddit 怀旧游戏社区 ──
    logger.info("[Step 3] Reddit 社区搜索")
    result = search_reddit(session_proxy, game, topic or "")
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # ── 第4步：搜索引擎兜底 ──
    if use_engine:
        logger.info("[Step 4] 所有来源搜索失败，启用搜索引擎兜底")
        result = search_via_engine(session_direct, game, topic or "")
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

    # ── 彻底无结果 ──
    print(json.dumps({"success": False, "error": "no_results"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
