// Flow follows doocs/cose (Apache-2.0): token from the logged-in home page,
// new-article editor, body through the editor's own JSAPI, then "save draft".
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import type { WechatSession } from './wechat-images'
import {
  clickButtonWhenReady,
  describePage,
  dispatchPaste,
  elementTextChars,
  pageMentions,
  replaceEditableText,
  setFieldValue,
  sleep,
  waitFor,
  waitForStable,
} from '../dom'
import { done, editorMissing, fail, navigate } from '../result'
import { createWechatUploader, uploadEmbeddedImages } from './wechat-images'

interface MpEditorJsApi {
  invoke: (options: {
    apiName: string
    apiParam: Record<string, unknown>
    sucCb: (value?: unknown) => void
    errCb: (error?: unknown) => void
  }) => void
}

interface WechatWindow {
  wx?: {
    commonData?: { t?: string | number, ticket?: string, user_name?: string }
    data?: { t?: string | number }
  }
  __MP_Editor_JSAPI__?: MpEditorJsApi
}

/** Only consulted once the editor failed to appear: the editor page carries hidden QR dialogs. */
const LOGIN_PHRASES = [`扫码登录`, `使用账号登录`, `请使用微信扫描`]
const BODY_PLACEHOLDER = `从这里开始写正文`
/** New-article editor types, in the order tried: `10` is what COSE opens, `77` the other current one. */
const EDITOR_TYPES = [`10`, `77`]

export function wechatEditorUrl(token: string, type: string = EDITOR_TYPES[0]): string {
  return `https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=${type}&token=${encodeURIComponent(token)}&lang=zh_CN`
}

export function readWechatToken(win: Window & WechatWindow = window as Window & WechatWindow): string | null {
  const fromUrl = win.location.href.match(/[?&]token=(\d+)/)?.[1]
  if (fromUrl)
    return fromUrl
  const fromGlobal = win.wx?.commonData?.t ?? win.wx?.data?.t
  if (fromGlobal !== undefined && /^\d+$/.test(String(fromGlobal)))
    return String(fromGlobal)
  for (const link of Array.from(win.document.querySelectorAll<HTMLAnchorElement>(`a[href*="token="]`))) {
    const match = link.href.match(/[?&]token=(\d+)/)
    if (match)
      return match[1]
  }
  return null
}

export function readWechatSession(win: Window & WechatWindow = window as Window & WechatWindow): WechatSession | null {
  const token = readWechatToken(win)
  if (!token)
    return null
  const data = win.wx?.commonData
  return {
    token,
    ticket: typeof data?.ticket === `string` ? data.ticket : ``,
    userName: typeof data?.user_name === `string` ? data.user_name : ``,
  }
}

async function prepare(): Promise<AgentResult> {
  // The token wins as soon as it shows up, so a logged-in home page is never misread.
  const found = await waitFor(
    () => readWechatToken() ?? (pageMentions(LOGIN_PHRASES) ? `login` : null),
    { timeout: 12000 },
  )
  if (!found || found === `login`)
    return fail(`login-required`, `No logged-in session on mp.weixin.qq.com`)
  return navigate(wechatEditorUrl(found), `fill`)
}

function area(el: Element): number {
  const rect = el.getBoundingClientRect()
  return rect.width * rect.height
}

/**
 * The editor may render several ProseMirror instances (the title is one), and
 * the body can mount after the title, so pick carefully instead of the first.
 */
export function pickWechatBodyEditor(doc: Document = document): HTMLElement | null {
  const candidates = Array.from(doc.querySelectorAll<HTMLElement>(`.ProseMirror`))
    .filter(el => !el.closest(`.title-editor__input`))
  if (candidates.length <= 1)
    return candidates[0] ?? null
  const byPlaceholder = candidates.find(el => (el.textContent ?? ``).includes(BODY_PLACEHOLDER))
  if (byPlaceholder)
    return byPlaceholder
  return candidates.sort((a, b) => area(b) - area(a))[0]
}

function legacyBody(doc: Document = document): HTMLElement | null {
  const frame = doc.querySelector<HTMLIFrameElement>(`#ueditor_0`)
  const body = frame?.contentDocument?.body
  return body?.isContentEditable ? body : null
}

function fillTitle(title: string): boolean {
  let filled = false
  const titleEditor = document.querySelector<HTMLElement>(`.title-editor__input .ProseMirror`)
  if (titleEditor)
    filled = replaceEditableText(titleEditor, title)
  const titleField = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#title`)
  if (titleField)
    filled = setFieldValue(titleField, title) || filled
  return filled
}

function setContentViaJsApi(html: string): Promise<boolean> {
  const api = (window as Window & WechatWindow).__MP_Editor_JSAPI__
  if (!api || typeof api.invoke !== `function`)
    return Promise.resolve(false)
  return new Promise((resolve) => {
    let settled = false
    const finish = (ok: boolean) => {
      if (!settled) {
        settled = true
        resolve(ok)
      }
    }
    try {
      api.invoke({
        apiName: `mp_editor_set_content`,
        apiParam: { content: html },
        sucCb: () => finish(true),
        errCb: () => finish(false),
      })
    }
    catch {
      finish(false)
    }
    setTimeout(finish, 8000, false)
  })
}

/** Editor never appeared: tell a logged-out page from the other editor type from a changed page. */
function whenEditorMissing(): AgentResult {
  const token = readWechatToken()
  if (!token || pageMentions(LOGIN_PHRASES))
    return fail(`login-required`, `WeChat editor did not open and the page looks logged out — ${describePage()}`)
  const type = new URL(location.href).searchParams.get(`type`) ?? ``
  const index = EDITOR_TYPES.indexOf(type)
  const next = index === -1 ? undefined : EDITOR_TYPES[index + 1]
  if (next)
    return navigate(wechatEditorUrl(token, next), `fill`)
  return editorMissing(`WeChat article editor (type=${type})`)
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const bodyEditor = await waitFor(() => pickWechatBodyEditor() ?? legacyBody(), { timeout: 20000 })
  if (!bodyEditor)
    return whenEditorMissing()
  await sleep(800)

  const titleFilled = fillTitle(article.title)
  const digest = document.querySelector<HTMLTextAreaElement>(`#js_description`)
  if (digest && article.summary)
    setFieldValue(digest, article.summary)

  const session = readWechatSession()
  const images = await uploadEmbeddedImages(
    article.wechatHtml || article.html,
    session ? createWechatUploader(session) : async () => null,
  )

  const editor = pickWechatBodyEditor() ?? legacyBody() ?? bodyEditor
  let method = `jsapi`
  if (!(await setContentViaJsApi(images.html))) {
    method = `paste`
    editor.focus()
    dispatchPaste(editor, { html: images.html })
  }

  const bodyLength = await waitForStable(() => elementTextChars(pickWechatBodyEditor() ?? legacyBody() ?? editor))
  const report: FillReport = {
    titleFilled,
    bodyLength,
    expectedLength: article.textLength,
    method,
    draftSaved: false,
    ...(images.total > 0 ? { images: { total: images.total, failed: images.failed } } : {}),
  }
  if (bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `The editor stayed empty after inserting the article`, report)

  report.draftSaved = await clickButtonWhenReady(`保存为草稿`, { timeout: 5000 })
  if (report.draftSaved)
    await sleep(1500)
  return done(report)
}

export const wechat: PlatformFiller = {
  steps: { prepare, fill },
}
