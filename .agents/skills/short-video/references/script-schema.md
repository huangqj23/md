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

## 剧情片（`template: "drama"`）

剧情片的每个镜头时长写在剧本里，台词由视频模型在生成画面时一起说出来。先读 [templates.md](templates.md) 选模板。

项目级新增：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `template` | 是 | 写 `"drama"`；不写就是旁白片 |
| `aspect` | | 剧情片用 `"16:9"` |
| `seconds` | | 目标时长，各镜头 `seconds` 合计和它差 20% 以上会提示 |
| `style` | | `film-history`（历史群像、纪念人物）或 `pov-drama`（第一人称） |
| `letterbox` | | 黑边里画面的宽高比，例如 `2.35`；台词显示在下方黑边里 |
| `notice` | | 右侧竖排的小字声明，例如「剧情演绎，无不良导向」「改编自相关史料」 |
| `credits` | | 片尾署名，字符串列表 |
| `scenes` | | 场景参考：`[{"id", "name", "look"}]`，每个场景出一张 16:9 参考图，镜头用 `refs` 引用；每个镜头最多 1 个场景 |
| `cover_title` | | 片名，第 5 秒在左上角以《》小字出现，也用于封面 |

角色新增：

- `voice_desc`：**有台词的角色必写**。声音的文字描述（年龄、音色、语速），例如「四十七八岁，嗓音低沉略沙哑，说话慢」，会拼进台词镜头的提示词，让视频模型按人物年纪配音。
- `tts_voice`（可选）：MiniMax 音色 id，只在台词改用 TTS 配（`routing.dialogue: tts`）时用。

镜头字段（剧情片）：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `seconds` | 是 | 这个镜头的时长，1–30 的整数秒。视频模型最短 4 秒，更短的按 4 秒生成再裁掉；H3 最长 15 秒 |
| `voice` | | 声音来源：`ambient`（默认，只要环境声）、`dialogue`（台词）、`narration`（画外旁白，用 TTS）、`none`（只有音乐） |
| `speaker` | 台词镜头必填 | 说话的角色 id |
| `line` | 台词镜头必填 | 一句台词，12 字以内。**不要再写进 `video_prompt`**：流水线会拼成「杜甫（声音描述）说：「……」」 |
| `caption` | | 史实注释，台词上方的灰色小字，20 字以内，例如「（759 年，杜甫弃官西行）」 |
| `label` | | 章节标签：人物名、人生阶段或「地点 · 事件」。只在变化时显示，左上角 3 秒 |
| `image_prompt` | 首帧模式必填 | 首帧画面 |
| `video_prompt` | 是 | 动作，只写一个主要动作 |
| `mode` | | `frame`（默认，首帧图生视频）或 `ref`（参考模式：用 `refs` 里的角色图和场景图，不画关键帧，构图由模型定）。只在大幅运镜或多人同框时用 `ref` |
| `transition` | | 进入这个镜头的方式：`cut`（默认）、`dissolve`（叠化 0.6 秒）、`black`（经黑场 1 秒） |
| `hero` | | `true` 表示重点镜头（钩子、高潮），配了 `routing.video.hero` 时单独走那个模型 |
| `kind` | | 写 `"card"` 表示**金句卡**：只要 `text` 和 `seconds`，可选 `over`（叠在哪个镜头的最后一帧上，不写就是黑场）。合成时用书法字体画，一个分句一行，逗号和句号不显示；不调用任何模型 |

剧情片示例（节选）：

```json
{
  "prompt": "杜甫 弃官 秦州 古诗",
  "template": "drama",
  "aspect": "16:9",
  "seconds": 100,
  "style": "film-history",
  "letterbox": 2.35,
  "title": "五个诗人，五次离开",
  "cover_title": "离开",
  "notice": "改编自相关史料 · 剧情演绎",
  "credits": ["出品：ai-video"],
  "characters": [
    {"id": "dufu", "name": "杜甫", "look": "四十七八岁的唐代男子，清瘦，灰白短须，黑色幞头，洗旧的青灰色圆领长袍",
     "voice_desc": "四十七八岁，嗓音低沉略沙哑，说话慢"}
  ],
  "scenes": [{"id": "longshan", "name": "陇山道", "look": "深秋傍晚的黄土山道，枯草，远处土筑关隘"}],
  "shots": [
    {"id": "s05", "label": "杜甫", "caption": "（759 年，杜甫弃官，携家西行）", "seconds": 6,
     "voice": "dialogue", "speaker": "dufu", "line": "这官，不做了", "transition": "dissolve",
     "image_prompt": "黄昏的官署门口，杜甫把叠好的官服放在石阶上，侧面中远景", "video_prompt": "他把官服轻轻放下，直起身，转身离开",
     "camera": "固定", "refs": ["dufu"]},
    {"id": "s06", "seconds": 8, "mode": "ref", "refs": ["dufu", "longshan"],
     "video_prompt": "深秋的陇山道上，他牵着一匹瘦马从左往右走过转弯处，远处关隘渐渐露出", "camera": "跟拍"},
    {"id": "c02", "kind": "card", "text": "露从今夜白，月是故乡明", "seconds": 4, "over": "s06", "transition": "black"}
  ]
}
```

## 导入时的检查

**硬错误**会拒绝导入，退出码 2，同时列出问题：

- 缺 `prompt`，或镜头少于 2 个；
- 某个镜头缺旁白、首帧画面或动作；
- `aspect` 或 `camera` 不在可选范围；
- 角色 id 格式不对，或者缺 `look`；
- `refs` 引用了没声明的角色，或者第 1 个镜头用了 `prev`。

剧情片另外检查：缺 `seconds`；台词镜头缺 `line` 或 `speaker`；参考模式的 `refs` 里没有角色或场景；金句卡缺 `text`，或 `over` 不是它前面的画面镜头；`voice`、`mode`、`transition` 不在可选范围；`prev` 的上一个镜头没有关键帧（金句卡或参考模式）；场景 id 和角色重复。

**软提示**只打印，不阻止导入：

- 镜头数不在建议范围：约为 秒数/5.5 到 秒数/3.8；
- 旁白总字数不在 秒数 × 3.7 到 秒数 × 4.2 之间（各留 15% 余量）；
- 单句旁白超过 30 字；
- 没有填 `check`。
- 剧情片：各镜头合计时长偏离 `seconds`；台词超过 12 字、注释超过 20 字；估算的有人说话时间不在 15–70%（对标爆款 20–60%）；第 1 个镜头没有台词也没有注释；同一角色台词镜头超过 6 个；有台词的角色没写 `voice_desc`；镜头不到 4 秒。

## 改剧本后会重做什么

`ai-video script` 按镜头 id 比较新旧两版，只清掉受影响的产物：

| 改动 | 重做 |
| --- | --- |
| `narration` | 配音和视频 |
| `image_prompt`、`refs`，或引用的角色 `look` 变了 | 关键帧和视频；下一个镜头如果引用了 `prev`，也一起重做 |
| `video_prompt`、`camera`、`photoreal` | 只重做视频 |
| 角色的 `look` | 这个角色的参考图，以及引用它的镜头 |
| `aspect` 或 `style` | 所有画面 |
| 剧情片的 `line`、`speaker`、`voice`，或说话角色的 `voice_desc` | 这个镜头的视频（台词在视频原声里） |
| 剧情片的 `seconds`、`mode`、`hero` | 只重做视频 |
| 场景的 `look` | 这个场景的参考图，以及引用它的镜头 |
| `caption`、`label`、`transition`、金句卡、`letterbox`、`notice`、`credits` | 不重做任何东西，重新合成即可（`make <id> -y`） |
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
