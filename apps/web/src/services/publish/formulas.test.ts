// @vitest-environment jsdom
import type { AgentArticle } from '@/publish-agent/protocol'
import { describe, expect, it, vi } from 'vitest'
import { withPlainFormulas } from './formulas'

const base: AgentArticle = { title: `T`, summary: ``, markdown: `md`, html: `<p>plain</p>`, wechatHtml: ``, textLength: 5 }

/** Formulas as the WeChat HTML carries them: MathJax SVG inside md's wrappers. */
function inline(raw: string) {
  return `<span class="katex-inline" data-math-display="false" data-math-raw="${raw}"><svg xmlns="http://www.w3.org/2000/svg" width="2ex" height="1.6ex" style="vertical-align: -0.5ex;" viewBox="0 -456 878 706"><path d="M0 0"></path></svg></span>`
}
function block(raw: string, path = `M1 1`) {
  return `<section class="katex-block" data-math-display="true" data-math-raw="${raw}" style="text-align: center; overflow-x: auto;"><svg xmlns="http://www.w3.org/2000/svg" width="20ex" height="3ex" viewBox="0 0 10 10"><path d="${path}"></path></svg></section>`
}

const drawnAs = (src: string) => ({ src, width: 320.4, height: 48.2 })

describe(`withPlainFormulas`, () => {
  it(`writes inline formulas as text inside their sentence and draws display ones as centered images`, async () => {
    const wechatHtml = `<ol><li>在 ${inline(`$6 \\times 10^{18}$`)} 到 ${inline(`$10^{22}$`)} FLOPs 上</li></ol>${block(`$$L = -\\log p$$`)}`
    const render = vi.fn(async () => drawnAs(`data:image/png;base64,BLK`))

    const result = await withPlainFormulas({ ...base, wechatHtml }, render)

    expect(render).toHaveBeenCalledTimes(1)
    expect(result.wechatHtml).toBe(
      `<ol><li>在 6&nbsp;×&nbsp;10¹⁸ 到 10²² FLOPs 上</li></ol>`
      + `<p style="text-align: center;"><img src="data:image/png;base64,BLK" alt="$$L = -\\log p$$" width="320" height="48" style="width: 320.4px; height: 48.2px; max-width: 100%;"></p>`,
    )
    // Only the WeChat HTML carries SVG formulas; the rest of the article is untouched.
    expect(result.html).toBe(base.html)
    expect(result.textLength).toBe(base.textLength)
  })

  it(`draws a repeated display formula once`, async () => {
    const wechatHtml = `${block(`$$x$$`)}<p>又一次</p>${block(`$$x$$`)}${block(`$$y$$`, `M2 2`)}`
    const render = vi.fn(async () => drawnAs(`data:image/png;base64,X`))
    const result = await withPlainFormulas({ ...base, wechatHtml }, render)
    expect(render).toHaveBeenCalledTimes(2)
    expect(result.wechatHtml.match(/<img /g)).toHaveLength(3)
  })

  it(`keeps the TeX source when a formula cannot be written as text or drawn`, async () => {
    const pending = `<section class="katex-block katex-pending" data-math-raw="$$z$$"><span>…</span></section>`
    const wechatHtml = `<p>${inline(`$\\begin{pmatrix} a \\end{pmatrix}$`)}</p>${block(`$$b$$`)}${pending}`
    const result = await withPlainFormulas({ ...base, wechatHtml }, async () => {
      throw new Error(`no canvas`)
    })
    expect(result.wechatHtml).toBe(`<p>$\\begin{pmatrix} a \\end{pmatrix}$</p><p style="text-align: center;">$$b$$</p><p style="text-align: center;">$$z$$</p>`)
  })

  it(`returns the same article when there is nothing to change`, async () => {
    const render = vi.fn()
    expect(await withPlainFormulas(base, render)).toBe(base)
    const noFormulas = { ...base, wechatHtml: `<p>x</p>` }
    expect(await withPlainFormulas(noFormulas, render)).toBe(noFormulas)
    expect(render).not.toHaveBeenCalled()
  })
})
