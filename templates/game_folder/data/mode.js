/* mode.js —— 模式机制配置（决定引擎开哪些功能；由游戏模式模板的 runtime 节生成） */
window.MODE = {
  "mode_id": "classic",
  "mode_name": "叙事冒险",
  "mechanics": ["choices", "affection"],
  "attributes": [
    // 属性/好感度定义：场景中的 requires/effects 按 id 引用
    { "id": "affection_lin_daiyu", "label": "林黛玉好感", "min": 0, "max": 100, "start": 0, "visible": true }
  ],
  "inventory": {
    "enabled": false,
    "limit": 20,
    "items": [
      // { "id": "clue_handkerchief", "name": "手帕", "desc": "绣着潇湘竹的手帕" }
    ]
  },
  "panels": ["status", "choices"],
  "chapter_progress": {
    // narrative 章节叙事：进度条。chapters 为章节名列表（与 scenes 的 chapter 字段对应）
    "enabled": false,
    "chapters": []
  },
  "galgame": {
    // galgame 恋爱养成：对话框排版（大立绘 + 底部对话框）。scenes 用 speaker 驱动对白
    "enabled": false
  },
  "survival": {
    // survival 生存试炼：初始点数分配 + 生命值 + 死亡判定
    "enabled": false,
    "alloc_points": 30,          // 玩家可分配总点数
    "alloc_attrs": [],           // 可分配属性 id 列表（默认全部 attributes）
    "hp_attr": "hp",             // 生命值属性 id
    "death_threshold": 0,        // hp ≤ 此值视为死亡
    "death_scene": ""            // 死亡场景 id（bad 结局节点，引擎 hp 归零自动跳转）
  },
  "riddle": {
    // riddle 字谜问答：文字谜题。scenes 节点用 riddle 字段（见 scenes.js schema）
    "enabled": false
  },
  "fate": {
    // fate 命运轮回：开局分配命运点数 → 按禀赋抽取转生身份 → 命运事件池随机遭遇
    "enabled": false,
    "alloc_points": 20,          // 命运点数（开局分配）
    "alloc_attrs": [],           // 可分配禀赋 id 列表（默认全部 attributes）
    "draw_pool": [
      // 转生身份池（≥2 个）：requires 为禀赋门槛（命中才进抽取池），
      // start_scene 指向该身份剧情线起点，effects 为身份加成
      // { "id": "fate_rich", "name": "钟鸣鼎食之子", "desc": "生于豪族，锦衣玉食",
      //   "start_scene": "s101", "requires": {"attrs": {"jiach": {"gte": 5}}},
      //   "effects": {"attrs": {"jiach": 2}} }
    ]
  },
  "perspectives": [
    // epic 多线史诗：多视角切换
    // { "id": "p_buendia", "name": "布恩迪亚家" }
  ],
  "achievements": {
    "enabled": false,
    "list": [
      // strategy 策略模拟成就：condition 同 requires 语法
      // { "id": "ach_three", "name": "三分天下", "desc": "势力值均≥60", "condition": {"attrs": {"兵力": {"gte": 60}}} }
    ]
  },
  "commentary": { "enabled": false },
  "endings": { "min": 2, "max": 5 }
};
