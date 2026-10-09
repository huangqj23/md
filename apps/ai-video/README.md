# ai-video

AI 短视频流水线。目前是 **Phase 0：模型对比测试**：用同一批测试用例跑各家模型，在报告页并排看片、盲评打分，产出"内容类型 → 模型"的路由表和实测成本。整体计划见 [reports/AI 短视频全流程生成开发计划.md](../../reports/AI短视频全流程生成开发计划.md)。

## 安装

```powershell
cd apps/ai-video
py -3.13 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env
```

## 用 302.AI 中转（推荐，一个 key 全搞定）

在 `.env` 里只填 `RELAY_API_KEY`（302.AI 的 API Key）就能跑。国内网络可以把 `RELAY_BASE_URL` 设为 `https://api.302ai.com`，默认是 `https://api.302.ai`。`providers.yaml` 里 id 以 `_302` 结尾的条目会自动启用：

| 条目                                 | 模型                                 | 302 上的接口                                               |
| ------------------------------------ | ------------------------------------ | ---------------------------------------------------------- |
| `seedance_302` / `seedance_25_302`   | Seedance 2.0 / 2.5（最高 720p）      | `/volcengine/api/v3`：火山方舟原生格式透传                 |
| `kling_302`                          | 可灵 3.0（pro，3–15 秒）             | `/klingai`：可灵官方格式透传，用 302 的 key 鉴权           |
| `h3_302`                             | MiniMax H3（768P）                   | `/minimaxi`：MiniMax 视频 V2 格式透传                      |
| `seedream_302`                       | Seedream 5.0 Pro                     | `/doubao/images/generations`                               |
| `gpt_image_302`                      | GPT Image 2                          | `/v1/images/generations`                                   |
| `doubao_tts_302` / `minimax_tts_302` | 豆包 / MiniMax（speech-2.8-hd）配音  | `/302/tts/generate`（供应商名分别是 `doubao`、`minimaxi`） |
| `deepseek_302` 等                    | LLM 盲评                             | `/v1/chat/completions`                                     |
| `happyhorse_302` / `wan27_302`       | HappyHorse 1.0、万相 2.7（默认关闭） | `/302/v2/video/create` 统一视频接口                        |

文案盲评里另有一个 `claude_manual`：不调用接口，是 Claude 在会话里按同样的任务直接写的，文件放在 `data/bench/<run>/llm/<任务>.claude_manual.md`，状态文件放在 `jobs/llm/`。配置里标了 `manual: true` 的条目只参与报告和评分，不会被调用。

查 302 上有哪些模型和音色：

```powershell
.venv\Scripts\ai-video relay-models                    # 统一视频接口的全部模型和价格（不需要 key）
.venv\Scripts\ai-video relay-models --tts doubao,minimaxi --grep 解说   # 豆包和 MiniMax 的音色（需要 key）
```

两家配音已经各配了 3 个适合旁白的音色，想换就改 `doubao_tts_302` / `minimax_tts_302` 的 `voices`。302 按 PTC 计价（1 PTC ≈ 1 美元）。只走中转时，全量一轮约 ¥450（可灵按标价上限估），建议先用 `--cases` 小范围试跑，再看 302 后台的实际扣费。

## 直连各家（可选）

| 厂商                       | 用途                                             | 要拿到的东西                                                 | 备注                                                             |
| -------------------------- | ------------------------------------------------ | ------------------------------------------------------------ | ---------------------------------------------------------------- |
| 火山方舟                   | Seedance 2.0 视频、Seedream 5.0 图片、豆包大模型 | API Key → `ARK_API_KEY`；在控制台开通对应模型                | Seedance 2.0 的 API 需要企业认证和权限审批；模型 ID 以控制台为准 |
| 可灵开放平台               | 可灵 3.0 视频                                    | AccessKey、SecretKey                                         | 国内账号的接口是 `api-beijing.klingai.com`                       |
| MiniMax 开放平台           | H3 视频、Speech 2.8 配音                         | API Key                                                      | H3 需要在控制台"按量购买"                                        |
| 豆包语音                   | 语音合成 2.0（可选）                             | APP ID、Access Token；在 `providers.yaml` 里填 2–3 个音色 ID |                                                                  |
| OpenAI                     | GPT Image 2（可选）                              | API Key；可用 `OPENAI_BASE_URL` 走中转                       |                                                                  |
| DeepSeek / 百炼 / Moonshot | LLM 盲评（可选）                                 | API Key                                                      | 豆包大模型用 `ARK_API_KEY`，在 `providers.yaml` 里填模型 ID      |

只有火山方舟也能跑（关键帧 + Seedance）；其他厂商填上 key 后自动加入对比，没填的自动跳过。直连和中转的 key 都填会两边都跑、花双份钱，可以用 `--providers` 指定只跑哪些。

## 运行

