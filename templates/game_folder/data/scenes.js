/* scenes.js —— 场景图（游戏核心数据）
 *
 * 节点字段：
 *   id          唯一 ID（入口由 game.js 的 entry 指定）
 *   title       场景标题（顶部显示）
 *   narration   正文文本，多段用 \n 分隔
 *   speaker     说话人（可选，显示在正文上方：'林黛玉：'）
 *   bg          背景图相对路径（可选）
 *   characters  在场角色 id 列表（可选，显示立绘）
 *   perspective 视角 id（可选，epic 使用）
 *   commentary  旁批列表（可选，epic 使用）：[{choice, original, note}]
 *   chapter     章节归属（可选，narrative 模式使用）：章节名或序号，驱动进度条
 *   chapter_end 章节锚点（可选，narrative 模式使用）：true = 该章分支汇聚点，
 *               章内所有选择路径最终都 goto 到这里，保证叙事可控
 *   riddle      字谜（可选，riddle 模式使用）：{question, answer, hints, goto}
 *               谜面正常显示，答案逐字方块遮罩，玩家输入文字逐步揭示；
 *               解出后自动进入 goto 场景。riddle 节点不显示选项
 *   fate_event  true = 命运事件点（可选，fate 模式使用）：渲染时额外提供
 *               '抽取命运事件'按钮，从 SCENES.events 事件池随机抽一个事件
 *               （未用过且条件满足）。节点自身必须有 choices/auto/ending 出路
 *               （玩家可跳过事件继续主线）
 *
 * 顶层 events：fate 命运事件池（可选）：
 *   [{id, title, narration, requires?, effects?, goto}] —— 随机抽取的事件，
 *   不入场景图（不参与可达性分析）；requires 按禀赋过滤，goto 指回主线节点
 *   choices     选项列表（可选）：
 *     text       选项文案
 *     goto       目标场景 id
 *     requires   前置条件（可选）：attrs/flags/inventory/perspective
 *     effects    效果（可选）：attrs 数值增减 / flags 置值 / inventory add|remove
 *   auto        自动跳转（可选）：{goto, requires?}，条件满足时自动进入下一场景
 *   ending      结局标记（可选）：{type: "good"|"bad"|"neutral", title, desc}
 *
 * 硬性规则：
 *   1. 每个节点必须恰好有：choices(≥1) 或 auto 或 ending 或 riddle 其中之一
 *   2. choices.goto / auto.goto 必须指向存在的节点 id
 *   3. requires.attrs 的 key 必须是 mode.js attributes 里的 id
 *   4. 所有节点必须从 entry 可达（QA 检查）
 */
window.SCENES = {
  "events": [
    // fate 命运事件池（fate 模式使用）
    // { "id": "evt_001", "title": "天降横财", "narration": "...",
    //   "requires": {"attrs": {"yunqi": {"gte": 3}}}, "effects": {"attrs": {"yunqi": -1}},
    //   "goto": "s020" }
  ],
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
