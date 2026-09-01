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
    return data, None


def validate(data: dict, game_dir: Path):
    issues = []
    game = data['game.js']
    mode = data['mode.js']
    chars = data['characters.js']
    scenes = data['scenes.js']
    theme = data['theme.js']

    # ---- game.js ----
    if not game.get('title'):
        issues.append(Issue('error', 'game.js', 'title 为空'))
    if not re.fullmatch(r'[a-z0-9_]+', game.get('book_id', '')):
        issues.append(Issue('error', 'game.js', 'book_id 必须是小写字母数字下划线'))
    if not game.get('entry'):
        issues.append(Issue('error', 'game.js', 'entry 为空'))

    # ---- mode.js ----
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
        if node.get('id') != sid:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 的 id 字段不一致: {node.get('id')}"))
        has_choices = bool(node.get('choices'))
        has_auto = bool(node.get('auto'))
        has_ending = bool(node.get('ending'))
        if not (has_choices or has_auto or has_ending):
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 没有 choices/auto/ending 任一"))
        targets = []
        for ch in node.get('choices', []):
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
            if not node['auto'].get('goto'):
                issues.append(Issue('error', 'scenes.js', f"节点 {sid} auto 缺 goto"))
            else:
                targets.append(node['auto']['goto'])
            _check_req_eff(issues, node['auto'], attr_ids, inv, mode, sid)
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

    # ---- 图分析：可达性 / 死路 / 结局数 ----
    reach = set()
    if entry in graph:
        q = deque([entry])
        while q:
            cur = q.popleft()
            if cur in reach:
                continue
            reach.add(cur)
            for t in graph.get(cur, []):
                if t not in reach:
                    q.append(t)
    for sid in node_ids:
        if sid not in reach:
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 从入口不可达（孤立节点）"))

    for sid, node in scene_map.items():
        if node.get('ending'):
            continue
        choices = [c for c in node.get('choices', []) if c.get('goto') in scene_map]
        if not choices and not node.get('auto'):
            issues.append(Issue('error', 'scenes.js', f"节点 {sid} 是死路（无选项无 auto 无 ending）"))

    n_ending = len(ending_nodes)
    if end:
        if n_ending < end.get('min', 0):
            issues.append(Issue('error', 'scenes.js', f"结局节点 {n_ending} 个，少于配置下限 {end['min']}"))
        if n_ending > end.get('max', 99):
            issues.append(Issue('warning', 'scenes.js', f"结局节点 {n_ending} 个，超过配置上限 {end['max']}"))

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
