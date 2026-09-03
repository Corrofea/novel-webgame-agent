/* ============================================================
 * novel-webgame-agent 引擎（固定代码，LLM 永不修改）
 * 读取 window.GAME / MODE / CHARACTERS / SCENES / THEME 五个数据对象，
 * 渲染可玩的互动文字游戏：选项分支、属性/好感度、物品栏、
 * 多视角切换（epic）、旁批（epic）、成就（strategy）、存档（localStorage）。
 *
 * 约定：
 *  - 所有数据通过 textContent 渲染（防 XSS），禁止 innerHTML 拼接用户数据
 *  - 所有素材引用为相对 index.html 的路径，缺失时优雅降级为占位色块
 *  - 存档 key 一律带 save_key 前缀，重置时清空全部相关 key（ClickMacondo Bug #1）
 * ============================================================ */
(function () {
  'use strict';

  var GAME = window.GAME || {};
  var MODE = window.MODE || { mechanics: [], attributes: [], inventory: { enabled: false, items: [] } };
  var CHARACTERS = window.CHARACTERS || { characters: [] };
  var SCENES = window.SCENES || { scenes: {} };
  var THEME = window.THEME || { colors: {} };

  var SAVE_KEY = (GAME.save_key || 'nwa_game') + '_v1';
  var META_KEY = (GAME.save_key || 'nwa_game') + '_meta_v1';

  /* ---------- 存储封装（localStorage 降级） ----------
   * Safari 直接打开本地文件（file://）时 localStorage 抛 SecurityError，
   * 静默吞掉会让存档与结局收集全部失效且无提示。统一走 storage 抽象：
   * localStorage 可用则跨会话持久；不可用则降级为内存 Map（本页面内有效），
   * 并在封面显示提示条。 */
  var storage = (function () {
    var mem = {};
    var ok = true;
    try {
      var probe = '__nwa_probe__';
      window.localStorage.setItem(probe, '1');
      window.localStorage.removeItem(probe);
    } catch (e) { ok = false; }
    return {
      available: ok,
      getItem: function (k) {
        if (ok) { try { return window.localStorage.getItem(k); } catch (e) { return mem[k] || null; } }
        return mem[k] || null;
      },
      setItem: function (k, v) {
        if (ok) { try { window.localStorage.setItem(k, v); return; } catch (e) {} }
        mem[k] = v;
      },
      removeItem: function (k) {
        if (ok) { try { window.localStorage.removeItem(k); return; } catch (e) {} }
        delete mem[k];
      },
      keys: function () {
        if (ok) {
          try {
            var out = [];
            for (var i = 0; i < window.localStorage.length; i++) out.push(window.localStorage.key(i));
            return out;
          } catch (e) {}
        }
        return Object.keys(mem);
      }
    };
  })();
  var CHAR_INDEX = {};
  CHARACTERS.characters.forEach(function (c) { CHAR_INDEX[c.id] = c; });

  /* ---------- 状态 ---------- */
  var state = {
    scene: null, attrs: {}, flags: {}, inventory: [], perspective: null,
    ended: false, visited: []
  };

  function initState(alloc) {
    state.scene = GAME.entry;
    state.attrs = {};
    (MODE.attributes || []).forEach(function (a) {
      // survival 模式：玩家分配值优先（未分配项用模板 start）
      state.attrs[a.id] = (alloc && alloc[a.id] != null) ? alloc[a.id] : (a.start || a.min || 0);
    });
    state.flags = {}; state.inventory = []; state.fate_used = [];
    state.perspective = (MODE.perspectives && MODE.perspectives.length) ? MODE.perspectives[0].id : null;
    state.ended = false; state.visited = [];
  }

  /* ---------- survival：生命值死亡判定 ---------- */
  function survivalCfg() { return MODE.survival && MODE.survival.enabled ? MODE.survival : null; }
  function fateCfg() { return MODE.fate && MODE.fate.enabled ? MODE.fate : null; }
  // 初始点数分配：survival 与 fate 共用同一套分配界面
  function allocCfg() { return survivalCfg() || fateCfg(); }

  function hpAtOrBelowZero() {
    var cfg = survivalCfg();
    if (!cfg) return false;
    var hp = state.attrs[cfg.hp_attr || 'hp'];
    return hp != null && hp <= (cfg.death_threshold != null ? cfg.death_threshold : 0);
  }

  function checkDeath() {
    var cfg = survivalCfg();
    if (!cfg || !hpAtOrBelowZero()) return false;
    if (cfg.death_scene && SCENES.scenes[cfg.death_scene]) {
      state.scene = cfg.death_scene;   // 死亡场景（bad ending 节点）
      enterScene();
    } else {
      flashHint('生命值耗尽，你倒在了路上……');
      setTimeout(function () { show('cover'); }, 1400);
    }
    return true;
  }

  function saveState() {
    storage.setItem(SAVE_KEY, JSON.stringify(state));
  }
  function loadState() {
    try {
      var raw = storage.getItem(SAVE_KEY);
      if (!raw) return false;
      var s = JSON.parse(raw);
      if (!s || !SCENES.scenes[s.scene]) return false;
      state = s; return true;
    } catch (e) { return false; }
  }
  // 开始新游戏：只清存档，保留结局收集（曾按前缀清空全部 key，把 meta 也删了）
  function clearSave() { storage.removeItem(SAVE_KEY); }
  // 重置游戏：清空本游戏全部数据（存档 + 结局收集）
  function clearAllData() {
    var prefix = GAME.save_key || 'nwa_game';
    storage.keys().forEach(function (k) { if (k.indexOf(prefix) === 0) storage.removeItem(k); });
  }
  function getMeta() {
    try { return JSON.parse(storage.getItem(META_KEY)) || { endings: [], playthrough: 0 }; }
    catch (e) { return { endings: [], playthrough: 0 }; }
  }
  function setMeta(m) {
    try { storage.setItem(META_KEY, JSON.stringify(m)); } catch (e) {}
  }

  /* ---------- 条件与效果 ---------- */
  function evalRequires(req) {
    if (!req) return true;
    if (req.attrs) for (var k in req.attrs) {
      var v = req.attrs[k];
      var cur = state.attrs[k] || 0;
      if (v.gte != null && !(cur >= v.gte)) return false;
      if (v.lte != null && !(cur <= v.lte)) return false;
      if (v.eq != null && !(cur === v.eq)) return false;
      if (v.has != null && !(cur >= v.has)) return false;
    }
    if (req.flags) for (var f in req.flags) {
      if (state.flags[f] !== req.flags[f]) return false;
    }
    if (req.inventory) for (var it in req.inventory) {
      var need = req.inventory[it];
      var have = state.inventory.filter(function (x) { return x === it; }).length;
      if (have < need) return false;
    }
    if (req.perspective && state.perspective !== req.perspective) return false;
    return true;
  }

  function applyEffects(effects) {
    if (!effects) return [];
    var gained = [];
    if (effects.attrs) for (var k in effects.attrs) {
      var delta = effects.attrs[k];
      var def = attrDef(k);
      var cur = state.attrs[k] || 0;
      var next = cur + delta;
      if (def) { if (def.max != null) next = Math.min(def.max, next); if (def.min != null) next = Math.max(def.min, next); }
      state.attrs[k] = next;
    }
    if (effects.flags) for (var f in effects.flags) { state.flags[f] = effects.flags[f]; }
    if (effects.inventory) {
      (effects.inventory.add || []).forEach(function (id) { state.inventory.push(id); gained.push(id); });
      (effects.inventory.remove || []).forEach(function (id) {
        var i = state.inventory.indexOf(id); if (i >= 0) state.inventory.splice(i, 1);
      });
    }
    return gained;
  }

  function attrDef(id) {
    var found = null;
    (MODE.attributes || []).forEach(function (a) { if (a.id === id) found = a; });
    return found;
  }

  /* ---------- DOM 工具（textContent 防 XSS） ---------- */
  var el = function (tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  };
  function clearNode(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function setText(n, t) { n.textContent = t; }

  /* ---------- 屏幕 ---------- */
  var root = document.getElementById('app');
  var screens = {};
  var coverEndingsBtn = null;   // 封面结局计数按钮（回封面时刷新文本）
  function refreshCoverMeta() {
    if (coverEndingsBtn) {
      setText(coverEndingsBtn, '结局收集 (' + getMeta().endings.length + '/' + (MODE.endings ? MODE.endings.max : 5) + ')');
    }
  }
  function show(name) {
    Object.keys(screens).forEach(function (k) {
      screens[k].style.display = (k === name) ? 'block' : 'none';
    });
    if (name === 'cover') refreshCoverMeta();   // 每次回封面刷新结局计数
  }

  /* ---------- 封面 ---------- */
  function buildCover() {
    var s = el('div', 'screen cover', root); screens.cover = s;
    // 封面主视觉（可选 assets/cover.webp，illustrate 阶段生成；缺失自动隐藏，
    // 旧产物零影响——质感仍由主题 CSS 纹理兜底）
    var art = el('div', 'cover-art', s);
    var artImg = document.createElement('img');
    // WebP 是默认转码格式；.png 仅兼容历史产物，两者都缺才隐藏
    artImg.src = 'assets/cover.webp';
    artImg.onerror = function () {
      if (artImg.src.indexOf('cover.webp') >= 0) { artImg.src = 'assets/cover.png'; }
      else { art.style.display = 'none'; }
    };
    art.appendChild(artImg);
    el('div', 'cover-ornament', s);
    var title = el('h1', 'cover-title', s); setText(title, GAME.title || '未命名');
    var sub = el('div', 'cover-sub', s); setText(sub, GAME.subtitle || '');
    var author = el('div', 'cover-author', s); setText(author, GAME.author ? GAME.author + ' 著' : '');
    var modeTag = el('div', 'cover-mode', s);
    setText(modeTag, '『' + (MODE.mode_name || '互动游戏') + '』');
    if (!storage.available) {
      var warn = el('div', 'cover-warn', s);
      setText(warn, '⚠ 当前环境无法持久保存（浏览器禁止了本地存储，如 Safari 直接打开本地文件），' +
                    '进度与结局收集仅本次页面内有效。建议通过本地服务器访问：' +
                    'python3 -m http.server 后打开 http://localhost:8000/games/...');
    }
    var start = el('button', 'btn btn-primary', s); setText(start, '开始游戏');
    start.addEventListener('click', function () {
      clearSave();  // 只清存档，保留结局收集
      if (allocCfg()) { showAllocation(); return; }  // survival/fate：先分配初始数值
      initState(); saveState(); enterScene(); unlockAudio();
    });
    var cont = el('button', 'btn', s); setText(cont, '继续游戏');
    cont.addEventListener('click', function () {
      if (!loadState()) { alert('没有可继续的存档，请点"开始游戏"'); return; }
      enterScene(); unlockAudio();
    });
    if (!storage.getItem(SAVE_KEY)) cont.style.display = 'none';
    coverEndingsBtn = el('button', 'btn', s);
    refreshCoverMeta();
    coverEndingsBtn.addEventListener('click', function () { showEndings(); });
  }

  /* ---------- 结局收集 ---------- */
  function showEndings() {
    var s = el('div', 'screen endings', root); screens.endings = s;
    var h = el('h2', '', s); setText(h, '结局收集');
    var meta = getMeta();
    var list = meta.endings || [];
    if (!list.length) { var e = el('p', 'sub', s); setText(e, '尚未解锁任何结局'); }
    list.forEach(function (en) {
      // 兼容旧版数据（曾存 title 字符串）
      if (typeof en === 'string') en = { type: 'neutral', title: en, desc: '' };
      var card = el('div', 'ending-card', s);
      var t = el('div', 'ending-type ' + en.type, card);
      setText(t, ['good', 'bad', 'neutral'].indexOf(en.type) >= 0 ? (en.type === 'good' ? '好结局' : en.type === 'bad' ? '坏结局' : '普通结局') : '结局');
      var n = el('div', '', card); setText(n, en.title || '');
      if (en.desc) { var d = el('div', 'sub', card); setText(d, en.desc); }
    });
    var back = el('button', 'btn', s); setText(back, '返回');
    back.addEventListener('click', function () { show('cover'); });
    show('endings');
  }

  /* ---------- survival 初始点数分配 ---------- */
  var alloc = {};  // attr_id -> 玩家分配值
  var allocView = null;

  function buildAllocation() {
    var s = el('div', 'screen allocation', root); screens.allocation = s;
    allocView = s;
    el('div', 'cover-ornament', s);
    var h = el('h2', 'alloc-title', s); setText(h, '分配初始数值');
    var sub = el('div', 'alloc-sub', s);
    setText(sub, fateCfg()
      ? '命运的齿轮开始转动：把命运点数分配到你的先天禀赋上，禀赋将决定你能转生为何人。'
      : '你即将踏上旅程，把有限的能力点分配到各项生存资源上。');
    var remain = el('div', 'alloc-remain', s); s.remain = remain;
    var list = el('div', 'alloc-list', s); s.list = list;
    var row = el('div', 'alloc-actions', s);
    var go = el('button', 'btn btn-primary', row); setText(go, '确认出发');
    go.addEventListener('click', function () {
      initState(alloc); saveState();
      if (fateCfg()) { showFateDraw(); return; }  // fate：分配完进入转生抽取
      enterScene(); unlockAudio();
    });
    var back = el('button', 'btn', row); setText(back, '返回封面');
    back.addEventListener('click', function () { alloc = {}; show('cover'); });
  }

  function showAllocation() {
    alloc = {};
    renderAllocation();
    show('allocation');
  }

  function renderAllocation() {
    if (!allocView) return;
    var cfg = allocCfg();
    var defs = {};
    (MODE.attributes || []).forEach(function (a) { defs[a.id] = a; });
    var ids = (cfg.alloc_attrs && cfg.alloc_attrs.length) ? cfg.alloc_attrs
      : (MODE.attributes || []).map(function (a) { return a.id; });
    var total = cfg.alloc_points || 0;
    var used = 0;
    ids.forEach(function (id) { used += alloc[id] || 0; });

    var remainEl = allocView.remain;
    clearNode(remainEl);
    setText(remainEl, '剩余点数：' + (total - used) + ' / ' + total);

    var list = allocView.list;
    clearNode(list);
    ids.forEach(function (id) {
      var def = defs[id];
      var min = def ? (def.min != null ? def.min : 0) : 0;
      var max = def ? (def.max != null ? def.max : 100) : 100;
      var row = el('div', 'alloc-row', list);
      var lab = el('span', 'alloc-label', row); setText(lab, def ? def.label : id);
      var minus = el('button', 'btn btn-small', row); setText(minus, '−');
      var val = el('span', 'alloc-val', row); setText(val, String(alloc[id] || min));
      var plus = el('button', 'btn btn-small', row); setText(plus, '+');
      minus.addEventListener('click', function () {
        var cur = alloc[id] || min;
        if (cur > min) { alloc[id] = cur - 1; renderAllocation(); }
      });
      plus.addEventListener('click', function () {
        var cur = alloc[id] || min;
        if (used < total && cur < max) { alloc[id] = cur + 1; renderAllocation(); }
      });
    });
  }

  /* ---------- fate 转生抽取 ---------- */
  function buildFateDraw() {
    var s = el('div', 'screen fate-draw', root); screens.fate = s;
    el('div', 'cover-ornament', s);
    var h = el('h2', 'alloc-title', s); setText(h, '命运的转盘');
    var sub = el('div', 'alloc-sub', s);
    setText(sub, '命运在你面前展开，你的禀赋点亮了其中几条路。');
    var list = el('div', 'alloc-list', s); s.fateList = list;
    var row = el('div', 'alloc-actions', s);
    var go = el('button', 'btn btn-primary', row); setText(go, '抽取命运');
    go.addEventListener('click', drawFatePerson);
  }

  function showFateDraw() {
    var list = screens.fate.fateList;
    clearNode(list);
    // 禀赋命中（满足 requires）的身份列出；兜底：全不满足时列出全部
    var pool = (fateCfg().draw_pool || []).filter(function (d) { return evalRequires(d.requires); });
    if (!pool.length) pool = fateCfg().draw_pool || [];
    pool.forEach(function (d) {
      var card = el('div', 'fate-identity', list);
      var n = el('div', 'fate-identity-name', card); setText(n, d.name || d.id);
      if (d.desc) { var de = el('div', 'fate-identity-desc', card); setText(de, d.desc); }
    });
    show('fate');
  }

  function drawFatePerson() {
    var pool = (fateCfg().draw_pool || []).filter(function (d) { return evalRequires(d.requires); });
    if (!pool.length) pool = fateCfg().draw_pool || [];
    if (!pool.length) { flashHint('没有可转生的身份（draw_pool 为空）'); return; }
    var pick = pool[Math.floor(Math.random() * pool.length)];
    var list = screens.fate.fateList;
    clearNode(list);
    var card = el('div', 'fate-identity picked', list);
    var n = el('div', 'fate-identity-name', card);
    setText(n, '你转生为：' + (pick.name || pick.id));
    if (pick.desc) { var de = el('div', 'fate-identity-desc', card); setText(de, pick.desc); }
    var row = el('div', 'alloc-actions', list);
    var go = el('button', 'btn btn-primary', row); setText(go, '开始这一世');
    go.addEventListener('click', function () {
      applyEffects(pick.effects);
      state.scene = pick.start_scene;
      saveState(); enterScene(); unlockAudio();
    });
  }

  /* ---------- 命运事件抽取（fate_event 节点触发，从预生成事件池随机抽） ---------- */
  function drawFateEvent() {
    var pool = (SCENES.events || []).filter(function (e) {
      return state.fate_used.indexOf(e.id) < 0 && evalRequires(e.requires);
    });
    if (!pool.length) { flashHint('命运的风声停止了……没有可抽取的命运事件'); return; }
    var ev = pool[Math.floor(Math.random() * pool.length)];
    state.fate_used.push(ev.id);
    var s = el('div', 'screen fate-event', root); screens.fateEvent = s;
    var h = el('h2', '', s); setText(h, ev.title || '命运事件');
    var p = el('div', 'narration', s); setText(p, ev.narration || '');
    var btn = el('button', 'btn btn-primary', s); setText(btn, '继续');
    btn.addEventListener('click', function () {
      applyEffects(ev.effects);
      state.scene = ev.goto;
      saveState(); enterScene();
    });
    show('fateEvent');
  }

  /* ---------- 场景 ---------- */
  function buildScene() {
    var s = el('div', 'screen scene', root); screens.scene = s;
    var bgWrap = el('div', 'bg-layer', s); s.bg = bgWrap;
    var box = el('div', 'scene-box', s);
    var titleBar = el('div', 'scene-titlebar', box);
    var progress = el('div', 'chapter-progress', box); s.progress = progress;
    var perspectiveBar = el('div', 'perspective-bar', box); s.perspectiveBar = perspectiveBar;
    var charRow = el('div', 'char-row', box); s.charRow = charRow;
    var narr = el('div', 'narration', box); s.narr = narr;
    var speaker = el('div', 'speaker', box); s.speaker = speaker;
    var divider = el('div', 'chapter-divider', box); s.divider = divider;
    var choiceBox = el('div', 'choices', box); s.choices = choiceBox;
    var status = el('div', 'status-panel', s); s.status = status;
    var inv = el('div', 'inventory-panel', s); s.inventory = inv;
    var comment = el('div', 'commentary', s); s.commentary = comment;
    var side = el('div', 'side-panel', s);
    side.appendChild(status); side.appendChild(inv);
    var reset = el('button', 'btn btn-danger side-reset', side); setText(reset, '重置游戏');
    reset.addEventListener('click', function () {
      if (!confirm('确定重置游戏？所有进度与结局收集将清空。')) return;
      clearAllData(); initState(); saveState(); location.reload();
    });
  }

  /* ---------- 章节进度条（narrative 模式） ---------- */
  function chapterInfo(node) {
    var cfg = MODE.chapter_progress;
    if (!cfg || !cfg.enabled || !node.chapter) return null;
    var chapters = cfg.chapters || [];
    var idx = -1;
    for (var i = 0; i < chapters.length; i++) {
      if (String(chapters[i]) === String(node.chapter) || String(i + 1) === String(node.chapter)) { idx = i; break; }
    }
    if (idx < 0 && chapters.length) {
      // 找不到精确匹配时按顺序推定：当前是第几个不同章节
      var seen = [];
      for (var sid in SCENES.scenes) {
        var n = SCENES.scenes[sid];
        if (n.chapter && seen.indexOf(String(n.chapter)) < 0) seen.push(String(n.chapter));
      }
      idx = seen.indexOf(String(node.chapter));
    }
    if (idx < 0) return { label: String(node.chapter), pos: 0, total: chapters.length || 1, known: false };
    return { label: chapters[idx] || String(node.chapter), pos: idx + 1, total: chapters.length, known: true };
  }

  function renderProgress(node) {
    var pr = screens.scene.progress;
    clearNode(pr);
    pr.style.display = 'none';
    var info = chapterInfo(node);
    if (!info) return;
    pr.style.display = 'block';
    var bar = el('div', 'chapter-progress-bar', pr);
    var fill = el('div', 'chapter-progress-fill', bar);
    fill.style.width = (info.total > 0 ? info.pos / info.total * 100 : 0) + '%';
    var lab = el('div', 'chapter-progress-label', pr);
    setText(lab, (info.known ? '第 ' + info.pos + '/' + info.total + ' 章 · ' : '') + info.label);
  }

  function renderDivider(node) {
    var dv = screens.scene.divider;
    clearNode(dv);
    dv.style.display = 'none';
    if (!node.chapter_end) return;
    dv.style.display = 'block';
    var t = el('div', '', dv);
    setText(t, '◆ ' + (node.chapter ? String(node.chapter) : '本章') + ' · 完 ◆');
  }

  function perspectiveLabel() {
    if (!state.perspective) return '';
    var p = null;
    (MODE.perspectives || []).forEach(function (x) { if (x.id === state.perspective) p = x; });
    return p ? p.name : state.perspective;
  }

  function renderPerspectiveBar() {
    var bar = screens.scene.perspectiveBar;
    clearNode(bar);
    if (!(MODE.perspectives || []).length) return;
    el('span', 'persp-label', bar).textContent = '视角：';
    (MODE.perspectives || []).forEach(function (p) {
      var b = el('button', 'btn btn-small' + (p.id === state.perspective ? ' active' : ''), bar);
      setText(b, p.name);
      b.addEventListener('click', function () {
        state.perspective = p.id;
        saveState(); renderPerspectiveBar(); renderStatus();
      });
    });
  }

  function renderStatus() {
    var st = screens.scene.status;
    clearNode(st);
    var showAttrs = (MODE.attributes || []).filter(function (a) { return a.visible !== false; });
    if (!showAttrs.length) { st.style.display = 'none'; return; }
    st.style.display = 'block';
    var h = el('div', 'panel-title', st); setText(h, '属性');
    showAttrs.forEach(function (a) {
      var row = el('div', 'attr-row', st);
      var lab = el('span', 'attr-label', row); setText(lab, a.label || a.id);
      var bar = el('div', 'attr-bar', row);
      var fill = el('div', 'attr-fill', bar);
      var max = a.max || 100, min = a.min || 0;
      var v = Math.max(min, Math.min(max, state.attrs[a.id] || min));
      fill.style.width = (max === min ? 100 : ((v - min) / (max - min) * 100)) + '%';
      var val = el('span', 'attr-val', row); setText(val, String(v));
    });
    var vis = perspectiveLabel();
    if (vis) { var pv = el('div', 'attr-row', st); setText(pv, '当前视角：' + vis); }
  }

  function renderInventory() {
    var iv = screens.scene.inventory;
    clearNode(iv);
    if (!MODE.inventory || !MODE.inventory.enabled) { iv.style.display = 'none'; return; }
    iv.style.display = 'block';
    var items = {};
    (MODE.inventory.items || []).forEach(function (it) { items[it.id] = it; });
    var h = el('div', 'panel-title', iv); setText(h, '物品 (' + state.inventory.length + ')');
    if (!state.inventory.length) { var e = el('div', 'sub', iv); setText(e, '空空如也'); }
    state.inventory.forEach(function (id) {
      var it = items[id];
      var row = el('div', 'inv-item', iv);
      setText(row, (it ? it.name : id) + (it && it.desc ? ' — ' + it.desc : ''));
    });
  }

  function renderCommentary(node) {
    var cm = screens.scene.commentary;
    clearNode(cm);
    if (!MODE.commentary || !MODE.commentary.enabled) { cm.style.display = 'none'; return; }
    if (!node.commentary || !node.commentary.length) { cm.style.display = 'none'; return; }
    cm.style.display = 'block';
    var h = el('div', 'panel-title', cm); setText(h, '旁批');
    node.commentary.forEach(function (c) {
      var card = el('div', 'comment-card', cm);
      if (c.choice) { var a = el('div', 'comment-choice', card); setText(a, '你的选择：' + c.choice); }
      if (c.original) { var b = el('div', 'comment-original', card); setText(b, '原文：' + c.original); }
      if (c.note) { var d = el('div', 'comment-note', card); setText(d, c.note); }
    });
  }

  function renderBg(node) {
    var bg = screens.scene.bg;
    clearNode(bg);
    if (node.bg) {
      var img = document.createElement('img');
      img.className = 'bg-img';
      img.src = node.bg;
      img.onerror = function () { // 素材缺失：降级为占位色块
        clearNode(bg); bg.classList.add('bg-placeholder');
      };
      img.onload = function () { bg.classList.remove('bg-placeholder'); };
      bg.appendChild(img);
    } else {
      bg.classList.add('bg-placeholder');
    }
  }

  function renderChars(node) {
    var row = screens.scene.charRow;
    clearNode(row);
    if (!node.characters || !node.characters.length) return;
    node.characters.forEach(function (cid) {
      var c = CHAR_INDEX[cid];
      var chip = el('div', 'char-chip', row);
      var nm = el('span', '', chip); setText(nm, c ? (c.name || cid) : cid);
      if (c && c.portrait) {
        var im = document.createElement('img');
        im.className = 'char-portrait';
        im.src = c.portrait;
        im.onerror = function () { im.style.display = 'none'; };
        chip.insertBefore(im, chip.firstChild);
      }
    });
  }

  /* ---------- riddle：文字谜题（方块遮罩 + 输入揭示） ---------- */
  function renderRiddle(node) {
    var box = screens.scene.choices;
    clearNode(box);
    var r = node.riddle;
    if (!r || !r.answer) return;
    var q = el('div', 'riddle-question', box);
    setText(q, r.question || '这是哪一位？');
    var revealed = {};
    var answer = String(r.answer);
    var tiles = el('div', 'riddle-tiles', box);
    var tileEls = [];
    answer.split('').forEach(function (ch, i) {
      var t = el('span', 'riddle-tile', tiles);
      setText(t, '□');
      tileEls.push({ el: t, idx: i, ch: ch });
    });
    var row = el('div', 'riddle-input-row', box);
    var input = document.createElement('input');
    input.className = 'riddle-input';
    input.maxLength = 1;   // 逐字输入（支持英文等多字节提示由 value 判空）
    input.placeholder = '输入一个字';
    row.appendChild(input);
    var submit = el('button', 'btn btn-primary', row); setText(submit, '揭字');
    var hintBtn = el('button', 'btn', row); setText(hintBtn, '提示');
    var hintBox = el('div', 'riddle-hint', box);

    function refresh() {
      var done = true;
      tileEls.forEach(function (t) {
        if (revealed[t.idx]) {
          t.el.classList.add('revealed');
          setText(t.el, t.ch);
        }
        if (!revealed[t.idx]) done = false;
      });
      if (done) {
        var ok = el('div', 'riddle-solved', box);
        setText(ok, '✔ 破解成功：' + answer);
        var cont = el('button', 'btn btn-primary', box); setText(cont, '继续');
        cont.addEventListener('click', function () {
          if (r.goto) { state.scene = r.goto; enterScene(); }
        });
      }
    }

    submit.addEventListener('click', function () {
      var v = String(input.value || '').trim();
      if (!v) return;
      // 逐字匹配：输入的字与答案任一字相同 → 揭示所有匹配位置
      var hit = false;
      answer.split('').forEach(function (ch, i) {
        if (!revealed[i] && ch === v) { revealed[i] = true; hit = true; }
      });
      if (!hit) flashHint('没有匹配的字');
      input.value = '';
      refresh();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') submit.click();
    });

    var hints = r.hints || [];
    var hi = 0;
    hintBtn.addEventListener('click', function () {
      if (hi < hints.length) {
        setText(hintBox, '提示' + (hi + 1) + '/' + hints.length + '：' + hints[hi]);
        hi++;
      } else {
        setText(hintBox, '没有更多提示了。');
      }
    });
    refresh();
  }

  function narrate(text) {
    var box = screens.scene.narr;
    clearNode(box);
    var paras = String(text || '').split('\n');
    paras.forEach(function (p) {
      if (!p.trim()) return;
      var d = el('div', 'para', box);
      setText(d, p.trim());
    });
  }

  function enterScene() {
    var node = SCENES.scenes[state.scene];
    if (!node) { fatal('场景不存在: ' + state.scene); return; }
    state.visited.push(state.scene);
    if (state.visited.length > 400) { fatal('疑似场景循环（访问节点过多）'); return; }

    show('scene');
    // 结局节点：直接进入结局画面
    if (node.ending) { endGame(node); return; }
    var sp = screens.scene.speaker;
    setText(sp, node.speaker ? (node.speaker + '：') : '');
    narrate(node.narration || '');
    renderBg(node);
    renderChars(node);
    renderPerspectiveBar();
    renderProgress(node);
    renderDivider(node);
    renderStatus();
    renderInventory();
    renderCommentary(node);
    if (node.riddle) { renderRiddle(node); } else { renderChoices(node); }
    saveState();
    playBgm(node);
    if (node.auto && evalRequires(node.auto.requires)) {
      setTimeout(function () { state.scene = node.auto.goto; enterScene(); }, 400);
    }
  }

  function renderChoices(node) {
    var box = screens.scene.choices;
    clearNode(box);
    var choices = node.choices || [];
    choices.forEach(function (ch) {
      var ok = evalRequires(ch.requires);
      var b = el('button', 'btn choice' + (ok ? '' : ' locked'), box);
      setText(b, ch.text);
      if (!ok) {
        var why = describeRequires(ch.requires);
        b.title = why;
        b.addEventListener('click', function () { flashHint(why); });
        return;
      }
      b.addEventListener('click', function () {
        var gained = applyEffects(ch.effects);
        if (gained.length) flashHint('获得物品：' + gained.map(function (g) {
          var it = null;
          (MODE.inventory.items || []).forEach(function (x) { if (x.id === g) it = x; });
          return it ? it.name : g;
        }).join('、'));
        checkAchievements();
        if (checkDeath()) return;  // survival：hp 归零 → 死亡结局（已跳转）
        state.scene = ch.goto;
        enterScene();
      });
    });
    if (node.fate_event && fateCfg()) {
      var fateBtn = el('button', 'btn btn-primary choice', box);
      setText(fateBtn, '⚡ 抽取命运事件');
      fateBtn.addEventListener('click', drawFateEvent);
    }
    if (!choices.length && !node.ending && !node.auto && !node.riddle) {
      var dead = el('div', 'error-box', box);
      setText(dead, '⚠ 此处没有任何选项（数据错误：死路节点）');
    }
  }

  function describeRequires(req) {
    if (!req) return '';
    var parts = [];
    if (req.attrs) for (var k in req.attrs) {
      var d = attrDef(k);
      var v = req.attrs[k];
      var label = d ? d.label : k;
      if (v.gte != null) parts.push(label + ' ≥ ' + v.gte);
      if (v.has != null) parts.push('拥有' + label + ' ' + v.has);
    }
    if (req.flags) for (var f in req.flags) parts.push('需要条件:' + f);
    if (req.inventory) for (var it in req.inventory) {
      var name = it;
      (MODE.inventory.items || []).forEach(function (x) { if (x.id === it) name = x.name; });
      parts.push('需要物品:' + name + ' ×' + req.inventory[it]);
    }
    return parts.length ? ('未满足条件：' + parts.join('，')) : '';
  }

  function flashHint(msg) {
    var h = el('div', 'hint', root);
    setText(h, msg);
    setTimeout(function () { if (h.parentNode) h.parentNode.removeChild(h); }, 1800);
  }

  function checkAchievements() {
    if (!MODE.achievements || !MODE.achievements.enabled) return;
    var meta = getMeta();
    var got = meta.achievements || [];
    (MODE.achievements.list || []).forEach(function (a) {
      if (got.indexOf(a.id) >= 0) return;
      if (evalRequires(a.condition)) {
        got.push(a.id);
        flashHint('成就解锁：『' + a.name + '』');
      }
    });
    meta.achievements = got;
    setMeta(meta);
  }

  function playBgm(node) {
    if (!window._audioUnlocked) return;
    if (!node.bgm) { if (window._bgm) window._bgm.pause(); return; }
    if (window._bgm && window._bgm.src && window._bgm.src.endsWith(encodeURI(node.bgm))) return;
    var a = new Audio(node.bgm);
    a.loop = true;
    a.volume = 0.5;
    a.onerror = function () { /* 音频缺失：静默跳过 */ };
    a.play().catch(function () {});
    window._bgm = a;
  }
  function unlockAudio() {
    window._audioUnlocked = true;
    if (window._bgm) window._bgm.play().catch(function () {});
  }

  /* ---------- 结局 ---------- */
  function endGame(node) {
    state.ended = true;
    var en = node.ending;
    var meta = getMeta();
    meta.playthrough = (meta.playthrough || 0) + 1;
    if (en && en.title) {
      // 存储对象 {type, title, desc}（曾存 title 字符串，收集页按对象渲染 → 空卡片）
      var dup = meta.endings.some(function (x) {
        var t = (typeof x === 'object') ? x.title : x;
        return t === en.title;
      });
      if (!dup) meta.endings.push({ type: en.type || 'neutral', title: en.title, desc: en.desc || '' });
    }
    setMeta(meta);
    var s = el('div', 'screen ending', root); screens.ending = s;
    var t = el('div', 'ending-type ' + (en && en.type || 'neutral'), s);
    setText(t, (en && en.type || '') === 'good' ? '好结局' : (en && en.type || '') === 'bad' ? '坏结局' : '结局');
    var h = el('h2', '', s); setText(h, (en && en.title) || '终');
    if (en && en.desc) { var d = el('div', 'ending-desc', s); setText(d, en.desc); }
    var meta2 = getMeta();
    var info = el('div', 'sub', s);
    setText(info, '第 ' + meta2.playthrough + ' 周目 · 已解锁结局 ' + meta2.endings.length + ' 个');
    var again = el('button', 'btn btn-primary', s); setText(again, '再来一次');
    again.addEventListener('click', function () { clearSave(); initState(); saveState(); enterScene(); });
    var back = el('button', 'btn', s); setText(back, '返回封面');
    back.addEventListener('click', function () { show('cover'); });
    show('ending');
  }

  /* ---------- 致命错误显示（数据加载失败时可见） ---------- */
  function fatal(msg) {
    var s = el('div', 'screen error', root); screens.error = s;
    var h = el('h2', '', s); setText(h, '⚠ 游戏数据错误');
    var p = el('p', '', s); setText(p, msg);
    show('error');
  }

  /* ---------- 自检模式：?selftest=1 自动随机游玩，报告运行时错误 ---------- */
  function selfTest() {
    var report = [];
    var runs = 25, steps = 0;
    // fate：入口是抽取锚点（空壳），起点直接取各转生身份起点（模拟抽取）
    var starts = fateCfg()
      ? (fateCfg().draw_pool || []).map(function (d) { return d.start_scene; })
      : [GAME.entry];
    if (!starts.length) starts = [GAME.entry];
    for (var i = 0; i < runs; i++) {
      initState();
      state.scene = starts[Math.floor(Math.random() * starts.length)];
      var guard = 0;
      while (!state.ended && guard < 300) {
        var node = SCENES.scenes[state.scene];
        guard++;
        if (!node) { report.push('run' + i + ': 场景不存在 ' + state.scene); break; }
        if (node.ending) { state.ended = true; break; }
        if (node.auto && evalRequires(node.auto.requires)) { state.scene = node.auto.goto; continue; }
        if (node.riddle && node.riddle.goto) { state.scene = node.riddle.goto; continue; }
        var cs = (node.choices || []).filter(function (c) { return evalRequires(c.requires); });
        if (!cs.length) { report.push('run' + i + ': 死路 @' + state.scene); break; }
        var pick = cs[Math.floor(Math.random() * cs.length)];
        applyEffects(pick.effects);
        if (hpAtOrBelowZero()) {   // survival 死亡路径：跳死亡场景（ending 节点）
          var cfg = survivalCfg();
          if (cfg && cfg.death_scene && SCENES.scenes[cfg.death_scene]) {
            state.scene = cfg.death_scene;
          }
          continue;
        }
        state.scene = pick.goto;
      }
      if (guard >= 300) report.push('run' + i + ': 疑似循环');
      steps += guard;
    }
    var s = el('div', 'screen selftest', root); screens.selftest = s;
    var h = el('h2', '', s); setText(h, '引擎自检结果');
    var p = el('p', '', s);
    setText(p, '随机游玩 ' + runs + ' 次，平均 ' + Math.round(steps / runs) + ' 步/次，错误 ' + report.length + ' 条');
    var box = el('pre', 'selftest-log', s);
    setText(box, report.length ? report.join('\n') : '无运行时错误 ✓');
    return report.length === 0;
  }

  /* ---------- 启动 ---------- */
  function boot() {
    // 模式类：CSS 按 mode_id 定制排版（galgame 对话框 / survival 加点等）
    var galgameMode = MODE.mode_id === 'galgame';
    // 预览钩子（调试用，不进任何产物路径）：?galgame=1 以 galgame 排版预览任意游戏
    if ((location.search.match(/[?&]galgame=1/) || [])[1]) galgameMode = true;
    document.body.classList.add('mode-' + (galgameMode ? 'galgame' : (MODE.mode_id || 'default')));
    // 视觉风格：theme.js 只存 {"name":"<id>"}，配色与质感全在 theme.css 的
    // body.style-<name> 块。预览钩子：?theme=<id> 临时换肤。
    // 旧产物兼容：theme.js 含非空 colors = generate 时代遗留 → 走原注入路径
    // （只覆盖 9 个旧变量，观感不变，等回填启用新视觉）。
    var qTheme = (location.search.match(/[?&]theme=([a-z0-9_-]+)/) || [])[1];
    var styleName = qTheme || (THEME.name ? String(THEME.name) : 'default');
    var legacy = !qTheme && THEME.colors && Object.keys(THEME.colors).length > 0;
    if (legacy) {
      var vars = {
        '--bg': THEME.colors.bg || '#f5f5f5',
        '--panel': THEME.colors.panel || '#ffffff',
        '--accent': THEME.colors.accent || '#2196f3',
        '--accent-light': THEME.colors.accent_light || '#90caf9',
        '--text': THEME.colors.text || '#222222',
        '--sub': THEME.colors.sub || '#888888',
        '--border': THEME.colors.border || '#dddddd',
        '--font-title': THEME.fonts && THEME.fonts.title || 'serif',
        '--font-body': THEME.fonts && THEME.fonts.body || 'sans-serif'
      };
      for (var k in vars) document.documentElement.style.setProperty(k, vars[k]);
    } else {
      document.body.classList.add('style-' + styleName);
    }

    if (!window.SCENES || !GAME.entry || !SCENES.scenes || !SCENES.scenes[GAME.entry]) {
      fatal('数据加载失败：请检查 data/ 下的 5 个数据文件是否齐全且格式正确。');
      return;
    }
    buildCover();
    buildScene();
    buildAllocation();
    buildFateDraw();
    if (location.search.indexOf('selftest=1') >= 0) { selfTest(); return; }
    show('cover');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
})();
