// @vitest-environment jsdom
/**
 * Each fixture reproduces the editor contract a filler relies on (selectors,
 * editor globals, dialogs), as documented by doocs/cose and MultiPost. They
 * catch logic regressions; they cannot prove the live sites still match.
 */
import type { AgentArticle, AgentResult } from '../protocol'
import type { PlatformFiller } from '../result'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { baijiahao } from './baijiahao'
import { bilibili, hasBilibiliSession } from './bilibili'
import { cnblogs } from './cnblogs'
import { csdn } from './csdn'
import { countJianshuLinks, dropBlankText, fitBlocks, fitLinks, jianshu } from './jianshu'
import { juejin } from './juejin'
import { fillSummaryOf, toutiao, toutiaoFailure, unwrapOutsideLinks, uploadedImageUrl, watchToutiaoFailures } from './toutiao'
import { pickWechatBodyEditor, readWechatToken, wechat, wechatEditorUrl } from './wechat'
import { overLimitBy, withZhihuFormulas, zhihu } from './zhihu'

const article: AgentArticle = {
  title: `论文笔记：McByte`,
  summary: `一句话总结`,
  markdown: `## 背景\n\n**McByte** 用掩码做关联。\n\n- 免训练\n- 免调参\n`,
  html: `<h2>背景</h2><p><strong>McByte</strong> 用掩码做关联。</p><ul><li>免训练</li><li>免调参</li></ul>`,
  wechatHtml: `<section style="color:#333"><h2 style="color:#0F4C81">背景</h2><p><strong>McByte</strong> 用掩码做关联。</p></section>`,
  textLength: 20,
}

beforeAll(() => {
  if (typeof globalThis.DataTransfer === `undefined`) {
    class DataTransferStub {
      private store = new Map<string, string>()
      setData(type: string, value: string) { this.store.set(type, value) }
      getData(type: string) { return this.store.get(type) ?? `` }
    }
    ;(globalThis as Record<string, unknown>).DataTransfer = DataTransferStub
  }
  if (typeof globalThis.ClipboardEvent === `undefined`) {
    class ClipboardEventStub extends Event {
      clipboardData: DataTransfer | null
      constructor(type: string, init: ClipboardEventInit = {}) {
        super(type, init)
        this.clipboardData = init.clipboardData ?? null
      }
    }
    ;(globalThis as Record<string, unknown>).ClipboardEvent = ClipboardEventStub
  }
})

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  document.body.innerHTML = ``
  window.history.replaceState({}, ``, `/`)
  for (const key of [`CodeMirror`, `UE`, `UE_V2`, `tinymce`, `__MP_Editor_JSAPI__`, `wx`])
    delete (window as unknown as Record<string, unknown>)[key]
})

/** Run a step with fake timers until it settles. */
async function runStep(filler: PlatformFiller, step: string, input: AgentArticle = article): Promise<AgentResult> {
  const promise = filler.steps[step](input)
  await vi.runAllTimersAsync()
  return promise
}

function onPaste(el: Element, handler: (data: DataTransfer) => boolean | void) {
  el.addEventListener(`paste`, (event) => {
    if (handler((event as ClipboardEvent).clipboardData!) !== false)
      event.preventDefault()
  })
}

function codeMirrorStub() {
  let value = ``
  return {
    setValue: vi.fn((next: string) => {
      value = next
    }),
    getValue: () => value,
    refresh: vi.fn(),
  }
}

describe(`juejin`, () => {
  it(`fills the title and sets Markdown through the CodeMirror instance`, async () => {
    document.body.innerHTML = `<input class="title-input" placeholder="输入文章标题..."><div class="bytemd"><div class="CodeMirror"></div></div>`
    const cm = codeMirrorStub()
    Object.assign(document.querySelector(`.CodeMirror`)!, { CodeMirror: cm })

    const result = await runStep(juejin, `fill`)

    expect(result.kind).toBe(`done`)
    expect(document.querySelector<HTMLInputElement>(`.title-input`)!.value).toBe(article.title)
    expect(cm.setValue).toHaveBeenCalledWith(article.markdown)
    if (result.kind === `done`) {
      expect(result.report.method).toBe(`codemirror`)
      expect(result.report.bodyLength).toBe(result.report.expectedLength)
    }
  })

  it(`reports login-required when the login form shows instead of the editor`, async () => {
    document.body.innerHTML = `<div class="auth-modal">登录掘金 <button>手机登录</button></div>`
    const result = await runStep(juejin, `fill`)
    expect(result).toMatchObject({ kind: `error`, code: `login-required` })
  })

  it(`waits for autosave to move the URL off /drafts/new`, async () => {
    window.history.replaceState({}, ``, `/editor/drafts/new?v=2`)
    document.body.innerHTML = `<input class="title-input"><div class="CodeMirror"></div>`
    Object.assign(document.querySelector(`.CodeMirror`)!, { CodeMirror: codeMirrorStub() })
    setTimeout(() => window.history.replaceState({}, ``, `/editor/drafts/7300`), 3000)

    const result = await runStep(juejin, `fill`)
    expect(result.kind === `done` && result.report.draftSaved).toBe(true)
  })
})

describe(`csdn`, () => {
  it(`writes Markdown into the contenteditable editor and saves the draft`, async () => {
    document.body.innerHTML = `
      <div class="article-bar__title"><input placeholder="请输入文章标题（5~100个字）"></div>
      <pre class="editor__inner" contenteditable="true"></pre>
      <button>保存草稿</button>`
    const onInput = vi.fn()
    document.querySelector(`.editor__inner`)!.addEventListener(`input`, onInput)
    const onSave = vi.fn()
    document.querySelector(`button`)!.addEventListener(`click`, onSave)

    const result = await runStep(csdn, `fill`)

    expect(result.kind).toBe(`done`)
    expect(document.querySelector(`.editor__inner`)!.textContent).toBe(article.markdown)
    expect(onInput).toHaveBeenCalled()
    expect(onSave).toHaveBeenCalledOnce()
    expect(result.kind === `done` && result.report).toMatchObject({ titleFilled: true, draftSaved: true, method: `contenteditable` })
  })

  it(`falls back to a CodeMirror editor`, async () => {
    document.body.innerHTML = `<input placeholder="文章标题"><div class="CodeMirror"></div>`
    const cm = codeMirrorStub()
    Object.assign(document.querySelector(`.CodeMirror`)!, { CodeMirror: cm })
    const result = await runStep(csdn, `fill`)
    expect(cm.setValue).toHaveBeenCalledWith(article.markdown)
    expect(result.kind === `done` && result.report.method).toBe(`codemirror`)
  })

  it(`fails with editor-not-found when the page has no editor`, async () => {
    document.body.innerHTML = `<p>nothing here</p>`
    expect(await runStep(csdn, `fill`)).toMatchObject({ kind: `error`, code: `editor-not-found` })
  })
})

