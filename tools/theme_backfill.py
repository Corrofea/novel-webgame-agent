#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""theme 2.0 存量游戏回填（一次性，幂等可重跑）。

背景：theme 2.0 之前，theme.js 由 generate 阶段 LLM 从模板拷入 colors/fonts
（自创色漂移）；配色与质感的唯一权威现在 = engine/theme.css 的 style-<id> 块，
theme.js 只存 {"name": "<id>"}。旧产物必须回填，否则 engine 的 legacy 分支
（colors 非空 → CSS 变量注入）会一直生效、新视觉永不出现。

对 games/*/ 逐个：
  1) 从项目 engine/ 重拷 engine.js + theme.css（单一来源，同 game_init）——
     旧游戏的 engine 拷贝没有 style 类逻辑与风格块，不拷则回填无效；
  2) 主题 id 解析降级链：
       runtime/<目录名>/mode.json 的 theme_id（真实 detect 决定，最优先）
       → theme.js 现有 name ∈ 12 个白名单 id 直接采用
       → 旧版中文展示名别名表
       → 回落 modern + 告警
  3) 重写 data/theme.js 为 {"name": <resolved>}——
     只拷引擎不够：旧 colors 字段会激活 legacy 注入分支，class 被跳过。

archive/*.zip 不回填。用法: python tools/theme_backfill.py [--dry-run]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_FILES = ('engine.js', 'theme.css')
THEME_IDS = {p.stem for p in (ROOT / 'templates' / 'themes').glob('*.json')}

# 旧版主题模板的中文展示名 → 主题 id（theme 2.0 之前 theme.js 的 name 是中文名）
ALIASES = {
    '现代简洁': 'modern', '暗黑悬疑': 'noir', '古风': 'ancient',
    '轻小说清新': 'light', '科幻霓虹': 'scifi', '西幻羊皮纸': 'western',
}


def read_theme(game_dir: Path) -> dict:
    p = game_dir / 'data' / 'theme.js'
    if not p.exists():
        return {}
    text = p.read_text(encoding='utf-8')
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def resolve_theme_id(game_dir: Path) -> str:
    """解析降级链：runtime mode.json → 现有 name ∈ 白名单 → 中文别名 → modern。"""
    run_id = game_dir.name
    mode_file = ROOT / 'runtime' / run_id / 'mode.json'
    if mode_file.exists():
        try:
            tid = json.loads(mode_file.read_text(encoding='utf-8')).get('theme_id')
            if tid in THEME_IDS:
                return tid, 'runtime/mode.json'
        except (OSError, ValueError):
            pass
    old = read_theme(game_dir)
    name = old.get('name')
    if name in THEME_IDS:
        return name, 'theme.js name ∈ 白名单'
    if name in ALIASES:
        return ALIASES[name], f'中文别名表（{name!r}）'
    return 'modern', f'回落 modern（无可用主题信号: name={name!r}）'


def backfill(game_dir: Path, dry_run: bool) -> str:
    tid, source = resolve_theme_id(game_dir)
    if dry_run:
        return f'{game_dir.name}: [dry] {tid}（{source}）'
    # 1) 重拷引擎（旧拷贝无 style 逻辑）
    for f in ENGINE_FILES:
        shutil.copy2(ROOT / 'engine' / f, game_dir / 'engine' / f)
    # 2) 重写 theme.js（去 colors/fonts，旧字段会激活 legacy 注入分支）
    p = game_dir / 'data' / 'theme.js'
    p.write_text(
        '/* theme.js —— 视觉风格开关（theme 2.0 回填）\n'
        ' * 配色与质感全部由 engine/theme.css 的 body.style-<id> 块决定。\n'
        f' * 来源: {source} */\n'
        f'window.THEME = {json.dumps({"name": tid}, ensure_ascii=False, indent=2)};\n',
        encoding='utf-8')
    return f'{game_dir.name}: {tid}（{source}）'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只解析不写盘')
    args = ap.parse_args()
    game_dirs = sorted(p for p in (ROOT / 'games').glob('*/') if p.is_dir())
    if not game_dirs:
        print('games/ 下没有游戏目录')
        return
    for g in game_dirs:
        print(backfill(g, args.dry_run))
    print(f'\n共 {len(game_dirs)} 个游戏目录'
          + ('（dry-run，未写盘）' if args.dry_run else '，回填完成'))


if __name__ == '__main__':
    main()
