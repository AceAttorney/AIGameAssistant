# -*- coding: utf-8 -*-
"""
Game name resolution script — 双重校验 (Wikipedia + RAWG)

流程:
  L1: NAMES.json 缓存映射
  L2: Wikipedia (en+zh) + RAWG (en) 并行搜索
  L3: 游民星空站内搜 fallback
  L4: DuckDuckGo 搜索引擎兜底

用法:
  python scripts/name_resolve.py --game "老头环" --en-name "Elden Ring"
  python scripts/name_resolve.py --game "老头环"

输出 JSON 到 stdout，日志到 stderr。
"""

import argparse
import concurrent.futures
import json
import sys
import os
import re
import logging
from urllib.parse import urlencode, quote_plus

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
NAMES_PATH = os.path.join(PROJECT_ROOT, "memory", "NAMES.json")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "sources.json")

sys.path.insert(0, SCRIPT_DIR)

from common import make_session, fetch_html, search_engine_results, build_proxy_url
from rawg_client import RAWGClient

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

WIKIPEDIA_ZH_API = "https://zh.wikipedia.org/w/api.php"
WIKIPEDIA_EN_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_ZH_BASE = "https://zh.wikipedia.org"
WIKIPEDIA_EN_BASE = "https://en.wikipedia.org"

TIMEOUT = 10
WIKI_TIMEOUT = 8
RAWG_TIMEOUT = 5  # RAWG 走代理，给短超时

logger = logging.getLogger("name_resolve")


# ── CLI ──

def parse_args():
    p = argparse.ArgumentParser(description="游戏名称解析脚本 (Wikipedia + RAWG)")
    p.add_argument("--game", type=str, required=True, help="玩家输入的原始名称")
    p.add_argument("--en-name", type=str, default=None, help="AI 翻译的英文名（可选）")
    p.add_argument("--debug", action="store_true", default=False, help="调试日志")
    return p.parse_args()