describe(`cnblogs`, () => {
  it(`fills the Markdown textarea and the summary`, async () => {
    document.body.innerHTML = `<input id="post-title"><textarea id="md-editor"></textarea><textarea id="summary"></textarea>`
    const result = await runStep(cnblogs, `fill`)
    expect(result.kind).toBe(`done`)
    expect(document.querySelector<HTMLTextAreaElement>(`#md-editor`)!.value).toBe(article.markdown)
    expect(document.querySelector<HTMLTextAreaElement>(`#summary`)!.value).toBe(article.summary)
    expect(result.kind === `done` && result.report.draftSaved).toBe(false)
  })

  it(`uses TinyMCE with HTML when the account uses the rich-text editor`, async () => {
    document.body.innerHTML = `<input id="post-title">`
    let content = ``
    ;(window as unknown as Record<string, unknown>).tinymce = {
      activeEditor: {
        setContent: (html: string) => {
          content = html
        },
        getContent: () => content.replace(/<[^>]+>/g, ``),
      },
    }
    const result = await runStep(cnblogs, `fill`)
    expect(content).toBe(article.html)
    expect(result.kind === `done` && result.report.method).toBe(`tinymce`)
  })

  describe(`formulas`, () => {
    const withFormula = { ...article, html: `<p>见 <span data-tex="x^2">$x^2$</span></p>` }
    /** The account preferences the page loads from `/api/preferences`. */
    function preferences(body: unknown, ok = true) {
      const fetchMock = vi.fn(async () => (ok ? new Response(JSON.stringify(body)) : new Response(``, { status: 401 })))
      vi.stubGlobal(`fetch`, fetchMock)
      return fetchMock
    }

    beforeEach(() => {
      document.body.innerHTML = `<input id="post-title"><textarea id="md-editor"></textarea>`
    })
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    it(`flags formulas that will show as source while the account has formula support off`, async () => {
      const fetchMock = preferences({ isEnableMathFormula: false, mathEngine: 1 })
      const result = await runStep(cnblogs, `fill`, withFormula)
      expect(fetchMock).toHaveBeenCalledWith(`/api/preferences`, expect.objectContaining({ credentials: `include` }))
      expect(result.kind === `done` && result.report.formulas).toEqual({ total: 1, placed: 0, needsSetting: true })
    })

    it(`counts formulas as rendered once formula support is on`, async () => {
      preferences({ isEnableMathFormula: true })
      const result = await runStep(cnblogs, `fill`, withFormula)
      expect(result.kind === `done` && result.report.formulas).toEqual({ total: 1, placed: 1 })
    })

    it(`claims nothing when the preferences cannot be read, and does not ask without formulas`, async () => {
      preferences({}, false)
      const unread = await runStep(cnblogs, `fill`, withFormula)
      expect(unread.kind === `done` && unread.report.formulas).toBeUndefined()

      const fetchMock = preferences({ isEnableMathFormula: false })
      await runStep(cnblogs, `fill`)
      expect(fetchMock).not.toHaveBeenCalled()
    })
  })
})

describe(`toutiao`, () => {
  it(`pastes clean HTML into the ProseMirror body`, async () => {
    document.body.innerHTML = `<textarea placeholder="请输入文章标题（2～30个字）"></textarea><div class="ProseMirror" contenteditable="true"></div>`
    const editor = document.querySelector(`.ProseMirror`)!
    onPaste(editor, (data) => {
      editor.innerHTML = data.getData(`text/html`)
    })

    const result = await runStep(toutiao, `fill`)

    expect(result.kind).toBe(`done`)
    expect(editor.innerHTML).toBe(article.html)
    expect(result.kind === `done` && result.report).toMatchObject({ method: `paste`, titleFilled: true })
  })

  it(`falls back to insertHTML when the paste is ignored`, async () => {
    document.body.innerHTML = `<textarea placeholder="标题"></textarea><div class="ProseMirror" contenteditable="true"></div>`
    const editor = document.querySelector(`.ProseMirror`)!
    const exec = vi.fn((command: string, _ui: boolean, value?: string) => {
      if (command === `insertHTML`)
        editor.innerHTML = value ?? ``
      return command === `insertHTML`
    })
    Object.assign(document, { execCommand: exec })

    const result = await runStep(toutiao, `fill`)

    expect(exec).toHaveBeenCalledWith(`insertHTML`, false, article.html)
    expect(result.kind === `done` && result.report.method).toBe(`insertHTML`)
    delete (document as unknown as Record<string, unknown>).execCommand
  })

  describe(`images`, () => {
    const png = `data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==`
    const outside = `https://cdn.example.com/fig2.png`
    const hosted = `https://p3-sign.toutiaoimg.com/tos-cn-i-1/fig3~tplv.image`
    const html = `<p>图一</p><img src="${png}"><p>图二</p><img src="${outside}"><p>图三</p><img src="${hosted}">`

    /** The page's `window.Garr`, whose network client the editor uploads images with. */
    function garr(post: (url: string, body: FormData) => Promise<unknown>) {
      const network = { post: vi.fn(post) }
      ;(window as unknown as Record<string, unknown>).Garr = { network, pgc_info: { media: { watermark: 1 } } }
      return network
    }

    beforeEach(() => {
      document.body.innerHTML = `<textarea placeholder="标题"></textarea><div class="ProseMirror" contenteditable="true"></div>`
      // A jsdom Blob: jsdom's FormData rejects the Node Blob a real Response would give.
      vi.stubGlobal(`fetch`, vi.fn(async () => ({ ok: true, blob: async () => new Blob([`png`], { type: `image/png` }) })))
    })
    afterEach(() => {
      vi.unstubAllGlobals()
      delete (window as unknown as Record<string, unknown>).Garr
    })

    it(`puts embedded and outside images on Toutiao's image host before pasting`, async () => {
      const editor = document.querySelector(`.ProseMirror`)!
      let pasted = ``
      onPaste(editor, (data) => {
        pasted = data.getData(`text/html`)
        editor.innerHTML = pasted
      })
      let uploads = 0
      const network = garr(async () => ({ data: { url: `https://p3-sign.toutiaoimg.com/up-${++uploads}~tplv.image`, web_uri: `up-${uploads}` } }))

      const result = await runStep(toutiao, `fill`, { ...article, html, textLength: 6 })

      expect(network.post).toHaveBeenCalledTimes(2)
      const [url, body] = network.post.mock.calls[0]!
      expect(url).toContain(`/mp/agw/article_material/photo/upload_picture?type=ueditor&pgc_watermark=1`)
      expect(body.get(`upfile`)).toBeInstanceOf(Blob)
      expect(pasted).not.toContain(`data:`)
      expect(pasted).not.toContain(outside)
      expect(pasted).toContain(hosted)
      expect(pasted.match(/toutiaoimg\.com\/up-\d/g)).toHaveLength(2)
      expect(result.kind === `done` && result.report.images).toEqual({ total: 2, failed: 0 })
    })

    it(`keeps an image it could not upload, and passes on why`, async () => {
      const editor = document.querySelector(`.ProseMirror`)!
      onPaste(editor, (data) => {
        editor.innerHTML = data.getData(`text/html`)
      })
      garr(async () => ({ code: 7, message: `图片格式不支持` }))

      const result = await runStep(toutiao, `fill`, { ...article, html: `<p>图一</p><img src="${png}">`, textLength: 2 })

      expect(editor.innerHTML).toContain(png)
      expect(result.kind === `done` && result.report.images).toEqual({ total: 1, failed: 1, reasons: [`图片格式不支持`] })
    })

    it(`uploadedImageUrl reads a URL, a nested URL or an image uri`, () => {
      expect(uploadedImageUrl({ state: `SUCCESS`, url: `//p3.toutiaoimg.com/large/a` })).toBe(`https://p3.toutiaoimg.com/large/a`)
      expect(uploadedImageUrl({ data: { web_url: `https://p26-sign.toutiaoimg.com/b` } })).toBe(`https://p26-sign.toutiaoimg.com/b`)
      expect(uploadedImageUrl({ data: { web_uri: `tos-cn-i-1/c` } })).toBe(`https://p3.toutiaoimg.com/large/tos-cn-i-1/c`)
      expect(uploadedImageUrl({ message: `error` })).toBeNull()
    })
  })

  describe(`failure diagnosis`, () => {
    afterEach(() => {
      const page = window as unknown as Record<string, unknown>
      delete page.Garr
      delete page.__mdToutiaoWatched
      document.getElementById(`md-publish-toutiao-diagnosis`)?.remove()
    })

    it(`toutiaoFailure reads the reason out of a failed response body`, () => {
      expect(toutiaoFailure({ code: 0, message: `success`, data: {} })).toBeNull()
      expect(toutiaoFailure({ code: 7050, message: `正文包含不支持的链接` })).toBe(`正文包含不支持的链接（code 7050）`)
      expect(toutiaoFailure({ err_no: 2, err_tips: `草稿保存失败` })).toBe(`草稿保存失败（code 2）`)
      expect(toutiaoFailure({ code: 1 })).toBe(`没有说明原因（code 1）`)
      expect(toutiaoFailure(`plain text`)).toBeNull()
    })

    it(`shows what Toutiao answered a failed save or publish with, and what the request carried`, async () => {
      type Handler = { fulfilled: (value: unknown) => unknown, rejected: (error: unknown) => unknown } | null
      const theirs: Handler = { fulfilled: value => value, rejected: error => Promise.reject(error) }
      const handlers: Handler[] = [theirs]
      ;(window as unknown as Record<string, unknown>).Garr = { network: { interceptors: { response: { handlers } } } }

      watchToutiaoFailures()
      watchToutiaoFailures()
      // Once, and ahead of Toutiao's own interceptors, which reduce a response to its body.
      expect(handlers).toHaveLength(2)
      expect(handlers[1]).toBe(theirs)
      const { fulfilled, rejected } = handlers[0]!
      const banner = () => document.getElementById(`md-publish-toutiao-diagnosis`)?.textContent ?? null

      const unrelated = { config: { url: `/mp/agw/creator_center/notice` }, data: { code: 5, message: `x` } }
      expect(fulfilled(unrelated)).toBe(unrelated)
      expect(banner()).toBeNull()

      const content = `content=${encodeURIComponent(`<p>正文</p><img src="data:image/png;base64,AAAA">`)}`
      fulfilled({ config: { url: `/mp/agw/article/publish?source=mp`, data: content }, data: { code: 5009, message: `发布失败，请重试`, data: {} } })
      expect(banner()).toContain(`头条返回：发布失败，请重试（code 5009）`)
      expect(banner()).toContain(`请求：/mp/agw/article/publish，约 1 KB，里面还有 1 张内嵌图片（没在头条图床上）`)
      expect(banner()).toContain(`返回：{"code":5009,"message":"发布失败，请重试","data":{}}`)

      // A body that an earlier interceptor already unwrapped.
      fulfilled({ code: 3, message: `内容过长` })
      expect(banner()).toContain(`内容过长（code 3）`)

      const failure = Object.assign(new Error(`500`), { response: { config: { url: `/mp/agw/article/save` }, data: { code: 500, message: `服务繁忙` } } })
      await expect(rejected(failure)).rejects.toBe(failure)
      expect(banner()).toContain(`服务繁忙（code 500）`)
    })

    it(`adds an interceptor the usual way when the client keeps its list to itself`, () => {
      const use = vi.fn()
      ;(window as unknown as Record<string, unknown>).Garr = { network: { interceptors: { response: { use } } } }
      watchToutiaoFailures()
      expect(use).toHaveBeenCalledTimes(1)
    })

    it(`fillSummaryOf says whether every image reached Toutiao's image host`, () => {
      expect(fillSummaryOf({ total: 20, failed: 0, reasons: [] }, 12)).toBe(`同步：20 张图片都已传到头条图床；12 个站外链接改成了文字`)
      expect(fillSummaryOf({ total: 20, failed: 20, reasons: [`csrf`] }, 0)).toBe(`同步：20/20 张图片没能传到头条图床，还是内嵌的：csrf`)
      expect(fillSummaryOf({ total: 0, failed: 0, reasons: [] }, 0)).toBe(`同步：正文里没有需要上传的图片`)
    })
  })

  describe(`links`, () => {
    it(`unwrapOutsideLinks keeps only links to Toutiao's own pages`, () => {
      const html = `<p>见 <a href="https://arxiv.org/abs/2407.21783"><strong>论文</strong></a>、`
        + `<a href="https://github.com/meta-llama/llama-models">https://github.com/meta-llama/llama-models</a>、`
        + `<a href="Llama2_论文学习笔记_内嵌图片版.md">上一篇</a>、<a href="#fn1">[1]</a>、`
        + `<a href="https://www.toutiao.com/article/1/">头条旧文</a></p>`
      expect(unwrapOutsideLinks(html)).toEqual({
        html: `<p>见 <strong>论文</strong>、https://github.com/meta-llama/llama-models、上一篇、[1]、<a href="https://www.toutiao.com/article/1/">头条旧文</a></p>`,
        unwrapped: 4,
      })
      expect(unwrapOutsideLinks(article.html)).toEqual({ html: article.html, unwrapped: 0 })
    })

    it(`pastes outside links as text and says how many`, async () => {
      document.body.innerHTML = `<textarea placeholder="标题"></textarea><div class="ProseMirror" contenteditable="true"></div>`
      const editor = document.querySelector(`.ProseMirror`)!
      onPaste(editor, (data) => {
        editor.innerHTML = data.getData(`text/html`)
      })

      const result = await runStep(toutiao, `fill`, { ...article, html: `<p>代码在 <a href="https://github.com/x/y">GitHub</a></p>`, textLength: 9 })

      expect(editor.innerHTML).toBe(`<p>代码在 GitHub</p>`)
      expect(result.kind === `done` && result.report.links).toEqual({ limit: 0, unwrapped: 1, remaining: 0 })
    })
  })
})

