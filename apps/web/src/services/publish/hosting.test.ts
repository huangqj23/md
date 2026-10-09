import type { AgentArticle } from '@/publish-agent/protocol'
import { describe, expect, it, vi } from 'vitest'
import { canHostImages, createImageHosting } from './hosting'

const PNG = `data:image/png;base64,iVBORw0KGgo=`
const WEBP = `data:image/webp;base64,UklGRg==`
const SVG = `data:image/svg+xml;base64,PHN2Zy8+`

function article(body: string): AgentArticle {
  return {
    title: `t`,
    summary: ``,
    markdown: `正文\n\n<img alt="图1" src="${PNG}" style="max-width:100%;" />\n\n${body}`,
    html: `<p>正文</p><img alt="图1" src="${PNG}"><p>${body}</p>`,
    wechatHtml: `<section><img src="${PNG}" style="width:100%"></section>`,
    textLength: 2,
  }
}

describe(`canHostImages`, () => {
  it(`uses a configured image host, not the shared default or WeChat's library`, () => {
    expect(canHostImages(`txCOS`)).toBe(true)
    expect(canHostImages(`github`)).toBe(true)
    expect(canHostImages(`default`)).toBe(false)
    expect(canHostImages(`mp`)).toBe(false)
  })
})

describe(`createImageHosting`, () => {
  it(`uploads each distinct image once and swaps it in every body`, async () => {
    const upload = vi.fn(async (dataUrl: string) => `https://cdn.example.com/${dataUrl === PNG ? `a.png` : `b.png`}`)
    const hosting = createImageHosting(upload)

    const first = await hosting.hostArticle(article(`<img src="${WEBP}">`))
    // A platform's own version (tables drawn as pictures) shares the uploads already made.
    const second = await hosting.hostArticle(article(``))

    expect(upload).toHaveBeenCalledTimes(2)
    for (const text of [first.markdown, first.html, first.wechatHtml, second.markdown])
      expect(text).not.toContain(`data:image`)
    expect(first.markdown).toContain(`src="https://cdn.example.com/a.png" style="max-width:100%;"`)
    expect(first.html).toContain(`<img src="https://cdn.example.com/b.png">`)
    expect(first.textLength).toBe(2)
    expect(hosting.report()).toEqual({ total: 2, done: 2, failed: 0, reasons: [] })
  })

  it(`reports each image as it is found and as its upload finishes`, async () => {
    const onProgress = vi.fn()
    const hosting = createImageHosting(async () => `https://cdn.example.com/x.png`, { onProgress })

    await hosting.hostArticle(article(`<img src="${WEBP}">`))

    const seen = onProgress.mock.calls.map(([report]) => `${report.done}/${report.total}`)
    expect(seen.slice(0, 2)).toEqual([`0/1`, `0/2`])
    expect(seen[seen.length - 1]).toBe(`2/2`)
    expect(seen).toHaveLength(4)
  })

  it(`leaves SVG and linked images alone`, async () => {
    const upload = vi.fn(async () => `https://cdn.example.com/x.png`)
    const source = { ...article(``), markdown: `![](${SVG}) ![](https://a.io/x.png)`, html: `<p>none</p>`, wechatHtml: `` }

    const result = await createImageHosting(upload).hostArticle(source)

    expect(result).toEqual(source)
    expect(upload).not.toHaveBeenCalled()
  })

  it(`keeps an image embedded when its upload fails, once, and reports why`, async () => {
    const upload = vi.fn(async (dataUrl: string) => {
      if (dataUrl === WEBP)
        throw new Error(`Upload failed: Forbidden`)
      return `https://cdn.example.com/a.png`
    })
    const hosting = createImageHosting(upload)

    const result = await hosting.hostArticle(article(`<img src="${WEBP}">`))
    await hosting.hostArticle(article(`<img src="${WEBP}">`))

    expect(result.html).toContain(`<img src="${WEBP}">`)
    expect(result.html).toContain(`https://cdn.example.com/a.png`)
    expect(upload).toHaveBeenCalledTimes(2)
    expect(hosting.report()).toEqual({ total: 2, done: 2, failed: 1, reasons: [`Upload failed: Forbidden`] })
  })

  it(`runs at most the given number of uploads at once`, async () => {
    let active = 0
    let peak = 0
    const upload = vi.fn(async (dataUrl: string) => {
      peak = Math.max(peak, ++active)
      await new Promise(resolve => setTimeout(resolve, 5))
      active--
      return `https://cdn.example.com/${dataUrl.length}.png`
    })
    const images = Array.from({ length: 7 }, (_, i) => `<img src="data:image/png;base64,${`A`.repeat(i + 1)}">`).join(``)

    await createImageHosting(upload, { concurrency: 3 }).hostArticle({ ...article(``), markdown: ``, html: images, wechatHtml: `` })

    expect(upload).toHaveBeenCalledTimes(7)
    expect(peak).toBe(3)
  })
})
