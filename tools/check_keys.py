#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API key 自检：验证 DeepSeek（LLM）与硅基流动（生图）key 是否可用。

用法: python tools/check_keys.py
只读接口（/models），不发费用。输出：
  - 两个 key 的状态（未设置 / 有效 / 401 无效 / 网络错误）
  - 生图模型清单，标注 config.json 里配置的默认模型是否在售
"""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'core'))
from utils import load_dotenv  # noqa: E402


def check_deepseek(cfg: dict) -> str:
    key = os.environ.get(cfg.get('api_key_env', 'DEEPSEEK_API_KEY'), '')
    if not key:
        return '❌ 未设置（.env 里填 DEEPSEEK_API_KEY=sk-...）'
    try:
        r = requests.get(cfg.get('base_url', 'https://api.deepseek.com').rstrip('/')
                         + '/models', headers={'Authorization': f'Bearer {key}'},
                         timeout=15)
        if r.status_code == 200:
            models = [m.get('id') for m in r.json().get('data', [])]
            return f'✅ 有效（可用模型: {", ".join(models[:6])}）'
        if r.status_code == 401:
            return '❌ key 无效（401）——检查是否 sk- 开头、复制完整，或到 platform.deepseek.com 重新生成'
        return f'❌ HTTP {r.status_code}: {r.text[:120]}'
    except requests.RequestException as e:
        return f'❌ 网络错误（无法连接 {cfg.get("base_url")}）: {e}'


def check_siliconflow(cfg: dict) -> str:
    key = os.environ.get(cfg.get('api_key_env', 'SILICONFLOW_API_KEY'), '')
    if not key:
        return '❌ 未设置（.env 里填 SILICONFLOW_API_KEY=sk-...；不填也能跑，只是不出图）'
    wanted = cfg.get('model', 'black-forest-labs/FLUX.1-schnell')
    try:
        r = requests.get(cfg.get('base_url', 'https://api.siliconflow.cn/v1').rstrip('/')
                         + '/models', headers={'Authorization': f'Bearer {key}'},
                         timeout=15)
        if r.status_code == 200:
            ids = [m.get('id') for m in r.json().get('data', [])]
            img_ids = [i for i in ids if i and any(k in i.lower() for k in
                       ('flux', 'image', 'kolors', 'z-image', 'sd3', 'seedream'))]
            ok = f'✅ 配置模型 {wanted} 在售' if wanted in ids \
                else f'⚠ 配置模型 {wanted} 不在列表（见下方清单，改 config.json 的 image.model）'
            return (f'{ok}（共 {len(ids)} 个模型）\n'
                    f'  生图类模型: {", ".join(img_ids[:8]) or "未识别到（密钥可能缺 images:generate 权限）"}')
        if r.status_code in (401, 403):
            return (f'❌ key 无效或权限不足（HTTP {r.status_code}）——'
                    '到 cloud.siliconflow.cn 密钥管理检查：key 是否 sk- 开头、'
                    '创建时是否勾选了 images:generate 和 models:use 权限')
        return f'❌ HTTP {r.status_code}: {r.text[:120]}'
    except requests.RequestException as e:
        return f'❌ 网络错误（无法连接 {cfg.get("base_url")}）: {e}'


def main():
    load_dotenv()
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    print('== DeepSeek（LLM，必填）==')
    print(' ', check_deepseek(config.get('api', {})))
    print()
    print('== 硅基流动（生图，可选）==')
    print(' ', check_siliconflow(config.get('image', {})))


if __name__ == '__main__':
    main()