describe(`zhihu`, () => {
  function zhihuEditor() {
    document.body.innerHTML = `
      <textarea placeholder="请输入标题（最多 100 个字）"></textarea>
      <div class="DraftEditor-root"><div class="public-DraftEditor-content" contenteditable="true"></div></div>`
    return document.querySelector(`.public-DraftEditor-content`)!
  }

  it(`pastes the HTML with an empty text/plain part, so Zhihu's Markdown detection stays out of the way`, async () => {
    const editor = zhihuEditor()
    const plain: string[] = []
    onPaste(editor, (data) => {
      plain.push(data.getData(`text/plain`))
      editor.innerHTML = data.getData(`text/html`)
    })

    const result = await runStep(zhihu, `fill`)

    expect(plain).toEqual([``])
    expect(editor.innerHTML).toBe(article.html)
    expect(result.kind === `done` && result.report).toMatchObject({ method: `html`, titleFilled: true, expectedLength: article.textLength })
  })

  it(`hands formulas over as Zhihu's formula markup`, async () => {
    const editor = zhihuEditor()
    let pasted = ``
    onPaste(editor, (data) => {
      pasted = data.getData(`text/html`)
      editor.innerHTML = pasted
    })
    const html = `<p>面积 <span data-tex="\\pi r^2">$\\pi r^2$</span></p><p data-tex="\\sum_i x_i" data-tex-display="">$$\\sum_i x_i$$</p>`

    // 面积 (2) + $\pi r^2$ (8) + $$\sum_i x_i$$ (13), whitespace not counted.
    const result = await runStep(zhihu, `fill`, { ...article, html, textLength: 23 })

    expect(pasted).toBe(
      `<p>面积 <img eeimg="1" alt="\\pi r^2" src="https://www.zhihu.com/equation?tex=%5Cpi%20r%5E2"></p>`
      + `<p><img eeimg="2" alt="\\sum_i x_i" src="https://www.zhihu.com/equation?tex=%5Csum_i%20x_i"></p>`,
    )
    // Formulas show as images, so their `$…$` text is not expected in the body.
    expect(result.kind === `done` && result.report).toMatchObject({ bodyLength: 2, expectedLength: 2 })
  })

  it(`lets the paste replace what the editor holds instead of emptying it by hand`, async () => {
    const editor = zhihuEditor()
    editor.innerHTML = `<div data-contents="true"><div>上次留下的草稿</div></div>`
    let selectedAtPaste = ``
    onPaste(editor, (data) => {
      selectedAtPaste = document.getSelection()!.toString()
      editor.innerHTML = data.getData(`text/html`)
    })

    await runStep(zhihu, `fill`)

    expect(selectedAtPaste).toBe(`上次留下的草稿`)
    expect(editor.innerHTML).toBe(article.html)
  })

  it(`reports an article over Zhihu's length limit`, async () => {
    const editor = zhihuEditor()
    onPaste(editor, () => {
      // Zhihu keeps the editor empty and shows its counter instead.
      const counter = document.createElement(`span`)
      counter.textContent = `已超过 7973 个字`
      document.body.appendChild(counter)
    })

    const result = await runStep(zhihu, `fill`)

    expect(result).toMatchObject({ kind: `error`, code: `too-long` })
    expect(result.kind === `error` && result.message).toContain(`7973`)
  })

  it(`fails when the editor stays empty after the paste`, async () => {
    zhihuEditor()
    const result = await runStep(zhihu, `fill`)
    expect(result).toMatchObject({ kind: `error`, code: `fill-failed` })
  })

  it(`withZhihuFormulas leaves HTML without formulas alone`, () => {
    expect(withZhihuFormulas(article.html)).toEqual({ html: article.html, formulaChars: 0 })
  })

  it(`overLimitBy reads Zhihu's over-limit counter`, () => {
    expect(overLimitBy(`正文 已超过 7973 个字`)).toBe(7973)
    expect(overLimitBy(`已超过7973字`)).toBe(7973)
    expect(overLimitBy(`共 3000 字`)).toBeNull()
  })

  it(`detects Zhihu's login form`, async () => {
    document.body.innerHTML = `<div>验证码登录 密码登录</div>`
    expect(await runStep(zhihu, `fill`)).toMatchObject({ kind: `error`, code: `login-required` })
  })
})

