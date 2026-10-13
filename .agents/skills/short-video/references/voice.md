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

## 剧情片的台词

- **默认由视频模型原生生成**（`providers.yaml` 的 `routing.dialogue: native`）。2026-10-13 试听：H3 自己配的声音比 TTS 做的样本更合人物年纪。
- **声音靠 `voice_desc` 控制**：每个有台词的角色写清年龄、音色、语速，例如「四十七八岁，嗓音低沉略沙哑，说话慢」「七八岁的男孩，声音清亮」。不写的话，模型会自己挑声音，可能不合年纪。
- **不要给台词镜头加 TTS 声音样本**：参考音频只能在参考模式下用，而且试听下来 TTS 样本不合年纪。需要严格一致时，可以从这个角色最满意的一段 H3 原声里截 2–15 秒做样本（还没实测）。
- **TTS 只作兜底**（`routing.dialogue: tts`）：视频只出环境声，台词用 TTS 配，音色取角色的 `tts_voice`，要按人物年纪先试听。口型对不上，只适合远景。
- 剧情片里的画外旁白（`voice: "narration"`）照旧用上面的 TTS。

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
