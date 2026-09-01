#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel-webgame-agent 主入口与编排器（plan-execute + 阶段内 ReAct）。

用法:
    python agent.py 小说.txt                  # 正常执行（需 DEEPSEEK_API_KEY）
    python agent.py 小说.txt --mock           # 用 mock LLM 跑通管线（测试/无 key）
    python agent.py 小说.txt --resume         # 从断点继续（跳过已完成阶段）

输入：小说文件（TXT/MD/EPUB，均为标准库处理）
输出：games/<book_id>/ 游戏文件夹 + archive/<book_id>.zip 打包存档

流程（plan 实例化阶段序列，execute 逐阶段执行，每阶段写检查点）：
  ingest → detect → chunk → [summarize(长篇)] → characters → style
  → game_init → design → generate(分批) → qa(含修复循环) → illustrate → package
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from core.contracts import (run_qa_scripts, validate_characters, validate_detect,
                            validate_style)
from core.llm import DeepSeekClient, MockLLM, LLMError
from core.react import ReactResult, json_validator, react_loop
from core.utils import (ROOT, clamp_context, read_json, read_text, slugify,
                        write_json, write_text)
from core.workers import get_worker

VERSION = '0.1.0'
LONG_NOVEL_CHARS = 80000
GENERATE_BATCH_SCENES = 10


# ---------------------------------------------------------------- 数据包写盘

def apply_patch(game_dir: Path, patch: dict) -> list:
    """把生成/修复包写进 games/<book_id>/data/。返回写盘错误列表。"""
    errors = []
    data_dir = game_dir / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    for key in ('game', 'mode', 'characters', 'theme'):
        if key in patch and patch[key] is not None:
            _write_js(data_dir / f'{key}.js', key.upper(), patch[key])
    scenes = patch.get('scenes')
    if scenes is not None:
        path = data_dir / 'scenes.js'
        existing = _load_js(path, 'SCENES', {})
        merged = dict(existing.get('scenes', {}))
        for sid, node in scenes.items():
            if node is None:
                merged.pop(sid, None)
            else:
                merged[sid] = node
        _write_js(path, 'SCENES', {'scenes': merged})
    return errors


