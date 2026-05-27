# AIGameAssistant

AI 驱动的游戏攻略与百科查询技能。支持怀旧游戏和热门大作，用记忆体系越用越准。

## 功能

- **攻略搜索**: 游民星空（翻页合并+内容清洗+真实截图）+ Archive.org 官方攻略书 + Reddit 社区 + DuckDuckGo 兜底
- **百科查询**: 30+ 游戏专有 Wiki 路由（Fandom/BWiki/52poke）+ Wikipedia/萌娘百科/Wikibooks
- **代理**: HTTP/SOCKS5 代理，按源独立配置（国内直连，海外走代理）
- **自进化记忆**: INDEX 路由表 + sources 渐进披露 + queries 日志
- **零安装**: 依赖内置在 vendor/ 目录，解压即用
- **攻略验证**: 用 Wiki 数据校准攻略中的技能/装备/数值声明

## 快速开始

```bash
# 解压到 skills 目录
unzip AIGameAssistant.zip -d ~/.config/alma/skills/AIGameAssistant

# 配置代理（可选——海外源需要）
# 编辑 config/sources.json → proxy.enabled = true

# 测试
python scripts/walkthrough_search.py --game "宝可梦" --keyword "全流程"
python scripts/wiki_search.py --game "恶魔城" --topic "阿鲁卡多"
```

## 项目结构

```
AIGameAssistant/
├── SKILL.md                  # AI 入口（行为指南 + 记忆体系）
├── DESIGN.md                 # 架构文档
├── config/
│   ├── sources.json          # 数据源 + 代理 + 开关
│   ├── wiki_routes.json      # 30+ 游戏 → 专有 Wiki 路由表
│   └── parsers/
│       └── gamersky.json     # 游民星空解析规则
├── scripts/
│   ├── common.py             # 共享模块（Session/反爬/代理/Vendor）
│   ├── walkthrough_search.py # 攻略搜索
│   └── wiki_search.py        # 百科搜索
├── memory/                   # 自进化记忆体系
│   ├── INDEX.md              # 路由索引（每次查询前必读）
│   ├── sources/              # 源详述（INDEX 指向后才读）
│   │   ├── gamersky.md
│   │   ├── archive_org.md
│   │   ├── reddit.md
│   │   ├── search.md
│   │   ├── fandom.md
│   │   └── poe2db.md
│   └── queries/              # 原始日志（追溯用）
├── vendor/                   # 内置依赖（requests, bs4, urllib3）
└── LICENSE                   # MIT
```

## 搜索流程

```
walkthrough: 游民星空 → Archive.org → Reddit → DuckDuckGo
wiki:        路由表(Fandom/52poke) → Wikipedia/萌百 → Reddit → DuckDuckGo
```

## License

MIT © 2026 AceAttorney
