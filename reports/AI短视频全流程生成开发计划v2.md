# AI 短视频全流程生成开发计划 v2：订阅套餐 + Claude 写文案

> 2026-10-09 · 接替 [v1](./AI短视频全流程生成开发计划.md)。v1 里的主流技术调研、四类模板、合规清单和 Phase 0 实测结果仍然有效，本文不再重复。
>
> 本版有三处变化：
>
> 1. 302.AI 中转下线。
> 2. 图像、视频、配音改走自己开通的订阅：方舟 Agent Plan、MiniMax M Plan、Gemini API。
> 3. 所有文案改由 Claude 在对话里写，流水线不再调用任何大模型 API。
>
> 套餐与价格以 2026-10-09 的官方文档为准，做预算前请再到控制台核对。

---

## 0. 结论先行

1. **三家订阅分工如下。**

   | 订阅                     | 负责                                                                                            |
   | ------------------------ | ----------------------------------------------------------------------------------------------- |
   | 方舟 Agent Plan（Large） | Seedance 视频、Seedream 关键帧                                                                  |
   | MiniMax M Plan（Build）  | H3（写实人脸镜头，以及 Seedance 拒收时的自动兜底）、speech-2.8 备用配音                         |
   | Gemini API               | 配音 Gemini 3.8 Flash TTS（Key 修好后设为默认，目前先用 MiniMax）；可选图像模型 Nano Banana 2.1 |

2. **文案不调用任何 API。** 选题卡、剧本、分镜、事实核查都由 Claude 在 Claude Code 对话里完成，写成一份剧本 JSON，再导入流水线。
   - 这个流程由 `short-video` skill 承载。
   - Phase 0 盲评里，Claude 会话内撰写得 5.0 分，排第一。
   - 方舟套餐条款也明确：文本模型不能通过 API 调用。
3. **成本按 AFP 计。**
   - 默认视频模型 Seedance 2.5（2026-10-09 起）：720p 约 756 AFP/秒，折合约 ¥1.51/秒；Seedance 2.0 约 497 AFP/秒。
   - 一条 60 秒成片约 6–8 万 AFP（用 2.0 约 4.5–5.5 万）。Large 套餐每月 25 万 AFP，大约够 3–4 条。
   - 草稿用 2.0-mini 480p，约 115 AFP/秒，折合约 ¥0.23/秒。
4. **角色一致性恢复。** 直连方舟后，Seedream 的参考图可以正常用了。做法是每个角色先出「全身 + 大头照」两张参考图，后面每个镜头的关键帧都带上这两张。
5. **Skill 生态。**
   - 引入 huashu-art-motion，下一阶段接入，用于物理公式镜头和古诗动态文字。它的「一帧先行」「独立审片」两条方法本期就用上。
   - Remotion 和 HyperFrames 暂不引入。

---

## 1. 订阅与 Key 配置

| 服务                | 套餐           | 本项目用到的模型                                                                      | `.env` 变量                      | 接口地址                                                        | 额度                                 |
| ------------------- | -------------- | ------------------------------------------------------------------------------------- | -------------------------------- | --------------------------------------------------------------- | ------------------------------------ |
| 火山方舟 Agent Plan | Large，¥500/月 | `doubao-seedance-2.0` / `-2.0-fast` / `-2.0-mini` / `-2.5`，`doubao-seedream-5-0-pro` | `ARK_PLAN_API_KEY`               | `https://ark.cn-beijing.volces.com/api/plan/v3`                 | 月 250,000 AFP，日 125,000 AFP       |
| MiniMax M Plan      | Build          | `MiniMax-H3`、`speech-2.8-hd`                                                         | `MINIMAX_API_KEY`                | `https://api.minimax.cn`                                        | 额度是 Go 档的 12 倍                 |
| Gemini API          | 按量计费       | `gemini-3.8-flash-tts`、`gemini-nano-banana-2.1`                                      | `GEMINI_API_KEY`、`GEMINI_PROXY` | `https://generativelanguage.googleapis.com/v1beta/interactions` | TTS 有免费层；Nano Banana 没有免费层 |

