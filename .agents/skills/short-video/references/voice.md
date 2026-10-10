# 配音

## 当前默认：MiniMax speech-2.8-hd

- 音色是男性主持人 `presenter_male`，它在 Phase 0 的古诗、节气、物理三段测试里都是满分。2026-10-09 已用 M Plan 订阅 Key 实测通过。
- 语速取风格预设的 `speed`（`apps/ai-video/presets/styles.yaml`），不读 `tts_style`。
- **停顿**：可以在旁白里插入 `<short pause>` 或 `<long pause>`，流水线会自动换成 MiniMax 的写法（0.3 秒 / 0.8 秒），字幕里也会自动去掉。
  - 适合的位置：诗题和作者之后、诗句与诗句之间、反转之前。
  - 不要滥用，一条片子用几次就够。
  - 其他标签（如 `<sigh>`）MiniMax 不认，会被删掉，不要用。
- **旁白文本照原样朗读。** 不要在旁白里写舞台说明，比如「（缓慢地）」。
- **难读字**：写进剧本的 `pronunciations`，例如 `"天姥": "(tian1)(mu3)"`、`"脚著": "(jiao3)(zhuo2)"`。只有旁白里出现的词才会发给 MiniMax。改了读音后重新导入剧本，用到这些词的镜头会重配音。

## 以后切换：Gemini 3.8 Flash TTS

用户首选这个模型。目前 Key 鉴权没通过（401），所以还在用 MiniMax。等 Key 修好后：

1. 先试音色：`ai-video bench tts --run voices --providers gemini_tts,minimax_tts`，跑一轮盲评，让用户挑。候选音色有 Charon（默认）、Gacrux（成熟）、Sadaltager（博学）、Algenib（沙哑）。
2. 把 `providers.yaml` 的 `routing.tts.provider` 改成 `gemini_tts`，`voice` 改成选中的音色。

Gemini 配音的特点：

- **语气**：由风格预设的 `tts_style` 统一控制，相当于给配音员的导演说明。整条片子共用一个语气，不能按镜头单独设置。
- **停顿标签**：Gemini 原生支持 `<short pause>` 和 `<long pause>`。

切换配音模型后，已生成的配音要删掉项目里的 `audio/` 目录，再重跑 `make`。新配音如果比原来的视频长，合成时会定格补足，所以最好也重做视频：`make --revideo all`。

## 节奏

- 古诗朗诵：语速稍慢，诗句之间留停顿。
- 物理讲解：节奏明快。
- 风格预设里已经写好对应的 `speed` 和 `tts_style`，一般不用再改。
