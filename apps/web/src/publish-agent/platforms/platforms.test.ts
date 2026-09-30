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
import { jianshu } from './jianshu'
import { juejin } from './juejin'
import { toutiao } from './toutiao'
import { pickWechatBodyEditor, readWechatToken, wechat, wechatEditorUrl } from './wechat'
import { markdownSignature, zhihu } from './zhihu'

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
})

describe(`zhihu`, () => {
  function zhihuEditor() {
    document.body.innerHTML = `
      <textarea placeholder="请输入标题（最多 100 个字）"></textarea>
      <div class="DraftEditor-root"><div class="public-DraftEditor-content" contenteditable="true"></div></div>`
    return document.querySelector(`.public-DraftEditor-content`)!
  }

  it(`pastes Markdown and confirms Zhihu's parse prompt`, async () => {
    const editor = zhihuEditor()
    onPaste(editor, (data) => {
      const prompt = document.createElement(`button`)
      prompt.textContent = `确认并解析`
      prompt.addEventListener(`click`, () => {
        prompt.remove()
        const confirm = document.createElement(`button`)
        confirm.textContent = `确认`
        confirm.addEventListener(`click`, () => {
          editor.innerHTML = `<h2>背景</h2><p>McByte 用掩码做关联。</p><ul><li>免训练</li><li>免调参</li></ul>`
          confirm.remove()
        })
        document.body.appendChild(confirm)
      })
      expect(data.getData(`text/plain`)).toBe(article.markdown)
      document.body.appendChild(prompt)
    })

    const result = await runStep(zhihu, `fill`)

    expect(result.kind === `done` && result.report.method).toBe(`markdown`)
    expect(editor.textContent).toContain(`免调参`)
    expect(editor.textContent).not.toContain(`**`)
  })

  it(`replaces raw Markdown with pasted HTML when no parse prompt appears`, async () => {
    const editor = zhihuEditor()
    onPaste(editor, (data) => {
      const html = data.getData(`text/html`)
      if (html)
        editor.innerHTML = html
      else
        editor.textContent = data.getData(`text/plain`)
    })

    const result = await runStep(zhihu, `fill`)

    expect(result.kind === `done` && result.report.method).toBe(`html`)
    expect(editor.innerHTML).toBe(article.html)
  })

  it(`detects Zhihu's login form`, async () => {
    document.body.innerHTML = `<div>验证码登录 密码登录</div>`
    expect(await runStep(zhihu, `fill`)).toMatchObject({ kind: `error`, code: `login-required` })
  })

  it(`markdownSignature picks a line only Markdown syntax would produce`, () => {
    expect(markdownSignature(`plain text\n\n## Heading`)).toBe(`## Heading`)
    expect(markdownSignature(`see [link](https://a.b)`)).toBe(`see [link](https://a.b)`)
    expect(markdownSignature(`just words`)).toBeNull()
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

  it(`inserts HTML through the legacy UEditor`, async () => {
    document.body.innerHTML = `<textarea placeholder="请输入标题（建议30字以内）"></textarea><button>存草稿</button>`
    const editor = ueditorStub()
    ;(window as unknown as Record<string, unknown>).UE = { instants: { ueditorInstant0: editor } }

    const result = await runStep(bilibili, `fill`)

    expect(editor.setContent).toHaveBeenCalledWith(``)
    expect(editor.execCommand).toHaveBeenCalledWith(`inserthtml`, article.wechatHtml)
    expect(result.kind === `done` && result.report).toMatchObject({ method: `ueditor`, titleFilled: true, draftSaved: true })
  })

  it(`fails when the legacy editor is not available`, async () => {
    document.body.innerHTML = `<div class="new-editor"></div>`
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
})