### 1.1 方舟 Agent Plan

- **Key 和地址都是专属的。** 普通方舟 Key、Coding Plan Key 都不能用在 Agent Plan 上。地址里必须带 `/plan`，填错地址会走按量计费。
- **额度规则。** 视觉模型和语音模型没有 5 小时、周限额，只受日额度（月额度的一半）和月额度约束。
- **超额后付费保持关闭。** 关闭后，额度用完就等下个周期，不会扣账户余额。
- **只有 Large 及以上档能出视频**，Small 和 Medium 只能生图。
- **视觉模型必须写明模型 ID**，Auto 模式和控制台切换对视觉模型无效。

### 1.2 MiniMax M Plan

- **用订阅 Key 直接调原有接口。** 视频走 `/v2/video_generation`，配音走 `/v1/t2a_v2`，按目录价扣套餐额度。视频只受周窗口限制。
- **订阅 Key 以 `sk-cp-` 开头。** 按量 Key 以 `sk-api-` 开头，两者不通用；填成按量 Key 会扣账户余额。`ai-video check` 会检查前缀并提醒。
- **查剩余额度**用 `GET https://www.minimax.cn/v1/token_plan/remains`，也就是 `ai-video quota` 背后调用的接口。
- **音乐 API 从 2026-08-20 起不再对新用户开放。** BGM 改用自备音乐，或者发布时在抖音曲库里选。

### 1.3 Gemini API

- **会员额度不覆盖 API。** Gemini（Google AI Pro）会员的权益只在 Gemini App 和 AI Studio 网页端有效，API 调用另外计费。
  - TTS 有免费层，超出后的价格也很低：60 秒旁白约 $0.014。
  - Nano Banana 2.1 没有免费层，需要在 Google Cloud 项目上开通结算；2K 图约 $0.05/张。
- **AI Studio 新生成的 Key 是 `AQ.` 开头**，旧的是 `AIza`。按请求头 `x-goog-api-key` 传入即可。
- **国内访问要走代理。**
  - `GEMINI_PROXY=http://127.0.0.1:7890` 只给 Gemini 用。
  - 方舟和 MiniMax 仍然直连：`AI_VIDEO_PROXY=off`。

### 1.4 检查命令

```bash
ai-video check --ping   # 三家 Key 是否有效（只调不收费的接口）
ai-video quota          # 方舟：按账本统计今日 / 本期用量；MiniMax：剩余额度
```

---

## 2. 模型路由与成本

### 2.1 路由

| 环节   | 默认                                                   | 备选 / 专用                                                                                                                          | 依据                                                                                 |
| ------ | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| 文案   | **Claude**（对话内撰写）                               | —                                                                                                                                    | Phase 0 盲评 5.0，国内模型 4.25–4.5                                                  |
| 关键帧 | **Seedream 5.0 Pro**（1.5K，每张 150 AFP）             | **Nano Banana 2.1**：封面、画面里需要写字的镜头                                                                                      | Phase 0：Seedream 4.75；Nano Banana 2.1 还没实测                                     |
| 视频   | **Seedance 2.5 720p**（2026-10-09 起默认）             | **H3 768P**：写实人脸镜头，以及 Seedance 拒收时的自动兜底<br>**2.0-mini 480p**：`--draft` 草稿<br>**2.0 / 2.0-fast**：更省额度的选择 | Phase 0（经 302）：Seedance 2.0 4.73、H3 4.33、Seedance 2.5 4.27；按你的选择改为 2.5 |
| 配音   | **MiniMax speech-2.8-hd · 男性主持人**（Phase 0 满分） | Gemini 3.8 Flash TTS（你的首选，Key 修好后切换，音色先试听再定）                                                                     | 2026-10-09 Gemini Key 鉴权未通过，先用 MiniMax                                       |
| BGM    | 自备音乐 / 抖音曲库                                    | —                                                                                                                                    | MiniMax 音乐 API 已不对新用户开放                                                    |

