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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'core'))
from utils import load_dotenv  # noqa: E402


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


def upload_local(zip_path: Path, ttl_minutes: int, web_dir=None, base_url=''):
    """local 后端：zip 已在 archive/；配了 web_dir 时拷贝到静态发布目录。

    base_url 非空 → 打印真实 HTTP 下载链接（服务器上由 nginx/caddy 等把
    web_dir 作为静态站点托管；本地开发无 base_url 时打印文件路径兜底）。
    """
    record_expiry(zip_path.stem, str(zip_path), ttl_minutes)
    if web_dir:
        wd = Path(web_dir)
        wd.mkdir(parents=True, exist_ok=True)
        dst = wd / zip_path.name
        shutil.copy2(zip_path, dst)
        if base_url:
            link = f'{base_url.rstrip("/")}/{zip_path.name}'
            print(f'[local→web] 下载链接（{ttl_minutes} 分钟内有效）: {link}')
        else:
            link = str(dst)
            print(f'[local→web] 已发布到静态目录: {dst}')
            print('[local→web] 提示：config.json 配 upload.base_url 为服务器域名后，'
                  '即输出真实 HTTP 下载链接')
    else:
        link = str(zip_path.resolve())
        print(f'[local] 下载链接（{ttl_minutes} 分钟内有效）: {link}')
    print(f'[local] 到期时间已登记: runtime/expiry.json（由 cleanup.py 执行删除）')
    return link


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
    load_dotenv()  # S3_* 等环境变量来自项目根目录 .env
    ap = argparse.ArgumentParser()
    ap.add_argument('zip_path')
    ap.add_argument('--backend', choices=['local', 's3'], default='local')
    ap.add_argument('--ttl', type=int, default=30)
    ap.add_argument('--web-dir', default='',
                    help='发布目录（local 后端：把 zip 拷贝到此静态目录并输出下载链接）')
    ap.add_argument('--base-url', default='',
                    help='静态站点域名（如 https://dl.example.com，需以 / 结尾外均可）')
    args = ap.parse_args()
    zip_path = Path(args.zip_path)
    if not zip_path.exists():
        raise SystemExit(f'文件不存在: {zip_path}')
    if args.backend == 's3':
        upload_s3(zip_path, args.ttl)
    else:
        upload_local(zip_path, args.ttl,
                     web_dir=args.web_dir or None, base_url=args.base_url)


if __name__ == '__main__':
    main()
