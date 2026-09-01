# 设计 brief（阶段二·第一步，深模型）

你已读完全部材料（世界观圣经 world.md、人物卡 characters.json、游戏模式模板、主题模板）。
输出一份设计 brief（严格 JSON），作为数据生成阶段的蓝图。

## 必须包含
```json
{
  "game_title": "游戏标题（取自小说气质，可与书名不同）",
  "subtitle": "副标题",
  "player": "玩家扮演说明（S4 为多视角描述）",
  "attributes": [{"id": "affection_x", "label": "显示名", "min": 0, "max": 100, "start": 0, "visible": true}],
  "inventory_items": [{"id": "...", "name": "...", "desc": "..."}],   // 启用物品栏的模式才需要
  "scene_blueprint": [
    {"id": "s001", "title": "场景标题", "summary": "一句话剧情", "options": 2, "branch": "分支去向说明", "ending": null}
  ],
  "endings": [{"type": "good|bad|neutral", "title": "结局名", "desc": "一句话描述", "require": "达成条件说明"}],
  "commentary_points": []   // S4：旁批点 [{scene, choice, original, note}]
}
```

## 规则
- attributes 3~6 个为佳；好感度用 `affection_<角色id>` 命名
- scene_blueprint 15~40 个场景（短篇下限 10）；分支 2~3 个节点内回收
- 每个场景标注 options 数（2~3）；ending 非空 = 结局节点
- 结局 2~5 个，与模式模板 endings.min/max 一致
- 决策点必须来自 world.md 中原文可考的事件，不得凭空设计主线
- S4 模式必须给出 perspectives 清单：`[{"id": "...", "name": "..."}]`
