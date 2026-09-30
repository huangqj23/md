# 多平台发布（浏览器扩展）

在扩展版编辑器里点「发布」，把当前文章填进各平台的编辑器并保存为草稿。**不会自动点击发布**，每个平台都需要你检查排版后手动发布。

网页版（非扩展）不受影响，仍然使用原来依赖 [COSE](https://github.com/doocs/cose) 扩展的「发布」对话框。

## 使用

1. 构建并加载扩展：
   ```bash
   pnpm web ext:zip        # 或开发模式：pnpm web ext:dev
   ```
   Chrome / Edge 打开「扩展程序 → 开发者模式 → 加载已解压的扩展程序」，选择 `apps/web/.output/chrome-mv3`。
2. 打开编辑器（扩展选项页或侧边栏），点顶部「发布」，或「文件 → 多平台发布」（侧边栏较窄时只有菜单入口）。
3. 首次使用点「授权」，在浏览器弹窗里确认。之后对话框会检测各平台的登录状态；未登录的点「去登录」。
4. 勾选平台，点「同步到草稿」。扩展会依次打开并切换到各平台的标签页（归在一个标签组里），填完后回到编辑器。
5. 在各平台标签页检查排版，手动发布。结果列表里的「查看」可以直接切过去。

## 各平台怎么接收内容

| 平台       | 收到的内容                            | 填写方式                                                      | 草稿                 | 注意                           |
| ---------- | ------------------------------------- | ------------------------------------------------------------- | -------------------- | ------------------------------ |
| 微信公众号 | 公众号样式 HTML（与「复制」按钮一致） | 编辑器 JSAPI `mp_editor_set_content`，失败时模拟粘贴          | 自动点「保存为草稿」 | 标题 ≤ 64 字；内嵌图片自动上传 |
| 知乎       | Markdown                              | 粘贴 Markdown 并确认「确认并解析」；没出现解析提示就改贴 HTML | 平台自动保存         | 公式、外链图片由知乎解析和转存 |
| CSDN       | Markdown                              | Markdown 编辑器 `editor.csdn.net/md`                          | 自动点「保存草稿」   | 标题 ≤ 100 字                  |
| 掘金       | Markdown                              | 编辑器的 CodeMirror 实例                                      | 平台自动保存         |                                |
| 今日头条   | 无样式 HTML（公式退回 TeX 文本）      | 粘贴到 ProseMirror 编辑器                                     | 平台自动保存         | 标题 ≤ 30 字，超出会截断并提示 |
| 百家号     | 公众号样式 HTML                       | 页面上的 UEditor                                              | 自动点「存草稿」     | 标题 ≤ 64 字                   |
| 简书       | Markdown（富文本模式下贴 HTML）       | 先在第一个文集里新建文章，再填内容                            | 平台自动保存         | 至少要有一个文集               |
| B 站专栏   | 公众号样式 HTML                       | 旧版编辑器（`newEditor=-1`）的 UEditor                        | 自动点「存草稿」     | 标题 ≤ 40 字                   |
| 博客园     | Markdown，或 TinyMCE 的 HTML          | 取决于账号设置的默认编辑器                                    | 需手动保存           |                                |

每个平台填完后，扩展会读回编辑器里的内容长度来判断结果：

- **已填入**：标题和正文都在。
- **需检查**：标题被截断、标题没填进去、正文明显不完整，或需要手动保存草稿。
- **需要登录**：页面跳到了登录页。
- **失败**：没找到编辑器（多半是平台改版）或超时。失败信息下方的英文说明是给排查用的，里面带了页面路径、标题和页面上编辑器元素的数量（不含网址参数，不会泄露登录 token），反馈问题时截图这一行即可。

## 图片与公式

- **图片**：公众号会在填写前把内嵌（base64）图片上传到你的图片素材库，再换成素材库链接（WebP 会先转成 PNG，公众号不收 WebP）；没传成功的会在结果里提示。其他平台只能接收公网链接：文章里有内嵌或本地路径的图片时，对话框会提示，先用图床上传再同步。公网图片由平台自行转存或直接引用。
- **公式**：Markdown 平台保留 `$…$` 源码，由平台渲染；公众号、百家号、B 站用 MathJax 渲染的 SVG；头条退回 TeX 文本。扩展页面的 CSP 不允许加载远程脚本，所以构建时会把 MathJax 打包进扩展（`static/libs/mathjax/tex-svg.js`，与 uTools 版同一机制），否则扩展里的公式一直显示「…」。

## 公众号的编辑器地址

新建文章的地址有两种（`type=10` 和 `type=77`）。先打开 `type=10`；如果 20 秒内没出现编辑器，再试 `type=77`；两种都不行才报「没找到编辑器」。登录状态只在编辑器没出现时才判断，而且只看页面上可见的文字：编辑器页面里藏着「扫码确认」之类的隐藏对话框，不能据此判断为未登录。

## 权限

- 平台域名、`scripting`、`cookies`、`tabGroups`（仅 Chrome / Edge）都是**可选权限**，第一次使用时才申请，安装或升级扩展不会新增权限提示。
- 只有扩展自己的页面能发起同步，普通网页无法调用。
- `cookies` 只用来读取 CSDN 的登录 cookie，判断是否已登录。

## 代码结构

```
apps/web/src/
├── publish-agent/                 # 注入到平台页面（MAIN world）运行
│   ├── protocol.ts                # 编辑器与 agent 之间的请求/结果协议
│   ├── dom.ts                     # 通用 DOM 工具：设值、粘贴、等待、找按钮
│   ├── index.ts                   # 按平台和步骤分发
│   └── platforms/<id>.ts          # 各平台的填写步骤
├── entrypoints/publish-agent.ts   # WXT 打包入口 → publish-agent.js
├── services/publish/
│   ├── platforms.ts               # 平台清单：URL、权限、登录检测、标题长度
│   ├── article.ts                 # 生成 Markdown / 无样式 HTML / 公众号 HTML
│   ├── runner.ts                  # 逐个平台开标签页、注入、执行、汇总
│   ├── extension-api.ts           # tabs / scripting 的最小封装
│   ├── permissions.ts             # 可选权限申请
│   └── login.ts                   # 登录状态检测
├── stores/publish.ts
└── components/editor/editor-header/PublishDialog.vue
```

一次同步的流程：对话框 → `buildAgentArticle` → `runPublish` → 每个平台：打开标签页 → 等待加载 → 检查是否跳到登录页 → 注入 `publish-agent.js` → 执行步骤（可能先 `prepare` 再跳转到 `fill`）→ 读回结果。

## 新增或修改平台

1. `publish-agent/protocol.ts` 的 `PUBLISH_PLATFORM_IDS` 加上平台 id。
2. 新建 `publish-agent/platforms/<id>.ts`，实现 `fill`（需要先建草稿或拿 token 时再加 `prepare`，返回 `navigate(url, 'fill')`），并在 `publish-agent/index.ts` 注册。
3. `services/publish/platforms.ts` 加一项：`startUrl`、`hostPermissions`（要覆盖用到的所有 URL）、`loginUrlPattern`、`format`、`detectLogin` 等。
4. 四个语言的 `i18n/messages/*/publish.ts` 里加 `publish.platforms.<id>`。
5. 测试：`platforms.test.ts` 会检查权限是否覆盖所有 URL；在 `publish-agent/platforms/platforms.test.ts` 为新平台加一个页面结构的测试。
6. `pnpm web test`，然后重新构建扩展，在扩展管理页点「重新加载」。

平台改版导致选择器失效时，先看失败信息里的英文说明，再对照 [doocs/cose](https://github.com/doocs/cose) 和 [MultiPost-Extension](https://github.com/leaperone/MultiPost-Extension) 里同一平台的最新实现。两者都是 Apache-2.0，本功能参考了它们的做法，见 `apps/web/src/publish-agent/THIRD_PARTY_NOTICES.md`。

## 已知限制

- 只在 Chromium（Chrome / Edge）上验证过；Firefox 构建能生成，但没有测试。
- 各平台的页面结构和接口参照 COSE / MultiPost 的实现，本仓库的测试只用模拟页面验证了流程；平台改版后需要跟着更新。
- 不会自动点击「发布」。
