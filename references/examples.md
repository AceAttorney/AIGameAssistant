# 使用示例

## 攻略查询（含名称补全 + 并行搜索）
```
玩家: "宝可梦叶绿的全流程攻略？"

执行步骤:
1. 识别为攻略查询
2. 名称补全:
   → 先查 NAMES.json → 未命中
   → name_resolve.py --game "宝可梦叶绿" → 返回候选: ["宝可梦 火红/叶绿", "Pokemon FireRed LeafGreen"]
   → AI 确认 → 写入 NAMES.json
3. 查 INDEX → 未命中
4. 并行搜索:
   → walkthrough_search.py --game "宝可梦 火红/叶绿" --keyword "全流程"
   → 等待 NDJSON 输出（游民星空+Archive+Reddit+搜索引擎）
5. 整合多源结果，标注来源
```

## 知识查询
```
玩家: "艾尔登法环的世界观是什么？"

执行步骤:
1. 识别为知识查询
2. 名称补全: NAMES.json 命中 → "Elden Ring"
3. 调用 wiki_search.py --game "艾尔登法环" --topic "世界观"
   → Wiki 路由命中 → Fextralife Wiki 返回背景介绍
4. 整理返回，标注来源
```

## 混合查询（攻略+知识并行）
```
玩家: "艾尔登法环玛莉卡的背景故事和打法"

执行步骤:
1. 识别为混合查询（"背景故事"= 知识 + "打法"= 攻略）
2. 名称补全: NAMES.json 命中
3. 查 INDEX → 未命中
4. 并行执行两个脚本:
   → walkthrough_search.py --game "艾尔登法环" --keyword "玛莉卡打法"
   → wiki_search.py --game "艾尔登法环" --topic "玛莉卡背景"
5. 收集两边结果，整合为一份回复:
   ├─ 背景故事部分（来自Wiki）
   └─ 打法攻略部分（来自攻略站）
6. 标注各信息来源
```

## 怀旧游戏百科查询（通过 Archive.org）
```
玩家: "黄金太阳的精灵收集攻略"

执行步骤:
1. 识别为攻略+知识混合查询
2. 名称补全: name_resolve.py → NAMES.json 未命中 → 搜索补全 → "Golden Sun"
3. walkthrough_search.py --game "Golden Sun" --keyword "精灵"
   → Archive.org 命中 Prima Official Strategy Guide
   → NDJSON 返回 archive_id + 下载链接
4. archive_extract.py --archive-id "xxx" --keyword "精灵"
   → 返回相关段落
5. 语义理解，标注来源: "Prime Official Strategy Guide (Internet Archive)"
```

## 未找到结果（重要——必须严格遵守）

**当所有源都返回 `success: false` 时，必须在此停止，绝不自行搜索。**

```
玩家: "某游戏的某个隐藏任务"

脚本返回 NDJSON 全部为 success:false
→ AI 绝不能使用自己的 web search / web fetch 工具
→ AI 直接回复:

"抱歉，我查询了多个攻略源，但没有找到关于该任务的攻略信息。
可能原因：
1. 游戏名称可能需要补全（请确认游戏的完整名称）
2. 任务名称或描述可能有误
3. 该任务可能是玩家自制内容，非官方内容
4. 相关攻略尚未发布

建议：
- 尝试提供更准确的游戏名称和任务名称
- 提供游戏的具体版本信息
- 如果确认信息无误但仍找不到，欢迎到项目 GitHub 提交 issue"
```

**全 false 的行为红线**: 脚本返回全部失败 = 查询结束。不允许使用任何自带工具自行搜索攻略内容。