def setup_logging(debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False


# ── Config ──

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ── L1: NAMES.json ──

def load_names():
    if not os.path.exists(NAMES_PATH):
        os.makedirs(os.path.dirname(NAMES_PATH), exist_ok=True)
        return {}
    try:
        with open(NAMES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("[NAMES] 读取失败: %s", e)
        return {}


def check_names_json(game, names_db):
    """检查 NAMES.json 是否有映射"""
    game_lower = game.lower().strip()

    # 精确匹配
    if game in names_db:
        return names_db[game]
    if game_lower in names_db:
        return names_db[game_lower]

    # 别名匹配
    for key, entry in names_db.items():
        aliases = entry.get("aliases", [])
        for alias in aliases:
            if alias.lower().strip() == game_lower:
                return entry

    return None


def save_names(game_input, entry_data):
    """将名称映射写入 NAMES.json（合并已有 aliases）"""
    names_db = load_names()

    # 合并 aliases：如果 game_input 已存在，保留旧 aliases
    if game_input in names_db:
        old = names_db[game_input]
        old_aliases = set(old.get("aliases") or [])
        new_aliases = set(entry_data.get("aliases") or [])
        merged = list(old_aliases | new_aliases)
        # 把 game_input 本身也加入 aliases（避免重复）
        if game_input not in merged:
            merged.insert(0, game_input)
        entry_data = dict(entry_data)  # 复制避免修改原 dict
        entry_data["aliases"] = merged

    names_db[game_input] = entry_data
    os.makedirs(os.path.dirname(NAMES_PATH), exist_ok=True)
    with open(NAMES_PATH, "w", encoding="utf-8") as f:
        json.dump(names_db, f, ensure_ascii=False, indent=2)
    logger.info("[NAMES] 写入映射: %s → %s (aliases: %d)",
                game_input,
                entry_data.get("name_zh", entry_data.get("name_en", "")),
                len(entry_data.get("aliases", [])))


# ── L2a: Wikipedia API ──

def wiki_search(session, api_url, query, timeout=WIKI_TIMEOUT):
    """MediaWiki API list=search"""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": 3,
        "srprop": "snippet",
    }
    try:
        url = api_url.rstrip("/") + "?" + urlencode(params)
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("query", {}).get("search", [])
    except Exception as e:
        logger.warning("[Wikipedia] 搜索失败 (%s): %s", api_url, str(e)[:60])
        return []


def wiki_resolve(session_direct, session_proxy, game, en_name):
    """通过 Wikipedia 解析游戏名称

    Returns:
        dict or None: { name_zh, name_en, wiki_url_zh, wiki_url_en, titles[], source }
    """
    results = []

    # 并行搜: zh Wikipedia + en Wikipedia
    def _search_zh():
        if not game:
            return None
        sess = session_proxy  # zh.wikipedia 需要代理
        search_results = wiki_search(sess, WIKIPEDIA_ZH_API, game)
        if not search_results:
            # 降级 en Wikipedia 搜中文（反正 zh 可能被墙）
            logger.info("[Wikipedia] zh 无结果，用 en 搜中文")
            search_results = wiki_search(session_direct, WIKIPEDIA_EN_API, game)

        if search_results:
            top = search_results[0]
            title = top.get("title", "")
            # 从标题提取中英文名
            name_en, name_zh = parse_wiki_title(title, game)
            return {
                "name_zh": name_zh,
                "name_en": name_en or title,
                "wiki_url_zh": "%s/wiki/%s" % (WIKIPEDIA_ZH_BASE.rstrip("/"), quote_plus(title)),
                "title": title,
                "source": "wikipedia",
            }
        return None

    def _search_en():
        query = en_name or game
        if not query:
            return None
        sess = session_proxy  # en.wikipedia 也需要代理
        search_results = wiki_search(sess, WIKIPEDIA_EN_API, query)

        if search_results:
            top = search_results[0]
            title = top.get("title", "")
            return {
                "name_en": title,
                "name_zh": game,  # 保留原始输入的中文名
                "wiki_url_en": "%s/wiki/%s" % (WIKIPEDIA_EN_BASE.rstrip("/"), quote_plus(title.replace(" ", "_"))),
                "title": title,
                "source": "wikipedia",
            }
        return None

    # 并行执行
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_zh = ex.submit(_search_zh)
        f_en = ex.submit(_search_en)

        zh_result = f_zh.result(timeout=WIKI_TIMEOUT + 2)
        en_result = f_en.result(timeout=WIKI_TIMEOUT + 2)

    # 合并
    if zh_result and en_result:
        return {
            "name_zh": zh_result.get("name_zh") or game,
            "name_en": en_result.get("name_en") or zh_result.get("name_en", ""),
            "wiki_url_zh": zh_result.get("wiki_url_zh"),
            "wiki_url_en": en_result.get("wiki_url_en"),
            "source": "wikipedia",
        }
    elif zh_result:
        return zh_result
    elif en_result:
        return en_result

    return None


def parse_wiki_title(title, game_input):
    """从 Wikipedia 页面标题中分离中英文名

    示例:
        "Elden Ring" → (name_en="Elden Ring", name_zh=None)
        "艾尔登法环" → (name_en=None, name_zh="艾尔登法环")
        "艾尔登法环 (游戏)" → (name_en=None, name_zh="艾尔登法环")
    """
    # 去括号
    title_clean = re.sub(r'\s*[（(][^)）]*[)）]\s*$', '', title).strip()

    # 判断是否含中文
    has_cjk = bool(re.search(r'[\u4e00-\u9fff]', title_clean))

    if has_cjk:
        return None, title_clean
    else:
        return title_clean, None


# ── L2b: RAWG API ──

def rawg_resolve(client, en_name):
    """通过 RAWG 搜索游戏名称（自动补全 alternative_names）

    Args:
        client: RAWGClient 实例
        en_name: AI 翻译的英文名

    Returns:
        dict or None: { name, name_original, alternative_names, platforms, genres, released, rating, ... }
    """
    if not client or not en_name:
        return None

    try:
        results = client.search_and_enrich(en_name, limit=3)
    except Exception as e:
        logger.warning("[RAWG] 搜索异常: %s", e)
        return None

    if not results:
        logger.info("[RAWG] 无结果: %s", en_name)
        return None

    best = results[0]
    summary = RAWGClient.extract_summary(best)

    logger.info("[RAWG] 命中: %s (id=%s, %d 别名)",
                best.get("name"), best.get("id"),
                len(summary.get("alternative_names") or []))

    return {
        "name": best.get("name", ""),
        "name_original": best.get("name_original", ""),
        "rawg_id": best.get("id"),
        "alternative_names": summary.get("alternative_names") or [],
        "platforms": summary.get("platforms") or [],
        "parent_platforms": summary.get("parent_platforms") or [],
        "genres": summary.get("genres") or [],
        "released": summary.get("released") or "",
        "rating": summary.get("rating"),
        "metacritic": summary.get("metacritic"),
        "playtime": summary.get("playtime"),
        "developers": summary.get("developers") or [],
        "publishers": summary.get("publishers") or [],
        "description": summary.get("description") or "",
        "esrb_rating": summary.get("esrb_rating") or "",
        "source": "rawg",
    }


# ── 结果合并 ──

def merge_wiki_rawg(wiki_result, rawg_result, game, en_name):
    """合并 Wikipedia 和 RAWG 结果，计算置信度

    Returns:
        dict: {
            found, names, confidence, sources, candidates
        }
    """
    wiki_hit = wiki_result is not None
    rawg_hit = rawg_result is not None

    # 两边都没命中
    if not wiki_hit and not rawg_hit:
        return {"found": False, "names": None, "confidence": "none",
                "sources": {"wikipedia": False, "rawg": False}}

    # 提取名称
    name_zh = wiki_result.get("name_zh") if wiki_hit else game
    name_en = (
        (wiki_result.get("name_en") if wiki_hit else None) or
        (rawg_result.get("name") if rawg_hit else None) or
        en_name or game
    )

    # 合并别名
    aliases = []
    if wiki_hit:
        w_title = wiki_result.get("title", "")
        if w_title and w_title not in aliases:
            aliases.append(w_title)
    if rawg_hit:
        for an in rawg_result.get("alternative_names", []) or []:
            n = an if isinstance(an, str) else an.get("name", "")
            if n and n not in aliases and n.lower() != name_en.lower():
                aliases.append(n)
        # 也把 name_original 加进去
        no = rawg_result.get("name_original", "")
        if no and no not in aliases and no.lower() != name_en.lower():
            aliases.append(no)

    # 交叉验证：两边说的是同一个游戏吗？
    same_game = True
    if wiki_hit and rawg_hit:
        wiki_en = (wiki_result.get("name_en") or "").lower().strip()
        rawg_name = (rawg_result.get("name") or "").lower().strip()
        # 简单比较：忽略大小写和常见后缀
        wiki_en_clean = re.sub(r'\s*\(video game\)\s*', '', wiki_en)
        rawg_name_clean = re.sub(r'\s*\(video game\)\s*', '', rawg_name)
        if wiki_en_clean and rawg_name_clean:
            if wiki_en_clean != rawg_name_clean:
                # 尝试 token 级别比较
                wiki_tokens = set(wiki_en_clean.split())
                rawg_tokens = set(rawg_name_clean.split())
                overlap = wiki_tokens & rawg_tokens
                if not overlap and not any(t in wiki_en_clean for t in rawg_tokens if len(t) > 3):
                    same_game = False
                    logger.warning("[合并] Wikipedia 和 RAWG 指向不同游戏: %s vs %s",
                                   wiki_en_clean, rawg_name_clean)

    # 置信度
    if wiki_hit and rawg_hit and same_game:
        confidence = "high"
        source_tag = "wikipedia+rawg"
    elif wiki_hit and rawg_hit and not same_game:
        confidence = "low"
        source_tag = "wikipedia+rawg_conflict"
    elif wiki_hit:
        confidence = "medium"
        source_tag = "wikipedia"
    elif rawg_hit:
        confidence = "medium"
        source_tag = "rawg"
    else:
        confidence = "none"
        source_tag = "none"

    names = {
        "name_zh": name_zh or game,
        "name_en": name_en,
        "aliases": list(set(aliases)),
        "source": source_tag,
        "confidence": confidence,
    }

    # 可选元数据（从 RAWG 补全）
    if rawg_hit:
        names["platforms"] = rawg_result.get("platforms") or []
        names["released"] = rawg_result.get("released") or ""
        names["rating"] = rawg_result.get("rating")
        names["metacritic"] = rawg_result.get("metacritic")

    result = {
        "found": True,
        "names": names,
        "confidence": confidence,
        "sources": {
            "wikipedia": wiki_hit,
            "rawg": rawg_hit,
            "gamersky": False,
            "ddg": False,
        },
    }

    # 如果两边分歧，把两个结果都放进 candidates
    if wiki_hit and rawg_hit and not same_game:
        result["candidates"] = [
            {"name": wiki_result.get("name_en") or wiki_result.get("name_zh", ""),
             "source": "wikipedia"},
            {"name": rawg_result.get("name", ""), "source": "rawg"},
        ]

    return result


# ── L3: 游民星空站内搜 ──

def gamersky_resolve(session, game):
    """通过游民星空站内搜提取游戏规范名"""
    url = "https://www.gamersky.com/search?s=%s" % quote_plus(game)
    logger.info("[游民] 搜索: %s", url)

    try:
        html = fetch_html(session, url, timeout=TIMEOUT)
    except Exception as e:
        logger.warning("[游民] 请求失败: %s", e)
        return None

    import re as regex
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    titles = []

    # 提取搜索结果标题
    for link in soup.select(".search-result a, .result-item a, .list-item a, h3 a"):
        text = link.get_text(strip=True)
        if text and len(text) > 3 and game not in text[:6]:
            # 清洗: 去「攻略」「全流程」等后缀
            clean = regex.sub(r'[（(][^)）]*攻略[^)）]*[)）]', '', text)
            clean = regex.sub(r'攻略.*$', '', clean)
            clean = regex.sub(r'全流程.*$', '', clean)
            clean = regex.sub(r'图文.*$', '', clean)
            clean = clean.strip()
            if clean and clean not in titles:
                titles.append(clean)
            if len(titles) >= 3:
                break

    if not titles:
        logger.info("[游民] 无结果")
        return None

    logger.info("[游民] 候选名称: %s", titles[:3])
    return {
        "candidates": titles,
        "source": "gamersky",
    }


# ── L4: DDG 兜底 ──

def ddg_resolve(session, game):
    """DDG 搜索引擎兜底"""
    query = "%s 游戏" % game
    logger.info("[DDG] 搜索: %s", query)

    try:
        results = search_engine_results(session, query, max_results=5)
    except Exception as e:
        logger.warning("[DDG] 失败: %s", e)
        return None

    if not results:
        return None

    candidates = []
    seen = set()
    for r in results:
        title = r.get("title", "")
        # 清洗
        title = re.sub(r'\s*[-–|—]\s*.+$', '', title)
        title = re.sub(r'攻略.*$', '', title)
        title = re.sub(r'Wiki.*$', '', title)
        title = re.sub(r'百度百科.*$', '', title)
        title = title.strip()
        if title and len(title) >= 2 and title not in seen:
            seen.add(title)
            candidates.append(title)
        if len(candidates) >= 5:
            break

    if not candidates:
        return None

    return {
        "candidates": candidates,
        "source": "ddg",
    }


# ── 主流程 ──

def main():
    args = parse_args()
    game = args.game
    en_name = args.en_name

    if not game:
        sys.stderr.write("[ERROR] 需要 --game 参数\n")
        sys.exit(1)

    setup_logging(args.debug)
    config = load_config()
    proxy_cfg = config.get("proxy", {})

    # ── L1: NAMES.json ──
    names_db = load_names()
    hit = check_names_json(game, names_db)
    if hit:
        output = {
            "found": True,
            "names": hit,
            "confidence": hit.get("confidence", "high"),
            "sources": {"wikipedia": False, "rawg": False, "gamersky": False, "ddg": False,
                        "cache": True},
            "candidates": [],
        }
        logger.info("[NAMES] 命中: %s", hit.get("name_zh", hit.get("name_en", "")))
        print(json.dumps(output, ensure_ascii=False))
        return

    # ── Sessions ──
    session_direct = make_session(USER_AGENT, proxy_cfg, use_proxy=False, log_name="direct")
    session_proxy = make_session(USER_AGENT, proxy_cfg, use_proxy=True, log_name="proxy")

    # ── RAWG Client ──
    rawg_client = None
    rawg_cfg = config.get("rawg", {})
    if rawg_cfg.get("enabled") and rawg_cfg.get("api_key"):
        try:
            rawg_client = RAWGClient(
                rawg_cfg["api_key"],
                proxy_cfg,
            )
        except Exception as e:
            logger.warning("[RAWG] 初始化失败: %s，跳过 RAWG", e)

    # ── L2: Wikipedia + RAWG 并行 ──
    logger.info("[L2] 并行搜索: Wikipedia + RAWG")

    wiki_result = None
    rawg_result = None

    def _run_wiki():
        return wiki_resolve(session_direct, session_proxy, game, en_name)

    def _run_rawg():
        return rawg_resolve(rawg_client, en_name)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_wiki = ex.submit(_run_wiki)
        f_rawg = ex.submit(_run_rawg)

        try:
            wiki_result = f_wiki.result(timeout=WIKI_TIMEOUT + 2)
        except concurrent.futures.TimeoutError:
            logger.warning("[Wikipedia] 超时")
        except Exception as e:
            logger.warning("[Wikipedia] 异常: %s", e)

        try:
            rawg_result = f_rawg.result(timeout=RAWG_TIMEOUT + 2)
        except concurrent.futures.TimeoutError:
            logger.warning("[RAWG] 超时")
        except Exception as e:
            logger.warning("[RAWG] 异常: %s", e)

    # 合并
    merged = merge_wiki_rawg(wiki_result, rawg_result, game, en_name)

    if merged["confidence"] in ("high", "medium"):
        # 写入 NAMES.json
        entry = merged["names"].copy()
        save_names(game, entry)

        # 更新 sources 信息
        merged["sources"]["wikipedia"] = merged["sources"].get("wikipedia", False)
        merged["sources"]["rawg"] = merged["sources"].get("rawg", False)
        merged["sources"]["gamersky"] = False
        merged["sources"]["ddg"] = False

        print(json.dumps(merged, ensure_ascii=False))
        return

    # 低置信度（分歧）→ 也返回让 AI 判断
    if merged["confidence"] == "low":
        merged["sources"]["gamersky"] = False
        merged["sources"]["ddg"] = False
        print(json.dumps(merged, ensure_ascii=False))
        return

    # ── L3: 游民星空 ──
    logger.info("[L3] Wikipedia+RAWG 均无结果，尝试游民星空")
    gs_result = gamersky_resolve(session_direct, game)

    if gs_result:
        output = {
            "found": False,
            "names": None,
            "confidence": "unrated",
            "sources": {"wikipedia": False, "rawg": False, "gamersky": True, "ddg": False},
            "candidates": gs_result["candidates"],
        }
        print(json.dumps(output, ensure_ascii=False))
        return

    # ── L4: DDG ──
    logger.info("[L4] 游民星空无结果，尝试 DDG")
    ddg_result = ddg_resolve(session_proxy if proxy_cfg.get("enabled") else session_direct, game)

    if ddg_result:
        output = {
            "found": False,
            "names": None,
            "confidence": "unrated",
            "sources": {"wikipedia": False, "rawg": False, "gamersky": False, "ddg": True},
            "candidates": ddg_result["candidates"],
        }
        print(json.dumps(output, ensure_ascii=False))
        return

    # ── 完全无结果 ──
    output = {
        "found": False,
        "names": None,
        "confidence": "none",
        "sources": {"wikipedia": False, "rawg": False, "gamersky": False, "ddg": False},
        "candidates": [],
        "error": "no_results",
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
