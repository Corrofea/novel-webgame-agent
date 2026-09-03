#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏数据结构校验 + 场景图分析。

用法:
    python validate_game.py games/<书>/
    # 退出码 0=通过; 1=发现问题（--json 输出问题清单供 ReAct 修复循环使用）
"""
import argparse
import json
import re
import sys
from collections import Counter, deque
from pathlib import Path

DATA_FILES = ['game.js', 'mode.js', 'characters.js', 'scenes.js', 'theme.js']

ROOT = Path(__file__).resolve().parents[3]
THEMES_DIR = ROOT / 'templates' / 'themes'


def valid_theme_ids() -> list:
    """视觉主题白名单：templates/themes/*.json 的 id（与 CSS 风格块同源自动同步）。"""
    ids = []
    if THEMES_DIR.is_dir():
        for p in sorted(THEMES_DIR.glob('*.json')):
            try:
                t = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(t, dict) and t.get('id'):
                ids.append(t['id'])
    return ids


def strip_js_comments(text: str) -> str:
    """去掉 // 与 /* */ 注释（保留字符串内的内容）。"""
    out, i, n = [], 0, len(text)
    in_str = None
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == in_str:
                in_str = None
            i += 1; continue
        if c in '"\'':
            in_str = c; out.append(c); i += 1; continue
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            while i < n and text[i] != '\n': i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'): i += 1
            i += 2; continue
        out.append(c); i += 1
    return ''.join(out)


def remove_trailing_commas(text: str) -> str:
    return re.sub(r',(\s*[}\]])', r'\1', text)


def extract_js_object(text: str, var_name: str):
    """提取 `window.<var_name> = {...};` 中的对象（容忍注释与尾逗号）。"""
    m = re.search(r'window\.' + var_name + r'\s*=\s*', text)
    if not m:
        return None
    i = m.end()
    while i < len(text) and text[i] in ' \t\r\n':
        i += 1
    if i >= len(text) or text[i] != '{':
        return None
    depth, start, in_str = 0, i, None
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '\\': i += 2; continue
            if c == in_str: in_str = None
        elif c in '"\'':
            in_str = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                break
        i += 1
    obj_text = strip_js_comments(text[start:i + 1])
    obj_text = remove_trailing_commas(obj_text)
    return json.loads(obj_text)


class Issue:
    def __init__(self, severity, file, msg):
        self.severity = severity  # 'error' | 'warning'
        self.file = file
        self.msg = msg

    def to_dict(self):
        return {"severity": self.severity, "file": self.file, "message": self.msg}

    def __str__(self):
        return f"[{self.severity}] {self.file}: {self.msg}"


def load_game(game_dir: Path):
    data = {}
    for name in DATA_FILES:
        p = game_dir / 'data' / name
        if not p.exists():
            return None, f"缺少数据文件 data/{name}"
        try:
            var = name.replace('.js', '').upper()
            obj = extract_js_object(p.read_text(encoding='utf-8'), var)
            if obj is None:
                return None, f"data/{name} 解析失败：找不到 window.{var} = ... 或格式错误"
            data[name] = obj
        except json.JSONDecodeError as e:
            return None, f"data/{name} JSON 语法错误（第 {e.lineno} 行附近）: {e.msg}"
    # shape 容错：LLM 输出漂移（字符串代替对象）时规范化为对象，避免下游脚本崩溃；
    # 规范化的记录存进 data，由 validate() 转成可修的错误清单
    fixes = []
    for sid, node in data['scenes.js'].get('scenes', {}).items():
        if isinstance(node.get('ending'), str):
            fixes.append((sid, f"ending 应为对象（{{type/title/desc}}），收到字符串 '{node['ending'][:20]}'"))
            node['ending'] = {'type': 'neutral', 'title': node['ending'], 'desc': ''}
        if isinstance(node.get('auto'), str):
            fixes.append((sid, 'auto 应为对象（{requires/goto}），收到字符串'))
            node['auto'] = {}
        elif isinstance(node.get('auto'), list):
            # 多路分支数组（2026-09 狂人日记 s013 实录）：引擎只支持单个 auto 路由。
            # 归一为空对象，下游不崩；错误清单交给修复循环改写为带 requires 的 choices
            fixes.append((sid, f"auto 是数组（{len(node['auto'])} 个分支）——引擎只支持单个 "
                               'auto 路由；多路分支请改写为带 requires 的 choices'))
            node['auto'] = {}
        if isinstance(node.get('riddle'), str):
            fixes.append((sid, 'riddle 应为对象（{question/answer/goto}），收到字符串'))
            node['riddle'] = {}
        for i, ch in enumerate(node.get('choices', [])):
            if isinstance(ch, str):
                fixes.append((sid, f"选项 {i} 应为对象（{{text/goto}}），收到字符串"))
                node['choices'][i] = {'text': ch, 'goto': ''}
    if fixes:
        data['scenes.js']['_shape_fixes'] = fixes
    return data, None


ENGINE_CONTRACT = [
    # (缺失时报错/警告, 错误文案, 检查函数)
    # 结局收集契约：引擎必须能记录并展示已解锁结局（历史 bug：clearSave 连 meta 一起清、
    # 封面计数不刷新、file:// 下 localStorage 静默失效、endings 存字符串渲染时按对象访问）
    ('error', 'endGame 函数缺失（结局画面无法进入）', lambda js: 'function endGame' in js),
    ('error', '结局记录缺失：找不到 meta.endings 的写入', lambda js: 'meta.endings' in js and 'push' in js),
    ('error', '结局元数据读写函数缺失（getMeta/setMeta）', lambda js: 'function getMeta' in js and 'function setMeta' in js),
    ('error', 'clearSave 必须只清存档（曾按前缀清空把结局收集 meta 也删了）',
     lambda js: 'function clearSave() { storage.removeItem(SAVE_KEY); }' in js),
    ('error', 'clearAllData 缺失（重置游戏无法清空含 meta 的全部数据）',
     lambda js: 'function clearAllData()' in js),
    ('error', 'storage 降级封装缺失（file:// 下存档/结局收集会静默失效）', lambda js: 'var storage = (function' in js and 'available:' in js),
    ('warning', '封面结局计数刷新函数缺失（回封面不更新已解锁数）', lambda js: 'refreshCoverMeta' in js),
    # theme 2.0：配色与质感唯一权威 = theme.css 的 body.style-<id> 块；theme.js
    # 只存 {"name":"<id>"}，engine boot() 挂 class。colors 非空 = 旧产物 → 变量注入分支
    ('error', 'theme 2.0 风格类挂载缺失（boot 未挂 style-<name>）', lambda js: "'style-' +" in js),
    ('error', '旧产物兼容注入路径缺失（THEME.colors 非空时 setProperty 注入）',
     lambda js: 'THEME.colors' in js and 'setProperty' in js),
]


def check_theme(data: dict, game_dir: Path, issues: list):
    """theme 2.0 产物校验：theme.js 只许 {"name": 白名单 id}，视觉权威在 theme.css。

    旧产物（colors 非空）走引擎兼容注入，只告警（回填前合法）；新产物校验
    name 合法且 theme.css 里有对应 body.style-<id> 块（防改坏共享样式）。
    """
    css = None
    cp = game_dir / 'engine' / 'theme.css'
    if cp.exists():
        try:
            css = cp.read_text(encoding='utf-8')
        except OSError:
            css = None
    css = css or ''
    if ':root' not in css:
        issues.append(Issue('error', 'engine/theme.css',
                            'theme.css 缺失或缺少 :root（配色与质感权威缺失）'))
    theme = data['theme.js'] if isinstance(data['theme.js'], dict) else {}
    if theme.get('colors'):
        issues.append(Issue('warning', 'theme.js',
                            'theme.js 含 colors 字段（旧版视觉漂移残留；仅回填后启用 CSS 主题）'))
        return
    name = theme.get('name')
    if not name:
        issues.append(Issue('error', 'theme.js',
                            'theme.js 缺少 name（视觉主题开关，应形如 {"name": "modern"}）'))
        return
    ids = valid_theme_ids()
    if name not in ids:
        issues.append(Issue('error', 'theme.js',
                            f"主题名 {name} 不在视觉主题白名单（可选: {ids}）"))
        return
    if css and f'body.style-{name}' not in css:
        issues.append(Issue('error', 'engine/theme.css',
                            f'theme.css 缺 body.style-{name} 风格块（主题未实现）'))


def check_engine(game_dir: Path, issues: list):
    """引擎契约检查：结局收集功能必须完整（引擎是共享代码，改坏影响所有游戏）。"""
    ep = game_dir / 'engine' / 'engine.js'
    if not ep.exists():
        issues.append(Issue('error', 'engine.js', 'engine/engine.js 缺失（无法渲染游戏）'))
        return
    js = ep.read_text(encoding='utf-8')
    for severity, msg, cond in ENGINE_CONTRACT:
        if not cond(js):
            issues.append(Issue(severity, 'engine.js', msg))


def validate(data: dict, game_dir: Path):
    issues = []
    game = data['game.js']
    mode = data['mode.js']
    chars = data['characters.js']
    scenes = data['scenes.js']
    theme = data['theme.js']

    # ---- 引擎契约（结局收集等共享能力） ----
    check_engine(game_dir, issues)

    # ---- theme 2.0（theme.js 视觉开关契约） ----
    check_theme(data, game_dir, issues)

    # load_game 规范化过的 shape 漂移 → 转成错误清单（修复循环据此修数据源）
    for sid, msg in scenes.pop('_shape_fixes', []):
        issues.append(Issue('error', 'scenes.js',
                            f"节点 {sid} {msg}（已临时规范化，请按契约修复）"))

    # ---- game.js ----
    if not game.get('title'):
        issues.append(Issue('error', 'game.js', 'title 为空'))
    if not re.fullmatch(r'[a-z0-9_]+', game.get('book_id', '')):
        issues.append(Issue('error', 'game.js', 'book_id 必须是小写字母数字下划线'))
    if not game.get('entry'):
        issues.append(Issue('error', 'game.js', 'entry 为空'))

    # ---- mode.js ----
    surv = mode.get('survival') or {}
    fate = mode.get('fate') or {}
    attr_ids = [a['id'] for a in mode.get('attributes', [])]
    if len(attr_ids) != len(set(attr_ids)):
        issues.append(Issue('error', 'mode.js', 'attributes 存在重复 id'))
    for a in mode.get('attributes', []):
        if a.get('min', 0) > a.get('max', 100):
            issues.append(Issue('error', 'mode.js', f"属性 {a['id']} min>max"))
    inv = mode.get('inventory', {})
    if inv.get('enabled'):
        inv_ids = [it['id'] for it in inv.get('items', [])]
        if len(inv_ids) != len(set(inv_ids)):
            issues.append(Issue('error', 'mode.js', 'inventory items 存在重复 id'))
    end = mode.get('endings', {})
    if end and (end.get('min', 0) > end.get('max', 99)):
        issues.append(Issue('error', 'mode.js', 'endings min>max'))

    # ---- characters.js ----
    char_ids = [c['id'] for c in chars.get('characters', [])]
    if len(char_ids) != len(set(char_ids)):
        issues.append(Issue('error', 'characters.js', '角色 id 重复'))
    for c in chars.get('characters', []):
        if not c.get('name'):
            issues.append(Issue('error', 'characters.js', f"角色 {c['id']} 缺 name"))
        if 'portrait' in c and c['portrait'] and not c['portrait'].startswith('assets/'):
            issues.append(Issue('warning', 'characters.js', f"角色 {c['id']} 立绘路径应为相对路径 assets/..."))

    # ---- scenes.js ----
    scene_map = scenes.get('scenes', {})
    node_ids = list(scene_map.keys())
    if len(node_ids) != len(set(node_ids)):
        issues.append(Issue('error', 'scenes.js', '场景节点 id 重复'))
    entry = game.get('entry')
    if entry and entry not in scene_map:
        issues.append(Issue('error', 'scenes.js', f"入口场景 {entry} 不存在"))

    ending_nodes = []
    graph = {}
    for sid, node in scene_map.items():
        if not isinstance(node, dict):
            issues.append(Issue('error', 'scenes.js',
                                f"节点 {sid} 不是对象（{type(node).__name__}），跳过该节点分析"))
            continue
        if node.get('id') != sid:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 的 id 字段不一致: {node.get('id')}"))
        has_choices = bool(node.get('choices'))
        has_auto = bool(node.get('auto'))
        has_ending = bool(node.get('ending'))
        has_riddle = bool(node.get('riddle'))
        is_entry = sid == entry
        if not (has_choices or has_auto or has_ending or has_riddle) \
                and not (fate.get('enabled') and is_entry):
            # fate 模式：入口节点是转生抽取锚点，玩家不实际经过，允许无出边
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 没有 choices/auto/ending/riddle 任一"))
        targets = []
        if has_riddle:
            r = node['riddle']
            if not r.get('question', '').strip():
                issues.append(Issue('error', 'scenes.js', f"riddle 节点 {sid} 缺 question 谜面"))
            ans = r.get('answer', '').strip()
            if not ans:
                issues.append(Issue('error', 'scenes.js', f"riddle 节点 {sid} 缺 answer 答案"))
            elif not (2 <= len(ans) <= 8):
                issues.append(Issue('warning', 'scenes.js', f"riddle 节点 {sid} answer 应为 2~8 字（当前 {len(ans)} 字）"))
            goto = r.get('goto')
            if not goto:
                issues.append(Issue('error', 'scenes.js', f"riddle 节点 {sid} 缺 goto（解出后的去向）"))
            else:
                targets.append(goto)
            hints = r.get('hints', [])
            if hints and len(hints) > 3:
                issues.append(Issue('warning', 'scenes.js', f"riddle 节点 {sid} 提示超过 3 条（阶梯提示即可）"))
        for ch in node.get('choices', []):
            if not isinstance(ch, dict):
                issues.append(Issue('error', 'scenes.js', f"节点 {sid} 存在非对象选项（{type(ch).__name__}）"))
                continue
            if not ch.get('text', '').strip():
                issues.append(Issue('error', 'scenes.js', f"节点 {sid} 存在空选项文案"))
            if len(ch.get('text', '')) > 60:
                issues.append(Issue('warning', 'scenes.js', f"节点 {sid} 选项文案过长(>60字)"))
            goto = ch.get('goto')
            if not goto:
                issues.append(Issue('error', 'scenes.js', f"节点 {sid} 有选项缺 goto"))
            else:
                targets.append(goto)
            _check_req_eff(issues, ch, attr_ids, inv, mode, sid)
        if has_auto:
            a = node['auto']
            if isinstance(a, dict):
                if not a.get('goto'):
                    issues.append(Issue('error', 'scenes.js', f"节点 {sid} auto 缺 goto"))
                else:
                    targets.append(a['goto'])
                    _check_req_eff(issues, a, attr_ids, inv, mode, sid)
            elif isinstance(a, list) and a:
                # 引擎只支持单个 auto 路由（dict）。LLM 偶发把多路属性门槛分支写成 auto 数组
                # （2026-09 狂人日记 s013 实录）——报可修复错误；分支 goto 仍入图，可达性分析不误伤
                issues.append(Issue('error', 'scenes.js',
                                    f"节点 {sid} 的 auto 是数组（{len(a)} 个分支）——引擎只支持单个 "
                                    'auto 路由；多路分支请改写为带 requires 的 choices'))
                for br in a:
                    if isinstance(br, dict) and br.get('goto'):
                        targets.append(br['goto'])
            else:
                issues.append(Issue('error', 'scenes.js',
                                    f"节点 {sid} 的 auto 类型错误（{type(a).__name__}）"))
        if has_ending:
            if not node['ending'].get('title'):
                issues.append(Issue('error', 'scenes.js', f"节点 {sid} ending 缺 title"))
            if node['ending'].get('type') not in ('good', 'bad', 'neutral'):
                issues.append(Issue('warning', 'scenes.js', f"节点 {sid} ending.type 应为 good/bad/neutral"))
            ending_nodes.append(sid)
        if len(node.get('choices', [])) > 4:
            issues.append(Issue('warning', 'scenes.js', f"节点 {sid} 选项超过 4 个（分支膨胀）"))
        if node.get('bg') and not node['bg'].startswith('assets/'):
            issues.append(Issue('warning', 'scenes.js', f"节点 {sid} bg 应为相对路径 assets/..."))
        if 'perspective' in node and mode.get('perspectives') and node['perspective'] not in [p['id'] for p in mode['perspectives']]:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 引用未定义的视角 {node['perspective']}"))
        graph[sid] = targets

    for sid, targets in graph.items():
        for t in targets:
            if t not in scene_map:
                issues.append(Issue('error', 'scenes.js', f"节点 {sid} 指向不存在的节点 {t}"))

    # ---- fate 命运事件池（scenes.js 顶层 events；不参与场景图分析） ----
    events = scenes.get('events') or []
    event_ids = [e['id'] for e in events if e.get('id')]
    if len(event_ids) != len(set(event_ids)):
        issues.append(Issue('error', 'scenes.js', 'events 事件池存在重复 id'))
    for e in events:
        if not e.get('id') or not e.get('title'):
            issues.append(Issue('error', 'scenes.js', f'事件池有事件缺 id/title: {e}'))
        if not e.get('narration', '').strip():
            issues.append(Issue('error', 'scenes.js', f"事件 {e.get('id')} 缺 narration"))
        if not e.get('goto'):
            issues.append(Issue('error', 'scenes.js', f"事件 {e.get('id')} 缺 goto"))
        elif e['goto'] not in scene_map:
            issues.append(Issue('error', 'scenes.js', f"事件 {e.get('id')} 的 goto 指向不存在的节点 {e['goto']}"))
        _check_req_eff(issues, e, attr_ids, inv, mode, f"event.{e.get('id')}")
    if events and not fate.get('enabled'):
        issues.append(Issue('warning', 'scenes.js',
                            f'存在 {len(events)} 个命运事件但 fate 未启用（事件池不会生效）'))
    if fate.get('enabled') and not any(n.get('fate_event') for n in scene_map.values()):
        issues.append(Issue('warning', 'scenes.js',
                            'fate 模式：没有任何 fate_event 标记节点（事件池无处抽取）'))

    # ---- 图分析：可达性 / 死路 / 结局数 ----
    # 多源 BFS：入口 + survival 死亡场景 + fate 各转生身份起点（引擎跳转绕过入口）
    def bfs(starts):
        reach = set()
        q = deque([s for s in starts if s in graph])
        while q:
            cur = q.popleft()
            if cur in reach:
                continue
            reach.add(cur)
            for t in graph.get(cur, []):
                if t not in reach:
                    q.append(t)
        return reach

    fate_starts = [d['start_scene'] for d in fate.get('draw_pool', []) if d.get('start_scene')] \
        if fate.get('enabled') else []
    reach = bfs([entry] + ([surv.get('death_scene')] if surv.get('death_scene') else []) + fate_starts)
    for sid in node_ids:
        if sid not in reach:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 从入口不可达（孤立节点）"))

    for sid, node in scene_map.items():
        if node.get('ending'):
            continue
        if fate.get('enabled') and sid == entry:
            continue  # fate 入口锚点允许无出路
        choices = [c for c in node.get('choices', []) if c.get('goto') in scene_map]
        if not choices and not node.get('auto') and not node.get('riddle'):
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 是死路（无选项无 auto 无 riddle 无 ending）"))

    n_ending = len(ending_nodes)
    if end:
        if n_ending < end.get('min', 0):
            issues.append(Issue('error', 'scenes.js', f"结局节点 {n_ending} 个，少于配置下限 {end['min']}"))
        if n_ending > end.get('max', 99):
            issues.append(Issue('warning', 'scenes.js', f"结局节点 {n_ending} 个，超过配置上限 {end['max']}"))

    # ---- 章节叙事（narrative）结构校验（warning 级：保证叙事可控，不阻断） ----
    chapter_cfg = mode.get('chapter_progress') or {}
    if chapter_cfg.get('enabled'):
        chapters = [str(c) for c in chapter_cfg.get('chapters', [])]
        for sid, node in scene_map.items():
            if not node.get('chapter'):
                issues.append(Issue('warning', 'scenes.js',
                                    f"章节模式：节点 {sid} 缺 chapter 字段（进度条无法定位）"))
        anchors = [sid for sid, n in scene_map.items() if n.get('chapter_end')]
        if not anchors:
            issues.append(Issue('warning', 'scenes.js', '章节模式：没有任何 chapter_end 锚点节点（分支无法汇聚）'))
        elif chapters:
            for ch in chapters:
                if not any(n.get('chapter_end') and str(n.get('chapter')) == ch
                           for n in scene_map.values()):
                    issues.append(Issue('warning', 'scenes.js',
                                        f'章节模式：章节「{ch}」缺少 chapter_end 锚点'))
        # 跨章回跳检查：goto 目标应在本章或下一章锚点/结局
        for sid, node in scene_map.items():
            ch = node.get('chapter')
            if not ch or node.get('ending') or node.get('chapter_end'):
                continue
            for t in graph.get(sid, []):
                t_node = scene_map.get(t, {})
                if t_node.get('ending'):
                    continue
                if t_node.get('chapter') and str(t_node['chapter']) != str(ch) \
                        and not t_node.get('chapter_end'):
                    issues.append(Issue('warning', 'scenes.js',
                                        f'章节模式：节点 {sid} 跳到其他章节节点 {t}'
                                        f'（应经本章 chapter_end 锚点汇聚后再推进）'))

    # ---- survival 模式：死亡场景/分配属性配置校验（error 级：破坏核心玩法） ----
    if surv.get('enabled'):
        hp_attr = surv.get('hp_attr', 'hp')
        if hp_attr not in attr_ids:
            issues.append(Issue('error', 'mode.js', f'survival.hp_attr 属性 {hp_attr} 未在 attributes 中定义'))
        for aid in surv.get('alloc_attrs', []):
            if aid not in attr_ids:
                issues.append(Issue('error', 'mode.js', f'survival.alloc_attrs 属性 {aid} 未在 attributes 中定义'))
        death = surv.get('death_scene')
        if not death:
            issues.append(Issue('error', 'mode.js', 'survival.death_scene 未配置（死亡路径缺失）'))
        elif death not in scene_map:
            issues.append(Issue('error', 'scenes.js', f'survival.death_scene {death} 不存在'))
        elif not scene_map[death].get('ending'):
            issues.append(Issue('error', 'scenes.js', f'survival.death_scene {death} 必须是结局节点'))

    # ---- fate 模式：转生身份池配置校验 ----
    if fate.get('enabled'):
        for aid in fate.get('alloc_attrs', []):
            if aid not in attr_ids:
                issues.append(Issue('error', 'mode.js', f'fate.alloc_attrs 属性 {aid} 未在 attributes 中定义'))
        pool = fate.get('draw_pool', [])
        if len(pool) < 2:
            issues.append(Issue('warning', 'mode.js',
                                f'fate.draw_pool 只有 {len(pool)} 个转生身份（随机抽取无意义，至少 2 个）'))
        for d in pool:
            if not d.get('id') or not d.get('name'):
                issues.append(Issue('error', 'mode.js', f'fate.draw_pool 有身份缺 id/name: {d}'))
            start = d.get('start_scene')
            if not start:
                issues.append(Issue('error', 'mode.js', f"fate 身份 {d.get('id')} 缺 start_scene"))
            elif start not in scene_map:
                issues.append(Issue('error', 'scenes.js', f"fate 身份 {d.get('id')} 的 start_scene {start} 不存在"))
            _check_req_eff(issues, d, attr_ids, inv, mode, f"fate.{d.get('id')}")

    # ---- attrs 门槛可达性（warning 级） ----
    # 启发式：全图该属性正增量总和 < 门槛 → 任何路径都达不到（必要条件）。
    # 完整求解需状态空间搜索（scene×attrs），此处只拦明显不可达，不误伤可达图。
    # fate 模式跳过：门槛靠开局分配点数达成（非增量），启发式必然误报。
    if not fate.get('enabled'):
        attr_gates = {}
        for sid, node in scene_map.items():
            for ch in list(node.get('choices', [])) + ([node['auto']] if node.get('auto') else []):
                for k, cond in (ch.get('requires') or {}).get('attrs', {}).items():
                    for op in ('gte', 'has'):
                        if cond.get(op) is not None:
                            attr_gates.setdefault(k, []).append(cond[op])
        for k, thresholds in attr_gates.items():
            total_gain = 0
            for sid, node in scene_map.items():
                for ch in node.get('choices', []):
                    eff = (ch.get('effects') or {}).get('attrs', {}).get(k)
                    if isinstance(eff, (int, float)) and eff > 0:
                        total_gain += eff
            for t in thresholds:
                if total_gain < t:
                    issues.append(Issue('warning', 'scenes.js',
                                        f'属性 {k} 门槛 {t} 无法达成：全图正增量累计仅 {total_gain}'
                                        f'（可达路径上永远触发不了该条件）'))
                    break

    # ---- 素材引用存在性（warning 级：素材可后补） ----
    for sid, node in scene_map.items():
        for ref in [node.get('bg'), node.get('bgm')]:
            if ref and not (game_dir / ref).exists():
                issues.append(Issue('warning', 'scenes.js', f"节点 {sid} 引用素材缺失: {ref}"))
    for c in chars.get('characters', []):
        if c.get('portrait') and not (game_dir / c['portrait']).exists():
            issues.append(Issue('warning', 'characters.js', f"角色 {c['id']} 立绘缺失: {c['portrait']}"))

    return issues


def _check_req_eff(issues, item, attr_ids, inv, mode, sid):
    req = item.get('requires') or {}
    for k in req.get('attrs', {}):
        if k not in attr_ids:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} requires.attrs 引用了未定义的属性 {k}"))
    for k in req.get('inventory', {}):
        if inv.get('enabled') and k not in [it['id'] for it in inv.get('items', [])]:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} requires.inventory 引用了未定义的物品 {k}"))
    eff = item.get('effects') or {}
    for k in eff.get('attrs', {}):
        if k not in attr_ids:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} effects.attrs 引用了未定义的属性 {k}"))
    if 'inventory' in eff:
        for a in eff['inventory'].get('add', []) + eff['inventory'].get('remove', []):
            if inv.get('enabled') and a not in [it['id'] for it in inv.get('items', [])]:
                issues.append(Issue('error', 'scenes.js', f"节点 {sid} effects.inventory 引用了未定义的物品 {a}"))
    # auto 节点不允许带 effects（引擎不执行 auto 的效果）
    if 'auto' in item and item['auto'].get('effects'):
        issues.append(Issue('error', 'scenes.js', f"节点 {sid} auto 不允许带 effects（引擎忽略 auto 效果）"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game_dir', help='游戏文件夹路径')
    ap.add_argument('--json', action='store_true', help='输出 JSON 问题清单（供 ReAct 修复）')
    args = ap.parse_args()

    game_dir = Path(args.game_dir)
    data, fatal = load_game(game_dir)
    if fatal:
        issues = [Issue('error', 'scenes.js', fatal)]
    else:
        issues = validate(data, game_dir)
    errors = [i for i in issues if i.severity == 'error']
    warnings = [i for i in issues if i.severity == 'warning']

    if args.json:
        print(json.dumps({
            "ok": not errors,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issues": [i.to_dict() for i in issues],
        }, ensure_ascii=False, indent=2))
    else:
        for i in issues:
            print(i)
        print(f"\n结果: {'通过 ✓' if not errors else f'{len(errors)} 个错误'}"
              + (f'，{len(warnings)} 个警告' if warnings else ''))
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