describe(`wechat`, () => {
  it(`prepare navigates to the editor with the session token`, async () => {
    window.history.replaceState({}, ``, `/cgi-bin/home?t=home/index&lang=zh_CN&token=123456`)
    const result = await runStep(wechat, `prepare`)
    expect(result).toEqual({ kind: `navigate`, url: wechatEditorUrl(`123456`), step: `fill` })
  })

  it(`prepare reports login-required on the QR login page`, async () => {
    document.body.innerHTML = `<div>使用账号登录</div>`
    expect(await runStep(wechat, `prepare`)).toMatchObject({ kind: `error`, code: `login-required` })
  })

  it(`readWechatToken also finds the token in links and wx globals`, () => {
    document.body.innerHTML = `<a href="/cgi-bin/appmsg?token=777&lang=zh_CN">x</a>`
    expect(readWechatToken()).toBe(`777`)
    document.body.innerHTML = ``
    ;(window as unknown as Record<string, unknown>).wx = { commonData: { t: `888` } }
    expect(readWechatToken()).toBe(`888`)
  })

  function wechatEditor() {
    document.body.innerHTML = `
      <div class="title-editor__input"><div class="ProseMirror" contenteditable="true"></div></div>
      <textarea id="title"></textarea>
      <div class="ProseMirror" contenteditable="true" id="body"><p>从这里开始写正文</p></div>
      <textarea id="js_description"></textarea>
      <button>保存为草稿</button>`
    return document.querySelector(`#body`)!
  }

  it(`never mistakes the title editor for the body`, () => {
    wechatEditor()
    expect(pickWechatBodyEditor()?.id).toBe(`body`)
  })

  it(`sets the styled body through the editor JSAPI and saves a draft`, async () => {
    const body = wechatEditor()
    const invoke = vi.fn(({ apiParam, sucCb }: { apiParam: { content: string }, sucCb: () => void }) => {
      body.innerHTML = apiParam.content
      sucCb()
    })
    ;(window as unknown as Record<string, unknown>).__MP_Editor_JSAPI__ = { invoke }
    const onSave = vi.fn()
    document.querySelector(`button`)!.addEventListener(`click`, onSave)

    const result = await runStep(wechat, `fill`)

    expect(invoke).toHaveBeenCalledWith(expect.objectContaining({ apiName: `mp_editor_set_content`, apiParam: { content: article.wechatHtml } }))
    expect(document.querySelector<HTMLTextAreaElement>(`#title`)!.value).toBe(article.title)
    expect(document.querySelector(`.title-editor__input .ProseMirror`)!.textContent).toBe(article.title)
    expect(document.querySelector<HTMLTextAreaElement>(`#js_description`)!.value).toBe(article.summary)
    expect(onSave).toHaveBeenCalledOnce()
    expect(result.kind === `done` && result.report).toMatchObject({ method: `jsapi`, draftSaved: true, titleFilled: true })
  })

  it(`falls back to pasting when the JSAPI is missing`, async () => {
    const body = wechatEditor()
    onPaste(body, (data) => {
      body.innerHTML = data.getData(`text/html`)
    })
    const result = await runStep(wechat, `fill`)
    expect(body.innerHTML).toBe(article.wechatHtml)
    expect(result.kind === `done` && result.report.method).toBe(`paste`)
  })

  it(`fails honestly when the body stays empty`, async () => {
    wechatEditor()
    document.querySelector(`#body`)!.innerHTML = ``
    expect(await runStep(wechat, `fill`)).toMatchObject({ kind: `error`, code: `fill-failed` })
  })

  function installJsApi(body: Element) {
    const invoke = vi.fn(({ apiParam, sucCb }: { apiParam: { content: string }, sucCb: () => void }) => {
      body.innerHTML = apiParam.content
      sucCb()
    })
    ;(window as unknown as Record<string, unknown>).__MP_Editor_JSAPI__ = { invoke }
    return invoke
  }

  it(`is not fooled by the editor page's hidden QR-verification text (regression)`, async () => {
    window.history.replaceState({}, ``, `/cgi-bin/appmsg?t=media/appmsg_edit_v2&type=10&token=123456`)
    const body = wechatEditor()
    // The logged-in editor ships hidden dialogs such as the scan-to-confirm prompt.
    document.body.insertAdjacentHTML(`beforeend`, `<div style="display:none">请使用微信扫描二维码确认</div>`)
    installJsApi(body)

    const result = await runStep(wechat, `fill`)

    expect(result.kind).toBe(`done`)
    expect(body.innerHTML).toBe(article.wechatHtml)
  })

  it(`tries the type=77 editor when the type=10 editor never appears`, async () => {
    window.history.replaceState({}, ``, `/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=10&token=123456&lang=zh_CN`)
    expect(await runStep(wechat, `fill`)).toEqual({ kind: `navigate`, url: wechatEditorUrl(`123456`, `77`), step: `fill` })
  })

  it(`reports editor-not-found with a page summary once both editor types failed`, async () => {
    window.history.replaceState({}, ``, `/cgi-bin/appmsg?t=media/appmsg_edit_v2&type=77&token=123456`)
    document.title = `公众号`
    const result = await runStep(wechat, `fill`)
    expect(result).toMatchObject({ kind: `error`, code: `editor-not-found` })
    expect(result.kind === `error` && result.message).toContain(`/cgi-bin/appmsg "公众号" editable=0 prosemirror=0`)
    expect(result.kind === `error` && result.message).not.toContain(`token`)
  })

  it(`reports login-required when the editor never appears and the page is logged out`, async () => {
    window.history.replaceState({}, ``, `/`)
    document.body.innerHTML = `<div>使用账号登录</div>`
    expect(await runStep(wechat, `fill`)).toMatchObject({ kind: `error`, code: `login-required` })
  })

  const PNG_DATA_URL = `data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==`

  it(`uploads embedded images to the image library before inserting the body`, async () => {
    window.history.replaceState({}, ``, `/cgi-bin/appmsg?t=media/appmsg_edit_v2&type=10&token=123456`)
    ;(window as unknown as Record<string, unknown>).wx = { commonData: { t: `123456`, ticket: `tk`, user_name: `gh_abc` } }
    const body = wechatEditor()
    const invoke = installJsApi(body)
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toContain(`/cgi-bin/filetransfer?action=upload_material`)
      return new Response(JSON.stringify({ base_resp: { ret: 0, err_msg: `ok` }, cdn_url: `https://mmbiz.qpic.cn/mmbiz_png/abc/0?wx_fmt=png` }))
    })
    vi.stubGlobal(`fetch`, fetchMock)
    const withImages = { ...article, wechatHtml: `<p>图1</p><img src="${PNG_DATA_URL}"><p>图2</p><img src="${PNG_DATA_URL}">` }

    const result = await runStep(wechat, `fill`, withImages)

    expect(fetchMock).toHaveBeenCalledOnce()
    const params = new URL(String(fetchMock.mock.calls[0][0]), `https://mp.weixin.qq.com`).searchParams
    expect(Object.fromEntries([`token`, `ticket`, `ticket_id`].map(key => [key, params.get(key)]))).toEqual({ token: `123456`, ticket: `tk`, ticket_id: `gh_abc` })
    const sent = invoke.mock.calls[0][0].apiParam.content
    expect(sent).not.toContain(`data:image`)
    expect(sent.match(/mmbiz\.qpic\.cn/g)).toHaveLength(2)
    expect(result.kind === `done` && result.report.images).toEqual({ total: 2, failed: 0 })
  })

  it(`keeps images that fail to upload and counts them`, async () => {
    window.history.replaceState({}, ``, `/cgi-bin/appmsg?t=media/appmsg_edit_v2&type=10&token=123456`)
    const body = wechatEditor()
    const invoke = installJsApi(body)
    vi.stubGlobal(`fetch`, vi.fn(async () => new Response(JSON.stringify({ base_resp: { ret: -1, err_msg: `no permission` } }))))

    const result = await runStep(wechat, `fill`, { ...article, wechatHtml: `<p>x</p><img src="${PNG_DATA_URL}">` })

    expect(invoke.mock.calls[0][0].apiParam.content).toContain(PNG_DATA_URL)
    expect(result.kind === `done` && result.report.images).toEqual({ total: 1, failed: 1 })
  })
})

