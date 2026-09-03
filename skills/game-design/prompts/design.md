# 设计 brief（阶段二·第一步，深模型）

你已读完全部材料（世界观圣经 world.md、人物卡 characters.json、游戏模式模板、主题模板）。
输出一份设计 brief（严格 JSON），作为数据生成阶段的蓝图。

## 必须包含
```json
{
  "game_title": "游戏标题（取自小说气质，可与书名不同）",
  "subtitle": "副标题",
  "player": "玩家扮演说明（epic 为多视角描述）",
  "attributes": [{"id": "affection_x", "label": "显示名", "min": 0, "max": 100, "start": 0, "visible": true}],
  "inventory_items": [{"id": "...", "name": "...", "desc": "..."}],   // 启用物品栏的模式才需要
  "chapters": ["第一章 ...", "第二章 ..."],   // 仅 narrative 模式必填：章节名列表（来自 chapters.json），驱动进度条
  "scene_blueprint": [
    {"id": "s001", "title": "场景标题", "summary": "一句话剧情", "options": 2, "branch": "分支去向说明", "ending": null, "chapter": "第一章 ...", "chapter_end": false}
  ],
  "endings": [{"type": "good|bad|neutral", "title": "结局名", "desc": "一句话描述", "require": "达成条件说明"}],
  "commentary_points": []   // epic：旁批点 [{scene, choice, original, note}]
}
```

## 规则
- attributes **2~6 个必填**：好感度外至少 1 个剧情实质属性（勇气/洞察/声望/体力/
  金钱等，按题材取），且它必须**实际参与后果**——在 scene_blueprint 或结局条件里
  被 requires 门槛或 effects 变动引用。禁止"一个好感度属性空转到结局"的设计
- **结局必须状态条件裁决**：每个结局在 scene_blueprint 的 summary 或专用字段里写明
  达成条件（属性值/物品/flag 组合），蓝图须含状态裁决节点或带 requires 门槛的
  分支。禁止"每个结局一条专用选择路径一路走到底"——多条路应汇聚到裁决点，
  由玩家积累的状态决定进入哪个结局（同一选择点的不同历史可导向不同结局）
- 原文有可拿取的信物/道具/文档素材时，**classic 也启用 inventory_items**（brief
  声明物品 → 生成阶段会开物品栏并让物品参与门槛与效果）；仅当全书无任何物件时
  才省略
- 场景内写清每个 options 的"后果意图"（涨哪项属性/开哪扇门），供生成阶段落实
  effects/requires，避免选项做成纯文本分流
- scene_blueprint 规模按原文素材量缩放（防灌水：每场景约需 80~150 字原文可改写
  内容支撑，宁少勿滥）：长/中篇（≥1.5 万字）15~40 个；短篇（<1.5 万字）8~14 个；
  原文素材明显不足时（如 <3000 字）6~10 个且多节点复用同一素材展开
  （细化选项后果/追加小分支），不凭空编造大段剧情；分支 2~3 个节点内回收
- 每个场景标注 options 数（2~3）；ending 非空 = 结局节点
- 结局 2~5 个，与模式模板 endings.min/max 一致
- 决策点必须来自 world.md 中原文可考的事件，不得凭空设计主线
- epic 模式必须给出 perspectives 清单：`[{"id": "...", "name": "..."}]`
- narrative 模式必须给出 chapters 清单（章节名与 chapters.json 对应），并为每章
  标记 1 个 chapter_end: true 的锚点蓝图（该章分支汇聚点）；生成阶段按此输出 chapter/chapter_end
- fate 模式必须给出：attributes 为禀赋（家世/才气/运气类，max 20 左右）、
  draw_pool 蓝图（≥2 个转生身份：名号/描述/禀赋门槛/剧情线起点场景）、
  events 蓝图（5~10 个命运事件：标题/触发禀赋/效果/去向）、scene_blueprint 中
  标 2~4 个 fate_event: true 节点（命运事件点）；entry 为空壳锚点
