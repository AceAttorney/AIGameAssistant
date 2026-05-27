# 攻略源索引

> **用途**: AI 每次查询前先读此文件，按游戏→关键词→质量命中后，去对应源文件读详情。
> **原则**: 只记录路由，不存数据。数据在 `sources/*.md` 中渐进披露。
> **源文件**: gamersky.md | archive_org.md | reddit.md | search.md | fandom.md
> **最后更新**: 2026-05-27

---

## 宝可梦

| 版本 | 关键词 | 路由 | 质量 | 详情 |
|------|--------|------|------|------|
| 火红/叶绿 | 全流程 | gamersky | high | [sources/gamersky.md](sources/gamersky.md) |
| 晶灿钻石/明亮珍珠 | 全流程 | gamersky | high | [sources/gamersky.md](sources/gamersky.md) |
| 通用 | 宝石海星 | wiki→52poke | high | [sources/fandom.md](sources/fandom.md) |

## 恶魔城

| 版本 | 关键词 | 路由 | 质量 | 详情 |
|------|--------|------|------|------|
| 月下夜想曲 | 阿鲁卡多(攻略) | gamersky | low | [sources/gamersky.md](sources/gamersky.md) |
| 月下夜想曲 | 阿鲁卡多(百科) | wiki→fandom | high | [sources/fandom.md](sources/fandom.md) |
| 月下夜想曲 | 攻略 | archive | high | [sources/archive_org.md](sources/archive_org.md) |
| 暗影之王 | 阿鲁卡多 | gamersky | medium | [sources/gamersky.md](sources/gamersky.md) |

## Golden Sun

| 版本 | 关键词 | 路由 | 质量 | 详情 |
|------|--------|------|------|------|
| 通用 | walkthrough | archive | high | [sources/archive_org.md](sources/archive_org.md) |
| 通用 | 精灵/Djinn | gamersky | high | [sources/gamersky.md](sources/gamersky.md) |

## 幽城幻剑录

| 版本 | 关键词 | 路由 | 质量 | 详情 |
|------|--------|------|------|------|
| 通用 | 全流程/攻略 | gamersky | low | [sources/gamersky.md](sources/gamersky.md) |

## 最终幻想

| 版本 | 关键词 | 路由 | 质量 | 详情 |
|------|--------|------|------|------|
| VII | walkthrough | archive | high | [sources/archive_org.md](sources/archive_org.md) |

---

## 路由说明

- **gamersky**: `walkthrough_search.py --source gamersky`（中文攻略站）
- **archive**: `walkthrough_search.py --source archive`（Archive.org 攻略书）
- **reddit**: `walkthrough_search.py --source reddit`（Reddit 社区）
- **search**: `walkthrough_search.py --source search`（DuckDuckGo 兜底）
- **wiki→fandom**: `wiki_search.py --game "游戏名" --topic "关键词"`（Fandom 路由命中）
- **wiki→52poke**: `wiki_search.py --game "宝可梦" --topic "关键词"`（52poke 路由命中）
- **wiki→poe2db**: `wiki_search.py --game "流放之路2" --topic "技能名"`（POE2DB 路由命中）

## 流放之路2

| 版本 | 关键词 | 路由 | 质量 | 详情 |
|------|--------|------|------|------|
| 通用 | 技能/宝石/装备 | wiki→poe2db | — | [sources/poe2db.md](sources/poe2db.md) |
| 通用 | BD/Build/攻略 | search | — | [sources/search.md](sources/search.md) |
