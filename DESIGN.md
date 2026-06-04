# AIGameAssistant 架构设计与实现规范

> **文档定位**: 本文档面向开发者，描述 AIGameAssistant 的架构设计、配置文件结构、脚本接口与实现规范。
> **关联文档**: [SKILL.md](./SKILL.md) 面向 AI 助手，定义行为指导与交互规范。

---

## 配置文件规范

### 配置文件位置
- **路径**: `config/sources.json`
- **用途**: 集中管理查询数据源和功能开关

### 配置文件结构
```json
{
  "walkthrough_sources": [
    {
      "name": "游民星空",
      "enabled": true,
      "url": "https://www.gamersky.com"
    },
    {
      "name": "游侠攻略网",
      "enabled": true,
      "url": "https://gl.ali213.net"
    }
  ],
  "wiki_sources": [
    {
      "name": "游戏维基",
      "enabled": true,
      "url": "https://zh.wikipedia.org"
    }
  ],
  "features": {
    "use_search_engine": false,
    "search_engine": "bing",
    "max_results": 10
  },
  "rawg": {
    "enabled": true,
    "api_key": "",
    (暂无)
  }
}
```

### 字段说明

#### 数据源配置 (sources)
| 字段 | 类型 | 说明 |
|------|------|------|
| name | string | 数据源名称，用于日志和来源标注 |
| enabled | boolean | true/false，控制是否启用该数据源 |
| url | string | 数据源网站完整 URL |

#### 功能开关 (features)
| 字段 | 类型 | 说明 |
|------|------|------|
| use_search_engine | boolean | 是否启用搜索引擎作为备用查询方式 |
| search_engine | string | 搜索引擎选择 (bing / google / duckduckgo) |
| max_results | int | 最大返回结果数量 |

### 开关配置场景
- **关闭搜索引擎** (`use_search_engine: false`): 仅使用配置的攻略站和百科源，保证来源可靠性
- **开启搜索引擎** (`use_search_engine: true`): 配置源无结果时启用搜索引擎，必须标注来源

#### RAWG 配置 (rawg)
| 字段 | 类型 | 说明 |
|------|------|------|
| enabled | boolean | 是否启用 RAWG API |
| api_key | string | RAWG API Key（无需 OAuth） |
| (已合并到 api_key) |

RAWG 通过 API Key 鉴权。脚本自动管理 token 缓存（`cache/rawg_token.json`），不需要手动刷新。

---

## 解析器规则

### 文件结构
每个网站的解析规则存储在 `config/parsers/` 目录下，以网站域名为文件名：

```
config/parsers/
├── gamersky.json      # 游民星空
└── wikipedia.json     # 维基百科
```

### 解析规则文件格式
```json
{
  "name": "游民星空",
  "domains": ["gamersky.com", "www.gamersky.com"],
  "search": {
    "url_pattern": "https://www.gamersky.com/search?s={keyword}",
    "result_selector": ".search-result a",
    "title_selector": ".result-title",
    "link_selector": "a",
    "summary_selector": ".result-summary"
  },
  "content": {
    "title_selector": "h1",
    "content_selector": ".content-detail, .article-content",
    "image_selector": "img",
    "image_containers": ["figure", ".img-box"]
  }
}
```

### 字段说明
| 字段 | 类型 | 说明 |
|------|------|------|
| name | string | 网站显示名称 |
| domains | array | 匹配的域名列表（支持子域名） |
| search.url_pattern | string | 搜索页面 URL 模板，{keyword} 会被替换 |
| search.result_selector | string | 搜索结果列表的 CSS 选择器 |
| search.title_selector | string | 结果标题的 CSS 选择器 |
| search.link_selector | string | 结果链接的 CSS 选择器 |
| search.summary_selector | string | 结果摘要的 CSS 选择器 |
| content.title_selector | string | 文章标题的 CSS 选择器 |
| content.content_selector | string | 文章正文的 CSS 选择器 |
| content.image_selector | string | 文章内图片的 CSS 选择器 |
| content.image_containers | array | 图片容器的 CSS 选择器（用于提取 caption） |

