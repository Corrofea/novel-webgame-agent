# -*- coding: utf-8 -*-
"""Worker 定义：worker = (系统提示词 + 模型档位 + skill 上下文)。

系统提示词从 skills/ 下的文件加载（提示词不硬编码在代码里），
worker 只做装配：把角色框架 + skill 正文 + 固定引用拼成 system prompt。
"""
from pathlib import Path

from .utils import ROOT

SKILLS = ROOT / 'skills'
TEMPLATES = ROOT / 'templates'

ROLE_HEADER = (
    '你是 novel-webgame-agent 流水线中的「{role}」worker。'
    '你的所有输出都必须严格遵守本提示中的规则。'
)


def read_md(path: Path) -> str:
    # 去掉 frontmatter（--- ... ---）与首行标题，正文即操作手册
    text = path.read_text(encoding='utf-8')
    if text.startswith('---'):
        _, fm, body = text.split('---', 2)
        return body.strip()
    return text.strip()


def skill_prompt(skill_name: str) -> str:
    return read_md(SKILLS / skill_name / 'SKILL.md')


def load_prompt(*parts: str) -> str:
    """加载 skills/<...>/prompts/<name>.md 或模板文件。"""
    p = Path(*parts)
    if not p.exists():
        raise FileNotFoundError(f'缺少提示词文件: {p}')
    return read_md(p)


def make_worker(role: str, skill: str, model: str, extra_framing: str = '') -> dict:
    """装配一个 worker 配置。"""
    return {
        'role': role,
        'model': model,
        'system_prompt': ROLE_HEADER.format(role=role) + '\n\n' + skill_prompt(skill) + extra_framing,
    }


# ---- 各 worker 的 system prompt ----

def detect_worker() -> dict:
    return make_worker(
        '类型分析', 'game-design',
        'chat',
        extra_framing='\n\n' + load_prompt(SKILLS / 'game-design' / 'prompts' / 'detect.md'),
    )


def summarize_worker() -> dict:
    return make_worker(
        '分块摘要', 'world-bible', 'chat',
        extra_framing='\n\n' + load_prompt(SKILLS / 'world-bible' / 'prompts' / 'summarize.md'),
    )


def style_worker() -> dict:
    return make_worker(
        '主旨提炼', 'world-bible', 'chat',
        extra_framing='\n\n' + load_prompt(SKILLS / 'world-bible' / 'prompts' / 'style.md'),
    )


def characters_worker() -> dict:
    return make_worker('人物提取', 'novel-character-cards', 'chat')


def design_worker() -> dict:
    return make_worker(
        '游戏设计', 'game-design', 'reasoner',
        extra_framing='\n\n' + load_prompt(SKILLS / 'game-design' / 'prompts' / 'design.md'),
    )


def generate_worker() -> dict:
    return make_worker(
        '数据生成', 'game-design', 'chat',
        extra_framing='\n\n' + load_prompt(SKILLS / 'game-design' / 'prompts' / 'generate.md'),
    )


def repair_worker() -> dict:
    return make_worker(
        '数据修复', 'game-design', 'chat',
        extra_framing='\n\n' + load_prompt(SKILLS / 'game-design' / 'prompts' / 'repair.md'),
    )


def qa_review_worker() -> dict:
    return make_worker(
        '语义评审', 'qa-check', 'reasoner',
        extra_framing='\n\n' + read_md(SKILLS / 'qa-check' / 'review_prompt.md'),
    )


WORKERS = {
    'detect': detect_worker,
    'summarize': summarize_worker,
    'style': style_worker,
    'characters': characters_worker,
    'design': design_worker,
    'generate': generate_worker,
    'repair': repair_worker,
    'qa_review': qa_review_worker,
}


def get_worker(name: str) -> dict:
    if name not in WORKERS:
        raise KeyError(f'未知 worker: {name}')
    return WORKERS[name]()
