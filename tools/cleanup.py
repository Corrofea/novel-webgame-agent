#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""到期清理：删除已过期的游戏文件夹与打包文件。

依据 runtime/expiry.json 登记的时间执行本地删除（S3 对象由预签名 URL 自然失效，
如需主动删除对象请扩展 s3 后端）。

用法:
    python tools/cleanup.py            # 清理全部到期项
    python tools/cleanup.py --dry-run  # 只列出不清
"""
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    expiry = ROOT / 'runtime' / 'expiry.json'
    if not expiry.exists():
        print('无到期登记（runtime/expiry.json 不存在）')
        return
    recs = json.loads(expiry.read_text(encoding='utf-8'))
    now = datetime.now(timezone.utc)
    remaining = {}
    for book_id, rec in recs.items():
        expires = datetime.fromisoformat(rec['expires_at'])
        if now >= expires:
            game_dir = ROOT / 'games' / book_id
            artifact = Path(rec['artifact']) if not rec['artifact'].startswith('s3://') else None
            if args.dry_run:
                print(f'[过期] {book_id}（{expires.isoformat()}）→ 将删除 {game_dir} 与 {artifact}')
                continue
            if game_dir.exists():
                shutil.rmtree(game_dir)
                print(f'已删除游戏文件夹: {game_dir}')
            if artifact and artifact.exists():
                artifact.unlink()
                print(f'已删除打包文件: {artifact}')
                web_copy = ROOT / 'web' / artifact.name
                if web_copy.exists():
                    web_copy.unlink()
                    print(f'已删除发布副本: {web_copy}')
        else:
            remaining[book_id] = rec
    if not args.dry_run:
        expiry.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
