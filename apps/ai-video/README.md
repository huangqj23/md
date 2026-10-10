# ai-video

AI 短视频流水线，包括成片和模型对比两部分。

- **成片**
  - 选题卡、剧本、分镜和事实核查由 Claude 在 Claude Code 对话里写好，用的是 [`short-video` 技能](../../.agents/skills/short-video/SKILL.md)，产物是一份剧本 JSON。流水线不调用任何文本模型。
  - `ai-video new --script <剧本.json>` 导入剧本，然后依次执行：配音 → 角色参考图 → 关键帧 → 视频 → 合成（字幕、混音、AI 标识）。
  - 每一步都能停下来检查、修改，再接着跑。
- **模型对比**：`ai-video bench` 用同一批用例跑各家模型，盲评打分，产出路由表。

整体方案见 [reports/AI 短视频全流程生成开发计划 v2.md](../../reports/AI短视频全流程生成开发计划v2.md)。

## 账号与 Key

图像、视频、配音都走自己开通的订阅。Key 写在 `.env` 里（复制 `.env.example` 得到，不会提交）。

| 订阅                         | 用到的模型                                                                          | `.env`                           | 说明                                                                                                               |
| ---------------------------- | ----------------------------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 火山方舟 Agent Plan（Large） | Seedance 2.5 / 2.0 / Fast / Mini、Seedream 5.0 Pro                                  | `ARK_PLAN_API_KEY`               | 专属 Key 配专属地址 `/api/plan/v3`，按 AFP 抵扣；套餐条款不允许用 API 调用文本模型；控制台请保持「超额后付费」关闭 |
| MiniMax M Plan（Build）      | H3 视频、speech-2.8 配音                                                            | `MINIMAX_API_KEY`                | 订阅 Key 以 `sk-cp-` 开头，按量 Key 以 `sk-api-` 开头，两者不通用                                                  |
| Gemini API                   | Gemini 3.8 Flash TTS（可选配音，Key 修好后可设为默认）、Nano Banana 2.1（可选图片） | `GEMINI_API_KEY`、`GEMINI_PROXY` | TTS 有免费层；Nano Banana 没有免费层，要开通 Google Cloud 结算；国内需要代理，只有 Gemini 走代理                   |

可灵、豆包语音、GPT Image 2 的按量条目保留在 `providers.yaml` 里，默认关闭（`enabled: false`）。

```powershell
.venv\Scripts\ai-video check --ping   # 核对配置，用不收费的接口验证 Key
.venv\Scripts\ai-video quota          # 方舟今日 / 本期已用 AFP（按本机账本），MiniMax 剩余额度
```

## 出片

在 Claude Code 里输入 `/short-video 立冬 古诗`，Claude 会按下面的流程走：

1. 先出选题卡，你挑一张。
2. 写剧本 JSON，放在 `data/scripts/`。
3. 调用下面这些命令：

```powershell
.venv\Scripts\ai-video new --script data\scripts\20261009-lidong.json --until characters  # 建项目、配音、角色参考图
.venv\Scripts\ai-video sheet                    # 联系表：全部关键帧和角色图拼成一张，方便看图质检
.venv\Scripts\ai-video make --until keyframes   # 关键帧
.venv\Scripts\ai-video make --estimate          # 只报还要花多少（AFP / ¥ / 剩余额度），不执行
.venv\Scripts\ai-video make --draft -y          # 草稿：Seedance 2.0 Mini 480p 先出一版看节奏
.venv\Scripts\ai-video make --revideo all -y    # 正式版：Seedance 2.5 720p
.venv\Scripts\ai-video sheet --what final       # 成片抽帧联系表
.venv\Scripts\ai-video projects                 # 列出所有项目
```

运行规则：

- 阶段顺序是 script → audio → characters → keyframes → videos → compose，`--until` 指定做到哪一步。已经生成的产物不会重做。
- 每个花钱的阶段开始前都会显示预计花费，并核对套餐额度：
  - 「今日已用 + 预估」超过日额度，或「本期已用 + 预估」超过月额度，就不执行；
  - 预计花费超过预算（`--budget` 或 `.env` 的 `BENCH_BUDGET_CNY`）也不执行。
- 在没有终端交互的环境里（例如 Claude 调用时），程序会打印预计花费后退出。看过之后加 `-y` 再执行。
- `make` 不带项目 id 时，处理最近一个项目。

**改了不满意的地方再跑：**

