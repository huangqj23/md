// Paste flow follows leaperone/MultiPost-Extension (Apache-2.0); field
// handling follows doocs/cose (Apache-2.0).
//
// Images go to Toutiao's own image host before the paste. Toutiao publishes only images it serves
// itself (it also crops the default cover out of one), and an article whose images were embedded
// (data:) or sat on an outside host went into the editor fine but then failed with "发布失败，请重试".
// The upload is the editor's own: `upload_picture` through `window.Garr.network`, which carries the
// page's auth, and the response holds the image's address or its uri on p3.toutiaoimg.com. Read from
// mp.toutiao.com's shared vendor bundle (2026-10-08).
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import {
  clearEditable,
  dispatchPaste,
  elementTextChars,
  execCommand,
  queryFirst,
  setFieldValue,
  simulateClick,
  sleep,
  waitFor,
  waitForStable,
} from '../dom'
import { done, editorMissing, fail } from '../result'
import { dataUrlToBlob, toUploadableImage } from './wechat-images'

const TITLE_SELECTORS = [`textarea[placeholder*="标题"]`, `input[placeholder*="标题"]`]
const EDITOR_SELECTORS = [`.ProseMirror[contenteditable="true"]`, `.ProseMirror`]
const UPLOAD_PATH = `/mp/agw/article_material/photo/upload_picture`
/** Hosts Toutiao serves article images from. */
const TOUTIAO_IMAGE_HOST = /(?:^|\.)(?:toutiaoimg\.(?:com|cn)|pstatp\.com|toutiao\.com|byteimg\.com)$/i
const UPLOAD_CONCURRENCY = 3
/** Requests whose failure explains a failed save or publish. */
const ARTICLE_REQUEST = /article|publish|draft|save|content/i
const DIAGNOSIS_ID = `md-publish-toutiao-diagnosis`
/** Links that may stay links in an article: Toutiao's own pages. */
const TOUTIAO_LINK = /^(?:https?:)?\/\/(?:[\w-]+\.)*toutiao\.com(?:[/?#:]|$)/i

/**
 * Turns every link but those to Toutiao's own pages into its text. Toutiao takes outside links only
 * as the publish settings' 扩展链接 (a "了解更多" button): with dozens of them in the body, the draft
 * would not save and publishing failed with code 5009 (2026-10-08). URLs written out as text stay.
 */
export function unwrapOutsideLinks(html: string): { html: string, unwrapped: number } {
  const template = document.createElement(`template`)
  template.innerHTML = html
  let unwrapped = 0
  for (const anchor of Array.from(template.content.querySelectorAll(`a[href]`))) {
    if (TOUTIAO_LINK.test(anchor.getAttribute(`href`) ?? ``))
      continue
    anchor.replaceWith(...Array.from(anchor.childNodes))
    unwrapped++
  }
  return { html: unwrapped ? template.innerHTML : html, unwrapped }
}

interface AxiosHandler {
  fulfilled: (value: unknown) => unknown
  rejected: (error: unknown) => unknown
  synchronous?: boolean
  runWhen?: null
}

interface AxiosLike {
  interceptors?: {
    response?: {
      use?: (onFulfilled: (value: unknown) => unknown, onRejected: (error: unknown) => unknown) => unknown
      /** axios keeps its interceptors here and runs them in this order. */
      handlers?: Array<AxiosHandler | null>
    }
  }
}

interface RequestLike {
  url?: string
  data?: unknown
}

interface GarrLike {
  network?: AxiosLike & { post: (url: string, body: FormData, config?: { headers?: Record<string, string> }) => Promise<unknown> }
  pgc_info?: { media?: { watermark?: number | string } }
}

interface ImageUpload {
  html: string
  total: number
  failed: number
  reasons: string[]
}

function garr(): GarrLike | undefined {
  return (window as unknown as { Garr?: GarrLike }).Garr
}

/** Toutiao's reason in a response body that reports a failure (non-zero code), or null. */
export function toutiaoFailure(body: unknown): string | null {
  if (!body || typeof body !== `object`)
    return null
  const record = body as Record<string, unknown>
  const code = record.code ?? record.err_no ?? record.status_code
  if (code === undefined || code === null || code === 0 || code === `0` || code === ``)
    return null
  const message = [record.message, record.err_tips, record.reason, record.msg].find(value => typeof value === `string` && value)
  return `${typeof message === `string` ? message : `没有说明原因`}（code ${String(code)}）`
}

const SHOWN_BODY_CHARS = 400
const EMBEDDED_IMAGE = /data(?::|%3A)image(?:\/|%2F)/gi
/** What the fill did, kept as the banner's first line so a later failure shows next to it. */
let fillSummary = ``

function showDiagnosis(lines: string[] = []) {
  let banner = document.getElementById(DIAGNOSIS_ID)
  if (!banner) {
    banner = document.createElement(`div`)
    banner.id = DIAGNOSIS_ID
    banner.style.cssText = `position:fixed;right:16px;bottom:96px;z-index:2147483647;max-width:480px;max-height:50vh;overflow:auto;`
      + `padding:10px 14px;border:1px solid #ffd591;border-radius:8px;background:#fff7e6;color:#873800;`
      + `font:13px/1.6 system-ui,sans-serif;box-shadow:0 4px 12px rgba(0,0,0,.12);white-space:pre-wrap;word-break:break-all`
    banner.title = `点击关闭`
    banner.addEventListener(`click`, () => banner?.remove())
    document.body.append(banner)
  }
  banner.textContent = [`md 诊断`, ...(fillSummary ? [fillSummary] : []), ...lines].join(`\n`)
}

/** What a request sent: its path, its size, and how many embedded images its body still carried. */
export function describeRequest(config: RequestLike | undefined): string {
  const path = (config?.url ?? ``).split(`?`)[0]
  const data = config?.data
  let text = ``
  if (typeof data === `string`) {
    text = data
  }
  else if (data && !(data instanceof FormData)) {
    try {
      text = JSON.stringify(data) ?? ``
    }
    catch {}
  }
  if (!text)
    return path
  const embedded = (text.match(EMBEDDED_IMAGE) ?? []).length
  return `${path}，约 ${Math.max(1, Math.round(text.length / 1024))} KB${embedded ? `，里面还有 ${embedded} 张内嵌图片（没在头条图床上）` : ``}`
}

/**
 * Toutiao's editor answers a failed save or publish with nothing but "发布失败，请重试". Its network
 * clients are axios instances, so a read-only interceptor can show what Toutiao answered (code,
 * message, the whole body) and what the request carried, in a banner on the page. It goes first in
 * line, before Toutiao's own interceptors reduce a response to its body. Requests are left untouched.
 */
export function watchToutiaoFailures() {
  const page = window as unknown as { Garr?: GarrLike, Garfish?: { network?: AxiosLike }, __mdToutiaoWatched?: boolean }
  if (page.__mdToutiaoWatched)
    return
  page.__mdToutiaoWatched = true
  const inspect = (response: unknown) => {
    const value = response as { config?: RequestLike, data?: unknown } | null | undefined
    const config = value?.config
    // Without a config, an earlier interceptor already reduced the response to its body.
    const body = config ? value?.data : value
    if (config?.url && !ARTICLE_REQUEST.test(config.url))
      return
    const failure = toutiaoFailure(body)
    if (!failure)
      return
    let raw = ``
    try {
      raw = JSON.stringify(body) ?? ``
    }
    catch {}
    showDiagnosis([
      `头条返回：${failure}`,
      ...(config ? [`请求：${describeRequest(config)}`] : []),
      ...(raw ? [`返回：${raw.length > SHOWN_BODY_CHARS ? `${raw.slice(0, SHOWN_BODY_CHARS)}…` : raw}`] : []),
    ])
  }
  const fulfilled = (response: unknown) => {
    try {
      inspect(response)
    }
    catch {}
    return response
  }
  const rejected = (error: unknown) => {
    try {
      inspect((error as { response?: unknown } | null)?.response)
    }
    catch {}
    return Promise.reject(error)
  }
  for (const client of new Set([page.Garr?.network, page.Garfish?.network])) {
    const handlers = client?.interceptors?.response?.handlers
    if (Array.isArray(handlers))
      handlers.unshift({ fulfilled, rejected, synchronous: false, runWhen: null })
    else
      client?.interceptors?.response?.use?.(fulfilled, rejected)
  }
}

/** Whether Toutiao serves this image itself. */
export function isToutiaoImage(src: string): boolean {
  try {
    return TOUTIAO_IMAGE_HOST.test(new URL(src, location.href).hostname)
  }
  catch {
    return false
  }
}

/** The image's address in Toutiao's upload response: a URL, or a uri served from p3.toutiaoimg.com. */
export function uploadedImageUrl(response: unknown, depth = 0): string | null {
  if (!response || typeof response !== `object` || depth > 3)
    return null
  const record = response as Record<string, unknown>
  for (const key of [`url`, `web_url`, `origin_url`]) {
    const value = record[key]
    if (typeof value === `string` && /^(?:https?:)?\/\//.test(value))
      return value.startsWith(`//`) ? `https:${value}` : value
  }
  for (const key of [`web_uri`, `origin_web_uri`, `uri`]) {
    const value = record[key]
    if (typeof value === `string` && value)
      return `https://p3.toutiaoimg.com/large/${value}`
  }
  for (const value of Object.values(record)) {
    const found = uploadedImageUrl(value, depth + 1)
    if (found)
      return found
  }
  return null
}

function failureMessage(response: unknown): string {
  const record = (response && typeof response === `object` ? response : {}) as Record<string, unknown>
  const message = [record.message, record.msg, record.reason, record.state].find(value => typeof value === `string` && value && value !== `SUCCESS`)
  return typeof message === `string` ? message : `头条没有返回图片地址`
}

async function uploadToToutiao(image: Blob): Promise<string> {
  const watermark = garr()?.pgc_info?.media?.watermark ?? 0
  const url = `${UPLOAD_PATH}?type=ueditor&pgc_watermark=${watermark}&action=uploadimage&encode=utf-8&is_private=0`
  const form = new FormData()
  form.append(`upfile`, image, `md-${Date.now()}.${(image.type.split(`/`)[1] ?? `png`).replace(`jpeg`, `jpg`)}`)
  const network = garr()?.network
  // Same call as the editor's own; the plain request is for a page without the global.
  const response = network
    ? await network.post(url, form, { headers: { 'Content-Type': `multipart/form-data` } })
    : await fetch(url, { method: `POST`, body: form, credentials: `include` }).then(result => result.json())
  const address = uploadedImageUrl(response)
  if (!address)
    throw new Error(failureMessage(response))
  return address
}

async function imageBlob(src: string): Promise<Blob | null> {
  if (src.startsWith(`data:`))
    return dataUrlToBlob(src)
  try {
    const response = await fetch(src, { credentials: `omit` })
    return response.ok ? await response.blob() : null
  }
  catch {
    return null
  }
}

/** Puts every image Toutiao does not serve itself on Toutiao's image host, a few at a time. */
export async function uploadImagesToToutiao(html: string): Promise<ImageUpload> {
  const template = document.createElement(`template`)
  template.innerHTML = html
  const images = Array.from(template.content.querySelectorAll<HTMLImageElement>(`img[src]`))
    .filter(image => !isToutiaoImage(image.getAttribute(`src`)!))
  if (!images.length)
    return { html, total: 0, failed: 0, reasons: [] }

  const sources = [...new Set(images.map(image => image.getAttribute(`src`)!))]
  const uploaded = new Map<string, string>()
  const reasons = new Set<string>()
  let next = 0
  const worker = async () => {
    while (next < sources.length) {
      const src = sources[next++]
      try {
        const blob = await imageBlob(src)
        if (!blob)
          throw new Error(`读取不到这张图：${src.startsWith(`data:`) ? `内嵌图片` : new URL(src, location.href).hostname}`)
        uploaded.set(src, await uploadToToutiao(await toUploadableImage(blob)))
      }
      catch (error) {
        reasons.add(error instanceof Error ? error.message : String(error))
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(UPLOAD_CONCURRENCY, sources.length) }, worker))

  let failed = 0
  for (const image of images) {
    const address = uploaded.get(image.getAttribute(`src`)!)
    if (address)
      image.setAttribute(`src`, address)
    else
      failed++
  }
  return { html: template.innerHTML, total: images.length, failed, reasons: [...reasons] }
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const titleInput = await waitFor(() => queryFirst<HTMLTextAreaElement>(TITLE_SELECTORS), { timeout: 20000 })
  if (!titleInput)
    return editorMissing(`Toutiao title field`)
  // Before the paste, so a failing autosave right after it shows its reason too.
  watchToutiaoFailures()

  const editor = await waitFor(() => queryFirst<HTMLElement>(EDITOR_SELECTORS))
  if (!editor)
    return editorMissing(`Toutiao body editor (ProseMirror)`)

  await sleep(800)
  const titleFilled = setFieldValue(titleInput, article.title)

  const links = unwrapOutsideLinks(article.html)
  const images = await uploadImagesToToutiao(links.html)
  const { html } = images

  simulateClick(editor)
  clearEditable(editor)
  await sleep(200)

  let method = `paste`
  dispatchPaste(editor, { html })
  let bodyLength = await waitForStable(() => elementTextChars(editor))
  if (bodyLength < article.textLength * 0.3) {
    method = `insertHTML`
    clearEditable(editor)
    editor.focus()
    execCommand(document, `insertHTML`, html)
    bodyLength = await waitForStable(() => elementTextChars(editor))
  }

  const report: FillReport = {
    titleFilled,
    bodyLength,
    expectedLength: article.textLength,
    method,
    draftSaved: true,
    ...(images.total > 0
      ? { images: { total: images.total, failed: images.failed, ...(images.reasons.length ? { reasons: images.reasons } : {}) } }
      : {}),
    // No outside link may stay a link on Toutiao, hence a limit of 0.
    ...(links.unwrapped > 0 ? { links: { limit: 0, unwrapped: links.unwrapped, remaining: 0 } } : {}),
  }
  // Kept as the first line of the banner a later failure brings up; shown now only when images failed.
  fillSummary = fillSummaryOf(images, links.unwrapped)
  if (images.failed > 0)
    showDiagnosis()
  if (bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `Toutiao editor stayed empty`, report)
  return done(report)
}

/** One line on what went in: whether every image reached Toutiao's image host, and links made text. */
export function fillSummaryOf(images: Pick<ImageUpload, `total` | `failed` | `reasons`>, unwrappedLinks: number): string {
  const parts = [
    images.total === 0
      ? `正文里没有需要上传的图片`
      : images.failed === 0
        ? `${images.total} 张图片都已传到头条图床`
        : `${images.failed}/${images.total} 张图片没能传到头条图床，还是内嵌的：${images.reasons.join(`；`) || `原因不明`}`,
    ...(unwrappedLinks ? [`${unwrappedLinks} 个站外链接改成了文字`] : []),
  ]
  return `同步：${parts.join(`；`)}`
}

export const toutiao: PlatformFiller = {
  steps: { fill },
}
