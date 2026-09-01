/* ============================================================
 * novel-webgame-agent 引擎（固定代码，LLM 永不修改）
 * 读取 window.GAME / MODE / CHARACTERS / SCENES / THEME 五个数据对象，
 * 渲染可玩的互动文字游戏：选项分支、属性/好感度、物品栏、
 * 多视角切换（S4）、旁批（S4）、成就（G2）、存档（localStorage）。
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
  var CHAR_INDEX = {};
  CHARACTERS.characters.forEach(function (c) { CHAR_INDEX[c.id] = c; });

  /* ---------- 状态 ---------- */
  var state = {
    scene: null, attrs: {}, flags: {}, inventory: [], perspective: null,
    ended: false, visited: []
  };

  function initState() {
    state.scene = GAME.entry;
    state.attrs = {};
    (MODE.attributes || []).forEach(function (a) { state.attrs[a.id] = a.start || a.min || 0; });
    state.flags = {}; state.inventory = [];
    state.perspective = (MODE.perspectives && MODE.perspectives.length) ? MODE.perspectives[0].id : null;
    state.ended = false; state.visited = [];
  }

  function saveState() {
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify(state));
    } catch (e) { /* 隐私模式等，静默失败 */ }
  }
  function loadState() {
    try {
      var raw = localStorage.getItem(SAVE_KEY);
      if (!raw) return false;
      var s = JSON.parse(raw);
      if (!s || !SCENES.scenes[s.scene]) return false;
      state = s; return true;
    } catch (e) { return false; }
  }
  function clearSave() {
    // 重置必须清空与 save_key 相关的所有 key（含元数据）
    var keys = [];
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (k && k.indexOf(GAME.save_key || 'nwa_game') === 0) keys.push(k);
    }
    keys.forEach(function (k) { localStorage.removeItem(k); });
  }
  function getMeta() {
    try { return JSON.parse(localStorage.getItem(META_KEY)) || { endings: [], playthrough: 0 }; }
    catch (e) { return { endings: [], playthrough: 0 }; }
  }
  function setMeta(m) {
    try { localStorage.setItem(META_KEY, JSON.stringify(m)); } catch (e) {}
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
  function show(name) {
    Object.keys(screens).forEach(function (k) {
      screens[k].style.display = (k === name) ? 'block' : 'none';
    });
  }

  /* ---------- 封面 ---------- */
  function buildCover() {
    var s = el('div', 'screen cover', root); screens.cover = s;
    el('div', 'cover-ornament', s);
    var title = el('h1', 'cover-title', s); setText(title, GAME.title || '未命名');
    var sub = el('div', 'cover-sub', s); setText(sub, GAME.subtitle || '');
    var author = el('div', 'cover-author', s); setText(author, GAME.author ? GAME.author + ' 著' : '');
    var modeTag = el('div', 'cover-mode', s);
    setText(modeTag, '『' + (MODE.mode_name || '互动游戏') + '』');
    var start = el('button', 'btn btn-primary', s); setText(start, '开始游戏');
    start.addEventListener('click', function () {
      clearSave(); initState(); saveState(); enterScene(); unlockAudio();
    });
    var cont = el('button', 'btn', s); setText(cont, '继续游戏');
    cont.addEventListener('click', function () {
      if (!loadState()) { alert('没有可继续的存档，请点"开始游戏"'); return; }
      enterScene(); unlockAudio();
    });
    if (!localStorage.getItem(SAVE_KEY)) cont.style.display = 'none';
    var endingsBtn = el('button', 'btn', s); setText(endingsBtn, '结局收集 (' + getMeta().endings.length + '/' + (MODE.endings ? MODE.endings.max : 5) + ')');
    endingsBtn.addEventListener('click', function () { showEndings(); });
  }

  /* ---------- 结局收集 ---------- */
  function showEndings() {
    var s = el('div', 'screen endings', root); screens.endings = s;
    var h = el('h2', '', s); setText(h, '结局收集');
    var meta = getMeta();
    var list = meta.endings || [];
    if (!list.length) { var e = el('p', 'sub', s); setText(e, '尚未解锁任何结局'); }
    list.forEach(function (en) {
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

  /* ---------- 场景 ---------- */
  function buildScene() {
    var s = el('div', 'screen scene', root); screens.scene = s;
    var bgWrap = el('div', 'bg-layer', s); s.bg = bgWrap;
    var box = el('div', 'scene-box', s);
    var titleBar = el('div', 'scene-titlebar', box);
    var perspectiveBar = el('div', 'perspective-bar', box); s.perspectiveBar = perspectiveBar;
    var charRow = el('div', 'char-row', box); s.charRow = charRow;
    var narr = el('div', 'narration', box); s.narr = narr;
    var speaker = el('div', 'speaker', box); s.speaker = speaker;
    var choiceBox = el('div', 'choices', box); s.choices = choiceBox;
    var status = el('div', 'status-panel', s); s.status = status;
    var inv = el('div', 'inventory-panel', s); s.inventory = inv;
    var comment = el('div', 'commentary', s); s.commentary = comment;
    var side = el('div', 'side-panel', s);
    side.appendChild(status); side.appendChild(inv);
    var reset = el('button', 'btn btn-danger side-reset', side); setText(reset, '重置游戏');
    reset.addEventListener('click', function () {
      if (!confirm('确定重置游戏？所有进度与结局收集将清空。')) return;
      clearSave(); initState(); saveState(); location.reload();
    });
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
    renderStatus();
    renderInventory();
    renderCommentary(node);
    renderChoices(node);
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
        state.scene = ch.goto;
        enterScene();
      });
    });
    if (!choices.length && !node.ending && !node.auto) {
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
    if (en && en.title && meta.endings.indexOf(en.title) < 0) meta.endings.push(en.title);
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
    for (var i = 0; i < runs; i++) {
      initState();
      var guard = 0;
      while (!state.ended && guard < 300) {
        var node = SCENES.scenes[state.scene];
        guard++;
        if (!node) { report.push('run' + i + ': 场景不存在 ' + state.scene); break; }
        if (node.ending) { state.ended = true; break; }
        if (node.auto && evalRequires(node.auto.requires)) { state.scene = node.auto.goto; continue; }
        var cs = (node.choices || []).filter(function (c) { return evalRequires(c.requires); });
        if (!cs.length) { report.push('run' + i + ': 死路 @' + state.scene); break; }
        var pick = cs[Math.floor(Math.random() * cs.length)];
        applyEffects(pick.effects);
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
    // 应用主题
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

    if (!window.SCENES || !GAME.entry || !SCENES.scenes || !SCENES.scenes[GAME.entry]) {
      fatal('数据加载失败：请检查 data/ 下的 5 个数据文件是否齐全且格式正确。');
      return;
    }
    buildCover();
    buildScene();
    if (location.search.indexOf('selftest=1') >= 0) { selfTest(); return; }
    show('cover');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
})();
