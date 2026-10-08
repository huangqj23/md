// Note-creation flow follows doocs/cose and leaperone/MultiPost-Extension (both Apache-2.0).
//
// The writer opens a note in the editor of the note's type. Contract read from the writer's app and
// async-common-app bundles (cdn2.jianshu.io/writer/static/js, 2026-10-08):
// - Markdown: a textarea (`#arthur-editor`); images show from their URLs and nothing is uploaded.
// - Rich text: "Dante" on `.kalamu-area`. Its React component puts itself on `window` under
//   `Symbol("jianshu_editor")`, with the Dante instance as `editor` and the account as
//   `props.userInfo` (an Immutable Map of `/author/current_user`).
//
// What the rich-text editor does with a paste, and why the agent avoids it:
// - Every block is flattened into a plain paragraph: headings, lists, quotes and code are lost.
// - Each image is uploaded again unless it is already on Jianshu's image host (`jianshuImgUrlRegex`),
//   and every upload that fails opens its own "有图片未上传成功" dialog.
// So every image goes to Jianshu's image host first, by the same requests the editor makes (Qiniu
// for embedded ones, `/upload_images/fetch` for linked ones); an image that still fails leaves a
// marker in its place, since the editor would otherwise try again (or, for data:, drop it from the
// saved note). The body is then written into the editor element and Dante told it changed
// (`handleHtmlChange`), which saves it through Dante's own cleanup: tags other than
// blockquote/pre/code/p/div/ul/ol/li/br/hr/h1-h6/img/a/b/i/u/del/strike/strong/em are dropped with
// their text kept, and each `<img>` replaces its whole parent with Dante's image box. Pasting is
// the fallback when the component cannot be found.
//
// Also from the bundle: saving runs `countUrlsInContent` over the note's HTML and, for an account
// without `member`, refuses more than 2 links with "对不起，您的文章中包含链接多于2个。" on every
// change. Links past the limit go in as their text. Dante has no tables either; their cells would run
// together, so each row becomes a paragraph.
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import {
  countTextChars,
  dispatchPaste,
  elementTextChars,
  queryFirst,
  setFieldValue,
  sleep,
  waitFor,
  waitForStable,
} from '../dom'
import { FORMULA_DISPLAY_ATTR, FORMULA_TEX_ATTR } from '../protocol'
import { done, editorMissing, fail, navigate } from '../result'
import { beforeDeadline, createJianshuUploader, errorMessageOf, ImageUploadError, UPLOAD_WITHIN_STEP, uploadDataImages } from './platform-images'

const MARKDOWN_EDITOR_SELECTORS = [`#arthur-editor`, `textarea._3swFR`]
const RICH_EDITOR_SELECTORS = [`.kalamu-area[contenteditable="true"]`, `[contenteditable="true"]`]
const TITLE_SELECTORS = [`input._24i7u`, `input[class*="title" i]`]
/** Description of the symbol the rich-text editor's component is stored under on `window`. */
const WRITER_KEY = `jianshu_editor`

/** The editor's `jianshuImgUrlRegex`: images it keeps as they are instead of uploading them again. */
export const JIANSHU_IMAGE = /^(?:https?:)?\/\/(?:[\w-]+\.jianshu\.(?:io|com)|jianshu-(?:dev\.u|staging)\.qiniudn\.com)\//
/** The editor's `countUrlsInContent`: what it counts as a link in the note's HTML. */
const COUNTED_URL = /https?:\/\/[\w.-]+(?:\/[\w.-]*)*|\b(?:[\w-]+(?<!\d)\.){2,}[\w-]+(?:\/[\w.-]*)?/g
const NOT_COUNTED = /\.(?:jpe?g|png|gif|bmp|svg|webp|css|js|ico|pdf)$/i
const FILM_PAGE = /^https?:\/\/(?:www\.)?jianshu\.com\/film\//i
/** Links a note may hold when the account has no membership. */
export const NON_MEMBER_LINK_LIMIT = 2
/** Left where an image did not make it to Jianshu's image host. */
const IMAGE_MARKER = `［图片未能上传到简书］`
/** Linked images are copied until this long into the step (300 s); embedded ones stop earlier. */
const FETCH_BUDGET = 240000
const DATA_IMAGE_IN_MARKDOWN = /!\[[^\]]*\]\(\s*data:[^)\s]*\s*\)/g
const DATA_IMAGE_IN_HTML = /<img\s[^>]*?src\s*=\s*["']data:[^"']*["'][^>]*>/gi

