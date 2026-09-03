#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏文件夹初始化（阶段一任务⑥）：从模板实例化游戏文件夹。

用法:
    python tools/game_init.py --book-id novel_abc --run-id novel_abc_20260901-1030 \
        --title 测试 --game-dir games/novel_abc_20260901-1030

--run-id 是本次运行唯一 id（games/ 游戏库中一个文件夹 = 一次调用）。
存档 key 用 run_id 前缀，保证同书多次运行的游戏存档互不串扰。
"""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book-id', required=True)
    ap.add_argument('--run-id', required=True, help='本次运行唯一 id（游戏库隔离 + 存档 key）')
    ap.add_argument('--title', required=True)
    ap.add_argument('--game-dir', required=True, help='games/<run_id> 路径')
    args = ap.parse_args()

    src = ROOT / 'templates' / 'game_folder'
    dst = Path(args.game_dir)
    if dst.exists():
        print(f'游戏文件夹已存在: {dst}（跳过复制）')
    else:
        shutil.copytree(src, dst)
        # 引擎代码从项目 engine/ 复制（单一来源）
        (dst / 'engine').mkdir(exist_ok=True)
        for f in ('engine.js', 'theme.css'):
            shutil.copy2(ROOT / 'engine' / f, dst / 'engine' / f)

    import json

    # theme.js 只存视觉风格 id（配色与质感全在 engine/theme.css 的 style-<id> 块）。
    # 来源 = detect 产物 runtime/<run_id>/mode.json 的 theme_id（detect 先于本阶段执行）；
    # mode.json 缺失或 theme_id 非法时告警并回落 modern，保证手工初始化不崩。
    theme_id = 'modern'
    mode_file = ROOT / 'runtime' / args.run_id / 'mode.json'
    if mode_file.exists():
        try:
            cand = json.loads(mode_file.read_text(encoding='utf-8')).get('theme_id')
            if cand:
                theme_id = cand
        except (OSError, ValueError):
            pass
    theme_ok = (ROOT / 'templates' / 'themes' / f'{theme_id}.json').exists()
    if not theme_ok:
        print(f'[theme] mode.json 主题 {theme_id!r} 不在 templates/themes/ 中，回落 modern')
        theme_id = 'modern'

    # 写 game.js 元数据骨架（后续 generate 阶段会整体覆盖）
    game = {
        'title': args.title,
        'subtitle': '',
        'author': '',
        'book_id': args.book_id,
        'run_id': args.run_id,
        'mode': 'classic',
        'entry': 's001',
        'version': '0.1.0',
        'save_key': f'nwa_{args.run_id}',
    }
    data_file = dst / 'data' / 'game.js'
    data_file.write_text(
        f'/* game.js —— 游戏元数据 */\nwindow.GAME = {json.dumps(game, ensure_ascii=False, indent=2)};\n',
        encoding='utf-8')

    # 其余 4 个数据文件用空骨架覆盖：模板里的示例数据只作 schema 文档，
    # 必须清空，否则 generate 阶段的 patch 合并语义会把示例节点残留在游戏里
    empty = {
        'mode.js': {'mode_id': '', 'mode_name': '', 'mechanics': [], 'attributes': [],
                    'inventory': {'enabled': False, 'items': []}, 'panels': [],
                    'chapter_progress': {'enabled': False, 'chapters': []},
                    'galgame': {'enabled': False},
                    'survival': {'enabled': False, 'alloc_points': 30, 'alloc_attrs': [],
                                 'hp_attr': 'hp', 'death_threshold': 0, 'death_scene': ''},
                    'riddle': {'enabled': False},
                    'fate': {'enabled': False, 'alloc_points': 20, 'alloc_attrs': [],
                             'draw_pool': []},
                    'perspectives': [], 'achievements': {'enabled': False, 'list': []},
                    'commentary': {'enabled': False}, 'endings': {'min': 1, 'max': 9}},
        'characters.js': {'characters': []},
        'scenes.js': {'scenes': {}, 'events': []},
        'theme.js': {'name': theme_id},
    }
    if theme_id != 'modern':
        print(f'[theme] 主题: {theme_id}（来自 runtime/{args.run_id}/mode.json）')
    for fname, obj in empty.items():
        var = fname.replace('.js', '').upper()
        (dst / 'data' / fname).write_text(
            f'/* {fname} —— 由 generate 阶段填充 */\nwindow.{var} = '
            + json.dumps(obj, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')

    print(f'游戏文件夹已初始化: {dst}')


if __name__ == '__main__':
    main()
