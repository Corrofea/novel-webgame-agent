#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传游戏包并提供限时下载链接（30 分钟失效）。

后端：
  - local（默认）：复制到存档目录并打印本地路径（无外部依赖，离线可用）
  - s3：S3/R2 预签名 URL（需要 pip install boto3 + .env 配置 S3_*）

到期清理由 tools/cleanup.py 负责（读取 runtime/expiry.json）。
"""
import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def record_expiry(book_id: str, artifact: str, ttl_minutes: int):
    expiry = ROOT / 'runtime' / 'expiry.json'
    recs = {}
    if expiry.exists():
        recs = json.loads(expiry.read_text(encoding='utf-8'))
    recs[book_id] = {
        'artifact': str(artifact),
        'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat(),
    }
    expiry.parent.mkdir(parents=True, exist_ok=True)
    expiry.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding='utf-8')


def upload_local(zip_path: Path, ttl_minutes: int):
    # local 后端：zip 已在 archive/，登记到期即可
    record_expiry(zip_path.stem, str(zip_path), ttl_minutes)
    print(f'[local] 下载链接（30 分钟内有效）: {zip_path.resolve()}')
    print(f'[local] 到期时间已登记: runtime/expiry.json（由 cleanup.py 执行删除）')


def upload_s3(zip_path: Path, ttl_minutes: int):
    try:
        import boto3
    except ImportError:
        print('S3 上传需要 boto3: pip install boto3，并配置 .env 的 S3_* 变量')
        raise SystemExit(1)
    import os
    session = boto3.client(
        's3', endpoint_url=os.environ.get('S3_ENDPOINT'),
        aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
        aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
    )
    bucket = os.environ['S3_BUCKET']
    key = f'games/{zip_path.name}'
    session.upload_file(str(zip_path), bucket, key)
    url = session.generate_presigned_url(
        'get_object', Params={'Bucket': bucket, 'Key': key},
        ExpiresIn=ttl_minutes * 60)
    record_expiry(zip_path.stem, f's3://{bucket}/{key}', ttl_minutes)
    print(f'[s3] 下载链接（{ttl_minutes} 分钟内有效）:')
    print(url)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('zip_path')
    ap.add_argument('--backend', choices=['local', 's3'], default='local')
    ap.add_argument('--ttl', type=int, default=30)
    args = ap.parse_args()
    zip_path = Path(args.zip_path)
    if not zip_path.exists():
        raise SystemExit(f'文件不存在: {zip_path}')
    if args.backend == 's3':
        upload_s3(zip_path, args.ttl)
    else:
        upload_local(zip_path, args.ttl)


if __name__ == '__main__':
    main()