- **改剧本**：改好 JSON 后运行 `ai-video script <项目> <文件>`。程序按镜头比较新旧版本，只清掉受影响的产物：
  - 改了旁白 → 重做配音和视频；
  - 改了首帧画面或参考角色 → 重做关键帧和视频；
  - 改了动作或运镜 → 只重做视频；
  - 改了角色外貌 → 重做这个角色的参考图，以及用到它的所有镜头。
- **单独重做**：`make --redo s03,s05` 重做这些镜头；`make --redo shusheng` 重做这个角色的参考图和用到它的关键帧。
- **只换视频**：`make --revideo s07`，配音和关键帧保留。
- **背景音乐**：`make --bgm 某首歌.mp3`，人声出现时自动压低。如果要用抖音曲库里的歌，就不要加 `--bgm`，发布时在抖音里选。

**项目文件夹** `data/projects/<id>/`：

| 文件                                         | 内容                                                                                |
| -------------------------------------------- | ----------------------------------------------------------------------------------- |
| `project.json`                               | 剧本、角色、分镜，以及每个镜头的旁白、提示词、参考、时长、用了哪个模型              |
| `storyboard.md`                              | 分镜表、角色和事实核查结果，方便阅读                                                |
| `audio/` `characters/` `keyframes/` `clips/` | 配音、角色参考图（全身 + 大头照）、首帧、视频                                       |
| `sheets/`                                    | 联系表图片                                                                          |
| `out/final.mp4`                              | 成片：1080p、30fps、-16 LUFS，字幕烧录，右上角「AI 生成」角标，元数据里有 AIGC 标识 |
| `out/publish.md`                             | 发布用的标题、话题、引用出处和发布前检查清单                                        |
| `out/final.srt` `out/cover.png`              | 字幕文件、封面候选                                                                  |

**路由规则**（`providers.yaml` 的 `routing`）：

- 关键帧和角色参考图用 Seedream 5.0 Pro（1.5K，每张 150 AFP）。
- 视频默认用 Seedance 2.5 720p（`routing.video.default`，改成 `seedance` 就回到 2.0）。
- 写实人脸镜头（分镜里写了 `photoreal: true`）用 MiniMax H3；Seedance 拒收时也自动改用 H3。两家都失败的镜头，合成时用它的关键帧做缓推代替。
- 配音目前用 MiniMax speech-2.8-hd 的男性主持人音色，语速取 `presets/styles.yaml` 里的 `speed`。Gemini 3.8 Flash TTS 的 Key 修好后可以切过去，它的语气由同一文件里的 `tts_style` 控制。

**额度**：

- 计算公式：Seedance 的 AFP = (24 × 秒数 + 1) × 宽 × 高 / 1024 / 10000 × 抵扣系数。
- Seedance 2.5 720p 每秒约 756 AFP（约 ¥1.5），一条 60 秒成片约 6–8 万 AFP；用 2.0 是每秒约 500 AFP、约 4.5–5.5 万 AFP。
- Large 套餐月额度 25 万 AFP，日额度 12.5 万 AFP。

## 安装

```powershell
cd apps/ai-video
py -3.13 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env
```

另需安装 ffmpeg（用于合成和联系表）。

## 模型对比（bench）

```powershell
.venv\Scripts\ai-video bench plan      # 列出全部任务和预计花费，不调用接口
.venv\Scripts\ai-video bench run       # 关键帧 → 视频 → 配音，开始前确认预算和套餐额度
.venv\Scripts\ai-video bench tts --run voices --providers gemini_tts,minimax_tts   # 音色试听
```

- 可以分步跑：`bench images`（先在报告里看关键帧）→ `bench videos` → `bench tts`。
- 默认的测试轮次叫 `round2`，数据存在 `data/bench/round2/`。`data/bench/phase0/` 是之前那轮的结果，不要覆盖。

常用参数：

- `--cases taigong_mist,beauty_closeup`：只跑部分用例（第一次建议先这样小范围试跑）
- `--providers seedance,h3`：只用部分模型
- `--retry-failed`：重新提交被拒绝或失败的任务
- `--budget 100`：本次预算上限
- `-y`：不再询问

**看片打分**：

1. 打开 `data/bench/<run>/report.html`。同一用例下各家结果并排显示，默认隐藏模型名（盲评），打完分再点「显示模型名」。
2. 每条打 1–5 分：视频看画面、运动、遵循提示词；关键帧看画面、遵循；配音看自然度、韵律。
3. 评分存在浏览器里。全部打完后点「导出评分」，然后运行 `bench summary --scores <导出的文件>`，结果写入 `summary.md`。

**测试用例**（`bench/cases.yaml`）大多对标收藏里的爆款：