```powershell
.venv\Scripts\ai-video check --ping    # 核对配置，用不收费的接口验证 key
.venv\Scripts\ai-video bench plan      # 列出全部任务和预计花费，不调用接口
.venv\Scripts\ai-video bench run       # 关键帧 → 视频 → 配音 → 文案，开始前确认预算
```

也可以分步跑：`bench images`（先在报告里看关键帧）→ `bench videos` → `bench tts` → `bench llm`。

常用参数：

- `--cases taigong_mist,beauty_closeup`：只跑部分用例（建议第一次先这样小范围试跑）
- `--providers seedance,kling`：只用部分模型
- `--retry-failed`：重新提交被拒绝或失败的任务
- `--budget 100`：本次预算上限，默认取 `.env` 的 `BENCH_BUDGET_CNY`
- `-y`：不再询问

三家视频模型全量跑一轮大约 ¥200（按 `providers.yaml` 的参考单价估算）。

## 看片打分

打开 `data/bench/phase0/report.html`：

- 同一用例下各家结果并排显示，默认隐藏模型名（盲评），打完分再点「显示模型名」
- 每条打 1–5 分：视频看画面、运动、遵循提示词；关键帧看画面、遵循；配音看自然度、韵律；文案看文采、准确。可以勾「能用」、写备注
- 评分存在浏览器里，全部打完点「导出评分」，得到 `scores-phase0.json`

```powershell
.venv\Scripts\ai-video bench summary --scores $HOME\Downloads\scores-phase0.json
```

结果写入 `data/bench/phase0/summary.md`：视频路由表、各家完成/被拒/失败的数量、平均耗时和花费、被拒任务清单，以及关键帧、配音、文案的排名。

## 测试用例

`bench/cases.yaml`，大多数对标收藏里的爆款：

| 用例              | 对标 / 测什么                                                              |
| ----------------- | -------------------------------------------------------------------------- |
| `taigong_mist`    | 《太公在此》：远景神话人物 + 神兽，冷调电影感                              |
| `tang_longtake`   | 《梦回盛唐上元夜》：三张关键帧用首尾帧串成一镜到底；牌匾"醉仙楼"会不会乱码 |
| `qingshan_figure` | 《青山待我时》：国风意境人物背影，9:16                                     |
| `beauty_closeup`  | 写实古典美女特写，图生和文生各一次：测 Seedance 的写实人脸过滤             |
| `goldleaf_dance`  | 《淡墨藏清骨》：水墨金箔材质，动起来后质感还在不在                         |
| `giant_flower`    | 巨物美学：拉远揭示，比例能否保持                                           |
| `dragon_palace`   | 《龙藏渊薮》：金龙、宫殿、金光特效                                         |
| `long_march`      | 长征：红色题材的审核态度、群像运动                                         |
| `vangogh_wheat`   | 名画活化：梵高《麦田与柏树》（大都会博物馆公有领域图）                     |
| `qingming_scroll` | 长卷一镜到底：《清明上河图》清院本虹桥段，三个重叠窗口串成平移             |
| `rainbow_physics` | 物理讲解：主虹外红内紫、副虹顺序相反，看物理是否正确                       |
| `electron_scifi`  | 物理科幻叙事："宇宙里可能只有一个电子"                                     |

另有 3 段配音（古诗朗诵、节气旁白、物理讲解）和 4 个文案任务（古诗赏析、节气旁白、分镜 JSON、物理事实）。

名画原图首次运行时自动下载（《清明上河图》原图约 104 MB，来自维基共享资源）。下载不了的话，可以手动把图片放到 `data/bench/phase0/images/<用例>/original.jpg`。

## 断点续跑与费用

- 视频任务提交后先把 task_id 记到 `data/bench/<run>/jobs/`，中断后重跑会继续查询原任务，不会重复付费
- 已生成的结果不会重做；被拒绝和失败的结果会保留（拒绝率本身就是测试结果），要重试加 `--retry-failed`
- 每次生成的估算花费记在 `data/ledger.jsonl`，实际以各家账单为准

## 还没用真实 key 验证过的地方

- 豆包语音 V3 接口的字段（按公开文档写）
- Seedance 2.0 是否支持尾帧 `last_frame`：长镜头用例会直接测出来
- 302 透传的 Seedance 查询路径（按方舟原生格式推断为 `/volcengine/api/v3/contents/generations/tasks/{id}`）、LLM 在 302 上的模型名
- 可灵的 `sound` 参数能否和 `image_tail` 一起用
- 各家单价：`providers.yaml` 里是参考价，请改成控制台的实际价格

## 代码结构

- `ai_video/providers/`：各家适配器（火山方舟、可灵、MiniMax、豆包语音、OpenAI 图片、302.AI 统一接口），统一的提交 / 查询 / 估价 / 验证 key 接口；经中转时用 `base_url_env` + `base_path` 换地址
- `ai_video/bench/runner.py`：任务规划、按厂商分池并发、断点续跑、记账
- `ai_video/bench/report.py` + `report.html`：报告页；`summary.py`：汇总评分
- 测试：`.venv\Scripts\python -m pytest`