function ueditorStub() {
  let content = ``
  return {
    isReady: 1,
    setContent: vi.fn((html: string) => {
      content = html
    }),
    execCommand: vi.fn((command: string, html: string) => {
      if (command === `inserthtml`)
        content += html
    }),
    getContentTxt: () => content.replace(/<[^>]+>/g, ``),
    fireEvent: vi.fn(),
  }
}

describe(`baijiahao`, () => {
  it(`fills the contenteditable title, sets UEditor content and saves`, async () => {
    document.body.innerHTML = `<div class="client_components_titleInput"><div contenteditable="true"></div></div><button>存草稿</button>`
    const editor = ueditorStub()
    ;(window as unknown as Record<string, unknown>).UE_V2 = { instants: { ueditorInstant0: editor } }
    const onSave = vi.fn()
    document.querySelector(`button`)!.addEventListener(`click`, onSave)

    const result = await runStep(baijiahao, `fill`)

    expect(editor.setContent).toHaveBeenCalledWith(article.wechatHtml)
    expect(document.querySelector(`[contenteditable]`)!.textContent).toBe(article.title)
    expect(onSave).toHaveBeenCalledOnce()
    expect(result.kind === `done` && result.report).toMatchObject({ method: `ueditor`, titleFilled: true, draftSaved: true })
  })

  it(`uses the plain HTML when no WeChat HTML was produced`, async () => {
    document.body.innerHTML = `<textarea placeholder="请输入标题"></textarea>`
    const editor = ueditorStub()
    ;(window as unknown as Record<string, unknown>).UE_V2 = { instants: { ueditorInstant0: editor } }
    await runStep(baijiahao, `fill`, { ...article, wechatHtml: `` })
    expect(editor.setContent).toHaveBeenCalledWith(article.html)
  })
})