### 解析器优先级
当同一域名有多个匹配规则时：
1. 精确匹配 `www.domain.com`
2. 子域名匹配 `sub.domain.com`
3. 域名匹配 `domain.com`
4. 通配符匹配 `*.domain.com`

---

## 通用查询框架

### 1. 网站识别
网站识别器根据配置的 URL 自动匹配域名，加载对应的解析器规则：
- 检测是否为已知网站（游民星空等）
- 使用网站特定的 CSS 选择器和解析规则

### 2. 搜索机制
1. 从配置读取 `url_pattern`，替换 `{keyword}` 构造搜索 URL
2. 发送 HTTP 请求获取搜索结果页
3. 使用 `result_selector` 解析搜索结果列表
4. 提取标题（`title_selector`）、链接（`link_selector`）、摘要（`summary_selector`）

### 3. 内容提取
1. 访问结果详情页
2. 使用 `content_selector` 提取正文区域
3. 使用 `image_selector` 识别并提取图片资源
4. 从 `image_containers` 提取图片的 caption 说明
5. 清理无意义内容（广告、导航、脚本等）

### 4. 回退机制
1. 当前数据源解析失败时记录错误日志
2. 自动尝试配置中下一个 `enabled: true` 的数据源
3. 所有数据源均失败时返回空结果（success: false）

### 5. 扩展新网站
1. 在 `config/parsers/` 下创建 `{domain}.json` 文件
2. 填写基本信息（name、domains）
3. 使用浏览器开发者工具确定稳定的 CSS 选择器
4. 避免使用动态生成的类名
5. 验证搜索和内容提取功能正常
6. 不需要修改配置文件 `sources.json`

---

## 脚本接口规范

### 配置文件读取
所有查询脚本在启动时必须读取 `config/sources.json` 配置文件：
1. 读取 `walkthrough_sources` 或 `wiki_sources` 配置
2. 检查每个数据源的 `enabled` 状态
3. 只对 `enabled: true` 的数据源发起查询
4. 根据 `features.use_search_engine` 判断是否启用搜索引擎

### walkthrough_search.py

```python
# 配置文件读取
config = load_config('config/sources.json')
enabled_sources = [s for s in config['walkthrough_sources'] if s['enabled']]

# 输入参数
{
    "game": "游戏名称",
    "keyword": "攻略关键词",
    "use_engine": config['features']['use_search_engine']
}

# 返回格式
{
    "success": true/false,
    "source": "来源网站",
    "url": "原始链接",
    "content": "攻略内容（Markdown 格式，含图片链接）",
    "images": [
        {
            "url": "图片URL",
            "alt": "图片描述（从 alt 属性或上下文提取）",
            "caption": "图片说明（如有）"
        }
    ],
    "is_search_engine": false
}
```

### wiki_search.py

```python
# 配置文件读取
config = load_config('config/sources.json')
enabled_sources = [s for s in config['wiki_sources'] if s['enabled']]

# 输入参数
{
    "game": "游戏名称",
    "topic": "知识主题",
    "use_engine": config['features']['use_search_engine']
}

# 返回格式
{
    "success": true/false,
    "source": "来源网站",
    "url": "原始链接",
    "content": "知识内容（Markdown 格式，含图片链接）",
    "images": [
        {
            "url": "图片URL",
            "alt": "图片描述（从 alt 属性或上下文提取）",
            "caption": "图片说明（如有）"
        }
    ],
    "is_search_engine": false
}
```

### name_resolve.py — Wikipedia + RAWG 双重校验