| 用例              | 对标 / 测什么                                                                                      |
| ----------------- | -------------------------------------------------------------------------------------------------- |
| `taigong_mist`    | 《太公在此》：远景神话人物 + 神兽，冷调电影感                                                      |
| `tang_longtake`   | 《梦回盛唐上元夜》：三张关键帧（后两张以前一张为参考）用首尾帧串成一镜到底；牌匾"醉仙楼"会不会乱码 |
| `qingshan_figure` | 《青山待我时》：国风意境人物背影，9:16                                                             |
| `beauty_closeup`  | 写实古典美女特写：测 Seedance 的写实人脸过滤                                                       |
| `goldleaf_dance`  | 《淡墨藏清骨》：水墨金箔材质，动起来后质感还在不在                                                 |
| `giant_flower`    | 巨物美学：拉远揭示，比例能否保持                                                                   |
| `dragon_palace`   | 《龙藏渊薮》：金龙、宫殿、金光特效                                                                 |
| `long_march`      | 长征：红色题材的审核态度、群像运动                                                                 |
| `vangogh_wheat`   | 名画活化：梵高《麦田与柏树》（大都会博物馆公有领域图）                                             |
| `qingming_scroll` | 长卷一镜到底：《清明上河图》清院本虹桥段                                                           |
| `rainbow_physics` | 物理讲解：主虹外红内紫、副虹顺序相反                                                               |
| `electron_scifi`  | 物理科幻叙事："宇宙里可能只有一个电子"                                                             |

另有 3 段配音（古诗朗诵、节气旁白、物理讲解），每段带一句 `style` 语气说明，Gemini 配音会按它演绎。

## 断点续跑与记账

- 视频任务提交后，先把 task_id 记到 `jobs/`。中断后重跑会继续查询原任务，不会重复付费。
- 每次生成都记在 `data/ledger.jsonl`：方舟的条目带 `plan` 和 `afp` 两个字段，视频完成后按接口返回的 `usage.completion_tokens` 记实际用量；其他条目是估算值。
- 额度闸门只统计本工具自己的调用。控制台和其他工具的用量不在其中，以控制台为准。

## 实测情况（2026-10-09）

已用真实 Key 跑通：

- **方舟 Agent Plan**
  - 四个 Seedance 型号都能用首帧图生视频：2.0（720p，4 秒出 720×1280，229 秒）、2.0 Fast、2.0 Mini、2.5（480p）。
  - 接口返回的 `usage.completion_tokens` 和公式完全一致：2.0 跑 720p 4 秒是 87,300 token，2.5 跑 480p 是 38,830 token（尺寸 480×854）。
  - Seedream 5.0 Pro：
    - 模型 ID 写作 `doubao-seedream-5-0-pro`，1152×2048 尺寸被接受；
    - 文生图约 45 秒，带 1 张或 2 张参考图约 75–86 秒；
    - 不支持「组图」参数 `sequential_image_generation`；
    - 用 base64 返回时出现过一次约 5 分钟的卡死，现在改为返回图片链接再下载。
- **MiniMax M Plan**
  - 订阅 Key 能调 H3：768P 出 768×1344，4 秒的请求实际返回 4.46 秒，自带音轨。
  - 能调 speech-2.8-hd 配音：计费按「汉字算 2 个字符」。
  - 剩余额度接口返回 5 小时窗口和周窗口的剩余百分比。

还没验证：

- **Gemini**：Key 鉴权未通过（Google 返回 `ACCESS_TOKEN_TYPE_UNSUPPORTED`，不认这个 Key），配音和 Nano Banana 的返回字段还没实测过。
- **Seedance**：1080p、文生视频、首尾帧这三种用法。
- **整条流水线**：H3 拒收后的自动兜底，以及用新模型完整出一条片。
- **豆包语音 V3** 接口的字段（按公开文档写的，默认关闭）。

## 代码结构

- `ai_video/providers/`：各家适配器（方舟、MiniMax、Gemini、可灵、豆包语音、OpenAI 图片），统一提供提交、查询、估价（¥ 和 AFP）、验证 Key 的接口；`proxy_env` 让单个服务走代理
- `ai_video/pipeline/`：
  - `script.py`：剧本校验、导入、改动对比；
  - `stages.py`：各阶段执行；
  - `compose.py`：合成；
  - `sheet.py`：联系表。
- `ai_video/quota.py`：套餐额度统计和闸门；`ai_video/ledger.py`：账本
- `ai_video/bench/`：模型对比（任务规划、按厂商分池并发、报告页、评分汇总）
- 测试：`.venv\Scripts\python -m pytest`
