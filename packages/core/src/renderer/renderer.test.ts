import { describe, expect, it } from 'vitest'
import { postProcessHtml, renderMarkdown } from '../utils/markdownHelpers'
import { initRenderer } from './renderer-impl'

describe('initRenderer', () => {
  it('renders headings and paragraphs', () => {
    const renderer = initRenderer({})
    const { html } = renderMarkdown(`# Hello\n\nWorld`, renderer)

    expect(html).toContain(`<h1`)
    expect(html).toContain(`Hello`)
    expect(html).toContain(`World`)
  })

  it('strips script tags during sanitization', () => {
    const renderer = initRenderer({})
    const { html } = renderMarkdown(`<script>alert(1)</script>\n\nSafe text`, renderer)

    expect(html).not.toContain(`<script>`)
    expect(html).toContain(`Safe text`)
  })

  it('renders GFM alert blocks', () => {
    const renderer = initRenderer({})
    const { html } = renderMarkdown(`> [!NOTE]\n> Alert body`, renderer)

    expect(html).toContain(`markdown-alert`)
    expect(html).toContain(`Alert body`)
  })

  it('parses YAML front matter', () => {
    const renderer = initRenderer({})
    const { markdownContent, readingTime, yamlData } = renderer.parseFrontMatterAndContent(
      `---\ntitle: Test\n---\n\n# Body`,
    )

    expect(yamlData).toEqual({ title: `Test` })
    expect(markdownContent.trim()).toBe(`# Body`)
    expect(readingTime.words).toBeGreaterThan(0)
  })

  it('includes reading time stats in postProcessHtml output', () => {
    const renderer = initRenderer({ countStatus: true, isMacCodeBlock: false })
    const { html, readingTime } = renderMarkdown(`# Hi`, renderer)
    const output = postProcessHtml(html, readingTime, renderer)

    expect(output).toContain(`words`)
    expect(output).toContain(`Hi`)
  })

  it('uses injected renderMessages for footnotes and unknown components', () => {
    const renderer = initRenderer({
      citeStatus: true,
      renderMessages: {
        footnoteTitle: `引用リンク`,
        unknownComponent: `不明: {name}`,
        katexLoading: `数式読込中`,
      },
    })

    const withCite = renderMarkdown(`[Doocs](https://github.com/doocs)`, renderer)
    const withCiteHtml = postProcessHtml(withCite.html, withCite.readingTime, renderer)
    expect(withCiteHtml).toContain(`引用リンク`)

    const unknown = renderMarkdown(`<FakeWidget foo="1" />`, renderer)
    expect(unknown.html).toContain(`[不明: FakeWidget]`)
  })

  it('uses injected countMessages summary template', () => {
    const renderer = initRenderer({
      countStatus: true,
      countMessages: { summary: `単語 {words} / {minutes} 分` },
    })
    const { html, readingTime } = renderMarkdown(`# Hi`, renderer)
    const output = postProcessHtml(html, readingTime, renderer)
    expect(output).toMatch(/単語 \d+ \/ \d+ 分/)
  })

  it('renders single-line block formula as katex-block without paragraph wrapper', () => {
    const renderer = initRenderer({})
    const formula = `$$ITE_{i}=Y_{i,1}-Y_{i,0} \\tag{1}$$`
    const { html } = renderMarkdown(formula, renderer)

    expect(html).toContain(`katex-block`)
    expect(html).toContain(`data-math-raw`)
    expect(html).not.toMatch(/<p[^>]*>\s*<section class="katex-block"/)
  })

  it('renders list item followed by single-line block formula without paragraph wrapper', () => {
    const renderer = initRenderer({})
    const userMd = `1.比如识别段落之间带有编号的latex公式，如 

$$ITE_{i}=Y_{i,1}-Y_{i,0} \\tag{1}$$`
    const { html } = renderMarkdown(userMd, renderer)

    expect(html).toContain(`data-math-raw`)
    expect(html).toContain(`\\tag{1}`)
    expect(html).not.toMatch(/<p[^>]*>\s*<section class="katex-block"/)
  })

  it('collects headings in document order with plain text', () => {
    const renderer = initRenderer({})
    renderMarkdown(`# Title\n\n## Sub \`code\` & **bold**\n\nBody\n\n### Third`, renderer)

    expect(renderer.getHeadings()).toEqual([
      { level: 1, text: `Title` },
      { level: 2, text: `Sub code & bold` },
      { level: 3, text: `Third` },
    ])
  })

  it('decodes named and numeric entities in heading text like textContent', () => {
    const renderer = initRenderer({})
    renderMarkdown(`# Fish &amp; Chips &mdash; &#x2026; &nbsp;end`, renderer)

    expect(renderer.getHeadings()).toEqual([
      { level: 1, text: `Fish & Chips — … \u00A0end` },
    ])
  })

  it('includes the footnote title after postProcessHtml', () => {
    const renderer = initRenderer({
      citeStatus: true,
      renderMessages: { footnoteTitle: `脚注`, unknownComponent: ``, katexLoading: `` },
    })
    const { html, readingTime } = renderMarkdown(`# Doc\n\n[link](https://example.com)`, renderer)
    postProcessHtml(html, readingTime, renderer)

    const headings = renderer.getHeadings()
    expect(headings[0]).toEqual({ level: 1, text: `Doc` })
    expect(headings[headings.length - 1]).toEqual({ level: 4, text: `脚注` })
  })

  it('clears collected headings on reset', () => {
    const renderer = initRenderer({})
    renderMarkdown(`# Old`, renderer)
    expect(renderer.getHeadings()).toHaveLength(1)

    renderer.reset({})
    expect(renderer.getHeadings()).toHaveLength(0)

    renderMarkdown(`## New`, renderer)
    expect(renderer.getHeadings()).toEqual([{ level: 2, text: `New` }])
  })

  it('resolves <Emoji> tags through assetResolver', () => {
    const renderer = initRenderer({
      assetResolver: id => `https://cdn.example/${id}.png`,
    })
    const { html } = renderMarkdown(`hello <Emoji id="liulei" alt="流泪" />`, renderer)

    expect(html).toContain(`md-emoji`)
    expect(html).toContain(`https://cdn.example/liulei.png`)
    expect(html).toContain(`data-emoji-id="liulei"`)
    expect(html).not.toContain(`about:blank`)
  })
})