### 2.2 单价

套餐内按 ¥0.002/AFP 折算。

**视频**

计算公式：AFP = 秒数 × 宽 × 高 × 24 / 1024 / 10,000 × 系数。下表按 9:16、不输入参考视频计。

| 模型              | 分辨率           | 系数        | 每秒 AFP | 折合 ¥/秒                |
| ----------------- | ---------------- | ----------- | -------- | ------------------------ |
| Seedance 2.0      | 720p（720×1280） | 230         | ≈ 497    | ≈ 0.99                   |
| Seedance 2.0      | 1080p            | 255         | ≈ 1,239  | ≈ 2.48                   |
| Seedance 2.0-fast | 720p             | 185         | ≈ 400    | ≈ 0.80                   |
| Seedance 2.0-mini | 720p             | 115         | ≈ 248    | ≈ 0.50                   |
| Seedance 2.0-mini | 480p（496×864）  | 115         | ≈ 115    | ≈ 0.23                   |
| Seedance 2.5      | 720p             | 350         | ≈ 756    | ≈ 1.51                   |
| MiniMax H3        | 768P             | M Plan 额度 | —        | 按目录价折算，约 0.5–0.6 |

- Seedance 2.x 的时长只能是 4–15 秒的整数秒（2.5 可到 30 秒），按整秒计费。
  - 不足 4 秒的镜头按 4 秒生成，合成时再裁掉多余部分。
  - 按帧数控制时长的 `frames` 参数只对 Seedance 1.0 有效。
- 套餐内的视频单价和按量目录价（46 元 / 百万 token）一样。套餐的好处在于包月封顶、不扣余额，不在于折扣。

**图像和配音**

| 项目             | 价格                                                             |
| ---------------- | ---------------------------------------------------------------- |
| Seedream 5.0 Pro | 不超过 261 万像素（1.5K 及以下）每张 150 AFP，更大的每张 300 AFP |
| Seedream 参考图  | 第一张免费，第二张起每张 10 AFP                                  |
| Nano Banana 2.1  | 2K 约 $0.05/张，1K 约 $0.034/张                                  |
| Gemini TTS       | 免费层内不花钱；超出后 60 秒约 $0.014                            |
| MiniMax TTS      | 扣 M Plan 额度                                                   |

### 2.3 一条 60 秒成片和每月容量

| 项目         | 用量                        | AFP                                |
| ------------ | --------------------------- | ---------------------------------- |
| 角色参考图   | 1–2 个角色 × 2 张           | 300–600                            |
| 关键帧       | 14 张 × 150（含参考图）     | ≈ 2,300                            |
| 正式视频     | Seedance 2.5 720p，约 70 秒 | ≈ 53,000                           |
| 草稿（可选） | 2.0-mini 480p，约 70 秒     | ≈ 8,000                            |
| 重做余量     | 约 30%                      | ≈ 10,000                           |
| **合计**     |                             | **≈ 60,000–80,000，约合 ¥120–160** |

按这个消耗，Large 套餐每月 25 万 AFP 大约够 **3–4 条**（换回 2.0 约 4–6 条）；单日上限 12.5 万 AFP，大约够 1–2 条。想多出片，有三个办法：

- 次要镜头改用 2.0-fast，便宜 20%。
- 写实人物镜头交给 H3，它走 MiniMax 的额度。
- 升级到 Max 档。

---

## 3. 文案由 Claude 生成：`short-video` skill

### 3.1 为什么做成 skill

