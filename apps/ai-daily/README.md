# ai-daily · hollis23 AI 早报

每天自动采集 AI 动态，去重、选题、写中文初稿，落到 Obsidian（`<vault>/AI_Daily/YYYY-MM/`），人工审稿后发公众号。

定位：工程师向，覆盖 LLM、Agent、视觉多模态（主线只用于封面统计，正文不分栏）；每条标可信度，摘要附原文句，程序核对出处。

## 流程

```
collect（官方博客 / 国内大模型公司的官方渠道 / 海外媒体 / newsletter / HF / OpenRouter / GitHub / HN / Reddit / 手动投喂）
  → SQLite 去重（首次出现时间；近 7 天已发或写进过草稿的链接）
  → 选题（LLM 一次调用：合并同一事件、打分、丢掉旧闻）→ 按分数平铺：头条 + 要闻 15 + 快讯 10 + 备选 20
  → 抓原文 + 配图 + 显存估算（HF safetensors 元数据）
  → 写稿（LLM 逐条；原文句模糊匹配核对，数字比对，对不上标【待核对】）
  → 正文 + 审稿清单与备选 + 封面（调用 vault 的 _brand/hollis23/cover.py）
人工审稿（写“我的看法”、处理【待核对】、选标题）
  → ai-daily publish：检查 + 调用 watermark.py 生成内嵌版 + 记录已发链接
  → 用 md 存到公众号草稿箱，手动发布
```

## 信源的取舍

- **媒体只收国外的**（2026-09-30 起）：The Decoder、TechCrunch、The Verge、Ars Technica、MarkTechPost、MIT Technology Review、Techmeme 等。国内媒体（量子位、InfoQ 中文、雷峰网等）在 `sources.yaml` 里注释掉了，要恢复去掉注释即可。
- **国内大模型公司自己的发布照收**（`sources.yaml` 的 `labs` 和 `hf.orgs`，代码在 `ai_daily/collect/labs.py`）：
  - GitHub 组织新建的仓库：deepseek-ai、QwenLM、zai-org、stepfun-ai、MoonshotAI、MiniMax-AI 等 14 个；
  - Hugging Face 上这些公司新传的模型；
  - DeepSeek API 文档的新闻页（模型发布、API 和价格变动）；
  - Z.ai（智谱）的新模型发布页；
  - 阿里巴巴官方新闻站 Alizila（RSS）。
  - Qwen 官网、StepFun、MiniMax、Kimi 的新闻页是前端渲染或没有日期，抓不到；它们的发布靠 GitHub、HF、X 线索（AINews）和国外媒体覆盖。
- **X**：不付费、不抓登录接口；X 上的动态来自 AINews 的回顾和手动投喂。
- **聚合站只当发现渠道**：Techmeme、TLDR、Hacker News 的条目链接指向原文，信源名写原文网站（抓原文时换成网站名），聚合站记在 `via`。Techmeme 自己的链接是当天整页新闻流，不能拿来写稿。
- **OpenRouter 的标价变化**是第三方平台的列表价（常因默认供应商变化而变），不当作厂商调价报道，最多进快讯。

## 只收新动态

- **时间窗口**：从上一期（更早日期）最后一次生成的时间算起，多留 2 小时防漏，至少 12 小时、最多 72 小时；第一次运行取 26 小时。同一天重新生成起点不变，窗口不会越跑越短，也不会把上一期窗口里没选上的条目再捞回来。
- **旧闻识别**：选题时把“窗口开始前已经出现过的动态”（以前的运行采集到的）一起给模型，媒体今天才转述的旧消息不选；所有来源都早于窗口的事件由程序直接丢掉。
- **不重复**：前几天草稿里写过的链接不再选，标题交给选题判断“是不是后续”，没走 `publish` 也算。
- **榜单类信源**：HF 热门模型只收 72 小时内上传的；GitHub 热榜只收一年内创建的项目（创建时间用 GitHub API 查，查过的存进数据库；未认证每小时 60 次，在 `.env` 里设 `GITHUB_TOKEN` 可提高到 5000 次）。
- **只有日期的信源**：TLDR AI 按期号日期算，当期讲的是美国前一天的新闻；早上跑收得到，下午跑时新一期还没发，上一期会被窗口挡掉。

## 配图

头条和要闻每条配一张（`sources.yaml` 的 `layout.max_images`，默认 16），按下面的顺序找，第一张能下载、尺寸够（至少 320×160）、别的条目没用过的就用：

1. 主来源自带的图、主来源网页的 og:image（og:image 只是网站 logo 或没有时，取正文里的前两张图），推文自带的图片或视频封面（X 的嵌入组件接口，免登录）；
2. 同一事件其他来源网页的图；
3. 其他来源自带的图（论文缩略图、其他推文的图）；
4. 推文里附带链接的网页的图；
5. 通用卡片：HF 模型分享卡、GitHub 仓库预览图。

都是第三方图：文件名带 `_nowm`（只内嵌、不加水印），图注写“图源：网站名 / X @账号”。

## 安装

```powershell
cd apps\ai-daily            # 在 md 仓库根目录下；以下命令都在这个目录里运行
E:\python3\python.exe -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env      # vault 路径等；模型和 key 推荐在浏览器面板里配（见下文“模型与 key”）
```

## 用法

