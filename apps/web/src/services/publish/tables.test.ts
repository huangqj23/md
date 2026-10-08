// @vitest-environment jsdom
import type { AgentArticle } from '@/publish-agent/protocol'
import { describe, expect, it, vi } from 'vitest'
import { withTablesAsImages } from './tables'

const base: AgentArticle = { title: `T`, summary: ``, markdown: `md`, html: ``, wechatHtml: ``, textLength: 0 }

/** A table as the renderer emits it: inside a horizontal-scroll section. */
function table(rows: string) {
  return `<section style="max-width: 100%; overflow-x: auto"><table class="preview-table"><thead><tr><th>项目</th><th>内容</th></tr></thead><tbody>${rows}</tbody></table></section>`
}

describe(`withTablesAsImages`, () => {
  it(`swaps each table, with its scroll wrapper and hint, for the drawn image`, async () => {
    const html = `<p>前文</p>${table(`<tr><td>标题</td><td>Llama 3</td></tr>`)}<p class="table-scroll-hint">表格较宽，可左右滑动查看 →</p><p>后文</p>`
    const render = vi.fn(async (_table: HTMLTableElement) => `data:image/png;base64,AAAA`)

    const result = await withTablesAsImages({ ...base, html, textLength: 99 }, render)

    expect(render).toHaveBeenCalledTimes(1)
    expect(render.mock.calls[0][0].tagName).toBe(`TABLE`)
    expect(result.html).toBe(`<p>前文</p><img src="data:image/png;base64,AAAA"><p>后文</p>`)
    // The table's text went into the image, so the body expects less text.
    expect(result.textLength).toBe(4)
  })

  it(`draws every table, and leaves one the renderer could not draw`, async () => {
    const html = `${table(`<tr><td>a</td><td>b</td></tr>`)}<p>x</p>${table(`<tr><td>c</td><td>d</td></tr>`)}`
    let calls = 0
    const result = await withTablesAsImages({ ...base, html }, async () => (calls++ === 0 ? null : `data:image/png;base64,BBBB`))

    expect(result.html.match(/<table/g)).toHaveLength(1)
    expect(result.html).toContain(`<td>a</td>`)
    expect(result.html.endsWith(`<p>x</p><img src="data:image/png;base64,BBBB">`)).toBe(true)
  })

  it(`puts the same pictures in the WeChat HTML, table by table`, async () => {
    const html = `${table(`<tr><td>a</td><td>b</td></tr>`)}<p>x</p>${table(`<tr><td>c</td><td>d</td></tr>`)}`
    const styled = (rows: string) => `<section style="max-width: 100%; overflow-x: auto"><table style="color: red"><tbody>${rows}</tbody></table></section>`
    const wechatHtml = `<p style="margin: 0">x0</p>${styled(`<tr><td>a</td></tr>`)}<p class="table-scroll-hint" style="color: #999">hint</p><p>x</p>${styled(`<tr><td>c</td></tr>`)}`
    let calls = 0
    const result = await withTablesAsImages({ ...base, html, wechatHtml }, async () => (calls++ === 0 ? `data:image/png;base64,ONE` : null))

    expect(result.wechatHtml).toBe(`<p style="margin: 0">x0</p><img src="data:image/png;base64,ONE"><p>x</p>${styled(`<tr><td>c</td></tr>`)}`)
  })

  it(`leaves the WeChat HTML alone when its tables do not line up with the drawn ones`, async () => {
    const wechatHtml = `<table><tbody><tr><td>only</td></tr></tbody></table><table><tbody><tr><td>two</td></tr></tbody></table>`
    const result = await withTablesAsImages({ ...base, html: table(`<tr><td>a</td><td>b</td></tr>`), wechatHtml }, async () => `data:image/png;base64,AAAA`)
    expect(result.wechatHtml).toBe(wechatHtml)
  })

  it(`returns the same article when there is nothing to draw`, async () => {
    const plain = { ...base, html: `<p>x</p>`, textLength: 1 }
    const render = vi.fn()
    expect(await withTablesAsImages(plain, render)).toBe(plain)
    expect(render).not.toHaveBeenCalled()

    const undrawable = { ...base, html: table(`<tr><td>a</td><td>b</td></tr>`), textLength: 6 }
    expect(await withTablesAsImages(undrawable, async () => null)).toBe(undrawable)
  })
})
