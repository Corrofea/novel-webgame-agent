#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包：把游戏文件夹打成 zip 存到项目存档目录。

用法:
    python tools/package.py games/<book_id> [--archive archive]
"""
import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def package(game_dir: Path, archive_dir: Path) -> Path:
    zip_path = archive_dir / f'{game_dir.name}.zip'
    archive_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(game_dir.rglob('*')):
            if f.is_file() and f.name != '.gitkeep':
                zf.write(f, f.relative_to(game_dir))
    print(f'已打包: {zip_path}')
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game_dir')
    ap.add_argument('--archive', default=str(ROOT / 'archive'))
    args = ap.parse_args()
    package(Path(args.game_dir), Path(args.archive))


if __name__ == '__main__':
    main()