describe(`bilibili`, () => {
  beforeEach(() => {
    document.cookie = `bili_jct=csrf-token`
  })

  afterEach(() => {
    document.cookie = `bili_jct=; expires=Thu, 01 Jan 1970 00:00:00 GMT`
  })

  it(`hasBilibiliSession looks for the cookies Bilibili sets on login`, () => {
    expect(hasBilibiliSession(`buvid3=x; bili_jct=abc`)).toBe(true)
    expect(hasBilibiliSession(`DedeUserID=42`)).toBe(true)
    expect(hasBilibiliSession(`buvid3=x; bili_jct=`)).toBe(false)
    expect(hasBilibiliSession(``)).toBe(false)
  })

  it(`reports login-required at once for the blank logged-out page`, async () => {
    document.cookie = `bili_jct=; expires=Thu, 01 Jan 1970 00:00:00 GMT`
    expect(await runStep(bilibili, `fill`)).toMatchObject({ kind: `error`, code: `login-required` })
  })

  interface StubNode {
    type: { name: string }
    attrs: Record<string, unknown>
    isText?: boolean
    text?: string
  }

  /**
   * The York column editor: title textarea, a Tiptap instance on `.editor-container .ProseMirror`,
   * and the footer's 保存为草稿 button and save tip. Pasted data: images become image nodes the
   * editor uploads in the background (`status` 0 → 1, or 2 when the upload fails); each sits in a
   * wrapper whose node view can retry it. The body is one paragraph of text (position 1 on), and
   * `latex` nodes show up in it as `[formula]`.
   */
  function yorkEditor({ attached = true, handlesPaste = true, failUploads = [] as number[], retryFails = {} as Record<number, string>, saveFails = false, latex = true } = {}) {
    document.body.innerHTML = `<div class="title"><textarea placeholder="请输入标题（建议30字以内）"></textarea></div>`
      + `<div class="editor-container"><div class="ProseMirror" contenteditable="true"></div></div>`
      + `<div class="footer"><span class="counter">0/20000</span></div><button class="vui_button">保存为草稿</button>`
    const dom = document.querySelector(`.ProseMirror`) as HTMLElement & { editor?: unknown }
    let text = ``
    let pasted = ``
    const images: Array<Record<string, unknown>> = []
    const uploadedAtSave: boolean[] = []
    const retries: number[] = []
    const paragraph = { type: { name: `paragraph` }, attrs: { textAlign: null } as Record<string, unknown>, get textContent() {
      return text
    } }
    const splice = (from: number, to: number, value: string) => {
      text = text.slice(0, from - 1) + value + text.slice(to - 1)
    }
    const editor = {
      view: { dom, dispatch: vi.fn() },
      state: {
        doc: {
          descendants: (visit: (node: StubNode, pos: number) => void) => {
            visit({ type: { name: `text` }, attrs: {}, isText: true, text }, 1)
            images.forEach(attrs => visit({ type: { name: `enhancedImage` }, attrs }, 0))
          },
        },
        tr: {
          doc: { resolve: () => ({ depth: 1, parent: paragraph, before: () => 0 }) },
          replaceWith: (from: number, to: number, node: { formula: string }) => splice(from, to, `[${node.formula}]`),
          insertText: (value: string, from: number, to: number) => splice(from, to, value),
          setNodeMarkup: (_pos: number, _type: undefined, attrs: Record<string, unknown>) => {
            paragraph.attrs = attrs
          },
        },
      },
      schema: { nodes: latex ? { latex: { create: (attrs: Record<string, unknown>) => attrs } } : {} },
      commands: {
        focus: vi.fn(() => true),
        insertContent: vi.fn((html: string) => {
          text = html.replace(/<[^>]+>/g, ``)
          return true
        }),
      },
      getText: () => text,
    }
    if (attached)
      dom.editor = editor
    ;(window as unknown as Record<string, unknown>).editor = editor

    onPaste(dom, (data) => {
      if (!handlesPaste)
        return false
      const html = data.getData(`text/html`)
      pasted = html
      text = html.replace(/<[^>]+>/g, ``)
      for (const [, src] of html.matchAll(/<img src="([^"]+)"/g)) {
        const attrs: Record<string, unknown> = { src, status: 0 }
        const index = images.push(attrs) - 1
        const wrapper = document.createElement(`div`) as HTMLDivElement & { __nodeView?: unknown }
        wrapper.className = `eva3-enhanced-image-wrapper`
        wrapper.__nodeView = {
          node: { type: { name: `enhancedImage` }, attrs },
          handleRetry: async () => {
            retries.push(index)
            Object.assign(attrs, { status: 0, message: null })
            await new Promise(resolve => setTimeout(resolve, 1000))
            Object.assign(attrs, index in retryFails ? { status: 2, message: retryFails[index] } : { status: 1 })
          },
        }
        dom.append(wrapper)
        setTimeout(() => {
          if (failUploads.includes(index)) {
            Object.assign(attrs, { status: 2, message: `请求过于频繁` })
            return
          }
          Object.assign(attrs, { status: 1, [`data-url`]: `https://i0.hdslb.com/bfs/new_dyn/${index}.png` })
        }, 3000)
      }
    })
    document.querySelector(`button`)!.addEventListener(`click`, () => {
      uploadedAtSave.push(images.every(attrs => attrs.status !== 0))
      setTimeout(() => {
        const footer = document.querySelector(`.footer`)!
        footer.querySelector(`.save-tip`)?.remove()
        const tip = document.createElement(`div`)
        tip.className = saveFails ? `save-tip save-fail` : `save-tip`
        tip.textContent = saveFails ? `12:00:01保存失败，请尝试手动保存` : `12:00:01保存成功`
        footer.prepend(tip)
      }, 800)
    })
    return { editor, uploadedAtSave, retries, paragraph, pastedHtml: () => pasted }
  }

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).editor
  })

  const png = `data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==`

  it(`fills the title, pastes the plain HTML and saves the draft`, async () => {
    const { editor, uploadedAtSave } = yorkEditor()

    const result = await runStep(bilibili, `fill`)

    expect((document.querySelector(`textarea`) as HTMLTextAreaElement).value).toBe(article.title)
    expect(editor.commands.focus).toHaveBeenCalledWith(`end`)
    expect(editor.commands.insertContent).not.toHaveBeenCalled()
    expect(uploadedAtSave).toEqual([true])
    expect(result.kind === `done` && result.report).toMatchObject({ method: `paste`, titleFilled: true, draftSaved: true })
    expect(result.kind === `done` && result.report.fallback).toBeUndefined()
  })

  it(`falls back to window.editor when the ProseMirror element carries no instance`, async () => {
    yorkEditor({ attached: false })
    const result = await runStep(bilibili, `fill`)
    expect(result.kind === `done` && result.report).toMatchObject({ method: `paste`, draftSaved: true })
  })

  it(`lets the editor upload pasted images, retries a failed one, and saves only after the uploads finish`, async () => {
    const { uploadedAtSave, retries } = yorkEditor({ failUploads: [1] })

    const result = await runStep(bilibili, `fill`, { ...article, html: `<p>图一</p><img src="${png}"><p>图二</p><img src="${png}">` })

    expect(retries).toEqual([1])
    expect(uploadedAtSave).toEqual([true])
    expect(result.kind === `done` && result.report.images).toEqual({ total: 2, failed: 0 })
  })

  it(`retries failed uploads one at a time and reports what still fails in Bilibili's words`, async () => {
    const { retries } = yorkEditor({ failUploads: [0, 1], retryFails: { 1: `图片格式不支持` } })

    const result = await runStep(bilibili, `fill`, { ...article, html: `<p>图一</p><img src="${png}"><p>图二</p><img src="${png}">` })

    // Four rounds: both failed images once, then the one that keeps failing.
    expect(retries).toEqual([0, 1, 1, 1, 1])
    expect(result.kind === `done` && result.report.images).toEqual({ total: 2, failed: 1, reasons: [`图片格式不支持`] })
  })

  it(`leaves the draft unsaved while an image is not uploaded, since Bilibili refuses a draft holding its data: URL`, async () => {
    const { uploadedAtSave } = yorkEditor({ failUploads: [1], retryFails: { 1: `服务器错误` } })

    const result = await runStep(bilibili, `fill`, { ...article, html: `<p>图一</p><img src="${png}"><p>图二</p><img src="${png}">` })

    expect(uploadedAtSave).toEqual([])
    expect(result.kind === `done` && result.report).toMatchObject({ draftSaved: false, draftBlockedBy: `images`, images: { total: 2, failed: 1 } })
  })

  it(`pastes formulas as text tokens, then turns them into Bilibili's formula nodes`, async () => {
    const { editor, pastedHtml } = yorkEditor()
    const html = `<p>质能方程 <span data-tex="E=mc^2">$E=mc^2$</span> 成立</p><img src="${png}">`

    const result = await runStep(bilibili, `fill`, { ...article, html, textLength: 14 })

    // A formula image in the paste would make the editor skip uploading the other images.
    expect(pastedHtml()).not.toContain(`data-tex`)
    expect(pastedHtml()).not.toContain(`$E=mc^2$`)
    expect(pastedHtml()).toContain(`<img src="${png}">`)
    expect(editor.getText()).toBe(`质能方程 [E=mc^2] 成立`)
    expect(editor.view.dispatch).toHaveBeenCalledTimes(1)
    expect(result.kind === `done` && result.report).toMatchObject({ formulas: { total: 1, placed: 1 }, expectedLength: 6 })
  })

  it(`gives a display formula display style and centers its paragraph`, async () => {
    const { editor, paragraph } = yorkEditor()

    await runStep(bilibili, `fill`, { ...article, html: `<p data-tex="\\sum_i x_i" data-tex-display="">$$\\sum_i x_i$$</p>` })

    expect(editor.getText()).toBe(`[\\displaystyle \\sum_i x_i]`)
    expect(paragraph.attrs.textAlign).toBe(`center`)
  })

  it(`re-encodes embedded WebP as PNG before pasting`, async () => {
    vi.stubGlobal(`createImageBitmap`, vi.fn(async () => ({ width: 2, height: 2, close: vi.fn() })))
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, `getContext`)
      .mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D)
    const toBlob = vi.spyOn(HTMLCanvasElement.prototype, `toBlob`).mockImplementation((callback: BlobCallback) => {
      callback(new Blob([`png`], { type: `image/png` }))
    })
    try {
      const { pastedHtml } = yorkEditor()
      await runStep(bilibili, `fill`, { ...article, html: `<p>图</p><img src="data:image/webp;base64,UklGRg==">` })
      expect(pastedHtml()).not.toContain(`image/webp`)
      expect(pastedHtml()).toContain(`<img src="data:image/png;base64,`)
    }
    finally {
      vi.unstubAllGlobals()
      getContext.mockRestore()
      toBlob.mockRestore()
    }
  })

  it(`keeps the TeX source where the editor has no formula node`, async () => {
    const { editor } = yorkEditor({ latex: false })

    const result = await runStep(bilibili, `fill`, { ...article, html: `<p>见 <span data-tex="x">$x$</span></p>` })

    expect(editor.getText()).toBe(`见 $x$`)
    expect(result.kind === `done` && result.report.formulas).toEqual({ total: 1, placed: 0 })
  })

  it(`inserts the HTML directly when the paste is ignored, reporting un-uploaded images`, async () => {
    const { editor } = yorkEditor({ handlesPaste: false })
    const html = `${article.html}<img src="${png}">`
    // The stub keeps no image nodes for inserted content; give it the one a real editor would hold.
    editor.commands.insertContent.mockImplementation(() => {
      editor.state.doc.descendants = visit => visit({ type: { name: `enhancedImage` }, attrs: { src: png } }, 0)
      editor.getText = () => `背景McByte 用掩码做关联。`
      return true
    })

    const result = await runStep(bilibili, `fill`, { ...article, html })

    expect(editor.commands.insertContent).toHaveBeenCalledWith(html)
    expect(result.kind === `done` && result.report).toMatchObject({
      method: `tiptap-insert`,
      fallback: `paste-ignored`,
      images: { total: 1, failed: 1 },
      draftBlockedBy: `images`,
    })
  })

  it(`reports the draft as unsaved when the save tip shows a failure`, async () => {
    yorkEditor({ saveFails: true })
    const result = await runStep(bilibili, `fill`)
    expect(result.kind === `done` && result.report.draftSaved).toBe(false)
  })

  it(`says so when Bilibili shows the retired-editor notice instead of an editor`, async () => {
    document.body.innerHTML = `<h2>旧版编辑器已停止使用</h2><button>前往</button>`
    const result = await runStep(bilibili, `fill`)
    expect(result).toMatchObject({ kind: `error`, code: `editor-not-found` })
    expect(result.kind === `error` && result.message).toContain(`/york/read-editor`)
  })

  it(`fails when no editor shows up`, async () => {
    document.body.innerHTML = `<div class="loading"></div>`
    expect(await runStep(bilibili, `fill`)).toMatchObject({ kind: `error`, code: `editor-not-found` })
  })
})

