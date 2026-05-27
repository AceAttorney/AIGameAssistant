<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/vibe%20coding-yes-orange" alt="Vibe Coding">
</p>

# 🎮 AIGameAssistant

> AI 驱动的游戏攻略与百科查询技能。专为怀旧游戏优化，用记忆体系越用越准。

* [特性](#-特性) ✨
* [快速开始](#-快速开始) 🚀
* [搜索流程](#-搜索流程) 🔍
* [项目结构](#-项目结构) 📁
* [记忆体系](#-记忆体系) 🧠
* [配置说明](#-配置说明) ⚙️
* [License](#-license) 📄

---

## ✨ 特性

### 攻略搜索

| 🎯 源 | 📝 说明 |
|-------|---------|
| 游民星空 | 翻页合并(5页) · 内容清洗 · 图片懒加载修复 |
| Archive.org | 官方攻略书扫描版 · DjVuTXT 文本提取 |
| Reddit | 9个怀旧子板 · 高赞帖+热门评论 |
| DuckDuckGo | 搜索引擎兜底 · 优先游侠/巴哈姆特等攻略站 |

### 百科查询

| 🎯 源 | 📝 说明 |
|-------|---------|
| Fandom/BWiki/52poke | 30+ 游戏路由表 · 专有 Wiki 直达 |
| Wikipedia/萌娘百科 | 通用百科 · MediaWiki API |
| Wikibooks | 开放性攻略书 |

### 核心能力

| ✨ 能力 | 📝 说明 |
|---------|---------|
| 代理按源配置 | 国内源直连，海外源走代理 · HTTP/SOCKS5 |
| 零安装部署 | requests/bs4 内置 vendor/ · 解压即用 |
| 自进化记忆 | INDEX路由表 · sources渐进披露 · queries日志 |
| 攻略验证 | Wiki 数据交叉校准攻略声明的准确性 |
| 单源直达 | `--source gamersky` 记忆命中后秒级返回 |
| 24h 缓存 | 同一 query 免重复搜索 |

---

## 🚀 快速开始

```bash
# 解压到 skills 目录
unzip AIGameAssistant.zip -d ~/.config/alma/skills/AIGameAssistant

# 配置代理（海外源需要）
# 编辑 config/sources.json，设置 proxy.enabled: true

# 测试攻略搜索
python scripts/walkthrough_search.py --game "宝可梦" --keyword "全流程"

# 测试百科搜索
python scripts/wiki_search.py --game "恶魔城" --topic "阿鲁卡多"

# 单源直达（配合记忆体系）
python scripts/walkthrough_search.py --game "Golden Sun" --keyword "walkthrough" --source archive
```

> **重要**: 无 `pip install` 步骤。依赖（requests, bs4, urllib3）已内置在 `vendor/` 目录中。

---

## 🔍 搜索流程

```
walkthrough_search:
  游民星空 → Archive.org → Reddit → DuckDuckGo

wiki_search:
  路由表(Fandom/52poke) → Wikipedia/萌娘百科/Wikibooks → Reddit → DuckDuckGo
```

### 攻略验证

当攻略内容包含具体游戏数据声明时，自动用 wiki 交叉验证：

| 验证结果 | 处理 |
|---------|------|
| wiki 数据一致 | high 信心，直接返回 |
| wiki 数据矛盾 | medium，标注潜在问题 |
| wiki 无数据 | 保留原质量评级 |

---

## 📁 项目结构

```
AIGameAssistant/
├── SKILL.md                  # AI 行为指南（记忆体系+验证流程）
├── DESIGN.md                 # 架构文档
├── config/
│   ├── sources.json          # 数据源 · 代理 · 功能开关
│   ├── wiki_routes.json      # 30+ 游戏 → 专有 Wiki
│   └── parsers/
│       └── gamersky.json     # 游民星空解析规则
├── scripts/
│   ├── common.py             # 共享模块（Session/反爬/代理/Vendor）
│   ├── walkthrough_search.py # 攻略搜索
│   └── wiki_search.py        # 百科搜索
├── memory/                   # 自进化记忆体系
│   ├── INDEX.md              # 路由索引（Layer 1）
│   ├── sources/              # 源详述（Layer 2）
│   └── queries/              # 查询日志（Layer 3）
└── vendor/                   # 内置依赖
```

---

## 🧠 记忆体系

三层渐进披露——越用越快，越用越准：

| 层 | 文件 | 读取时机 | 写入时机 |
|----|------|---------|---------|
| 1 | INDEX.md | 每次查询前 | 新游戏+关键词组合出现 |
| 2 | sources/*.md | INDEX 指向后才读 | 有新数据时（新 archive_id 等） |
| 3 | queries/YYYY-MM-DD.md | 排查追溯时 | 每次查询结束 |

### 进化路径

```
首次查询 → 全链路搜索 → 追加三层记忆
二次查询 → INDEX 命中 high → --source 直达 → 秒级返回
N 次查询 → INDEX 有多条记录 → AI 选质量最高者
```

---

## ⚙️ 配置说明

### 代理

```json
// config/sources.json
"proxy": {
  "enabled": true,
  "type": "http",      // http | socks5
  "host": "127.0.0.1",
  "port": 10808
}
```

每个源独立声明 `uses_proxy`——国内源直连，海外源走代理。

### 搜索引擎

```json
"features": {
  "search_engine": "duckduckgo"   // duckduckgo | bing
}
```

> DuckDuckGo 中文分词远优于 Bing，不再出现「天地劫」被拆成「天」「地」的问题。

---

## 📄 License

MIT © 2026 AceAttorney

---

<p align="center">
  <sub>🤖 这是一个 vibe coding 项目，由 AI 辅助开发。</sub>
</p>
