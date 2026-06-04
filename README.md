<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/vibe%20coding-yes-orange" alt="Vibe Coding">
</p>

# 🎮 AIGameAssistant

> AI 驱动的游戏攻略与百科查询技能。自进化记忆体系越用越准，并行搜索多源整合。

* [特性](#-特性) ✨
* [快速开始](#-快速开始) 🚀
* [搜索架构](#-搜索架构) 🔍
* [项目结构](#-项目结构) 📁
* [记忆体系](#-记忆体系) 🧠
* [配置说明](#-配置说明) ⚙️
* [跨平台](#-跨平台) 🌐
* [License](#-license) 📄

---

## ✨ 特性

### 攻略搜索

| 🎯 源 | 📝 说明 |
|-------|---------|
| 游民星空 | 翻页合并(5页) · 内容清洗 · 图片懒加载修复 |
| Archive.org | 官方攻略书扫描版 · 本地下载 DjVuTXT · 语义理解 |
| Reddit | 9个怀旧子板 · 高赞帖+热门评论 |
| DuckDuckGo | `ddgs` 库驱动 · 多引擎智能切换 · 搜索引擎兜底 |

### 百科查询

| 🎯 源 | 📝 说明 |
|-------|---------|
| Fandom/BWiki/52poke | 30+ 游戏路由表（`config/wikis/` 目录，每游戏独立维护） |
| RAWG API | 结构化游戏数据库 · 别名/平台/发售日 · API Key 鉴权 |
| Wikipedia/萌娘百科 | 通用百科 · MediaWiki API |
| Wikibooks | 开放性攻略书 |

### 核心能力

| ✨ 能力 | 📝 说明 |
|---------|---------|
| **并行搜索 + NDJSON** | 所有源同时查询，20s 超时，NDJSON 标准化输出 |
| **多源结果整合** | AI 智能去重合并 · 矛盾标注 · 来源透明 |
| **名称自补全** | `name_resolve.py` + `memory/NAMES.json` 映射表，越用越全 |
| **Archive.org 本地下载** | `archive_extract.py` 纯下载到本地，AI 选择性读取 |
| **自进化记忆** | INDEX 路由表 · 三档质量（unrated→high/low）· 用户反馈驱动 |
| **代理按源配置** | 国内源直连，海外源走代理 · HTTP/SOCKS5 |
| **零安装部署** | requests/bs4 内置 `vendor/`，解压即用 |
| **攻略验证** | RAWG + Wikipedia 双重校准攻略声明的准确性 |

---

## 🚀 快速开始

```bash
# 解压到 skills 目录
unzip AIGameAssistant.zip -d ~/.config/alma/skills/AIGameAssistant

# 配置代理（海外源需要）
# 编辑 config/sources.json，设置 proxy.port 为实际端口

# 攻略并行搜索（默认模式，NDJSON 输出）
python scripts/walkthrough_search.py --game "艾尔登法环" --keyword "女武神打法"

# 单源直达（INDEX 记忆命中后）
python scripts/walkthrough_search.py --game "艾尔登法环" --keyword "女武神打法" --source gamersky

# 百科查询
python scripts/wiki_search.py --game "恶魔城" --topic "阿鲁卡多"

# 名称补全（新游戏首次查询前）
python scripts/name_resolve.py --game "老头环"

# Archive.org 攻略书下载到本地
python scripts/archive_extract.py --archive-id "ffvii_official_guide"

# 备选：pip 安装依赖（如果 vendor 不完整）
pip install -r requirements.txt
```

> **重要**: 依赖（requests, bs4, urllib3, ddgs）已内置在 `vendor/` 目录中，无需 `pip install`。`requirements.txt` 为备选方案。

---

## 🔍 搜索架构

### 攻略搜索流程

```
并行模式（默认）:
  游民星空 ─┐
  Archive.org ─┤ 同时启动 → 等待所有 → NDJSON → AI 整合
  Reddit    ─┤  20s 总超时
  DuckDuckGo ─┘

单源模式（--source）:
  指定源 → 秒级直达（INDEX 路由命中时使用）
```

### 百科搜索流程

```
Step 1: config/wikis/ 游戏路由命中 → 专有 Wiki 直达
Step 2: 通用 MediaWiki 源并行搜索
Step 3: Reddit 社区
Step 4: DuckDuckGo 兜底
```

### 名称补全（Wikipedia + RAWG 双重校验）

```
玩家输入 → AI 世界知识翻译英文名 →
  → name_resolve.py --game "老头环" --en-name "Elden Ring"
  → L1: NAMES.json 缓存命中 → 秒回
  → L2: Wikipedia(en+zh) + RAWG(en) 并行
  → L3: 游民星空 fallback
  → L4: DDG 兜底
```

### 攻略验证

当攻略内容包含具体游戏数据声明时，自动用 RAWG + Wikipedia 双重交叉验证：

| 验证结果 | 处理 |
|---------|------|
| RAWG + Wikipedia 双源一致 | high 信心，直接返回 |
| 仅一方验证通过 | medium，标注单源验证 |
| wiki 数据矛盾 | medium，标注潜在问题 |
| wiki 无数据 | 保留原质量评级 |

---

## 📁 项目结构

```
AIGameAssistant/
├── SKILL.md                  # AI 行为指南（流程+决策树+红线）
├── DESIGN.md                 # 架构文档（实现细节）
├── README.md                 # 项目说明
├── requirements.txt          # Python 依赖（备选）
├── config/
│   ├── sources.json          # 数据源 · 代理 · 功能开关
│   └── wikis/                # 游戏 Wiki 路由（每游戏一个 JSON 文件）
├── scripts/
│   ├── common.py             # 共享模块（Session/ddgs/反爬/代理/Vendor）
│   ├── walkthrough_search.py # 攻略搜索（并行+NDJSON）
│   ├── wiki_search.py        # 百科搜索
│   ├── name_resolve.py       # 游戏名称补全（Wikipedia+RAWG双重校验）
│   ├── rawg_client.py        # RAWG API 客户端（API Key+搜索）
│   └── archive_extract.py    # Archive.org 攻略书下载
├── references/               # 技能参考文档
│   ├── examples.md           # 使用示例
│   └── setup.md              # 安装与配置详解
├── memory/                   # 自进化记忆体系
│   ├── INDEX.md              # 路由索引（Layer 1）
│   ├── NAMES.json            # 游戏名称映射表
│   ├── sources/              # 源详述（Layer 2）
│   └── queries/              # 查询日志（Layer 3）
├── downloads/                # Archive.org 攻略书本地下载
└── vendor/                   # 内置依赖
```

---

## 🧠 记忆体系

三层渐进披露——越用越快，越用越准：

| 层 | 文件 | 读取时机 | 写入时机 |
|----|------|---------|---------|
| 1 | INDEX.md | 每次查询前 | 新游戏+关键词组合出现 |
| 1 | NAMES.json | 查询前名称补全 | name_resolve 补全后 AI 写入 |
| 2 | sources/*.md | INDEX 指向后才读 | 有新数据时（新 archive_id 等） |
| 3 | queries/YYYY-MM-DD.md | 排查追溯时 | 每次查询结束 |

### 质量体系

| 等级 | 含义 | 来源 |
|------|------|------|
| unrated | AI 初判，待用户确认 | AI 自动写入 |
| high | 最优路由，可直接使用 | 用户确认 |
| medium | 内容可用但不完整 | 用户反馈 |
| low | 不可用，下次跳过 | 用户反馈 |

> 定期检查 INDEX 中长时间未打分的 `unrated` 条目，提示用户确认或清理。

### 进化路径

```
首次查询 → 全链路搜索 → 追加三层记忆
二次查询 → INDEX 命中 high → --source 直达 → 秒级返回
N 次查询 → INDEX 有多条记录 → AI 选质量最高者
```

### 无缓存设计

本项目不依赖缓存。记忆体系的 INDEX 路由优化替代了缓存加速——路由直达省去全链路搜索的时间，同时确保每次获取最新攻略内容。

---

## ⚙️ 配置说明

### 代理

```json
// config/sources.json
"proxy": {
  "enabled": true,
  "type": "http",      // http | socks5
  "host": "127.0.0.1",
  "port": 10808        // ⚠️ 首次使用请改为实际端口
}
```

每个源独立声明 `uses_proxy`——国内源直连，海外源走代理。

### 搜索引擎

搜索使用 `ddgs` 库，支持 DuckDuckGo、Bing、Brave、Google 等多引擎智能切换。比传统 HTML 抓取更稳定可靠。

### 游戏 Wiki 路由

`config/wikis/` 目录下每游戏一个 JSON 文件（英文 slug 命名），社区可通过 PR 贡献新游戏 Wiki 路由。

---

## 🌐 跨平台

AIGameAssistant 遵循 [AgentSkills](https://agentskills.io/specification) 开放标准（`SKILL.md` 格式），支持 20+ AI 工具：

| 平台 | 安装路径 |
|------|---------|
| Alma | `~/.config/alma/skills/AIGameAssistant/` |
| Claude Code | `.claude/skills/AIGameAssistant/` |
| Trae | `~/.trae/skills/AIGameAssistant/` |
| Hermes | `~/.hermes/skills/AIGameAssistant/` |
| OpenClaw | `~/.openclaw/skills/AIGameAssistant/` |

> 不同平台共享的是技能指令，记忆体系（`memory/`）如需跨平台同步，将目录复制到对应平台的技能文件夹即可。

---

## 📄 License

MIT © 2026 AceAttorney

---

<p align="center">
  <sub>🤖 这是一个 vibe coding 项目，由 AI 辅助开发。</sub>
</p>
