#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成小说人物信息卡片 JPG（可选输出）。

用法:
    python generate_card.py --input outputs/characters.json --output outputs/

依赖: pip install Pillow
"""
import argparse
import json
import os
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    import sys
    print('缺少 Pillow 依赖，请安装: pip install Pillow')
    sys.exit(1)

THEMES = {
    'ancient': {'bg': (250, 245, 235), 'accent': (139, 0, 0), 'text': (50, 40, 30),
                'sub': (100, 85, 70), 'border': (180, 150, 120), 'label': '古风'},
    'modern': {'bg': (255, 255, 255), 'accent': (33, 150, 243), 'text': (33, 33, 33),
               'sub': (100, 100, 100), 'border': (220, 220, 220), 'label': '现代'},
    'scifi': {'bg': (18, 22, 32), 'accent': (0, 255, 255), 'text': (230, 240, 255),
              'sub': (150, 170, 200), 'border': (60, 80, 110), 'label': '科幻'},
    'western': {'bg': (240, 232, 215), 'accent': (75, 50, 30), 'text': (45, 35, 25),
                'sub': (110, 95, 75), 'border': (170, 140, 105), 'label': '西幻'},
}

CARD_W, CARD_H, MARGIN = 900, 1200, 60

# macOS 优先的系统字体（含中文字体）
FONT_CANDIDATES = [
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/System/Library/Fonts/Supplemental/Songti.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    'C:\\Windows\\Fonts\\msyh.ttc',
    'C:\\Windows\\Fonts\\simhei.ttf',
]
_cache = {}


def load_font(size):
    if size in _cache:
        return _cache[size]
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _cache[size] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _cache[size] = f
    return f


def wrap_text(text, font, max_width, draw):
    if not text:
        return []
    lines, cur = [], ''
    for ch in text:
        test = cur + ch
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def render_card(char, theme, out_path):
    img = Image.new('RGB', (CARD_W, CARD_H), theme['bg'])
    draw = ImageDraw.Draw(img)

    if theme['label'] == '古风':
        for i in range(0, CARD_W, 80):
            draw.line([(i, 0), (i, CARD_H)], fill=(240, 235, 225), width=1)
    elif theme['label'] == '科幻':
        for i in range(0, CARD_H, 80):
            draw.line([(0, i), (CARD_W, i)], fill=(30, 40, 60), width=1)

    draw.rounded_rectangle([20, 20, CARD_W - 20, CARD_H - 20], radius=24,
                           outline=theme['border'], width=3)

    title_font = load_font(48)
    alias_font = load_font(22)
    label_font = load_font(24)
    body_font = load_font(22)
    small_font = load_font(18)

    x, y = MARGIN, MARGIN
    name = char.get('name', '未命名')
    draw.text((x, y), name, font=title_font, fill=theme['accent'])
    y += 60

    aliases = char.get('aliases') or []
    if aliases:
        draw.text((x, y), '别名：' + ' / '.join(aliases), font=alias_font, fill=theme['sub'])
        y += 40

    draw.line([(x, y), (CARD_W - MARGIN, y)], fill=theme['accent'], width=2)
    y += 30

    # 内容区预算：预留底部时代标签空间
    BOTTOM_RESERVE = 60
    content_max_y = CARD_H - MARGIN - BOTTOM_RESERVE

    def draw_block(label, content):
        nonlocal y
        draw.text((x, y), label, font=label_font, fill=theme['accent'])
        y += 34
        items = content if isinstance(content, list) else [str(content)]
        items = [i for i in items if i]
        text = '\n'.join('• ' + str(i) for i in items) if items else '未提及'
        for line in wrap_text(text, body_font, CARD_W - MARGIN * 2, draw):
            if y > content_max_y:
                return  # 溢出保护：剩余内容不画（卡片有界）
            draw.text((x + 10, y), line, font=body_font, fill=theme['text'])
            y += 30
        y += 14

    gender, identity = char.get('gender', ''), char.get('identity', '')
    info = ' / '.join(filter(None, [gender, identity]))
    if info:
        draw_block('身份', info)
    draw_block('性格特点', char.get('traits', []))
    draw_block('主要经历', char.get('experiences', []))
    draw_block('人物关系', char.get('relationships', []))
    draw_block('结局/现状', char.get('ending', '未提及'))
    notes = char.get('notes') or []
    if notes:
        draw_block('备注', notes)

    label_text = f"#{theme['label']}"
    bbox = draw.textbbox((0, 0), label_text, font=small_font)
    draw.text((CARD_W - MARGIN - (bbox[2] - bbox[0]), CARD_H - MARGIN + 10),
              label_text, font=small_font, fill=theme['sub'])

    img.save(out_path, 'JPEG', quality=92)


def main():
    ap = argparse.ArgumentParser(description='生成小说人物信息卡片 JPG')
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text(encoding='utf-8'))
    theme = THEMES.get(data.get('era', 'modern'), THEMES['modern'])
    chars = data.get('characters', [])
    if not chars:
        print('JSON 中未找到人物数据')
        return
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    for char in chars:
        name = char.get('name', '未命名')
        safe = ''.join(c for c in name if c.isalnum() or c in '_-') or 'unnamed'
        out = out_dir / f'{safe}.jpg'
        render_card(char, theme, str(out))
        print(f'已生成: {out}')


if __name__ == '__main__':
    main()