- **合规又省钱。** 不调用任何文本模型 API。
- **质量最好。** Phase 0 盲评里 Claude 得分最高，而且在对话里可以随时和你来回改。
- **规则可复用。** skill 就是一份可复用的工作说明书，选题方法、分镜规则、提示词要点、事实核查和质检清单都写在里面，每次开新对话都会按同一套规则执行。
- **位置。** 放在 `.agents/skills/short-video/`，在 Claude Code 里输入 `/short-video 立冬 古诗` 就能开始。

### 3.2 工作流和检查点

```
/short-video <一句话>
  │ ① 环境与额度检查（check --ping、quota）
  ▼
② 选题卡 3–5 张（日历 / 经典库 / 热度；WebSearch 核实时令）──► 你挑一张      【检查点 A】
  ▼
③ 剧本 + 分镜 + 角色设定 → data/scripts/<日期>-<slug>.json
④ 事实核查：古诗逐字核对权威原文；物理、红色题材另派独立 subagent 复核
  ▼
⑤ ai-video new --script … --until characters → 角色参考图
   新风格或新角色先出 3 个方向，让你挑（一帧先行）                         【检查点 B】
  ▼
⑥ 关键帧 → 联系表（ai-video sheet）→ Claude 看图质检 → 改分镜 → ai-video script 增量重做
  ▼
⑦ 报预估（AFP / ¥ / 剩余额度）──► 你确认                                   【检查点 C】
   草稿（2.0-mini 480p）看节奏 → 正式版（Seedance 2.5 720p）
  ▼
⑧ 合成 → 联系表 → 独立 subagent 只看成片挑问题 → publish.md（标题、话题、封面、AI 声明）【检查点 D】
```

### 3.3 剧本 JSON

剧本 JSON 由 Claude 撰写，流水线负责校验后导入：

```json
{
  "prompt": "立冬 古诗 水墨",
  "aspect": "9:16",
  "seconds": 60,
  "style": "guofeng-ink",
  "title": "立冬，古人为什么要“补冬”？",
  "hook": "……",
  "cover_title": "立冬",
  "tags": ["立冬", "二十四节气", "古诗"],
  "source": { "title": "立冬", "author": "李白", "text": "……" },
  "characters": [{ "id": "c1", "name": "撑伞的书生", "look": "二十多岁，青灰色长衫，束发，背着竹编书箱" }],
  "check": { "ok": true, "issues": [] },
  "shots": [
    {
      "id": "s01",
      "narration": "……",
      "image_prompt": "……",
      "video_prompt": "……",
      "camera": "推近",
      "photoreal": false,
      "refs": ["c1"]
    }
  ]
}
```

导入时的校验：

- **硬错误**：缺字段、运镜不在词表里、`refs` 指向不存在的角色。有硬错误就拒绝导入，Claude 修改后再导入。
- **软提示**：镜头数和总字数偏离建议范围、单句旁白过长。只提示，不阻止导入。

重新导入修改后的剧本时，流水线按镜头 id 比较，只清掉受影响的产物：

| 改了什么           | 重做什么                               |
| ------------------ | -------------------------------------- |
| 旁白               | 配音和视频                             |
| 首帧画面或参考角色 | 关键帧和视频                           |
| 动作或运镜         | 只重做视频                             |
| 角色外貌           | 角色参考图，以及引用这个角色的所有镜头 |

### 3.4 命令速查

| 命令                                                                 | 作用                                   |
| -------------------------------------------------------------------- | -------------------------------------- |
| `ai-video new --script FILE [--until 阶段]`                          | 导入剧本、建项目，执行到指定阶段       |
| `ai-video script PROJECT FILE`                                       | 导入修改后的剧本，只重做改动过的镜头   |
| `ai-video make PROJECT --estimate`                                   | 只报预估（AFP / ¥ / 剩余额度），不花钱 |
| `ai-video make PROJECT [--draft] [--revideo all] -y`                 | 继续制作；草稿模式；草稿升级为正式版   |
| `ai-video sheet PROJECT --what keyframes\|clips\|final`              | 生成联系表图片，用于看图质检           |
| `ai-video quota` / `ai-video check --ping`                           | 查额度 / 查 Key                        |
| `ai-video bench tts --run voices --providers gemini_tts,minimax_tts` | 音色试听盲评                           |

