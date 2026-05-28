# -*- coding: utf-8 -*-
"""
Game name resolution script.
1. Checks memory/NAMES.json for existing mapping
2. If found, returns the canonical names
3. If not found, uses search engine to find candidates
4. Returns candidate list (sorted by search ranking) for AI to confirm

用法:
    python scripts/name_resolve.py --game "玩家输入的原始名称"

输出 JSON 到 stdout，日志到 stderr。
"""

import argparse
import json
import sys
import os
import re
import logging

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
NAMES_PATH = os.path.join(PROJECT_ROOT, "memory", "NAMES.json")

sys.path.insert(0, SCRIPT_DIR)
from common import make_session, search_engine_results

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

logger = logging.getLogger("name_resolve")


def parse_args():
    parser = argparse.ArgumentParser(description="游戏名称解析脚本")
    parser.add_argument("--game", type=str, required=True, help="玩家输入的原始名称")
    parser.add_argument("--debug", action="store_true", default=False, help="调试日志")
    return parser.parse_args()


def setup_logging(debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False


def load_names():
    """读取 memory/NAMES.json"""
    if not os.path.exists(NAMES_PATH):
        logger.info("[NAMES] NAMES.json 不存在，将创建目录")
        os.makedirs(os.path.dirname(NAMES_PATH), exist_ok=True)
        return {}
    try:
        with open(NAMES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("[NAMES] 读取失败: %s", e)
        return {}


def search_candidates(game):
    """通过搜索引擎查找游戏名称候选"""
    query = "%s 游戏 攻略" % game
    session = make_session(USER_AGENT)
    results = search_engine_results(session, query, max_results=5)
    candidates = [r["title"] for r in results if r["title"]]
    return candidates[:10]


def extract_game_names(candidates):
    """从搜索结果标题中提取候选游戏名（去重、清洗）"""
    names = []
    seen = set()

    for c in candidates:
        title = c
        # 清洗常见后缀
        title = re.sub(r'\s*[-–|—]\s*.+$', '', title)
        title = re.sub(r'攻略.*$', '', title)
        title = re.sub(r'Wiki.*$', '', title)
        title = re.sub(r'百度百科.*$', '', title)
        title = re.sub(r'\s*_\s*.+$', '', title)
        title = title.strip()
        # 过滤太短的结果
        if title and len(title) >= 2 and title not in seen:
            seen.add(title)
            names.append(title)
            if len(names) >= 5:
                break

    return names


def main():
    args = parse_args()
    game = args.game

    if not game:
        sys.stderr.write("[ERROR] 需要 --game 参数\n")
        sys.exit(1)

    setup_logging(args.debug)

    # ── 第1步：检查 NAMES.json ──
    names_db = load_names()

    # 精确匹配
    if game in names_db:
        entry = names_db[game]
        output = {
            "found": True,
            "names": entry,
            "candidates": [],
        }
        print(json.dumps(output, ensure_ascii=False))
        return

    # 别名匹配（遍历所有条目，检查 aliases）
    for key, entry in names_db.items():
        aliases = entry.get("aliases", [])
        aliases_lower = [a.lower().strip() for a in aliases]
        if game.lower().strip() in aliases_lower:
            output = {
                "found": True,
                "names": entry,
                "candidates": [],
            }
            print(json.dumps(output, ensure_ascii=False))
            return

    # ── 第2步：搜索引擎查找候选 ──
    logger.info("[搜索] 未在 NAMES.json 中找到 '%s'，通过搜索引擎查找候选", game)
    candidates = search_candidates(game)

    if not candidates:
        output = {
            "found": False,
            "names": None,
            "candidates": [],
            "error": "no_search_results",
        }
    else:
        game_names = extract_game_names(candidates)
        output = {
            "found": False,
            "names": None,
            "candidates": game_names,
        }

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