```powershell
.venv\Scripts\ai-daily collect --show 20          # 只看各信源采到多少条（不记录条目）
.venv\Scripts\ai-daily run --no-llm --out D:\tmp  # 不花钱试跑：启发式选题、原文节选，单独的试跑库
.venv\Scripts\ai-daily run                        # 正式运行，写到 vault；当天重新生成加 --force（旧稿备份成 .bak）
.venv\Scripts\ai-daily preview                    # 把当天草稿渲染成自带图片的 HTML 预览页（data/previews/）
.venv\Scripts\ai-daily publish                    # 审完稿后：检查 → 内嵌版 → 记录已发链接
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1   # 每天 07:00 自动运行（先配好模型）
```

白天看到的链接贴进 `<vault>/AI_Daily/_inbox.md`（每行一个，可带备注），下次运行会收录；推文用 X 的 oEmbed 免费取正文。

## 让 Claude Code 直接生成

在 vault（`D:\Obisidian`）里打开 Claude Code，说“生成今天的 AI 早报”，或者输入 `/ai-daily`（可以带日期）。流程写在 vault 的 `.claude/skills/ai-daily/SKILL.md` 里：

1. 先检查模型配置、草稿有没有被改过、GitHub API 额度；
2. 用命令行生成；
3. 检查国内媒体、旧闻、主来源、标题、【待核对】和配图；
4. 用 `ai-daily preview` 生成预览页，发成只有你能看的 Artifact 页面；
5. 汇报还要你做的事：写“我的看法”、处理【待核对】、选标题。

## 浏览器面板（md 扩展里的“AI 早报”）

同一仓库的 md Chrome / Edge 扩展（`apps/web`）里有“AI 早报”面板，能在浏览器里生成草稿、看待办、生成内嵌版、载入编辑器，也能在任意网页右键“投喂到 AI 早报”。它通过 Native Messaging 调用本目录的 `ai-daily-host.exe`（`ai_daily/native_host.py`），第一次用时按面板提示注册：

```powershell
.venv\Scripts\ai-daily install-host --extension-id <面板里显示的扩展 ID>
.venv\Scripts\ai-daily uninstall-host        # 需要时注销
```

慢操作（run / publish）在独立的后台进程里跑，状态在 `data/jobs/`，关掉面板不会中断。详细说明见 [docs/ai-daily.md](../../docs/ai-daily.md)。

## 模型与 key

在 md 扩展“AI 早报”面板的“模型设置”里配置（`ai-daily llm-status` 可以在命令行查看当前生效的配置）：

- **两个用途分别选厂商和模型**：选题（一次读 ~200 条候选，用便宜、长上下文的模型）和写稿（逐条写，质量优先），可以是不同厂商。
- **预置厂商**（`ai_daily/providers.py`，地址在 2026-09-30 逐个核实）：DeepSeek、通义千问（百炼）、Kimi、智谱 GLM、MiniMax、豆包（火山方舟）、硅基流动、OpenRouter、OpenAI、Google Gemini、xAI Grok、Claude（Anthropic 官方 SDK）、Ollama / 本机或内网服务。另外可以添加任意 OpenAI 兼容接口（自建 vLLM、代理等）。
- **测试连接**：用面板里当前填的 key（可以还没保存）拉取厂商的模型列表，再用选定模型做一次最小的 JSON 调用。
- **key 的存放**：面板经 native host 写入 `data/llm.json`，key 用 Windows DPAPI 加密（绑定当前 Windows 用户）；面板只能写入，读回来的只有 `sk-…a1b2` 这样的首尾几位，浏览器里不保存。计划任务和后台任务直接读这个文件，不需要开浏览器。
- **Claude**：走 `anthropic` 官方 SDK。Claude Opus 5 等模型默认开启服务端回退 `fallbacks: "default"`（安全分类器拒绝时由 Anthropic 换模型重跑），`stop_reason` 为 `refusal` 时标记该条需人工处理。
- **兼容接口的参数差异**：遇到不支持 `response_format`、只认 `max_completion_tokens`、不接受 `temperature` 的接口，会按报错自动调整后重试。
- **代理**：本机 / 内网地址（以及 Windows 代理设置里“不走代理”的地址）直连，其余走系统代理。httpx2 不认 Windows 的代理绕过列表，开着 Clash 时不这样处理，本机 Ollama、内网 vLLM 会被塞给代理而卡住。
- **输出被截断**：推理模型的思考 token 也算在输出上限里。输出因为上限被截断时，自动把上限翻倍重试（OpenAI 兼容接口最多 64000，Claude 非流式最多 20000）。
- 没有 `data/llm.json` 时，回退到 `.env` 的 `LLM_*`（一个 OpenAI 兼容接口同时用于两个用途）。

## 配置

- 信源：`sources.yaml`（每个都在 2026-09-30 实测可访问；不收的和原因写在文件里）。
- 版面：`sources.yaml` 的 `layout`（要闻、快讯、备选条数，配图上限）；时间窗口：`window`。
- 成本：2026-09-30 实测，选题和写稿都用 deepseek-flash，一期（头条 + 要闻 15 + 快讯 10）约 5.5 万输入 + 5.5 万输出 token（含思考 token），不到 ¥0.3；实际用量写在当天的审稿清单里。
- 测试：`.venv\Scripts\python -m pytest`（全部离线，用 `tests/fixtures/` 里保存的真实响应和脚本化的假 LLM）。
