/* mode.js —— 模式机制配置（决定引擎开哪些功能；由游戏模式模板的 runtime 节生成） */
window.MODE = {
  "mode_id": "g1_narrative",
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
  "perspectives": [
    // S4 多线史诗：多视角切换
    // { "id": "p_buendia", "name": "布恩迪亚家" }
  ],
  "achievements": {
    "enabled": false,
    "list": [
      // G2 策略模拟成就：condition 同 requires 语法
      // { "id": "ach_three", "name": "三分天下", "desc": "势力值均≥60", "condition": {"attrs": {"兵力": {"gte": 60}}} }
    ]
  },
  "commentary": { "enabled": false },
  "endings": { "min": 2, "max": 5 }
};
