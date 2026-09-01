#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏文件夹初始化（阶段一任务⑥）：从模板实例化游戏文件夹。

用法:
    python tools/game_init.py --book-id novel_abc --title 测试 --game-dir games/novel_abc
"""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book-id', required=True)
    ap.add_argument('--title', required=True)
    ap.add_argument('--game-dir', required=True, help='games/<book_id> 路径')
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

    # 写 game.js 元数据骨架（后续 generate 阶段会整体覆盖）
    game = {
        'title': args.title,
        'subtitle': '',
        'author': '',
        'book_id': args.book_id,
        'mode': 'g1_narrative',
        'entry': 's001',
        'version': '0.1.0',
        'save_key': f'nwa_{args.book_id}',
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
                    'perspectives': [], 'achievements': {'enabled': False, 'list': []},
                    'commentary': {'enabled': False}, 'endings': {'min': 1, 'max': 9}},
        'characters.js': {'characters': []},
        'scenes.js': {'scenes': {}},
        'theme.js': {'name': '', 'colors': {}, 'fonts': {}},
    }
    for fname, obj in empty.items():
        var = fname.replace('.js', '').upper()
        (dst / 'data' / fname).write_text(
            f'/* {fname} —— 由 generate 阶段填充 */\nwindow.{var} = '
            + json.dumps(obj, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')

    print(f'游戏文件夹已初始化: {dst}')


if __name__ == '__main__':
    main()
