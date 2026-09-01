/* scenes.js —— 场景图（游戏核心数据）
 *
 * 节点字段：
 *   id          唯一 ID（入口由 game.js 的 entry 指定）
 *   title       场景标题（顶部显示）
 *   narration   正文文本，多段用 \n 分隔
 *   speaker     说话人（可选，显示在正文上方：'林黛玉：'）
 *   bg          背景图相对路径（可选）
 *   characters  在场角色 id 列表（可选，显示立绘）
 *   perspective 视角 id（可选，S4 使用）
 *   commentary  旁批列表（可选，S4 使用）：[{choice, original, note}]
 *   choices     选项列表（可选）：
 *     text       选项文案
 *     goto       目标场景 id
 *     requires   前置条件（可选）：attrs/flags/inventory/perspective
 *     effects    效果（可选）：attrs 数值增减 / flags 置值 / inventory add|remove
 *   auto        自动跳转（可选）：{goto, requires?}，条件满足时自动进入下一场景
 *   ending      结局标记（可选）：{type: "good"|"bad"|"neutral", title, desc}
 *
 * 硬性规则：
 *   1. 每个节点必须恰好有：choices(≥1) 或 auto 或 ending 其中之一
 *   2. choices.goto / auto.goto 必须指向存在的节点 id
 *   3. requires.attrs 的 key 必须是 mode.js attributes 里的 id
 *   4. 所有节点必须从 entry 可达（QA 检查）
 */
window.SCENES = {
  "scenes": {
    "s001": {
      "id": "s001",
      "title": "开场",
      "narration": "故事从这里开始。",
      "characters": [],
      "choices": [
        { "text": "选择一", "goto": "s002", "effects": { "attrs": { "affection_lin_daiyu": 1 } } }
      ]
    },
    "s002": {
      "id": "s002",
      "title": "结局",
      "narration": "故事结束。",
      "ending": { "type": "neutral", "title": "结局一", "desc": "这是示例结局" }
    }
  }
};
