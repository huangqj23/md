// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import {
  auditImages,
  buildAgentArticle,
  degradeFormulas,
  extractMarkdownTitle,
  extractSummary,
  fitTitle,
  removeLeadingTitleHeading,
  stripLeadingTitle,
  toPlatformHtml,
  toWechatHtml,
} from './article'

function fragment(html: string): HTMLElement {
  const div = document.createElement(`div`)
  div.innerHTML = html
  return div
}

describe(`extractMarkdownTitle`, () => {
  it(`returns the first level-1 heading outside code fences`, () => {
    const md = '```md\n# not this\n```\n\n## Sub\n\n#  论文学习笔记：McByte  \n'
    expect(extractMarkdownTitle(md)).toBe(`论文学习笔记：McByte`)
  })

  it(`ignores closing hashes and returns null without an H1`, () => {
    expect(extractMarkdownTitle(`# Title ##`)).toBe(`Title`)
    expect(extractMarkdownTitle(`## only h2`)).toBeNull()
  })
})

describe(`stripLeadingTitle`, () => {
  it(`drops an opening H1 that repeats the title, with the blank lines after it`, () => {
    expect(stripLeadingTitle(`\n# My Title\n\n\nBody\n`, `My Title`)).toBe(`\nBody\n`)
  })

  it(`skips front matter before looking for the heading`, () => {
    const md = `---\nauthor: me\n---\n# T\n\ntext`
    expect(stripLeadingTitle(md, `T`)).toBe(`---\nauthor: me\n---\ntext`)
  })

  it(`keeps the document when the first heading differs or is not first`, () => {
    expect(stripLeadingTitle(`# Other\n\nx`, `Title`)).toBe(`# Other\n\nx`)
    expect(stripLeadingTitle(`intro\n\n# Title`, `Title`)).toBe(`intro\n\n# Title`)
  })
})

describe(`removeLeadingTitleHeading`, () => {
  it(`removes the first heading only when it is the matching H1`, () => {
    const root = fragment(`<h1> My  Title </h1><p>x</p><h1>My Title</h1>`)
    removeLeadingTitleHeading(root, `My Title`)
    expect(root.innerHTML).toBe(`<p>x</p><h1>My Title</h1>`)

    const other = fragment(`<h2>My Title</h2><h1>My Title</h1>`)
    removeLeadingTitleHeading(other, `My Title`)
    expect(other.querySelectorAll(`h1, h2`)).toHaveLength(2)
  })
})

describe(`degradeFormulas`, () => {
  it(`puts TeX source back in place of MathJax output`, () => {
    const root = fragment(
      `<p>score <span class="katex-inline" data-math-raw="$mf$"><svg><g/></svg></span> here</p>`
      + `<section class="katex-block" data-math-raw="$$E=mc^2$$"><svg/></section>`
      + `<span class="katex-inline katex-pending" data-math-raw="$x$"><span>…</span></span>`,
    )
    degradeFormulas(root)
    expect(root.querySelector(`svg`)).toBeNull()
    expect(root.querySelector(`p`)!.textContent).toBe(`score $mf$ here`)
    expect(root.querySelectorAll(`p`)[1].textContent).toBe(`$$E=mc^2$$`)
    expect(root.textContent).toContain(`$x$`)
  })
})

describe(`auditImages`, () => {
  it(`groups images by whether other sites can load them`, () => {
    const root = fragment(`
      <img src="https://cdn.example.com/a.png">
      <img src="//cdn.example.com/b.png">
      <img src="data:image/webp;base64,AAAA">
      <img src="images/fig1.png">
      <img src="blob:chrome-extension://x/y">`)
    expect(auditImages(root)).toEqual({ total: 5, remote: 2, embedded: 1, local: 2 })
  })
})

describe(`extractSummary`, () => {
  it(`uses the first real paragraph and skips quotes, lists and short lines`, () => {
    const root = fragment(`<blockquote><p>quoted paragraph that is long enough</p></blockquote><p>短</p><ul><li><p>list paragraph long enough here</p></li></ul><p>  McByte 在 ByteTrack 基础上   引入掩码线索。 </p>`)
    expect(extractSummary(root)).toBe(`McByte 在 ByteTrack 基础上 引入掩码线索。`)
  })

  it(`truncates by characters, not UTF-16 units`, () => {
    const root = fragment(`<p>${`😀`.repeat(130)}</p>`)
    expect(Array.from(extractSummary(root))).toHaveLength(120)
  })
})

describe(`toPlatformHtml / toWechatHtml`, () => {
  it(`strips styles, scripts, the title heading and formula SVG`, () => {
    const { html, textLength } = toPlatformHtml(
      `<style>p{}</style><h1>T</h1><p>a <span class="katex-inline" data-math-raw="$x$"><svg/></span></p><script>1</script>`,
      `T`,
    )
    expect(html).toBe(`<p>a $x$</p>`)
    expect(textLength).toBe(4)
  })

  it(`keeps WeChat inline styles but drops the title heading`, () => {
    expect(toWechatHtml(`<h1 style="color:red">T</h1><p style="color:#333">x</p>`, `T`)).toBe(`<p style="color:#333">x</p>`)
    expect(toWechatHtml(``, `T`)).toBe(``)
  })
})

describe(`buildAgentArticle`, () => {
  it(`produces every format from one Markdown source`, async () => {
    const article = await buildAgentArticle({
      title: ` 论文笔记  McByte `,
      summary: ` 摘要 `,
      markdown: `# 论文笔记 McByte\n\n## 背景\n\n**McByte** 用掩码。\n\n![fig](https://cdn.example.com/f.png)\n`,
      wechatHtml: `<h1>论文笔记 McByte</h1><h2 style="color:#0F4C81">背景</h2>`,
    })
    expect(article.title).toBe(`论文笔记 McByte`)
    expect(article.summary).toBe(`摘要`)
    expect(article.markdown.startsWith(`## 背景`)).toBe(true)
    expect(article.html).toContain(`<h2>背景</h2>`)
    expect(article.html).toContain(`<strong>McByte</strong>`)
    expect(article.html).toContain(`src="https://cdn.example.com/f.png"`)
    expect(article.html).not.toContain(`<h1`)
    expect(article.wechatHtml).toBe(`<h2 style="color:#0F4C81">背景</h2>`)
    expect(article.textLength).toBeGreaterThan(5)
  })
})

describe(`fitTitle`, () => {
  const base = { title: `12345😀789`, summary: ``, markdown: ``, html: ``, wechatHtml: ``, textLength: 0 }

  it(`cuts at the character limit without splitting surrogate pairs`, () => {
    expect(fitTitle(base, 6)).toEqual({ article: { ...base, title: `12345😀` }, truncated: true })
  })

  it(`leaves short titles and unlimited platforms alone`, () => {
    expect(fitTitle(base, 30)).toEqual({ article: base, truncated: false })
    expect(fitTitle(base)).toEqual({ article: base, truncated: false })
  })
})