describe(`jianshu`, () => {
  it(`prepare creates a note in the first notebook and navigates to it`, async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `/author/notebooks`)
        return new Response(JSON.stringify([{ id: 7 }, { id: 8 }]))
      if (url === `/author/notes` && init?.method === `POST`) {
        expect(JSON.parse(String(init.body))).toEqual({ notebook_id: `7`, title: article.title, at_bottom: false })
        return new Response(JSON.stringify({ id: 99 }))
      }
      return new Response(`not found`, { status: 404 })
    })
    vi.stubGlobal(`fetch`, fetchMock)

    const result = await runStep(jianshu, `prepare`)

    expect(result).toEqual({ kind: `navigate`, url: `https://www.jianshu.com/writer#/notebooks/7/notes/99`, step: `fill` })
  })

  it(`prepare explains a missing notebook`, async () => {
    vi.stubGlobal(`fetch`, vi.fn(async () => new Response(`[]`)))
    expect(await runStep(jianshu, `prepare`)).toMatchObject({ kind: `error`, code: `fill-failed` })
  })

  it(`fill writes Markdown into the Markdown editor`, async () => {
    document.body.innerHTML = `<input class="_24i7u"><textarea id="arthur-editor"></textarea>`
    document.querySelector<HTMLInputElement>(`input`)!.value = article.title
    const result = await runStep(jianshu, `fill`)
    expect(document.querySelector<HTMLTextAreaElement>(`#arthur-editor`)!.value).toBe(article.markdown)
    expect(result.kind === `done` && result.report).toMatchObject({ method: `markdown`, titleFilled: true })
  })

  describe(`rich-text editor`, () => {
    const png = `data:image/png;base64,${btoa(`x`)}`
    const hostedPng = `https://upload-images.jianshu.io/upload_images/1-a.png`
    const richArticle: AgentArticle = {
      ...article,
      html: [
        `<h2>背景</h2>`,
        `<p>见<a href="https://arxiv.org/abs/2407.21783">论文</a>、<a href="https://github.com/meta-llama/llama3">代码</a>和<a href="https://huggingface.co/meta-llama">权重</a>。</p>`,
        `<p><img src="${png}" alt="架构图"></p>`,
        `<p><img src="https://example.com/fig.png" alt="远程图"></p>`,
        `<table><thead><tr><th>模型</th><th>上下文</th></tr></thead><tbody><tr><td>8B</td><td>128K</td></tr></tbody></table>`,
        `<p>规模<span data-tex="3.8 \\times 10^{25}">$3.8 \\times 10^{25}$</span></p>`,
      ].join(``),
    }

    function jianshuWriter(userInfo: Map<string, unknown> | null) {
      document.body.innerHTML = `<input class="_24i7u"><div id="editor"><div class="kalamu-area" contenteditable="true"></div></div>`
      document.querySelector<HTMLInputElement>(`input`)!.value = article.title
      const area = document.querySelector<HTMLElement>(`.kalamu-area`)!
      const dante = { $editElem: [area], handleHtmlChange: vi.fn() }
      const writer: Record<string, unknown> = { editor: dante, props: { userInfo } }
      if (userInfo)
        (window as unknown as Record<symbol, unknown>)[Symbol(`jianshu_editor`)] = writer
      return { area, dante, writer }
    }

    function jianshuFetch(overrides: Record<string, () => Response> = {}) {
      const fetchMock = vi.fn(async (url: string) => {
        const path = String(url).split(`?`)[0]
        if (overrides[path])
          return overrides[path]()
        if (path === `/upload_images/token.json`)
          return new Response(JSON.stringify({ token: `tok`, key: `upload_images/1-a.png` }))
        if (path === `https://upload.qiniup.com/`)
          return new Response(JSON.stringify({ url: hostedPng }))
        if (path === `/upload_images/fetch`)
          return new Response(JSON.stringify({ error: [{ message: `图片无法访问` }] }), { status: 422 })
        if (path === `/author/current_user`)
          return new Response(JSON.stringify({ nickname: `me`, member: null }))
        return new Response(`not found`, { status: 404 })
      })
      vi.stubGlobal(`fetch`, fetchMock)
      return fetchMock
    }

    afterEach(() => {
      for (const key of Object.getOwnPropertySymbols(window).filter(key => key.description === `jianshu_editor`))
        delete (window as unknown as Record<symbol, unknown>)[key]
    })

    it(`hosts every image on Jianshu, writes the body through Dante and keeps a non-member under 2 links`, async () => {
      jianshuFetch()
      const { area, dante } = jianshuWriter(new Map([[`member`, null]]))

      const result = await runStep(jianshu, `fill`, richArticle)

      expect(dante.handleHtmlChange).toHaveBeenCalledWith(true)
      const html = area.innerHTML
      expect(html).toContain(`<h2>背景</h2>`)
      expect(html).toContain(`<p><img src="${hostedPng}" alt="架构图"></p>`)
      // Neither Jianshu nor the page could copy it, so it keeps its link.
      expect(html).toContain(`<p><img src="https://example.com/fig.png" alt="远程图"></p>`)
      expect(html).toContain(`<p><strong>模型 | 上下文</strong><br>8B | 128K</p>`)
      expect(html).not.toContain(`data-tex`)
      expect(html).toContain(`规模<span>3.8&nbsp;×&nbsp;10²⁵</span>`)
      expect(html).toContain(`<a href="https://arxiv.org/abs/2407.21783">论文</a>`)
      expect(html).toContain(`<a href="https://github.com/meta-llama/llama3">代码</a>`)
      expect(html).toContain(`和权重。`)
      expect(result.kind === `done` && result.report).toMatchObject({
        method: `dante`,
        draftSaved: true,
        images: { total: 2, failed: 1, reasons: [`fetch: 图片无法访问`, `读取不到图片（HTTP 404）：example.com`] },
        links: { limit: 2, unwrapped: 1, remaining: 2 },
      })
    })

    it(`leaves a member's links alone`, async () => {
      jianshuFetch()
      const { area } = jianshuWriter(new Map([[`member`, { type: `gold` }]]))
      const result = await runStep(jianshu, `fill`, richArticle)
      expect(area.querySelectorAll(`a`)).toHaveLength(3)
      expect(result.kind === `done` && result.report.links).toBeUndefined()
    })

    it(`reports a draft Jianshu will refuse when bare URLs alone pass the limit`, async () => {
      jianshuFetch()
      jianshuWriter(new Map([[`member`, null]]))
      const urls = [`https://a.io/1`, `https://b.io/2`, `https://c.io/3`]
      const result = await runStep(jianshu, `fill`, { ...article, html: `<p>${urls.map(url => `<a href="${url}">${url}</a>`).join(` `)}</p>` })
      expect(result.kind === `done` && result.report).toMatchObject({
        draftSaved: false,
        draftBlockedBy: `links`,
        links: { limit: 2, unwrapped: 3, remaining: 3 },
      })
    })

    it(`pastes when the editor component is not on window, asking Jianshu whether the account is a member`, async () => {
      const fetchMock = jianshuFetch()
      const { area } = jianshuWriter(null)
      onPaste(area, (data) => {
        area.innerHTML = data.getData(`text/html`)
      })
      const result = await runStep(jianshu, `fill`, richArticle)
      expect(fetchMock.mock.calls.some(([url]) => url === `/author/current_user`)).toBe(true)
      expect(area.innerHTML).toContain(`<img src="${hostedPng}"`)
      expect(result.kind === `done` && result.report).toMatchObject({ method: `paste`, fallback: `paste`, links: { unwrapped: 1 } })
    })

    const drawnFormula = `<p data-tex="L = -\\log p" data-tex-display="" style="text-align: center;"><img src="${png}" alt="$$L = -\\log p$$" width="120" height="30"></p>`

    it(`keeps a display formula the extension drew as a picture`, async () => {
      jianshuFetch()
      const { area } = jianshuWriter(new Map([[`member`, null]]))
      const result = await runStep(jianshu, `fill`, { ...article, html: `<p>损失：</p>${drawnFormula}` })
      expect(area.innerHTML).toBe(`<p>损失：</p><p style="text-align: center;"><img src="${hostedPng}" alt="$$L = -\\log p$$" width="120" height="30"></p>`)
      expect(result.kind === `done` && result.report.images).toEqual({ total: 1, failed: 0 })
    })

    it(`writes a drawn formula as text when its picture does not reach Jianshu`, async () => {
      jianshuFetch({ '/upload_images/token.json': () => new Response(`Too Many Requests`, { status: 429 }) })
      const { area } = jianshuWriter(new Map([[`member`, null]]))
      const result = await runStep(jianshu, `fill`, { ...article, html: `<p>损失：</p>${drawnFormula}` })
      expect(area.innerHTML).toBe(`<p>损失：</p><p style="text-align: center;">L = −log p</p>`)
      expect(result.kind === `done` && result.report.images).toMatchObject({ total: 1, failed: 1, reasons: [`token.json: HTTP 429`] })
    })

    it(`writes again when Jianshu replaces the editor after the first write`, async () => {
      jianshuFetch()
      const { dante, writer } = jianshuWriter(new Map([[`member`, null]]))
      const replacement = document.createElement(`div`)
      replacement.className = `kalamu-area`
      replacement.contentEditable = `true`
      const next = { $editElem: [replacement], handleHtmlChange: vi.fn() }
      // The component remounts: a new Dante on a new element, the old element gone.
      dante.handleHtmlChange.mockImplementation(() => {
        document.querySelector(`.kalamu-area`)!.replaceWith(replacement)
        writer.editor = next
      })

      const result = await runStep(jianshu, `fill`, richArticle)

      expect(next.handleHtmlChange).toHaveBeenCalledWith(true)
      expect(replacement.innerHTML).toContain(`<h2>背景</h2>`)
      expect(result.kind === `done` && result.report).toMatchObject({ method: `dante-rewrite1`, draftSaved: true })
    })

    it(`reads the note back and saves through the component when the change event saved nothing`, async () => {
      window.history.replaceState({}, ``, `/writer#/notebooks/7/notes/99`)
      let stored = ``
      jianshuFetch({ '/author/notes/99/content': () => new Response(JSON.stringify({ content: stored })) })
      const { writer } = jianshuWriter(new Map([[`member`, null]]))
      ;(writer.editor as Record<string, unknown>).clear = { getContent: () => `<p>cleaned</p>` }
      writer.props = { ...(writer.props as object), match: { params: { noteId: `99` } } }
      const saveNote = vi.fn(() => {
        stored = `<p>cleaned</p>`
      })
      writer.saveNote = saveNote

      const result = await runStep(jianshu, `fill`, richArticle)

      expect(saveNote).toHaveBeenCalledWith(article.title, `<p>cleaned</p>`, true)
      expect(result.kind === `done` && result.report).toMatchObject({ method: `dante+save`, draftSaved: true })
      expect(result.kind === `done` && result.report.draftBlockedBy).toBeUndefined()
    })

    it(`fails as too long when Jianshu will not store a body over its 64 KiB`, async () => {
      window.history.replaceState({}, ``, `/writer#/notebooks/7/notes/99`)
      jianshuFetch({ '/author/notes/99/content': () => new Response(JSON.stringify({ content: `` })) })
      jianshuWriter(new Map([[`member`, null]]))
      const long = `<p>${`长`.repeat(30000)}</p>`
      const result = await runStep(jianshu, `fill`, { ...article, html: long })
      expect(result).toMatchObject({ kind: `error`, code: `too-long` })
      expect(result.kind === `error` && result.message).toContain(`90007 bytes`)
    })

    it(`reports a draft that is still empty on Jianshu`, async () => {
      window.history.replaceState({}, ``, `/writer#/notebooks/7/notes/99`)
      jianshuFetch({ '/author/notes/99/content': () => new Response(JSON.stringify({ content: `` })) })
      jianshuWriter(new Map([[`member`, null]]))
      const result = await runStep(jianshu, `fill`, richArticle)
      expect(result.kind === `done` && result.report).toMatchObject({ draftSaved: false, draftBlockedBy: `unsaved` })
    })

    it(`keeps failed embedded images out of a Markdown note`, async () => {
      jianshuFetch({ '/upload_images/token.json': () => new Response(`Too Many Requests`, { status: 429 }) })
      document.body.innerHTML = `<input class="_24i7u"><textarea id="arthur-editor"></textarea>`
      document.querySelector<HTMLInputElement>(`input`)!.value = article.title
      const result = await runStep(jianshu, `fill`, { ...article, markdown: `前言\n\n![图](${png})\n\n结尾` })
      expect(document.querySelector<HTMLTextAreaElement>(`#arthur-editor`)!.value).toBe(`前言\n\n［图片未能上传到简书］\n\n结尾`)
      expect(result.kind === `done` && result.report).toMatchObject({ images: { total: 1, failed: 1, reasons: [`token.json: HTTP 429`] } })
    })
  })
})

