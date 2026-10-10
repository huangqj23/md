# AI 早报面板（浏览器扩展）

在扩展版编辑器里操作本机的 ai-daily（本仓库的 [`apps/ai-daily`](../apps/ai-daily/README.md)）早报流程：查看待办、生成内嵌版、载入编辑器，然后用「发布」存到各平台草稿箱。**草稿在 Claude Code 里生成**（在 vault 里说“生成今天的 AI 早报”，见 ai-daily 的 README；面板的「生成草稿」按钮 2026-10-10 去掉了），**改稿在 Obsidian 里进行**，面板只负责看状态和触发步骤，文件始终只有 vault 里那一份。

只在 Chrome / Edge 版扩展里可用（依赖 Native Messaging）；网页版和 Firefox 版不显示入口。

## 第一次使用

1. 在 `apps/ai-daily` 目录装好环境（见 [ai-daily 的 README](../apps/ai-daily/README.md#安装)），`pip install -e .` 会生成 `ai-daily-host`（Windows 在 `.venv\Scripts\`，macOS / Linux 在 `.venv/bin/`）。
2. 构建扩展（`corepack pnpm --filter @md/web ext:zip`），然后在 ai-daily 目录运行一次 `install-host`（Windows 是 `.venv\Scripts\ai-daily install-host`，macOS 是 `.venv/bin/ai-daily install-host`）。不带参数时，它按 `apps/web/.output/chrome-mv3` 的路径算出“加载已解压的扩展程序”后的扩展 ID（Windows 按 UTF-16LE、其他系统按 UTF-8 对路径取 SHA-256），不用先去面板里抄 ID。登记方式：

   - Windows：写 `HKCU\Software\{Google\Chrome, Microsoft\Edge, Chromium}\NativeMessagingHosts\com.hollis23.ai_daily`（不需要管理员）；
   - macOS / Linux：把 manifest 写进各浏览器的 `NativeMessagingHosts/` 目录，没装的浏览器跳过。macOS 上还包括豆包桌面版的内置浏览器（`~/Library/Application Support/Doubao/NativeMessagingHosts/`）。

   这一步只能在终端里做：Chrome 不允许扩展自己登记本机程序。

3. 在浏览器扩展页面加载 `apps/web/.output/chrome-mv3`，打开扩展编辑器，点顶部「AI 早报」（或「文件 → AI 早报」）。`nativeMessaging` 是 Chrome / Edge 版的必需权限（2026-10-02 起；以前是可选权限，要在面板里点「授权」），加载扩展时就有了，不用再授权。旧版扩展升级上来时面板仍会显示「授权」按钮，点一下即可。
4. 从别的目录加载、或者开发版和打包版 ID 不同时，面板会提示“还没有连接本机的 ai-daily”，并给出带本扩展 ID 的命令（`install-host --extension-id <ID>`），运行后点「重新检测」。多个 ID 会合并到同一个 manifest。

## 每天的流程

| 步骤       | 面板里                               | 说明                                                                                                                                                                                                          |
| ---------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 生成草稿   | 不在面板里                           | 在 Claude Code 里说“生成今天的 AI 早报”（`ai-daily prepare / materials / finalize`，选题和写稿由 Claude 完成）                                                                                                |
| 审稿       | 「在 Obsidian 打开正文」「审稿清单」 | 面板列出【待核对】所在条目、“我的看法”是否已写                                                                                                                                                                |
| 生成内嵌版 | 「生成内嵌版」                       | 先做发布前检查，有问题直接列出来；通过后在独立进程里跑 `ai-daily publish`（加水印、内嵌图片、记录已发链接），关掉面板也不会中断（任务进程用 `CREATE_BREAKAWAY_FROM_JOB` 脱离 Chrome 为 native host 建的 Job） |
| 载入编辑器 | 「载入编辑器」                       | 把内嵌版读进编辑器；同一天再次载入会更新同一篇文章，不会新建                                                                                                                                                  |
| 发布       | 顶部「发布」                         | 已有的多平台发布，公众号会自动上传内嵌图片                                                                                                                                                                    |

投喂：在面板的「手动投喂」里贴链接，或在任意网页 / 链接上右键「投喂到 AI 早报」；工具栏图标闪 ✓ 表示已写进 `AI_Daily/_inbox.md`，闪 ! 表示本机 host 没连上。

## 模型设置

面板的「模型设置」页（只给命令行的 `ai-daily run` 用；Claude Code 生成早报不调用模型）：

- **用哪个模型**：选题、写稿分别选厂商和模型（模型名可下拉选择，也可以直接输入）。
- **厂商与 key**：预置 DeepSeek、通义千问、Kimi、智谱、MiniMax、豆包、硅基流动、OpenRouter、OpenAI、Gemini、xAI、Claude、Ollama / 本机或内网服务；也可以「添加 OpenAI 兼容接口」（自建 vLLM、代理等，http 只允许本机或内网地址）。
- **测试连接**：用当前填的 key（可以还没保存）拉取模型列表，并用选定模型做一次最小的调用；拉到的模型会加进下拉列表。
- **key 的去向**：key 经 native host 写到 ai-daily 的 `data/llm.json`：Windows 上用 DPAPI 加密，macOS 上存进登录钥匙串（条目 `hollis23-ai-daily`，`llm.json` 里只记条目名）。扩展只写入、不回读明文（面板上只显示 `sk-…a1b2`），`chrome.storage` 和云同步里都没有 key。计划任务不开浏览器也能用同一份配置。

## 协议

扩展 → `runtime.sendNativeMessage("com.hollis23.ai_daily", {cmd, ...})`，host 是 ai-daily 的 `ai_daily/native_host.py`：

| cmd                        | 作用                                                               |
| -------------------------- | ------------------------------------------------------------------ |
| `ping`                     | 版本、vault 路径                                                   |
| `status`                   | 草稿、【待核对】、发布前检查、内嵌版是否过期、封面、当前任务       |
| `publish`                  | 启动后台任务（同一时间只跑一个）；检查不过时直接返回问题列表       |
| `job`                      | 任务状态和日志末尾                                                 |
| `read_embed`               | 分块读取内嵌版（单条回复上限 1 MB）                                |
| `open`                     | 用 `obsidian://open` 在 Obsidian 里打开正文 / 审稿清单 / 收件箱    |
| `inbox_add` / `inbox_list` | 手动投喂                                                           |
| `llm_get`                  | 模型配置（厂商、用途、key 是否已保存和首尾几位，不含明文）         |
| `llm_set`                  | 保存：只发改动过的字段；`api_key` 有值才更新，`clear_key` 清除     |
| `llm_test`                 | 测试连接，结果在 `success` / `error` 里（`ok` 是协议字段，不占用） |

## 代码结构

```
apps/web/src/
├── services/ai-daily/native.ts          # sendNativeMessage 封装、错误分类、分块读取
├── services/ai-daily/context-menu.ts    # 右键“投喂到 AI 早报”
├── services/ai-daily/llm.ts             # 模型设置的类型和“只发改动”的更新计算
├── components/editor/editor-header/AiDailyLlmSettings.vue   # 模型设置页
├── stores/aiDaily.ts                    # 状态、任务轮询、载入编辑器
└── components/editor/editor-header/AiDailyDialog.vue
```
