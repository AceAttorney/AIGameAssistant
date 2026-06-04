# -*- coding: utf-8 -*-
"""
RAWG API Client — 游戏数据库查询

API 文档: https://api.rawg.io/apidocs

认证: URL query 参数 ?key=API_KEY
无 OAuth，无 token 管理，更简单。

用法:
    from rawg_client import RAWGClient
    client = RAWGClient(api_key, proxy_cfg)
    results = client.search_games("Elden Ring")
    detail = client.get_game(119133)
    enriched = client.search_and_enrich("Elden Ring")  # 自动补全 alternative_names
"""

import sys
import os
import json
import logging

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
if os.path.isdir(_vendor_dir):
    sys.path.insert(0, _vendor_dir)

try:
    import requests
    import urllib3
except ImportError:
    sys.stderr.write("[ERROR] 缺少依赖 requests/urllib3\n")
    sys.exit(1)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("rawg_client")

RAWG_API_BASE = "https://api.rawg.io/api"
TIMEOUT = 15


class RAWGClient:
    """RAWG API 客户端"""

    def __init__(self, api_key, proxy_cfg=None):
        self.api_key = api_key
        self.proxy_cfg = proxy_cfg
        self.session = self._make_session()

    # ── Session ──

    def _make_session(self):
        s = requests.Session()
        s.verify = False
        s.headers.update({
            "Accept": "application/json",
            "User-Agent": "AIGameAssistant/1.0",
        })

        if self.proxy_cfg and self.proxy_cfg.get("enabled", False):
            ptype = self.proxy_cfg.get("type", "http")
            host = self.proxy_cfg.get("host", "127.0.0.1")
            port = self.proxy_cfg.get("port", 7890)
            username = self.proxy_cfg.get("username", "")
            password = self.proxy_cfg.get("password", "")
            auth = "%s:%s@" % (username, password) if username else ""
            if ptype == "socks5":
                proxy_url = "socks5://%s%s:%d" % (auth, host, port)
            else:
                proxy_url = "http://%s%s:%d" % (auth, host, port)
            s.proxies = {"http": proxy_url, "https": proxy_url}
            logger.info("[RAWG] 使用代理: %s", proxy_url)

        return s

    # ── 核心请求 ──

    def _get(self, endpoint, params=None, timeout=TIMEOUT):
        """发送 GET 请求，自动附加 API key"""
        params = params or {}
        params["key"] = self.api_key
        url = "%s/%s" % (RAWG_API_BASE, endpoint.lstrip("/"))

        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.warning("[RAWG] 请求超时: %s", endpoint)
            raise
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            logger.warning("[RAWG] HTTP %d: %s", status, endpoint)
            raise
        except Exception as e:
            logger.warning("[RAWG] 请求失败: %s", e)
            raise

    # ── 公开接口 ──

    def search_games(self, query, limit=5, page_size=None):
        """搜索游戏

        Args:
            query: 搜索关键词（英文）
            limit: 返回结果数量上限
            page_size: API 请求的 page_size（默认与 limit 一致）

        Returns:
            list of dict: 搜索结果列表，每个 dict 包含 name/released/platforms/genres/rating 等
        """
        if not query:
            return []

        page_size = page_size or limit
        params = {
            "search": query,
            "page_size": min(page_size, 40),  # RAWG 最大 40
        }
        logger.info("[RAWG] 搜索: %s", query)

        try:
            data = self._get("games", params)
            results = data.get("results", [])
            logger.info("[RAWG] 找到 %d 条结果", len(results))
            return results[:limit]
        except Exception:
            return []

    def get_game(self, game_id):
        """获取指定游戏的详细信息（含 alternative_names / name_original / description）

        Args:
            game_id: RAWG 的游戏 ID

        Returns:
            dict or None: 包含所有可用字段
        """
        logger.info("[RAWG] 获取游戏 #%d", game_id)
        try:
            return self._get("games/%d" % game_id)
        except Exception:
            return None

    def search_and_enrich(self, query, limit=5):
        """搜索 + 自动补全 top 结果的详细字段（alternative_names / name_original）

        Returns:
            list of dict: 第一个元素是详情，其余是搜索结果
        """
        results = self.search_games(query, limit=limit)
        if not results:
            return []

        top = results[0]
        top_id = top.get("id")
        if not top_id:
            return results

        # 自动获取详情补全 alternative_names
        try:
            detail = self.get_game(top_id)
            if detail:
                # 用 detail 替换 top，保留其他搜索结果
                return [detail] + results[1:]
        except Exception as e:
            logger.info("[RAWG] 详情补全失败: %s，使用基础搜索结果", e)

        return results

    def get_screenshots(self, game_id, limit=5):
        """获取游戏截图"""
        try:
            data = self._get("games/%d/screenshots" % game_id)
            return data.get("results", [])[:limit]
        except Exception:
            return []

    # ── 数据提取辅助 ──

    @staticmethod
    def extract_summary(game_data):
        """从详情数据提取标准摘要"""
        if not game_data:
            return {}

        platforms = [
            p.get("platform", {}).get("name", "")
            for p in game_data.get("platforms", [])
        ]
        parent_platforms = [
            p.get("platform", {}).get("name", "")
            for p in game_data.get("parent_platforms", [])
        ]
        genres = [g.get("name", "") for g in game_data.get("genres", [])]
        developers = [d.get("name", "") for d in game_data.get("developers", [])]
        publishers = [p.get("name", "") for p in game_data.get("publishers", [])]

        alt_names_raw = game_data.get("alternative_names", [])
        alt_names = alt_names_raw if isinstance(alt_names_raw, list) else []

        description = game_data.get("description", "")
        # 清理 HTML 标签
        import re
        description_clean = re.sub(r"<[^>]+>", "", description)[:500] if description else ""

        return {
            "name": game_data.get("name", ""),
            "name_original": game_data.get("name_original", ""),
            "rawg_id": game_data.get("id"),
            "slug": game_data.get("slug", ""),
            "released": game_data.get("released", ""),
            "rating": game_data.get("rating"),
            "metacritic": game_data.get("metacritic"),
            "playtime": game_data.get("playtime"),
            "platforms": platforms,
            "parent_platforms": parent_platforms,
            "genres": genres,
            "developers": developers,
            "publishers": publishers,
            "alternative_names": alt_names,
            "description": description_clean,
            "website": game_data.get("website", ""),
            "esrb_rating": game_data.get("esrb_rating", {}).get("name", "") if game_data.get("esrb_rating") else "",
        }


# ── CLI 调试入口 ──

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAWG API 客户端测试")
    parser.add_argument("--api-key", type=str, help="RAWG API key")
    parser.add_argument("--search", type=str, help="搜索游戏")
    parser.add_argument("--game-id", type=int, help="获取游戏详情")
    parser.add_argument("--config", type=str, default=None, help="sources.json 路径")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    api_key = args.api_key
    proxy_cfg = None

    if (not api_key) and args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        rawg_cfg = cfg.get("rawg", {})
        api_key = rawg_cfg.get("api_key", "")
        proxy_cfg = cfg.get("proxy", {})

    if not api_key:
        print("需要 --api-key 或在 sources.json 中配置 rawg.api_key")
        sys.exit(1)

    client = RAWGClient(api_key, proxy_cfg)

    if args.search:
        results = client.search_and_enrich(args.search)
        if results:
            print(json.dumps(RAWGClient.extract_summary(results[0]), ensure_ascii=False, indent=2))
        else:
            print("无结果")

    if args.game_id:
        detail = client.get_game(args.game_id)
        if detail:
            print(json.dumps(RAWGClient.extract_summary(detail), ensure_ascii=False, indent=2))