def _write_js(path: Path, var: str, obj):
    path.write_text(f'/* 由 agent 生成，勿手改 */\nwindow.{var} = '
                    + json.dumps(obj, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')


def _load_js(path: Path, var: str, default):
    if not path.exists():
        return default
    text = path.read_text(encoding='utf-8')
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        return default
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return default


def _extract_epub_text(epub_path: Path, out_path: Path):
    """EPUB → 纯文本（标准库实现，无需 ebooklib）。

    按 content.opf 的 spine 顺序提取各 html 章节，去标签后拼接。
    无法解析 spine 时退化为按文件名排序。
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile

    OPF_NS = '{http://www.idpf.org/2007/opf}'
    try:
        zf = zipfile.ZipFile(epub_path)
        names = [n for n in zf.namelist() if not n.endswith('/')]
        # 找 content.opf
        opf_name = next((n for n in names if n.endswith('.opf')), None)
        order = []
        if opf_name:
            root = ET.fromstring(zf.read(opf_name))
            manifest = {}
            for it in root.iter(OPF_NS + 'item'):
                manifest[it.get('id')] = it.get('href')
            spine = root.find(OPF_NS + 'spine')
            if spine is not None:
                base = opf_name.rsplit('/', 1)[0] if '/' in opf_name else ''
                for ref in spine.iter(OPF_NS + 'itemref'):
                    href = manifest.get(ref.get('idref'), '')
                    if href:
                        order.append(f'{base}/{href}' if base else href)
        if not order:
            order = sorted(n for n in names if n.endswith(('.html', '.xhtml', '.htm')))
        text = []
        for name in order:
            try:
                raw = zf.read(name).decode('utf-8', errors='ignore')
            except KeyError:
                continue
            body = re.search(r'<body[^>]*>(.*)</body>', raw, re.S)
            chunk = body.group(1) if body else raw
            chunk = re.sub(r'<[^>]+>', '\n', chunk)
            chunk = re.sub(r'&nbsp;?', ' ', chunk)
            chunk = re.sub(r'&amp;', '&', chunk)
            chunk = re.sub(r'\n{3,}', '\n\n', chunk).strip()
            if chunk:
                text.append(chunk)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text('\n\n'.join(text), encoding='utf-8')
    except (zipfile.BadZipFile, OSError, ET.ParseError) as e:
        raise RuntimeError(f'EPUB 解析失败: {e}')


def _fmt_prompt(r: dict) -> str:
    return (f"# {r['kind']} / 画风: {r['style_name']} ({r['style_id']}) / 尺寸: {r['size']}\n"
            f"Prompt: {r['prompt']}\n\nNegative: {r['negative_prompt']}\n")


def _envelope_validator(game_dir: Path, full_check: bool):
    """生成/修复输出的校验器：解析 → 写盘 → （末批）跑完整 QA。"""
    def validator(content):
        try:
            env = json.loads(content)
        except json.JSONDecodeError as e:
            return False, [f'JSON 语法错误: {e}']
        patch = env.get('patch') if 'patch' in env else env.get('data')
        if not isinstance(patch, dict):
            return False, ['输出必须是 {"patch": {...}} 或 {"data": {...}} 结构']
        errors = apply_patch(game_dir, patch)
        if errors:
            return False, errors
        if not full_check:
            return True, []
        report = run_qa_scripts(game_dir)
        if not report['ok']:
            return False, [f"{i['severity']} {i['file']}: {i['message']}"
                           for i in report['issues'] if i['severity'] == 'error']
        return True, []
    return validator


# ---------------------------------------------------------------- 编排器

class NovelAgent:
    def __init__(self, novel_path: str, config: dict, mock: bool = False, resume: bool = False,
                 mock_dirs: list = None):
        self.novel_path = Path(novel_path)
        if not self.novel_path.exists():
            raise SystemExit(f'小说文件不存在: {self.novel_path}')
        self.config = config
        self.mock = mock
        self.resume = resume
        self._mock_dirs = [Path(d) for d in (mock_dirs or [])]
        self.title = self.novel_path.stem
        self.book_id = slugify(self.title)
        self.work_dir = ROOT / 'runtime' / self.book_id
        self.game_dir = ROOT / 'games' / self.book_id
        self.state_path = self.work_dir / 'state.json'
        self.state = self._load_state()
        default_fixtures = ROOT / 'tests' / 'fixtures' / 'mock_data'
        # 自定义 fixture 目录优先（可覆盖同名的默认桩）
        self.llm = MockLLM(self._mock_dirs + [default_fixtures]) if mock else \
            DeepSeekClient(config)

    # ---- 状态与检查点 ----
    def _load_state(self) -> dict:
        if self.resume and self.state_path.exists():
            return read_json(self.state_path)
        return {'book_id': self.book_id, 'title': self.title, 'done': [],
                'mode': None, 'qa_rounds': 0, 'plan_id': None}

    def _checkpoint(self, stage: str):
        if stage not in self.state['done']:
            self.state['done'].append(stage)
        write_json(self.state_path, self.state)

    def _done(self, stage: str) -> bool:
        return self.resume and stage in self.state['done']

    # ---- 工具 ----
    def _run(self, *cmd):
        r = subprocess.run([sys.executable, *[str(c) for c in cmd]],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f'命令失败 {" ".join(cmd)}: {r.stderr[:500]}')
        return r.stdout

    def _llm_stage(self, stage: str, worker_name: str, task: str,
                   validator, model_hint=None) -> ReactResult:
        worker = get_worker(worker_name)
        model = model_hint or self.config['workers'].get(worker_name, {}).get('model', 'chat')
        result = react_loop(self.llm, worker['system_prompt'], task, validator,
                            max_rounds=3, model=model)
        if not result.ok:
            print(f'⚠ {stage} 阶段 {result.rounds} 轮后仍未通过校验')
            if model != 'chat':
                print('  （深模型校验失败，可能输出格式问题；考虑降级重试或人工介入）')
        return result

    def _read_source_text(self, budget=40000) -> str:
        """读取用于 LLM 阶段的原文（短篇：分块；长篇：摘要）。"""
        chunks = read_json(self.work_dir / 'chunks.json')['chunks']
        return clamp_context('\n\n'.join(c['text'] for c in chunks), budget)

    # ---- 阶段执行 ----
    def run(self):
        print(f'== novel-webgame-agent v{VERSION} ==')
        print(f'小说: {self.title}  工作目录: runtime/{self.book_id}/')
        if self.mock:
            print('（mock 模式：LLM 输出为测试桩）')

        # ===== EXECUTE：先 ingest（计划基于清洗后的真实字数） =====
        if not self._done('ingest'):
            print('\n[执行] ingest')
            self.stage_ingest()
            self._checkpoint('ingest')
            print('[完成] ingest')
        else:
            print('[跳过] ingest（已完成）')

        # ===== PLAN：基于 ingest 事实实例化阶段序列 =====
        chapters = read_json(self.work_dir / 'chapters.json')
        total_chars = chapters['cleaned_chars']
        plan_id = 'long_novel' if total_chars >= LONG_NOVEL_CHARS else 'short_novel'
        self.state['plan_id'] = plan_id
        plan = read_json(ROOT / 'templates' / 'plans' / f'{plan_id}.json')
        stages = [s['name'] for s in plan['stages']]
        print(f'计划: {plan_id}（{total_chars} 字） 阶段序列: {" → ".join(stages)}')

        # ===== EXECUTE：其余阶段 =====
        for stage in stages:
            if stage == 'ingest' or self._done(stage):
                if stage != 'ingest':
                    print(f'[跳过] {stage}（已完成）')
                continue
            print(f'\n[执行] {stage}')
            fn = getattr(self, f'stage_{stage}')
            fn()
            self._checkpoint(stage)
            print(f'[完成] {stage}')

        print('\n== 全部阶段完成 ==')
        print(f'游戏文件夹: {self.game_dir}/')
        print(f'打包存档:   {ROOT / "archive" / (self.book_id + ".zip")}')

    # ---- 各阶段 ----
    def stage_ingest(self):
        source = self.novel_path
        if source.suffix.lower() == '.epub':
            txt = self.work_dir / 'source.txt'
            _extract_epub_text(source, txt)
            print(f'EPUB 已提取: {len(txt.read_text(encoding="utf-8"))} 字 → source.txt')
            source = txt
        self._run(ROOT / 'skills' / 'text-processing' / 'scripts' / 'chunker.py',
                  '--ingest', source, '--out', self.work_dir)

    def stage_detect(self):
        chapters = read_json(self.work_dir / 'chapters.json')
        head = clamp_context('\n\n'.join(
            c['text'] for c in chapters['chapters'][:6]), 8000)
        task = ('[STAGE:detect]\n以下是小说开头（供类型分析）：\n\n' + head +
                '\n\n请按 detect.md 的判断流程输出 JSON。')
        result = self._llm_stage('detect', 'detect', task,
                                 json_validator(['mode_id', 'theme_id', 'chunk_strategy']))
        if not result.ok:
            raise RuntimeError(f'detect 输出未通过校验: {result.errors[:3]}')
        data = json.loads(result.content)
        ok, errors = validate_detect(data)
        if not ok:
            raise RuntimeError(f'detect 校验失败: {errors}')
        self.state['mode'] = data
        write_json(self.work_dir / 'mode.json', data)
        print(f'模式: {data["mode_id"]}  主题: {data["theme_id"]}  类型: {data.get("genre", "")}')

    def stage_chunk(self):
        mode_id = self.state['mode']['mode_id']
        self._run(ROOT / 'skills' / 'text-processing' / 'scripts' / 'chunker.py',
                  '--chunk', '--chapters-json', self.work_dir / 'chapters.json',
                  '--mode', mode_id, '--out', self.work_dir)

    def stage_summarize(self):
        chunks = read_json(self.work_dir / 'chunks.json')['chunks']
        worker = get_worker('summarize')
        lines = []
        for c in chunks:
            task = ('[STAGE:summarize]\n' + worker['system_prompt']
                    + '\n\n### 分块 c' + str(c['id']) + '\n' + clamp_context(c['text'], 9000))
            content = self.llm.chat([{'role': 'user', 'content': task}],
                                    model='chat', json_mode=False, max_tokens=600)
            lines.append(f'--- 块 {c["id"]} ---\n{content.strip()}')
        write_text(self.work_dir / 'bible' / 'summaries.md', '\n\n'.join(lines))
        print(f'分卷摘要: {len(chunks)} 块 → bible/summaries.md')

    def stage_characters(self):
        source = self._read_source_text(30000)
        task = ('[STAGE:characters]\n根据 novel-character-cards skill 整理人物信息。'
                '以下是文本：\n\n' + source +
                '\n\n输出严格 JSON：{"characters": [{"name","aliases","gender","identity",'
                '"traits","experiences","relationships","ending","notes"}]}')
        result = self._llm_stage('characters', 'characters', task,
                                 json_validator(['characters']))
        if not result.ok:
            raise RuntimeError(f'characters 输出未通过校验: {result.errors[:3]}')
        data = json.loads(result.content)
        ok, errors = validate_characters(data)
        if not ok:
            raise RuntimeError(f'人物卡校验失败: {errors[:3]}')
        write_json(self.work_dir / 'characters.json', data)
        print(f'人物: {len(data["characters"])} 人')

    def stage_style(self):
        source = self._read_source_text(40000)
        task = ('[STAGE:style]\n' + source +
                '\n\n按 style.md 的格式提炼主旨/世界观/情感基调，输出 Markdown。')
        worker = get_worker('style')
        content = self.llm.chat([{'role': 'user', 'content': task}],
                                model='chat', json_mode=False, max_tokens=2000)
        ok, errors = validate_style(content)
        if not ok:
            print(f'⚠ style 输出缺小节: {errors}（继续执行，下游可容忍）')
        write_text(self.work_dir / 'bible' / 'world.md', content)
        print('world.md 已生成')

    def stage_game_init(self):
        self._run(ROOT / 'tools' / 'game_init.py',
                  '--book-id', self.book_id, '--title', self.title,
                  '--game-dir', self.game_dir)

    def stage_design(self):
        world = read_text(self.work_dir / 'bible' / 'world.md')
        chars = read_json(self.work_dir / 'characters.json')
        mode_id = self.state['mode']['mode_id']
        mode_tpl = read_json(ROOT / 'templates' / 'game_modes' / f'{mode_id}.json')
        theme_id = self.state['mode']['theme_id']
        theme_tpl = read_json(ROOT / 'templates' / 'themes' / f'{theme_id}.json')
        task = ('[STAGE:design]\n'
                f'游戏模式模板：\n{json.dumps(mode_tpl, ensure_ascii=False, indent=1)}\n\n'
                f'主题模板：\n{json.dumps(theme_tpl, ensure_ascii=False, indent=1)}\n\n'
                f'世界观圣经：\n{clamp_context(world, 20000)}\n\n'
                f'人物卡：\n{json.dumps(chars, ensure_ascii=False, indent=1)}\n\n'
                '按 design.md 输出设计 brief（严格 JSON）。')
        result = self._llm_stage('design', 'design', task,
                                 json_validator(['game_title', 'scene_blueprint', 'endings', 'attributes']))
        if not result.ok:
            raise RuntimeError(f'design 输出未通过校验: {result.errors[:3]}')
        data = json.loads(result.content)
        write_json(self.work_dir / 'design' / 'brief.json', data)
        blueprint = data.get('scene_blueprint', [])
        print(f'设计 brief: {data["game_title"]}，{len(blueprint)} 个场景蓝图，'
              f'{len(data["endings"])} 个结局')

    def stage_generate(self):
        brief = read_json(self.work_dir / 'design' / 'brief.json')
        mode_id = self.state['mode']['mode_id']
        theme_id = self.state['mode']['theme_id']
        mode_tpl = read_json(ROOT / 'templates' / 'game_modes' / f'{mode_id}.json')
        theme_tpl = read_json(ROOT / 'templates' / 'themes' / f'{theme_id}.json')
        chars = read_json(self.work_dir / 'characters.json')
        blueprint = brief.get('scene_blueprint', [])
        batches = [blueprint[i:i + GENERATE_BATCH_SCENES]
                   for i in range(0, len(blueprint), GENERATE_BATCH_SCENES)]
        if not batches:
            raise RuntimeError('设计 brief 的 scene_blueprint 为空')
        worker = get_worker('generate')
        for idx, batch in enumerate(batches):
            last = (idx == len(batches) - 1)
            batch_ids = [s['id'] for s in batch]
            task = ('[STAGE:generate]\n'
                    + f'book_id: {self.book_id}\n'
                    + f'book 名: {self.title}\n'
                    + f'模式模板 runtime：\n{json.dumps(mode_tpl["runtime"], ensure_ascii=False, indent=1)}\n'
                    + f'主题模板：\n{json.dumps(theme_tpl, ensure_ascii=False, indent=1)}\n'
                    + f'人物卡：\n{json.dumps(chars, ensure_ascii=False, indent=1)}\n'
                    + f'设计 brief：\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n'
                    + f'本次生成第 {idx + 1}/{len(batches)} 批，场景 id：{batch_ids}\n'
                    + '输出格式：{"patch": {"game": {...}, "mode": {...}, "characters": {...}, '
                      '"theme": {...}, "scenes": {批量场景节点}}}。'
                    + ('这是最后一批，场景必须整体完整可达。' if last
                       else '非最后一批：只输出本批 scenes 与完整顶层字段，其余照常。'))
            result = react_loop(self.llm, worker['system_prompt'], task,
                                _envelope_validator(self.game_dir, full_check=last),
                                max_rounds=3, model='chat')
            if not result.ok:
                # 生成重试耗尽不阻断流程：问题清单留给 QA 阶段的 repair 循环
                # （repair 用同样的校验器 + 回传问题，最多 qa_max_rounds 轮）
                print(f'⚠ 批次 {idx + 1}/{len(batches)} 生成未通过校验（{result.rounds} 轮），'
                      f'前 {min(3, len(result.errors))} 个问题: {result.errors[:3]}')
                print('  问题将交由 QA 修复循环处理')
            else:
                print(f'生成批次 {idx + 1}/{len(batches)} 完成')

    def stage_qa(self):
        self.state['qa_rounds'] = self.state.get('qa_rounds', 0) + 1
        budget = self.config['pipeline'].get('qa_max_rounds', 3)
        report = run_qa_scripts(self.game_dir)
        errors = [i for i in report['issues'] if i['severity'] == 'error']
        warnings = [i for i in report['issues'] if i['severity'] == 'warning']

        # 语义评审（深模型）
        semantic = self._semantic_review()
        problems = semantic.get('problems', []) if isinstance(semantic, dict) else []
        major = [p for p in problems if isinstance(p, dict) and p.get('severity') == 'major']

        print(f'QA 第 {self.state["qa_rounds"]} 轮：结构错误 {len(errors)}，警告 {len(warnings)}，'
              f'语义主要问题 {len(major)}，评分 {semantic.get("score", "?") if isinstance(semantic, dict) else "?"}')

        if not errors and not major:
            write_json(self.work_dir / 'qa' / 'qa_report.json', {
                'ok': True, 'round': self.state['qa_rounds'], 'issues': report['issues'],
                'semantic': semantic})
            return
        if self.state['qa_rounds'] > budget:
            print(f'⚠ QA 修复轮数达到上限 {budget}，剩余问题：')
            for e in errors[:5]:
                print(f'  {e["message"]}')
            write_json(self.work_dir / 'qa' / 'qa_report.json', {
                'ok': False, 'round': self.state['qa_rounds'], 'issues': report['issues'],
                'semantic': semantic, 'degraded': True})
            return

        # 修复循环：把问题清单回传给 repair worker（补丁式输出）
        issues = [f"{i['severity']} {i['file']}: {i['message']}" for i in report['issues'][:40]]
        for p in major:
            issues.append(f"major {p.get('scene', '')}: {p.get('problem', '')}")
        worker = get_worker('repair')
        prompt = worker['system_prompt'].replace('{qa_issues}', '\n'.join(issues))
        task = '[STAGE:repair]\n输出 {"patch": {...}} 补丁格式，逐条修复上述问题清单。'
        result = react_loop(self.llm, prompt, task,
                            _envelope_validator(self.game_dir, full_check=True),
                            max_rounds=3, model='chat')
        if not result.ok:
            print(f'⚠ 修复未完全通过（{result.rounds} 轮），保留当前状态')
        # 回到 QA 循环
        self.stage_qa()

    def _semantic_review(self):
        try:
            world = read_text(self.work_dir / 'bible' / 'world.md')
        except FileNotFoundError:
            world = ''
        chars = read_json(self.work_dir / 'characters.json')
        scenes = _load_js(self.game_dir / 'data' / 'scenes.js', 'SCENES', {})
        worker = get_worker('qa_review')
        task = ('[STAGE:qa_review]\n'
                f'世界观圣经：{clamp_context(world, 15000)}\n'
                f'人物卡：{json.dumps(chars, ensure_ascii=False)[:4000]}\n'
                f'场景数据：{json.dumps(scenes, ensure_ascii=False)[:12000]}\n'
                '按 review_prompt.md 输出严格 JSON：{"score": 0-10, "problems": [...], "praise": [...]}')
        try:
            content = self.llm.chat([{'role': 'user', 'content': task}],
                                    model='reasoner', json_mode=True, max_tokens=3000)
            return json.loads(content)
        except (LLMError, json.JSONDecodeError) as e:
            print(f'⚠ 语义评审失败: {e}')
            return {'score': 0, 'problems': []}

    def stage_illustrate(self):
        """插画提示词生成（本地，无图片 API 依赖）。

        DeepSeek 是纯文本 API，不生成图片；本阶段为每个角色与有背景的场景
        生成统一画风的绘图提示词（写入 assets/.../*.prompt.txt），
        用户用外部生图工具出图后放到同名路径即可（引擎对缺失素材自动降级）。
        """
        sys.path.insert(0, str(ROOT / 'skills' / 'illustration' / 'scripts'))
        from prompt_builder import build_prompt  # noqa: E402

        theme_id = self.state['mode']['theme_id']
        style_id = self.config['pipeline'].get('illustration_styles', {}).get(theme_id, 'flat_modern')

        chars = _load_js(self.game_dir / 'data' / 'characters.js', 'CHARACTERS', {})
        scenes = _load_js(self.game_dir / 'data' / 'scenes.js', 'SCENES', {})
        written = []

        for c in chars.get('characters', []):
            desc = c.get('desc') or c.get('role') or ''
            try:
                r = build_prompt('portrait', c.get('name', c['id']), desc, style_id)
                path = self.game_dir / 'assets' / 'characters' / f"{c['id']}.prompt.txt"
                path.write_text(_fmt_prompt(r), encoding='utf-8')
                written.append(path.name)
            except Exception as e:
                print(f'  ⚠ 角色 {c.get("id")} 提示词生成失败: {e}')

        bg_files = set()
        for node in scenes.get('scenes', {}).values():
            if node.get('bg'):
                # 按引用路径的 basename 命名提示词：出图后放同名文件即可直接生效
                bg_files.add(Path(node['bg']).name)
        for fname in sorted(bg_files):
            try:
                r = build_prompt('bg', fname, f'背景 {fname} 的氛围与场景', style_id)
                path = self.game_dir / 'assets' / 'bg' / f'{fname}.prompt.txt'
                path.write_text(_fmt_prompt(r), encoding='utf-8')
                written.append(path.name)
            except Exception as e:
                print(f'  ⚠ 背景 {fname} 提示词生成失败: {e}')

        if written:
            print(f'插画提示词（画风 {style_id}）: {len(written)} 个 → assets/')
        else:
            print(f'插画提示词: 无（角色 0 人 / 无背景引用）')

    def stage_package(self):
        self._run(ROOT / 'tools' / 'package.py', self.game_dir, '--archive', ROOT / 'archive')
        backend = self.config.get('upload', {}).get('backend', 'local')
        ttl = self.config.get('upload', {}).get('link_ttl_minutes', 30)
        zip_path = ROOT / 'archive' / f'{self.book_id}.zip'
        if backend == 's3':
            self._run(ROOT / 'tools' / 'upload.py', zip_path, '--backend', 's3', '--ttl', str(ttl))
        else:
            self._run(ROOT / 'tools' / 'upload.py', zip_path, '--backend', 'local', '--ttl', str(ttl))
        print('（链接到期后可用 tools/cleanup.py 清理本地产物）')


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description='novel-webgame-agent：小说 → 网页游戏')
    ap.add_argument('novel', help='小说文件路径（TXT/MD/EPUB）')
    ap.add_argument('--config', default=str(ROOT / 'config.json'))
    ap.add_argument('--mock', action='store_true', help='使用 mock LLM（测试）')
    ap.add_argument('--mock-dir', action='append', default=[],
                    help='额外 mock fixture 目录（优先级高于默认 fixtures）')
    ap.add_argument('--resume', action='store_true', help='从检查点继续')
    args = ap.parse_args()

    config = read_json(args.config)
    try:
        agent = NovelAgent(args.novel, config, mock=args.mock, resume=args.resume,
                           mock_dirs=args.mock_dir)
    except LLMError as e:
        print(f'配置错误: {e}\n（离线测试请加 --mock）')
        sys.exit(1)
    agent.run()


if __name__ == '__main__':
    main()