interface DanteLike {
  $editElem: ArrayLike<HTMLElement>
  handleHtmlChange: (capture?: boolean) => void
}

interface WriterLike {
  editor?: unknown
  props?: { userInfo?: { get?: (key: string) => unknown } }
}

interface ImageReport {
  total: number
  failed: number
  reasons: string[]
}

function csrfHeaders(): Record<string, string> {
  const token = document.querySelector<HTMLMetaElement>(`meta[name="csrf-token"]`)?.content
  return token ? { 'X-CSRF-Token': token } : {}
}

async function prepare(article: AgentArticle): Promise<AgentResult> {
  const notebooksResponse = await fetch(`/author/notebooks`, { headers: { Accept: `application/json` } })
  if (notebooksResponse.status === 401 || notebooksResponse.redirected)
    return fail(`login-required`, `Jianshu session expired`)
  const notebooks = await notebooksResponse.json() as Array<{ id: number | string }>
  const notebookId = Array.isArray(notebooks) ? notebooks[0]?.id : undefined
  if (notebookId === undefined)
    return fail(`fill-failed`, `No Jianshu notebook (文集) exists yet; create one first`)

  const noteResponse = await fetch(`/author/notes`, {
    method: `POST`,
    headers: { 'Accept': `application/json`, 'Content-Type': `application/json`, ...csrfHeaders() },
    body: JSON.stringify({ notebook_id: String(notebookId), title: article.title, at_bottom: false }),
  })
  const note = await noteResponse.json() as { id?: number | string }
  if (!note?.id)
    return fail(`fill-failed`, `Jianshu refused to create a note (HTTP ${noteResponse.status})`)

  return navigate(`https://www.jianshu.com/writer#/notebooks/${notebookId}/notes/${note.id}`, `fill`)
}

function findTitleInput(title: string): HTMLInputElement | null {
  // The note was created with this title, so its input already shows it.
  const exact = Array.from(document.querySelectorAll<HTMLInputElement>(`input`)).find(input => input.value === title)
  return exact ?? queryFirst<HTMLInputElement>(TITLE_SELECTORS)
}

function findWriter(): WriterLike | null {
  const host = window as unknown as Record<PropertyKey, unknown>
  const symbol = Object.getOwnPropertySymbols(window).find(key => key.description === WRITER_KEY)
  // A polyfilled Symbol is a string key instead.
  const key = symbol ?? Object.keys(window).find(name => name.startsWith(`Symbol(${WRITER_KEY})`))
  const writer = key === undefined ? undefined : host[key]
  return writer && typeof writer === `object` ? writer as WriterLike : null
}

function danteOf(writer: WriterLike | null, area: HTMLElement): DanteLike | null {
  const editor = writer?.editor as Partial<DanteLike> | null | undefined
  return editor?.$editElem?.[0] === area && typeof editor.handleHtmlChange === `function` ? editor as DanteLike : null
}

async function isMember(writer: WriterLike | null): Promise<boolean> {
  const member = writer?.props?.userInfo?.get?.(`member`)
  if (member !== undefined)
    return !!member
  try {
    const response = await fetch(`/author/current_user`, { credentials: `include`, headers: { Accept: `application/json` } })
    const user = await response.json() as { member?: unknown } | null
    return !!user?.member
  }
  catch {
    return false
  }
}

/** Jianshu copies an image from its URL to its own host, as the editor asks it to for pasted images. */
export async function fetchToJianshu(url: string): Promise<string> {
  try {
    const response = await fetch(`/upload_images/fetch`, {
      method: `POST`,
      credentials: `include`,
      headers: { 'Accept': `application/json`, 'Content-Type': `application/json`, ...csrfHeaders() },
      body: JSON.stringify({ url }),
    })
    const result = await response.json().catch(() => null) as { url?: string } | null
    if (typeof result?.url === `string` && result.url)
      return result.url
    throw new ImageUploadError(`fetch: ${errorMessageOf(result) ?? `HTTP ${response.status}`}`)
  }
  catch (error) {
    if (error instanceof ImageUploadError)
      throw error
    throw new ImageUploadError(`fetch: ${error instanceof Error ? error.message : String(error)}`)
  }
}

/** What the editor's link check counts in `html`. */
export function countJianshuLinks(html: string): number {
  return (html.match(COUNTED_URL) ?? []).filter(url => !NOT_COUNTED.test(url) && !FILM_PAGE.test(url)).length
}