阶段顺序：script → audio → characters → keyframes → videos → compose。

---

## 4. 代码改动摘要

1. **删除 302 和大模型调用。**
   - 删除文件：`relay302.py`、`relay_async.py`、`llm.py`。
   - 删除 `relay-models` 命令、bench 的文案（llm）步骤，以及 providers.yaml 里所有 `_302` 条目、`llm:` 段和 `routing.llm`。
   - 删除 `frames` 代码路径：它只对 Seedance 1.0 有效，302 时期配置的 `frames: true` 实际没有生效。
2. **接入订阅套餐。**
   - 方舟条目改为 plan 地址加专属 Key。
   - providers.yaml 新增 `plans` 段（单价折算、日额度、月额度、计费周期）。
   - 视频按 token 公式估算 AFP；任务完成后用接口返回的 `usage.completion_tokens` 记实际 AFP。
   - 账本增加 `afp` 字段。
   - 执行前做额度闸门：「今日已用 + 预估」超过日额度，或「本期已用 + 预估」超过月额度，就拒绝执行。
3. **接入 Gemini。**
   - 新增 `providers/gemini.py`，包含 TTS 和 Nano Banana 两部分，都走 Interactions API。
   - 每个服务可以单独配代理（`proxy_env`），只有 Gemini 走代理。
   - 配音的语气由风格预设的 `tts_style` 控制。
   - 停顿标签（`<long pause>` 等）在字幕里剥掉；MiniMax 不认这类标签，同样剥掉。
4. **剧本导入。**
   - `writer.py` 改为 `script.py`，只负责校验、导入、比较差异和生成分镜表。
   - 新增命令 `new --script`、`script`、`make --estimate`、`sheet`、`quota`。
5. **角色一致性。**
   - 新增 `characters` 阶段，每个角色出全身图和大头照。
   - 关键帧生成时把引用角色的参考图一起带上。
6. **Skill。**
   - 新增 `.agents/skills/short-video/`，包含 SKILL.md 和 references（选题、剧本格式、分镜规则、配音、事实核查、质检）。
   - 按仓库惯例加 `.claude/skills/` 符号链接。本机 `core.symlinks=false`，所以另外建了一个用户级目录联接，让 Claude Code 能发现这个 skill。

---

## 5. Skill 生态评估

只看主流和你点名的项目。

