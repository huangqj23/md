---
name: short-video
description: >-
  Make a Douyin / 抖音 short video with the apps/ai-video pipeline. Claude writes the topic cards
  (选题卡), script and storyboard (剧本、分镜) and the fact check in the conversation as a JSON file
  — never through a language-model API — then drives character references, keyframes, clips and the
  final cut through the user's Ark Agent Plan, MiniMax M Plan and Gemini subscriptions. Use for
  短视频、抖音、选题、剧本、分镜, videos about 节气 / 古诗 / 文言文 / 物理 / 长征 / 神话 / 名画, or /short-video.
---

# 短视频出片

你负责全部文字工作：选题、剧本、分镜、事实核查、质检意见。图像、视频、配音由 `apps/ai-video` 调用用户订阅的模型生成。

## 硬规则

1. **文案只在对话里写。** 不调用任何文本模型 API，不往代码里加 LLM 调用。方舟套餐条款也禁止用 API 调它的文本模型。
2. **花钱前先报价。** 先用 `--estimate` 拿到 AFP、¥ 和剩余额度，用 AskUserQuestion 让用户确认，确认后才加 `-y` 执行。超过日额度时不要硬跑。
3. **不显示、不复述任何 Key**，不提交 `.env`。
4. **引用的诗文和史实一字不差**：核对过的才能写进旁白。
5. **红色题材**：尊重史实，不生成领袖和英烈的写实正脸，画面用群像、远景和象征意象。
6. **画面里不要文字。** 字幕、标题都在后期叠加。
7. **AI 标识不能去掉。** 成片右上角的「AI生成」角标和元数据由流水线自动加。

## 环境

- 工作目录 `apps/ai-video`，命令是 `.venv/Scripts/ai-video`。
- 在 Bash 里运行时，在命令前加 `PYTHONIOENCODING=utf-8`，否则中文输出会乱码。
- 程序没有终端可交互时，会打印预计花费后退出，并提示「加 -y 重新运行」。这是预期行为。
- 退出码：0 正常；1 用户拒绝、超预算或超额度；2 剧本或参数有错。

## 流程

### 0. 准备

运行 `ai-video check`（第一次加 `--ping`）和 `ai-video quota`。哪个模型未就绪，就告诉用户缺什么，例如 Key 或代理。

### 1. 选题卡（检查点 A）

1. 读 [references/topics.md](references/topics.md)。
2. 按用户的一句话，写 3–5 张选题卡。时令题材先用 WebSearch 核实日期，包括节气、纪念日、节日。
3. 用 AskUserQuestion 让用户挑一张，也可以合并或修改。

### 2. 剧本和分镜

1. 读 [references/script-schema.md](references/script-schema.md)、[references/storyboard-rules.md](references/storyboard-rules.md) 和 [references/voice.md](references/voice.md)。
2. 写到 `apps/ai-video/data/scripts/<YYYYMMDD>-<拼音>.json`，用 Write 工具，编码 UTF-8。
3. 有人物出镜就声明 `characters`，在镜头里用 `refs` 引用。每个镜头最多 2 个角色。

### 3. 事实核查

1. 按 [references/fact-check.md](references/fact-check.md) 逐条核对。
   - 古诗文用 WebSearch / WebFetch 对照权威原文。
   - 物理、历史、红色题材另派一个 subagent（Agent 工具）只做核查，不让它看你的推理过程。
2. 结果写进 JSON 的 `check` 字段。有问题先改剧本，再导入。

### 4. 导入和角色参考图（检查点 B）

1. 先运行 `ai-video new --script <文件> --until characters --estimate`，把预估报给用户，确认后去掉 `--estimate`、加 `-y` 执行。
   - `new` 会建项目，提示里有项目 id。之后一律用 `make <id>` 继续，不要再跑 `new`。
2. 用 Read 工具看 `data/projects/<id>/characters/*.png`。
3. **一帧先行**：新风格或新角色，在批量出图前先把方向定下来。
   - 不满意，就改 `look`，再用 `ai-video script <id> <文件>` 重新导入。这样只重做这个角色的参考图。
   - 重要选题可以先写 2–3 个只有 1–2 个镜头的小剧本，各自试一种风格，让用户挑一个方向。

### 5. 关键帧

1. 运行 `make <id> --until keyframes`，同样先 `--estimate`，确认后再 `-y`。
2. 运行 `ai-video sheet <id>`，用 Read 看 `sheets/keyframes.jpg`，按 [references/review.md](references/review.md) 逐镜检查。
3. 有问题的镜头：改 JSON 后用 `ai-video script <id> <文件>` 重新导入（只重做改动的镜头），或者用 `make <id> --redo s03`。

### 6. 视频（检查点 C）

1. 镜头多、节奏没把握时，先出草稿：`make <id> --draft -y`，用 Seedance 2.0 Mini 480p，每秒约 115 AFP。
2. 用 `sheet <id> --what clips` 查看草稿。
3. 草稿通过后出正式版：`make <id> --revideo all -y`，用 Seedance 2.5 720p，每秒约 756 AFP（约 ¥1.5）。
4. 镜头少、把握大时，可以跳过草稿，直接出正式版。

### 7. 合成和审片（检查点 D）

1. 运行 `make <id> -y` 合成成片，再运行 `sheet <id> --what final` 生成抽帧联系表。
2. 派一个没参与制作的 subagent，只给它这三样东西：联系表、`out/final.srt` 和 `out/publish.md`。让它按 review.md 的「成片」一节挑问题。
3. 交付给用户：
   - 成片路径和 `publish.md`；
   - 本项目花费（命令末尾会打印）和 `ai-video quota` 的结果；
   - 提醒发布时勾选「作品含 AI 生成内容」。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 剧本有问题，改好再导入 | 按列表改 JSON，再执行 `new --script` 或 `script` |
| 超过今日额度 / 本期额度 | 告诉用户；可以改用草稿模式、减少镜头，或者等额度刷新 |
| 配音失败（401 或连不上） | Gemini 走代理，检查 `.env` 的 `GEMINI_PROXY` 和 Key；临时把 `providers.yaml` 的 `routing.tts.provider` 改成 `minimax_tts` |
| Seedance 拒收（写实人脸） | 流水线会自动转 H3；以后预期是写实人脸的镜头，直接标 `photoreal: true` |
| 关键帧被拒 | 去掉真人名人名、敏感词或过于暴露的描述；人物是未成年人时，服装一律写端庄长款 |
| 物理公式、受力图 | 本期 AI 视频只拍现象，公式写在旁白和字幕里。用 huashu-art-motion 代码绘制的动画镜头是下一阶段的计划 |