describe('tables (scroll sideways in WeChat)', () => {
  const wide = [
    `| 方法 | SoccerNet 2022 HOTA / IDF1 / MOTA | MOT17 HOTA / IDF1 / MOTA | 说明 |`,
    `| --- | --- | ---: | --- |`,
    `| McByte | 85.0 / 79.9 / 96.8 | 64.2 / 79.4 / 80.2 | 不训练，只用现成的检测器和分割模型做关联，参数在所有数据集上固定 |`,
  ].join(`\n`)

  it('scrolls on the wrapping section with overflow-x, not the overflow shorthand', () => {
    const { html } = renderMarkdown(wide, initRenderer({}))

    expect(html).toContain(`overflow-x: auto; -webkit-overflow-scrolling: touch`)
    expect(html).not.toContain(`overflow: auto`)
  })

  it('keeps short cells on one line and gives long cells a minimum width', () => {
    const { html } = renderMarkdown(wide, initRenderer({}))

    expect(html).toContain(`<td class="td" style="text-align: left; white-space: nowrap">McByte</td>`)
    expect(html).toContain(`<td class="td" style="text-align: right; white-space: nowrap">64.2 / 79.4 / 80.2</td>`)
    expect(html).toMatch(/<td class="td" style="text-align: left; word-break: normal; overflow-wrap: anywhere"><section style="min-width: 12em; white-space: normal">不训练/)
    // headers follow the same rule: a 33-character header wraps inside its minimum width
    expect(html).toMatch(/<th class="th" style="[^"]*word-break: normal[^"]*"><section style="min-width: 12em/)
  })

  it('shows the swipe hint only when the table is wider than a phone screen', () => {
    const renderer = initRenderer({
      renderMessages: { footnoteTitle: ``, unknownComponent: ``, katexLoading: ``, tableScrollHint: `可左右滑动` },
    })
    expect(renderMarkdown(wide, renderer).html).toContain(`>可左右滑动</p>`)

    const narrow = `| 模型 | 参数 |\n| --- | --- |\n| LLaMA | 7B |`
    expect(renderMarkdown(narrow, renderer).html).not.toContain(`table-scroll-hint`)
  })

  it('falls back to the English hint without injected messages', () => {
    expect(renderMarkdown(wide, initRenderer({})).html).toContain(`Swipe to see the full table →`)
  })
})