/**
 * Turns links into their text, last first so the article's first links stay links, until the
 * editor would count no more than `limit`. URLs in the text itself (an autolink's included) still
 * count, so `remaining` says whether that was enough.
 */
export function fitLinks(body: HTMLElement, limit: number): { unwrapped: number, remaining: number } {
  let remaining = countJianshuLinks(body.innerHTML)
  let unwrapped = 0
  for (const anchor of Array.from(body.querySelectorAll(`a[href]`)).reverse()) {
    if (remaining <= limit)
      break
    const saved = countJianshuLinks(anchor.outerHTML) - countJianshuLinks(anchor.innerHTML)
    if (saved <= 0)
      continue
    anchor.replaceWith(...Array.from(anchor.childNodes))
    unwrapped++
    remaining -= saved
  }
  return { unwrapped, remaining: countJianshuLinks(body.innerHTML) }
}

/** One paragraph per row, cells joined by " | ", the header row bold. */
export function flattenTables(body: HTMLElement) {
  const doc = body.ownerDocument
  for (const table of Array.from(body.querySelectorAll(`table`)).filter(table => !table.parentElement?.closest(`table`))) {
    const header = table.tHead?.rows[0]
    const paragraphs = Array.from(table.rows).map((row) => {
      const text = Array.from(row.cells).map(cell => (cell.textContent ?? ``).replace(/\s+/g, ` `).trim()).join(` | `)
      const paragraph = doc.createElement(`p`)
      if (row === header)
        paragraph.append(Object.assign(doc.createElement(`strong`), { textContent: text }))
      else
        paragraph.textContent = text
      return paragraph
    })
    table.replaceWith(...paragraphs)
  }
}

/**
 * Gives each image a paragraph of its own, after the top-level block it was in: the editor swaps
 * an image's whole parent for its image box, so anything sharing that parent would be lost.
 */
export function isolateImages(body: HTMLElement) {
  const lastPlaced = new Map<Element, Element>()
  for (const image of Array.from(body.querySelectorAll(`img`))) {
    const parent = image.parentElement!
    const alone = parent.tagName === `P` && parent !== body && Array.from(parent.childNodes).every(node => node === image || !node.textContent?.trim())
    if (alone)
      continue
    const paragraph = body.ownerDocument.createElement(`p`)
    if (parent === body) {
      image.replaceWith(paragraph)
    }
    else {
      let top: Element = parent
      while (top.parentElement !== body)
        top = top.parentElement!
      ;(lastPlaced.get(top) ?? top).after(paragraph)
      lastPlaced.set(top, paragraph)
    }
    paragraph.append(image)
  }
  for (const anchor of Array.from(body.querySelectorAll(`a`))) {
    if (!anchor.textContent?.trim() && !anchor.querySelector(`img`))
      anchor.remove()
  }
}

/**
 * Puts every image of the HTML on Jianshu's image host, or replaces it with a marker when that fails.
 * Images already there are left alone and not counted.
 */
async function hostImages(html: string, fetchDeadline: number): Promise<{ html: string, images: ImageReport }> {
  const parse = (source: string) => new DOMParser().parseFromString(`<body>${source}</body>`, `text/html`).body
  const isLinked = (source: string) => /^https?:\/\//i.test(source) && !JIANSHU_IMAGE.test(source)
  // Uploading only swaps data: URLs for hosted ones, so both bodies list the same images in order.
  const originals = Array.from(parse(html).querySelectorAll(`img`), image => image.getAttribute(`src`) ?? ``)
  const embedded = await uploadDataImages(html, createJianshuUploader(), { ...UPLOAD_WITHIN_STEP, retries: 2 })
  const body = parse(embedded.text)
  const reasons = new Set(embedded.reasons)

  const fetched = new Map<string, string>()
  for (const source of new Set(originals.filter(isLinked))) {
    if (Date.now() >= fetchDeadline)
      break
    try {
      const url = await beforeDeadline(fetchToJianshu(source), fetchDeadline)
      if (url)
        fetched.set(source, url)
    }
    catch (error) {
      if (error instanceof ImageUploadError && error.message)
        reasons.add(error.message)
    }
  }

  let total = 0
  let failed = 0
  Array.from(body.querySelectorAll(`img`)).forEach((image, index) => {
    const original = originals[index] ?? ``
    if (JIANSHU_IMAGE.test(original))
      return
    total++
    const current = image.getAttribute(`src`) ?? ``
    const hosted = current !== original ? current : fetched.get(original)
    if (hosted) {
      image.setAttribute(`src`, hosted)
      return
    }
    failed++
    const alt = image.getAttribute(`alt`)?.trim()
    image.replaceWith(body.ownerDocument.createTextNode(alt ? `［图片未能上传到简书：${alt}］` : IMAGE_MARKER))
  })
  return { html: body.innerHTML, images: { total, failed, reasons: failed ? [...reasons] : [] } }
}

