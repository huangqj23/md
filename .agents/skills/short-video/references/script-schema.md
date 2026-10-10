# 剧本 JSON

一个文件就是一条片子。写好后：

- 新建项目：`ai-video new --script <文件>`
- 修改已有项目：`ai-video script <项目> <文件>`

## 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `prompt` | 是 | 选题，一句话 |
| `aspect` | | `9:16`（默认）、`16:9`、`1:1` |
| `seconds` | | 目标时长（秒），正整数，默认 60 |
| `style` | | 风格预设 id，见 `apps/ai-video/presets/styles.yaml`：`guofeng-ink`、`cinematic-guofeng`、`epic-myth`、`red-epic`、`physics-clean` |
| `title` | | 抖音标题，20 字以内，带钩子 |
| `hook` | | 第 1 个镜头的旁白 |
| `cover_title` | | 封面大字，8 字以内；成片开头会叠加 |
| `tags` | | 话题，不带 `#` 也行 |
| `source` | | 引用的经典：`{"title", "author", "text"}`，`text` 是核对过的原文 |
| `characters` | | 角色：`[{"id", "name", "look"}]` |
| `pronunciations` | 有难读字时必填 | 读音词典：`{"天姥": "(tian1)(mu3)", "訇然": "(hong1)(ran2)"}`，逐字写拼音加声调数字。MiniMax 配音按它读；古诗里的多音字、生僻字（姥 mǔ、著 zhuó、殷 yǐn、剡 shàn……）都要写 |
| `check` | 强烈建议 | 事实核查：`{"ok": true, "issues": [{"shot", "problem", "fix"}], "checker": "Claude"}` |
| `shots` | 是 | 至少 2 个镜头 |

角色：

- `id` 用小写英文，例如 `shusheng`，不能是 `prev`，也不能长得像镜头编号（`s01` 之类）。
- `look` 写死年龄、发型、服饰和颜色，用来生成全身和大头两张参考图。所有镜头共用这一份描述。

镜头的字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | | 默认按顺序编号 `s01`、`s02`……。**修改剧本时保持原来的 id 不变**；插入新镜头时可以自己起 id（例如 `s03b`），这样其他镜头不会被当成改动 |
| `narration` | 是 | 旁白，一句，12–22 个汉字 |
| `image_prompt` | 是 | 首帧画面，40–90 字 |
| `video_prompt` | 是 | 这个镜头里的一个主要动作，20–50 字 |
| `camera` | | 只能是 推近、拉远、横移、环绕、升起、下降、跟拍、固定 之一 |
| `photoreal` | | `true` 表示画面里有清晰可见的写实人脸，这类镜头走 H3（Seedance 不收写实人脸） |
| `refs` | | 参考图：角色 id，和 / 或 `"prev"`（上一个镜头的关键帧）。每个镜头最多 2 个角色，第 1 个镜头不能用 `prev` |

## 导入时的检查

**硬错误**会拒绝导入，退出码 2，同时列出问题：

- 缺 `prompt`，或镜头少于 2 个；
- 某个镜头缺旁白、首帧画面或动作；
- `aspect` 或 `camera` 不在可选范围；
- 角色 id 格式不对，或者缺 `look`；
- `refs` 引用了没声明的角色，或者第 1 个镜头用了 `prev`。

**软提示**只打印，不阻止导入：

- 镜头数不在建议范围：约为 秒数/5.5 到 秒数/3.8；
- 旁白总字数不在 秒数 × 3.7 到 秒数 × 4.2 之间（各留 15% 余量）；
- 单句旁白超过 30 字；
- 没有填 `check`。

## 改剧本后会重做什么

`ai-video script` 按镜头 id 比较新旧两版，只清掉受影响的产物：

| 改动 | 重做 |
| --- | --- |
| `narration` | 配音和视频 |
| `image_prompt`、`refs`，或引用的角色 `look` 变了 | 关键帧和视频；下一个镜头如果引用了 `prev`，也一起重做 |
| `video_prompt`、`camera`、`photoreal` | 只重做视频 |
| 角色的 `look` | 这个角色的参考图，以及引用它的镜头 |
| `aspect` 或 `style` | 所有画面 |
| 删掉的镜头 | 删除它的文件 |

## 示例

```json
{
  "prompt": "霜降 古诗 水墨",
  "aspect": "9:16",
  "seconds": 60,
  "style": "guofeng-ink",
  "title": "霜降：古人为什么说它是秋天的告别？",
  "hook": "你知道霜，是从哪里来的吗？",
  "cover_title": "霜降",
  "tags": ["霜降", "二十四节气", "古诗"],
  "source": {"title": "山行", "author": "杜牧", "text": "远上寒山石径斜，白云生处有人家。停车坐爱枫林晚，霜叶红于二月花。"},
  "characters": [
    {"id": "shusheng", "name": "书生", "look": "二十多岁的男子，束发，青灰色长衫，背竹编书箱，身形清瘦"}
  ],
  "check": {"ok": true, "issues": [], "checker": "Claude"},
  "shots": [
    {
      "narration": "你知道霜，是从哪里来的吗？",
      "image_prompt": "深秋清晨的枫林，近景是一片枫叶，叶面结着细密的白霜，背景虚化的山路延伸进薄雾，冷蓝与暖红对比，低机位特写",
      "video_prompt": "叶面上的霜花慢慢凝结，薄雾在背景里缓缓流动",
      "camera": "推近"
    },
    {
      "narration": "停车坐爱枫林晚，霜叶红于二月花。",
      "image_prompt": "书生的背影站在蜿蜒的石径上，远处是层林尽染的寒山和几户人家，夕阳斜照，远景，人物很小",
      "video_prompt": "书生停下脚步，抬头望向满山红叶",
      "camera": "拉远",
      "refs": ["shusheng"]
    }
  ]
}
```