describe(`jianshu blocks`, () => {
  it(`turns each list into one paragraph of numbered or bulleted lines and h5/h6 into h4`, () => {
    const body = document.createElement(`div`)
    body.innerHTML = `<h5>小节</h5><ol start="3"><li>甲<ul><li>子项</li></ul></li><li><p>乙</p></li></ol><blockquote><ul><li>引用里的</li></ul></blockquote>`
    fitBlocks(body)
    expect(body.innerHTML).toBe(`<h4>小节</h4><p>3. 甲<br>${String.fromCharCode(0x3000).repeat(2)}• 子项<br>4. 乙 </p><blockquote><p>• 引用里的</p></blockquote>`)
  })

  it(`drops the line breaks between blocks, which Dante would turn into empty paragraphs`, () => {
    const body = document.createElement(`div`)
    body.innerHTML = `<h2>标题</h2>
<p>一 <b>二</b> 三</p>
<blockquote>
<p>引用</p>
</blockquote>
<pre><code>a
  b
</code></pre>
`
    dropBlankText(body)
    expect(body.innerHTML).toBe(`<h2>标题</h2><p>一 <b>二</b> 三</p><blockquote><p>引用</p></blockquote><pre><code>a
  b
</code></pre>`)
  })
})

describe(`jianshu link limit`, () => {
  it(`counts what the editor counts: URLs and dotted names, not image files`, () => {
    expect(countJianshuLinks(`<a href="https://a.io/x">https://a.io/x</a>`)).toBe(2)
    expect(countJianshuLinks(`<img src="https://upload-images.jianshu.io/upload_images/1-a.png?imageMogr2/auto-orient/strip">`)).toBe(0)
    expect(countJianshuLinks(`<p>torch.nn.functional 与 Llama 3.1 8B</p>`)).toBe(1)
    expect(countJianshuLinks(`<p>arxiv.org</p>`)).toBe(0)
  })

  it(`unwraps the last links first; a URL in the text still counts`, () => {
    const body = document.createElement(`div`)
    body.innerHTML = `<a href="https://a.io/1">一</a><a href="https://b.io/2">二</a><a href="https://c.io/3">https://c.io/3</a><a href="https://d.io/4">四</a>`
    expect(fitLinks(body, 2)).toEqual({ unwrapped: 3, remaining: 2 })
    expect(body.innerHTML).toBe(`<a href="https://a.io/1">一</a>二https://c.io/3四`)
  })
})
