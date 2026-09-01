---
name: qa-check
description: 游戏质量检验。结构校验（schema/图）+ 冒烟测试（模拟游玩）+ LLM 语义评审（与世界观比对/体验问题）。发现问题时产出修复清单，供生成环节 ReAct 修复。
---

# QA：结构校验 + 冒烟 + 语义评审

## 输入
- `games/<书>/`（游戏文件夹）
- `runtime/<书>/bible/world.md`、`characters.json`（语义比对的基准）

## 输出
- `runtime/<书>/qa/qa_report.json`：
  ```json
  {
    "ok": false,
    "round": 2,
    "issues": [
      {"severity": "error", "file": "scenes.js", "message": "节点 s005 指向不存在的节点 s999"},
      {"severity": "warning", "file": "scenes.js", "message": "节点 s012 素材缺失: assets/bg/x.png"}
    ],
    "semantic": {"score": 7, "problems": ["角色性格与原文不符：...", "时间线矛盾：..."]}
  }
  ```

## 流程（三道闸门，依次执行）

### 1. 结构校验（脚本）
```bash
python skills/qa-check/scripts/validate_game.py games/<书>/ --json
```
退出码 0=通过。检测：数据解析、schema 字段、引用一致性（attr/角色/物品/goto）、
孤立节点、死路、结局数量、素材引用存在性。

### 2. 冒烟测试（脚本）
```bash
python skills/qa-check/scripts/smoke_test.py games/<书>/ --runs 40
```
模拟玩家随机游玩 40 次：结局到达率、状态模拟死路、auto 链循环、平均步数（节奏信号）。

### 3. 语义评审（LLM，`review_prompt.md`）
- 用 **world.md + characters.json 做事实表**，逐场景比对（重点：角色言行是否符合人设、
  时间线是否矛盾、结局是否违背原文硬性事实）
- 体验问题：选项是否有真实后果、好感度门槛是否合理、前 3 场景是否有选择
- 产出结构化问题清单（如上 qa_report.json 的 semantic 节）

## 修复循环（交给编排层）
- 错误分级：`error` 必须修复；`warning` 按数量批量修复（>10 条视为必修）
- 把 qa_report.json 的 issue 清单（**只回传问题，不传全书**）退回生成环节，
  要求按文件定位修复；修复后重跑结构校验与冒烟
- 重试预算由编排层控制（默认 3 轮），耗尽仍不通过则降级放行并记录（warning 降级，
  error 必须解决或人工介入）
- **常见 bug 自查清单**见 `../../docs/common-bugs.md`——语义评审与修复时必须逐条对照

## 注意事项
- 脚本只读游戏文件夹，不改写任何文件（修复由生成环节做）
- 语义评审是"判断力密集"环节，用深模型，上下文只注入 bible 与问题场景，不注入全书
- 冒烟失败不一定等于结构失败：如"40 次未到达结局"可能是条件结局门槛过高，这类问题
  报告为 warning + 具体门槛值，让生成环节决定调整门槛还是增加路径