function imagesField(images: ImageReport): Pick<FillReport, `images`> {
  if (!images.total)
    return {}
  return { images: { total: images.total, failed: images.failed, ...(images.reasons.length ? { reasons: images.reasons } : {}) } }
}

async function fillMarkdown(editor: HTMLTextAreaElement, article: AgentArticle, titleFilled: boolean): Promise<FillReport> {
  const upload = await uploadDataImages(article.markdown, createJianshuUploader(), { ...UPLOAD_WITHIN_STEP, retries: 2 })
  // An image left embedded would go into the note as megabytes of base64 text.
  const markdown = upload.text.replace(DATA_IMAGE_IN_MARKDOWN, IMAGE_MARKER).replace(DATA_IMAGE_IN_HTML, IMAGE_MARKER)
  setFieldValue(editor, markdown)
  return {
    titleFilled,
    bodyLength: countTextChars(editor.value),
    // Measured after the upload: the base64 text it replaced would otherwise inflate the target.
    expectedLength: countTextChars(markdown),
    method: `markdown`,
    draftSaved: true,
    ...imagesField({ total: upload.total, failed: upload.failed, reasons: upload.reasons ?? [] }),
  }
}

async function fillRichText(editor: HTMLElement, article: AgentArticle, titleFilled: boolean, fetchDeadline: number): Promise<FillReport> {
  const hosted = await hostImages(article.html, fetchDeadline)
  const body = new DOMParser().parseFromString(`<body>${hosted.html}</body>`, `text/html`).body
  for (const holder of Array.from(body.querySelectorAll(`[${FORMULA_TEX_ATTR}]`))) {
    holder.removeAttribute(FORMULA_TEX_ATTR)
    holder.removeAttribute(FORMULA_DISPLAY_ATTR)
  }
  flattenTables(body)
  isolateImages(body)

  const writer = findWriter()
  const links = await isMember(writer) ? null : fitLinks(body, NON_MEMBER_LINK_LIMIT)
  const html = body.innerHTML
  const dante = danteOf(writer, editor)
  if (dante) {
    dante.$editElem[0].innerHTML = html
    dante.handleHtmlChange(true)
  }
  else {
    editor.focus()
    dispatchPaste(editor, { html })
  }

  const blockedByLinks = !!links && links.remaining > NON_MEMBER_LINK_LIMIT
  return {
    titleFilled,
    bodyLength: await waitForStable(() => elementTextChars(editor)),
    expectedLength: countTextChars(body.textContent),
    method: dante ? `dante` : `paste`,
    draftSaved: !blockedByLinks,
    ...(blockedByLinks ? { draftBlockedBy: `links` as const } : {}),
    ...imagesField(hosted.images),
    ...(links && (links.unwrapped > 0 || blockedByLinks) ? { links: { limit: NON_MEMBER_LINK_LIMIT, ...links } } : {}),
    // A paste loses headings, lists, quotes and code (see the top of this file).
    ...(dante ? {} : { fallback: `paste` }),
  }
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const fetchDeadline = Date.now() + FETCH_BUDGET
  const editor = await waitFor(
    () => queryFirst<HTMLTextAreaElement>(MARKDOWN_EDITOR_SELECTORS) ?? queryFirst<HTMLElement>(RICH_EDITOR_SELECTORS),
    { timeout: 20000 },
  )
  if (!editor)
    return editorMissing(`Jianshu editor`)
  await sleep(800)

  const titleInput = findTitleInput(article.title)
  const titleFilled = titleInput
    ? titleInput.value === article.title || setFieldValue(titleInput, article.title)
    : false

  const report = editor instanceof HTMLTextAreaElement
    ? await fillMarkdown(editor, article, titleFilled)
    : await fillRichText(editor, article, titleFilled, fetchDeadline)
  if (report.bodyLength === 0 && report.expectedLength > 0)
    return fail(`fill-failed`, `Jianshu editor stayed empty`, report)
  return done(report)
}

export const jianshu: PlatformFiller = {
  steps: { prepare, fill },
}
