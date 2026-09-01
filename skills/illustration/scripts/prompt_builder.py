#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把中文角色卡/场景描述转换为英文绘图提示词。

用法:
    python prompt_builder.py --kind portrait --name 林黛玉 --desc "敏感多愁，才华横溢，体弱" --style ink_wash --out assets/prompts/
    python prompt_builder.py --kind bg --name 潇湘馆 --desc "竹林环绕的庭院，清冷幽静" --style ink_wash --out assets/prompts/
"""
import argparse
import json
import sys
from pathlib import Path

STYLE_REFS = Path(__file__).resolve().parent.parent / 'templates' / 'style_refs.json'

SIZE = {'portrait': '9:16', 'bg': '16:9', 'cg': '16:9'}
HEAD = {'portrait': 'character portrait of', 'bg': 'scene background of', 'cg': 'cinematic scene of'}
BODY = {
    'portrait': '{name}, {desc}, {role}, full body, centered composition, plain background',
    'bg': '{name}, {desc}, wide shot, no characters, no text',
    'cg': '{name}, {desc}, dynamic composition, emotionally expressive',
}


def build_prompt(kind, name, desc, style_id):
    refs = json.loads(STYLE_REFS.read_text(encoding='utf-8'))
    style = next((s for s in refs['styles'] if s['id'] == style_id), refs['styles'][0])
    body = BODY[kind].format(name=name, desc=desc, role='')
    prompt = (f"{style['prompt_prefix']}, {HEAD[kind]} {body}, "
              f"{SIZE[kind]}, high quality, highly detailed")
    return {
        "kind": kind, "style_id": style['id'], "style_name": style['name'],
        "prompt": prompt,
        "negative_prompt": style['negative'],
        "size": SIZE[kind],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', required=True, choices=['portrait', 'bg', 'cg'])
    ap.add_argument('--name', required=True, help='角色名/场景名')
    ap.add_argument('--desc', required=True, help='中文描述')
    ap.add_argument('--style', default='flat_modern')
    ap.add_argument('--out', default='.', help='输出目录（提示词 .txt）')
    args = ap.parse_args()

    result = build_prompt(args.kind, args.name, args.desc, args.style)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fname = out / f"{args.kind}_{args.name}.txt"
    fname.write_text(
        f"# {args.kind} / {args.name} / 画风: {result['style_name']} ({result['style_id']})\n"
        f"# 尺寸: {result['size']}\n\n"
        f"Prompt: {result['prompt']}\n\nNegative: {result['negative_prompt']}\n",
        encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"已写出: {fname}")


if __name__ == '__main__':
    main()
