/* game.js —— 游戏元数据（全局唯一入口信息） */
window.GAME = {
  "title": "书名",                 // 封面标题
  "subtitle": "副标题（可选）",
  "author": "作者（可选）",
  "book_id": "唯一英文ID",          // 用于存档 key 与文件名，只用小写字母数字下划线
  "mode": "g1_narrative",          // 模式 id，须与 mode.js 一致
  "entry": "s001",                 // 入口场景 id，必须存在于 scenes.js
  "version": "0.1.0",
  "save_key": "nwa_<book_id>"      // 存档前缀（引擎据此生成 localStorage key）
};