```python
# 配置文件读取
config = load_config('config/sources.json')
rawg_cfg = config.get('rawg', {})
proxy_cfg = config.get('proxy', {})

# 输入参数
{
    "game": "玩家原始输入（如'老头环'）",
    "en_name": "AI翻译的英文名（如'Elden Ring'）"  # 可选
}

# 四级降级搜索
# L1: NAMES.json 缓存命中 → 直接返回
# L2: Wikipedia(en+zh) + RAWG(en) 并行
# L3: 游民星空站内搜 fallback
# L4: DDG 搜索引擎兜底

# 返回格式
{
    "found": true/false,
    "names": {
        "name_zh": "艾尔登法环",
        "name_en": "Elden Ring",
        "aliases": ["ELDEN RING", "老头环", "法环"],
        "source": "wikipedia+rawg",     # wikipedia / rawg / wikipedia+rawg / gamersky / ddg
        "confidence": "high"            # high / medium / low / unrated / none
    },
    "confidence": "high",
    "sources": {
        "wikipedia": true,
        "rawg": true,
        "gamersky": false,
        "ddg": false
    },
    "candidates": []  # confidence=low 时返回 AI 可判断的候选列表
}
```

### rawg_client.py — RAWG API 客户端

```python
# OAuth 认证 → Token 缓存 → API 请求
from rawg_client import RAWGClient

client = RAWGClient(client_id, client_secret, proxy_cfg)

# 搜索游戏
results = client.search_games("Elden Ring", limit=5)
# → [{name, alternative_names, platforms, genres, first_release_date, summary, ...}]

# 获取游戏详情
game = client.get_game(119133)
# → {name, storyline, involved_companies, rating, websites, ...}
```

**认证流程**:
1. POST `https://id.twitch.tv/oauth2/token` 获取 access_token
2. Token 缓存到 `cache/rawg_token.json`（默认有效期 60 天）
3. 请求时自动检查过期并刷新

**代理**: 走 `config/sources.json` 的 proxy 配置（需要代理，`api.rawg.com` 和 `id.twitch.tv` 均为 Twitch 基础设施）

---

## 选择器编写规范

### 基本规则
1. **优先使用类选择器**: 如 `.article-content` 而非 `div.content`
2. **避免依赖标签名**: 标签结构可能变化，应使用语义化的类名
3. **路径尽量短**: 避免过长的嵌套选择器路径
4. **考虑兼容性**: 使用标准的 CSS3 选择器

### 选择器优先级
```
类选择器 (.class) > 属性选择器 [attr] > 标签选择器 (tag)
```

### 常用选择器参考
```css
/* 文章正文区域 */
.content-detail, .article-content, .entry-content

/* 搜索结果列表 */
.search-result, .result-item

/* 图片容器（含 caption） */
figure, .img-box, .image-wrapper

/* 标题 */
h1, .title, .article-title
```

---

## 图文处理规范

### 文本内容
- 使用 Markdown 格式返回内容
- 保留关键文本格式（标题、列表等）
- 图片以 `![](url)` 格式嵌入文本中

### 图片内容
- 提取所有图片的 URL、alt 属性和 caption
- 存储在独立的 `images` 数组中
- 图片应包含相关的上下文说明

### 返回示例
```json
{
    "success": true,
    "source": "游民星空",
    "url": "https://www.gamersky.com/guide/xxx",
    "content": "## BOSS名称\n\n第一阶段策略：\n\n1. 注意躲避攻击\n2. ![](https://xxx.com/img1.jpg)\n3. 等待时机输出\n\n第二阶段：\n\n![图片描述](https://xxx.com/img2.jpg)\n\n## 关键提示\n\n- 道具使用技巧",
    "images": [
        {
            "url": "https://xxx.com/img1.jpg",
            "alt": "BOSS第一阶段攻击范围",
            "caption": "图1：BOSS攻击范围示意图"
        },
        {
            "url": "https://xxx.com/img2.jpg",
            "alt": "第二阶段位置",
            "caption": "图2：推荐的站位位置"
        }
    ],
    "is_search_engine": false
}
```
