#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文本清洗 + 章节识别 + 分块工具。

用法:
    python chunker.py --ingest 小说.txt --out runtime/<书>/
    python chunker.py --chunk --chunks-json runtime/<书>/chunks.json --mode classic --out runtime/<书>/
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

AD_STRIP_PATTERNS = [
    re.compile(r'(本章未完，请点击下一页继续阅读|手机用户请访问|请记住本书首发域名|天才一秒记住|一秒记住|无弹窗|如果您觉得本站|某某看书|最新章节.*(首发|最快)|VIP章节|推荐票|月票|打赏|收藏|投推荐票|加入书签)'),
    re.compile(r'^\s*[\d]{2,}年[\d]{1,2}月[\d]{1,2}日\s*$'),
]

CHAPTER_RE = re.compile(
    r'^\s*(第\s*[零一二三四五六七八九十百千万0-9]+\s*[章节回卷部集篇]|'
    r'Chapter\s+\d+|楔子|序章|尾声|终章|番外|引子|前言|后记)'
)

GBK_NOISE = re.compile(r'[�]')


def read_text(path: Path) -> str:
    """编码探测读取：UTF-8 → GB18030 → latin-1。"""
    raw = path.read_bytes()
    for enc in ('utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', errors='replace')


def clean_text(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.lstrip('﻿')
    lines = []
    for line in text.split('\n'):
        s = line.strip()
        if not s:
            lines.append('')
            continue
        if any(p.search(s) for p in AD_STRIP_PATTERNS):
            continue
        lines.append(s)
    # 压缩连续空行（最多 1 个）
    out, prev_blank = [], False
    for line in lines:
        if line == '':
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out.append(line)
    return '\n'.join(out)


def split_chapters(text: str):
    """章节识别。返回 [(title, body), ...]，无标题时按 ~3000 字切伪章节。"""
    chapters, cur_title, cur_body = [], None, []
    para_mode = False  # 无章节标记时启用段落群切分
    for line in text.split('\n'):
        stripped = line.strip()
        m = CHAPTER_RE.match(stripped) if stripped else None
        if m:
            if cur_title is not None or cur_body:
                chapters.append((cur_title or f'第{len(chapters)+1}节', '\n'.join(cur_body)))
            cur_title = stripped
            cur_body = []
        else:
            if stripped:
                cur_body.append(line)
    if cur_title is not None or cur_body:
        chapters.append((cur_title or f'第{len(chapters)+1}节', '\n'.join(cur_body)))

    # 无标题（或标题太少）：伪章节切分
    if not chapters:
        para_mode = True
        paragraphs = [p for p in text.split('\n\n') if p.strip()]
        chapters, buf = [], []
        size = 0
        for p in paragraphs:
            buf.append(p)
            size += len(p)
            if size >= 3000:
                chapters.append((f'第{len(chapters)+1}节', '\n\n'.join(buf)))
                buf, size = [], 0
        if buf:
            chapters.append((f'第{len(chapters)+1}节', '\n\n'.join(buf)))

    result = []
    for i, (title, body) in enumerate(chapters):
        body = body.strip()
        if not body:
            continue
        result.append({"id": i + 1, "title": title, "volume": None, "text": body})
    return result


def chunk_by_mode(chapters, mode: str, chunk_chars: int = 8000):
    """按模式分块（当前实现为定长切块，模式特异性处理在 extract 阶段）。"""
    chunks, buf, buf_ids, size = [], [], [], 0
    for ch in chapters:
        buf.append(ch)
        buf_ids.append(ch["id"])
        size += len(ch["text"])
        if size >= chunk_chars:
            chunks.append({"id": f"c{len(chunks)+1:03d}", "chapters": buf_ids,
                           "mode": mode, "text": '\n\n'.join(c["text"] for c in buf)})
            buf, buf_ids, size = [], [], 0
    if buf:
        chunks.append({"id": f"c{len(chunks)+1:03d}", "chapters": buf_ids,
                       "mode": mode, "text": '\n\n'.join(c["text"] for c in buf)})
    return chunks


def ingest(path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    text = read_text(path)
    cleaned = clean_text(text)
    noise = len(GBK_NOISE.findall(cleaned))
    chapters = split_chapters(cleaned)
    chapters_path = out_dir / 'chapters.json'
    chapters_path.write_text(
        json.dumps({"source": str(path), "raw_chars": len(text),
                    "cleaned_chars": len(cleaned), "noise_chars": noise,
                    "chapters": chapters}, ensure_ascii=False, indent=2), encoding='utf-8')
    ratio = (len(text) - len(cleaned)) / max(len(text), 1)
    print(f"章节数: {len(chapters)}  清洗比例: {ratio:.1%}  乱码字符: {noise}")
    if ratio > 0.3:
        print("⚠ 警告: 清洗比例超过 30%，可能误删正文，请检查")
    print(f"已写出: {chapters_path}")


def do_chunk(chapters_path: Path, mode: str, out_dir: Path, chunk_chars: int):
    data = json.loads(chapters_path.read_text(encoding='utf-8'))
    chunks = chunk_by_mode(data["chapters"], mode, chunk_chars)
    out = out_dir / 'chunks.json'
    out.write_text(json.dumps({"mode": mode, "chunks": chunks}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"分块数: {len(chunks)}  平均 {sum(len(c['text']) for c in chunks) // max(len(chunks),1)} 字/块")
    print(f"已写出: {out}")


def main():
    ap = argparse.ArgumentParser(description='文本清洗/章节/分块')
    ap.add_argument('--ingest', help='原始小说文件路径')
    ap.add_argument('--chunk', action='store_true', help='执行分块')
    ap.add_argument('--chapters-json', help='chapters.json 路径（chunk 模式用）')
    ap.add_argument('--mode', default='classic', help='游戏模式 id')
    ap.add_argument('--chunk-chars', type=int, default=8000)
    ap.add_argument('--out', required=True, help='输出目录 runtime/<书>/')
    args = ap.parse_args()

    out_dir = Path(args.out)
    if args.ingest:
        ingest(Path(args.ingest), out_dir)
    if args.chunk:
        if not args.chapters_json:
            print('--chunk 需要 --chapters-json', file=sys.stderr)
            sys.exit(1)
        do_chunk(Path(args.chapters_json), args.mode, out_dir, args.chunk_chars)
    if not args.ingest and not args.chunk:
        ap.print_help()


if __name__ == '__main__':
    main()