| 项目                                                                                  | 是什么                                                                                                                                             | 结论                 | 理由                                                                                                                     |
| ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| [huashu-art-motion](https://github.com/alchaincyf/huashu-art-motion)（约 2.7k★，MIT） | 用代码画 Canvas 动画：35 种画风（含水墨、敦煌、皮影、新海诚），9 种解说语法（3b1b、白板、动态文字、Kurzgesagt 等），可按 JSON 出时长精确到帧的片段 | **引入**（下一阶段） | 补上我们缺的「程序化画面」：公式、受力图、古诗动态文字零模型成本，而且不会画错物理（Phase 0 里可灵把太阳画进了彩虹中央） |
| [Remotion 官方 skills](https://github.com/remotion-dev/skills)（约 4.9k★）            | 用 React 写视频                                                                                                                                    | 暂不引入             | 和 ffmpeg 合成加 huashu 功能重叠，还要引入 Node/React 渲染栈                                                             |
| [HyperFrames](https://github.com/heygen-com/hyperframes)（HeyGen）                    | 用 HTML 加 GSAP 写视频合成，面向 agent                                                                                                             | 暂不引入             | 以后需要模板化包装（片头、花字、口播包装）时再评估                                                                       |
| byted-ark-seedance / seedream skill（方舟官方）                                       | 在 AI 工具里单次生图、生视频                                                                                                                       | 不需要               | 我们的 CLI 已经有续跑、记账、路由和批量；可以装来临时试单个提示词                                                        |
| [MiniMax CLI（mmx）](https://github.com/MiniMax-AI/cli) 及其 skill                    | 在终端里调 MiniMax 多模态、查额度                                                                                                                  | 可选                 | `mmx quota` 可以当查额度的备用手段                                                                                       |
| Seedance 提示词类 skill（seedance2-skill、seedance-forge 等）                         | 提示词模板库                                                                                                                                       | 不引入               | 星数普遍很低；我们从官方《Seedance 2.0 提示词指南》提炼规则写进自己的 skill                                              |

### 5.1 huashu-art-motion 怎么接

- **能力。**
  - `render.py --spec clip.json` 按 JSON 规格渲染片段，时长等于 `duration`，帧数等于 duration × fps。
  - 支持竖屏（1080×1920）和透明底（ProRes 4444），可以用 `safe.bottom` 给字幕让位。
  - 依赖 uv、Playwright Chromium 和 ffmpeg。
- **接法（Phase 2）。**
  - 剧本里新增镜头类型 `kind: "motion"`，内容是一份 spec。视频阶段对这类镜头调用 `render.py`，生成的 clip 和 AI 视频一样参与合成，不花模型额度。
  - 第二种用法是叠加层：用 `y5_kinetic_type` 渲染透明底的诗句动画，叠在 AI 视频上。
- **适合的方向。**
  - 物理现象、公式和定律：用 t1 3b1b 或 y3 白板语法。
  - 古诗金句和章节卡：用 y5 动态文字。
  - 片头标题。
  - 「名画穿越」长卷，可以和 Seedance 的名画活化配合使用。
- **本期就吸收的方法。**
  - 一帧先行：新风格或新角色先出 3 个方向，挑定后再批量生成。
  - 独立审片：成片交给一个没参与制作的 agent，只看成片挑问题。
  - 拆解脚本 `breakdown.py`：分析参考片的切点和节拍，留给 T2 音乐模板用。
- **许可。** 代码和文档是 MIT；字体是 OFL；花叔的角色形象只限示范使用，不能用在我们的片子里。

---

## 6. 路线图

| 阶段                     | 内容                                                                                                                                                | 验收                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| **Phase 1.5（本期）**    | 订阅切换、删除 302、Gemini 配音、剧本导入、`short-video` skill、角色一致性、额度闸门                                                                | 用 skill 做出一条「立冬 · 古诗」60 秒成片，AFP 在日额度内，字幕和 AI 标识正确 |
| **Phase 2 可控性**       | huashu motion 镜头和诗句叠加；HTML 联系表加重做按钮；开头钩子镜头 best-of-N；Nano Banana 封面（带标题字）；LUT 和颗粒                               | 物理定律一条成片（现象用 AI 视频，公式用代码画）                              |
| **Phase 3 音乐与长镜头** | T2 音乐卡点：自备音乐 → 节拍检测 → 按拍切镜、遮挡转场（复刻《琵琶行》做法）；一镜到底链（前一镜关键帧作参考，配合 `return_last_frame`）；数字人唱歌 | 美景或特效一条成片，外加一条 30 秒一镜到底                                    |
| **Phase 4 选题引擎**     | 节气和纪念日日历、古诗文库和名画库、选题打分去重、系列管理；名画活化；群像叙事                                                                      | 每周一推送 10 个带分数的选题卡                                                |

---

## 7. 风险与待核实

1. **Agent Plan 的使用边界。**
   - 官方文档为视觉模型提供了 API 接入方式，但也提醒：在非 AI 工具里使用套餐的地址和 Key 可能被判为违规。
   - 应对：流水线只在 Claude Code 里由 skill 驱动，不做无人值守的批量任务，并发保持 2。
2. **`doubao-seed-tts-2.0` 的调用方式没有公开**，本期不接。
3. **以下两点要在冒烟测试里确认。**
   - Seedream 的模型 ID 写法：官方表格写 `doubao-seedream-5-0-pro`，命令行示例写 `5.0-pro`。
   - 1.5K 档的自定义尺寸（如 1152×2048）是否被接受。
4. **Gemini 的几个风险。**
   - 有报告说 `AQ.` 格式的 Key 在部分 REST 接口上被拒，以冒烟测试为准。
   - 免费层有限速，而且免费层的数据可能被用于改进 Google 的产品。
   - Google 对使用地区有限制。
5. **Seedance 2.0 拒收写实人脸。** 应对：剧本里标 `photoreal`，这类镜头走 H3；被拒后也会自动转 H3。
6. **限流。** M Plan 有速率限制，高峰期会动态收紧。方舟 Agent Plan 的 TPM 也只按正常开发强度设计。
7. **音乐。** MiniMax 音乐 API 已经关闭，BGM 需要自备，或者在抖音曲库里选；曲库授权只在站内有效。

---

## 8. 来源

- **方舟 Agent Plan**
  - [套餐概览](https://ark.volcengine.com/region:cn-beijing/docs/ark/agent-plan-personal-plan-overview?lang=zh)
  - [快速开始](https://ark.volcengine.com/region:cn-beijing/docs/ark/agent-plan-personal-get-started)
  - [接入视觉模型](https://ark.volcengine.com/region:cn-beijing/docs/ark/agent-plan-personal-visual-models)
  - [AFP 抵扣规则](https://ark.volcengine.com/region:cn-beijing/docs/ark/agent-plan-personal-afp-credits-billing-rules)
  - [常见问题](https://ark.volcengine.com/region:cn-beijing/docs/ark/agent-plan-personal-faq)
- **Seedance**
  - [创建视频生成任务 API](https://ark.volcengine.com/region:cn-beijing/docs/ark/create-video-generation-task-api)（frames、duration、分辨率像素表）
  - [Seedance 2.0 提示词指南](https://docs.volcengine.com/docs/82379/2222480)
  - [模型价格与 token 估算](https://ark.volcengine.com/region:cn-beijing/docs/ark/model-pricing)
- **MiniMax**
  - [M Plan 概览](https://platform.minimaxi.com/docs/m-plan/intro.md)
  - [M Plan 常见问题](https://platform.minimaxi.com/docs/m-plan/faq.md)
  - [用量说明](https://platform.minimaxi.com/docs/m-plan/usage-rules.md)
  - [接口概览](https://platform.minimaxi.com/docs/api-reference/api-overview.md)（含音乐 API 调整通知）
  - [视频生成 V2](https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md)
  - [MiniMax CLI](https://platform.minimaxi.com/docs/m-plan/minimax-cli.md)
- **Gemini**
  - [语音生成](https://ai.google.dev/gemini-api/docs/speech-generation)
  - [图像生成（Nano Banana）](https://ai.google.dev/gemini-api/docs/image-generation)
  - [价格](https://ai.google.dev/gemini-api/docs/pricing)
  - [Google AI 套餐与 API 的关系](https://ai.google.dev/gemini-api/docs/google-ai-plans)
  - [AQ. 格式 Key 讨论](https://discuss.ai.google.dev/t/new-api-keys-generated-with-aq-prefix-dont-work-with-rest-endpoint/176177)
- **Skill 生态**
  - [huashu-art-motion](https://github.com/alchaincyf/huashu-art-motion)
  - [remotion-dev/skills](https://github.com/remotion-dev/skills)
  - [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes)
  - [awesome-claude-video-skills](https://github.com/zhuyansen/awesome-claude-video-skills)
  - [MiniMax-AI/cli](https://github.com/MiniMax-AI/cli)
