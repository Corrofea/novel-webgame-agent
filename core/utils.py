# -*- coding: utf-8 -*-
"""通用工具：路径、文件读写、文本处理。"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def slugify(name: str, max_len: int = 40) -> str:
    """把文件名转为 book_id（小写字母数字下划线）。中文名退化为 novel_<hash6>。"""
    s = re.sub(r'[^A-Za-z0-9_]+', '_', name).strip('_').lower()
    if not s:
        s = 'novel'
    if re.fullmatch(r'[a-z0-9_]+', s) and not any('一' <= c <= '鿿' for c in name):
        return s[:max_len]
    h = hashlib.md5(name.encode('utf-8')).hexdigest()[:6]
    return f'novel_{h}'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def read_text(path):
    return Path(path).read_text(encoding='utf-8')


def ensure_dir(p) -> Path:
    Path(p).mkdir(parents=True, exist_ok=True)
    return Path(p)


def char_count(text: str) -> int:
    return len(text)


def clamp_context(text: str, budget: int) -> str:
    """按预算截断上下文（用于注入上限控制）。"""
    if len(text) <= budget:
        return text
    head = budget * 7 // 10
    tail = budget - head
    return text[:head] + '\n\n……（中间内容已省略，共 {} 字）……\n\n'.format(len(text)) + text[-tail:]
