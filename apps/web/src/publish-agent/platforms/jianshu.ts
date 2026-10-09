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
// for embedded ones, `/upload_images/fetch` for linked ones). An embedded image that still fails
// leaves a marker in its place, since the editor would drop it from the saved note; a linked one
// keeps its link, which Dante saves as it is. The body is then written into the editor element and
// Dante told it changed
// (`handleHtmlChange`), which saves it through Dante's own cleanup: tags other than
// blockquote/pre/code/p/div/ul/ol/li/br/hr/h1-h6/img/a/b/i/u/del/strike/strong/em are dropped with
// their text kept, top-level blocks other than p/blockquote/pre/h1-h4 are rewrapped into a
// paragraph, and each `<img>` replaces its whole parent with Dante's image box. Pasting is the
// fallback when the component cannot be found.
//
// The editor component mounts again (a new Dante on a new `.kalamu-area`) while the note list and
// content load after the route switches to the new note, so the body is written to the editor found
// right then, again if that one is replaced, and the agent reads the note back from
// `/author/notes/:id/content` (`{ content }`) to know it was saved.
//
// Also from the bundle: saving runs `countUrlsInContent` over the note's HTML and, for an account
// without `member`, refuses more than 2 links with "对不起，您的文章中包含链接多于2个。" on every
// change. Links past the limit go in as their text. Dante has no tables or lists either; a table's
// rows and a list's items become paragraphs. Nor formulas: its formula pictures count as links, so
// formulas go in as text.
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import { texToText } from '../../services/publish/tex-text'
import {
  countTextChars,
  dispatchPaste,
  elementTextChars,
  htmlToText,
  pageMentions,
  queryFirst,
  setFieldValue,
  sleep,
  waitFor,
  waitForStable,
} from '../dom'
import { FORMULA_DISPLAY_ATTR, FORMULA_TEX_ATTR } from '../protocol'
import { done, editorMissing, fail, navigate } from '../result'
import { beforeDeadline, createJianshuUploader, errorMessageOf, ImageUploadError, readImage, UPLOAD_WITHIN_STEP, uploadDataImages } from './platform-images'
import { toUploadableImage } from './wechat-images'

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
/**
 * Linked images are copied until this long into the step (300 s), embedded ones stop earlier;
 * the rest is for writing the body and checking Jianshu saved it.
 */
const FETCH_BUDGET = 220000
/** Writes into an editor Jianshu then replaces before giving up on Dante. */
const WRITE_ATTEMPTS = 3
/** Dante saves 500 ms after a change; long enough for that and for a remount to show. */
const WRITE_SETTLE = 1500
/** How long Jianshu gets to store the body, each time the agent asks it to. */
const SAVE_WAIT = 10000
const SAVE_POLL = 1500
/**
 * Jianshu keeps a note's HTML in at most this many UTF-8 bytes (a 64 KiB text column: published
 * notes stop just under it) and refuses a longer body with this notice.
 */
const NOTE_MAX_BYTES = 65535
const TOO_LONG_NOTICE = `文章内容过长`
/** Two ideographic spaces per level of a nested list item. */
const NESTED_INDENT = String.fromCharCode(0x3000).repeat(2)
const NO_BREAK_SPACE = String.fromCharCode(0xA0)
const DATA_IMAGE_IN_MARKDOWN = /!\[[^\]]*\]\(\s*data:[^)\s]*\s*\)/g
const DATA_IMAGE_IN_HTML = /<img\s[^>]*?src\s*=\s*["']data:[^"']*["'][^>]*>/gi

interface DanteLike {
  $editElem: ArrayLike<HTMLElement>
  handleHtmlChange: (capture?: boolean) => void
  /** Dante's cleanup; `getContent()` is the HTML it saves. */
  clear?: { getContent?: () => string }
}

interface WriterLike {
  editor?: unknown
  props?: {
    userInfo?: { get?: (key: string) => unknown }
    match?: { params?: { noteId?: string | number } }
  }
  /** The rich-text component's save: title, content, force (skips its "unchanged" check). */
  saveNote?: (title?: string, content?: string, force?: boolean) => void
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

/** One line per row, cells joined by " | ", the header row bold, all in one paragraph. */
export function flattenTables(body: HTMLElement) {
  const doc = body.ownerDocument
  for (const table of Array.from(body.querySelectorAll(`table`)).filter(table => !table.parentElement?.closest(`table`))) {
    const header = table.tHead?.rows[0]
    const lines = Array.from(table.rows).map((row) => {
      const text = Array.from(row.cells).map(cell => (cell.textContent ?? ``).replace(/\s+/g, ` `).trim()).join(` | `)
      return [row === header ? Object.assign(doc.createElement(`strong`), { textContent: text }) : doc.createTextNode(text)]
    })
    table.replaceWith(linesParagraph(doc, lines))
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

function reasonOf(error: unknown): string | null {
  return error instanceof ImageUploadError && error.message ? error.message : null
}

/**
 * Puts every image of the HTML on Jianshu's image host. Images already there are left alone and not
 * counted. A linked image Jianshu cannot take keeps its link, which still shows it; an embedded or
 * local one is replaced with a marker.
 */
async function hostImages(html: string, fetchDeadline: number): Promise<{ html: string, images: ImageReport }> {
  const parse = (source: string) => new DOMParser().parseFromString(`<body>${source}</body>`, `text/html`).body
  const isLinked = (source: string) => /^https?:\/\//i.test(source) && !JIANSHU_IMAGE.test(source)
  // Uploading only swaps data: URLs for hosted ones, so both bodies list the same images in order.
  const originals = Array.from(parse(html).querySelectorAll(`img`), image => image.getAttribute(`src`) ?? ``)
  const embedded = await uploadDataImages(html, createJianshuUploader(), { ...UPLOAD_WITHIN_STEP, retries: 2 })
  const body = parse(embedded.text)
  const reasons = new Set(embedded.reasons)

  // A linked image: Jianshu copies it from its URL, as it does for the editor; failing that, the page
  // reads it (when its host allows cross-origin reads) and uploads it like an embedded one.
  const fetched = new Map<string, string>()
  const linkReasons = new Map<string, string[]>()
  const upload = createJianshuUploader()
  for (const source of new Set(originals.filter(isLinked))) {
    const why: string[] = []
    linkReasons.set(source, why)
    if (Date.now() >= fetchDeadline) {
      why.push(`上传超时`)
      continue
    }
    for (const copy of [() => fetchToJianshu(source), () => readImage(source).then(toUploadableImage).then(upload)]) {
      try {
        const url = await beforeDeadline(copy(), fetchDeadline)
        if (url) {
          fetched.set(source, url)
          break
        }
      }
      catch (error) {
        const reason = reasonOf(error)
        if (reason)
          why.push(reason)
      }
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
    if (isLinked(original)) {
      linkReasons.get(original)?.forEach(reason => reasons.add(reason))
      return
    }
    if (!original.startsWith(`data:`))
      reasons.add(`本地图片，先上传到图床再同步：${original.slice(0, 80)}`)
    // A display formula drawn as a picture goes in as text instead (see `formulasAsText`).
    const formula = image.closest(`[${FORMULA_TEX_ATTR}]`)
    if (formula) {
      formula.textContent = `$$${formula.getAttribute(FORMULA_TEX_ATTR) ?? ``}$$`
      return
    }
    const alt = image.getAttribute(`alt`)?.trim()
    image.replaceWith(body.ownerDocument.createTextNode(alt ? `［图片未能上传到简书：${alt}］` : IMAGE_MARKER))
  })
  return { html: body.innerHTML, images: { total, failed, reasons: failed ? [...reasons] : [] } }
}

/**
 * Writes formulas as plain text (`3.8 × 10²⁵`, see `texToText`): Dante has no formula node, and
 * its own formula pictures (`math.jianshu.com/math?formula=…`) count as links, which a non-member
 * may have only two of. A formula `texToText` cannot write keeps its `$…$` source. Display formulas
 * the extension drew as pictures (`withDisplayFormulaImages`) stay pictures.
 */
export function formulasAsText(body: HTMLElement) {
  for (const holder of Array.from(body.querySelectorAll(`[${FORMULA_TEX_ATTR}]`))) {
    const text = texToText(holder.getAttribute(FORMULA_TEX_ATTR) ?? ``)
    holder.removeAttribute(FORMULA_TEX_ATTR)
    holder.removeAttribute(FORMULA_DISPLAY_ATTR)
    if (holder.querySelector(`img`))
      continue
    // No-break spaces keep an inline formula on one line, as it was when typeset.
    if (text)
      holder.textContent = holder.tagName === `SPAN` ? text.replace(/ /g, NO_BREAK_SPACE) : text
  }
}

function imagesField(images: ImageReport): Pick<FillReport, `images`> {
  if (!images.total)
    return {}
  return { images: { total: images.total, failed: images.failed, ...(images.reasons.length ? { reasons: images.reasons } : {}) } }
}

async function fillMarkdown(article: AgentArticle, titleFilled: boolean): Promise<FillReport> {
  const upload = await uploadDataImages(article.markdown, createJianshuUploader(), { ...UPLOAD_WITHIN_STEP, retries: 2 })
  // An image left embedded would go into the note as megabytes of base64 text.
  const markdown = upload.text.replace(DATA_IMAGE_IN_MARKDOWN, IMAGE_MARKER).replace(DATA_IMAGE_IN_HTML, IMAGE_MARKER)
  const editor = queryFirst<HTMLTextAreaElement>(MARKDOWN_EDITOR_SELECTORS)
  if (editor)
    setFieldValue(editor, markdown)
  const stored = await waitUntilStored(openNoteId(), SAVE_WAIT)
  return {
    titleFilled,
    bodyLength: countTextChars(editor?.value),
    // Measured after the upload: the base64 text it replaced would otherwise inflate the target.
    expectedLength: countTextChars(markdown),
    method: `markdown`,
    draftSaved: stored !== false,
    ...(stored === false ? { draftBlockedBy: `unsaved` as const } : {}),
    ...imagesField({ total: upload.total, failed: upload.failed, reasons: upload.reasons ?? [] }),
  }
}

/** The note the writer has open, from its route (`#/notebooks/7/notes/99`). */
function openNoteId(): string | null {
  return location.hash.match(/\/notes\/(\d+)/)?.[1] ?? null
}

/**
 * The rich-text editor of the note being filled, when it is on the page. Jianshu builds a new Dante
 * (and a new `.kalamu-area`) whenever the editor component mounts or switches notes, which happens
 * while the note list and content load, so this is looked up again right before every write.
 */
function liveDante(noteId: string | null): { writer: WriterLike, dante: DanteLike } | null {
  const writer = findWriter()
  const dante = writer?.editor as Partial<DanteLike> | null | undefined
  const area = dante?.$editElem?.[0]
  if (!writer || !area?.isConnected || typeof dante?.handleHtmlChange !== `function`)
    return null
  const shown = writer.props?.match?.params?.noteId
  if (noteId && shown !== undefined && String(shown) !== noteId)
    return null
  return { writer, dante: dante as DanteLike }
}

/** Text characters of the note as Jianshu has stored it; null when that cannot be read. */
async function storedLength(noteId: string): Promise<number | null> {
  try {
    const response = await fetch(`/author/notes/${noteId}/content`, { credentials: `include`, headers: { Accept: `application/json` } })
    const data = await response.json() as { content?: unknown } | null
    return typeof data?.content === `string` ? countTextChars(htmlToText(data.content)) : null
  }
  catch {
    return null
  }
}

/** Whether Jianshu has stored a body for the note by `timeout`; null when that cannot be checked. */
async function waitUntilStored(noteId: string | null, timeout: number): Promise<boolean | null> {
  if (!noteId)
    return null
  const deadline = Date.now() + timeout
  while (true) {
    const length = await storedLength(noteId)
    if (length === null)
      return null
    if (length > 0)
      return true
    if (Date.now() >= deadline)
      return false
    await sleep(SAVE_POLL)
  }
}

/** The lines of a list: each item numbered or bulleted, nested items indented after their parent. */
function listLines(list: Element, depth: number): Node[][] {
  const start = Number(list.getAttribute(`start`)) || 1
  const lines: Node[][] = []
  Array.from(list.children).filter(item => item.tagName === `LI`).forEach((item, index) => {
    const nested = Array.from(item.children).filter(child => child.tagName === `UL` || child.tagName === `OL`)
    nested.forEach(child => child.remove())
    const line: Node[] = [list.ownerDocument.createTextNode(`${NESTED_INDENT.repeat(depth)}${list.tagName === `OL` ? `${start + index}. ` : `• `}`)]
    for (const node of Array.from(item.childNodes)) {
      // A loose list puts each item's text in paragraphs of its own.
      if (node instanceof Element && node.tagName === `P`)
        line.push(...Array.from(node.childNodes), list.ownerDocument.createTextNode(` `))
      else if (node.nodeType !== Node.TEXT_NODE || node.textContent?.trim())
        line.push(node)
    }
    lines.push(line)
    for (const child of nested)
      lines.push(...listLines(child, depth + 1))
  })
  return lines
}

/** One paragraph holding `lines`, a line break between each: no paragraph gap between them. */
function linesParagraph(doc: Document, lines: Node[][]): HTMLParagraphElement {
  const paragraph = doc.createElement(`p`)
  lines.forEach((line, index) => {
    if (index > 0)
      paragraph.append(doc.createElement(`br`))
    paragraph.append(...line)
  })
  return paragraph
}

/**
 * Dante keeps only p, blockquote, pre and h1-h4 as blocks; anything else it rewraps into a
 * paragraph holding the old markup. A list becomes one paragraph with a numbered or bulleted line
 * per item (a paragraph each would put the article's paragraph gap between items); h5/h6 become h4.
 */
export function fitBlocks(body: HTMLElement) {
  for (const heading of Array.from(body.querySelectorAll(`h5, h6`))) {
    const h4 = body.ownerDocument.createElement(`h4`)
    h4.append(...Array.from(heading.childNodes))
    heading.replaceWith(h4)
  }
  for (const list of Array.from(body.querySelectorAll(`ul, ol`)).filter(list => !list.parentElement?.closest(`ul, ol`)))
    list.replaceWith(linesParagraph(body.ownerDocument, listLines(list, 0)))
}

/**
 * Removes the whitespace (the HTML's line breaks) between blocks: Dante turns every such text node
 * into a paragraph of its own, an empty line between each pair of blocks.
 */
export function dropBlankText(body: HTMLElement) {
  for (const container of [body, ...Array.from(body.querySelectorAll(`blockquote`))]) {
    for (const node of Array.from(container.childNodes)) {
      if (node.nodeType === Node.TEXT_NODE && !node.textContent?.trim())
        node.remove()
    }
  }
}

/** The fill, and the size in bytes of the body Jianshu refused as too long (null when it did not). */
async function fillRichText(article: AgentArticle, titleFilled: boolean, fetchDeadline: number): Promise<{ report: FillReport, tooLong: number | null }> {
  // Everything slow happens before the editor is touched, so the write goes to the editor that is
  // there once the page has settled.
  const hosted = await hostImages(article.html, fetchDeadline)
  const body = new DOMParser().parseFromString(`<body>${hosted.html}</body>`, `text/html`).body
  formulasAsText(body)
  flattenTables(body)
  fitBlocks(body)
  isolateImages(body)
  dropBlankText(body)

  const noteId = openNoteId()
  let target = await waitFor(() => liveDante(noteId), { timeout: 15000 })
  const links = await isMember(target?.writer ?? findWriter()) ? null : fitLinks(body, NON_MEMBER_LINK_LIMIT)
  const html = body.innerHTML
  const blockedByLinks = !!links && links.remaining > NON_MEMBER_LINK_LIMIT

  const write = (into: { dante: DanteLike }) => {
    into.dante.$editElem[0].innerHTML = html
    into.dante.handleHtmlChange(true)
  }
  let method = `paste`
  let area: HTMLElement | null = null
  if (target) {
    method = `dante`
    for (let attempt = 1; attempt <= WRITE_ATTEMPTS; attempt++) {
      area = target.dante.$editElem[0]
      write(target)
      await sleep(WRITE_SETTLE)
      const now = liveDante(noteId)
      if (now?.dante === target.dante && elementTextChars(area) > 0)
        break
      // A new editor replaced the one written to: write again.
      target = now ?? await waitFor(() => liveDante(noteId), { timeout: 5000 })
      if (!target)
        break
      method = `dante-rewrite${attempt}`
    }
  }
  else {
    area = queryFirst<HTMLElement>(RICH_EDITOR_SELECTORS)
    area?.focus()
    if (area)
      dispatchPaste(area, { html })
  }

  let stored = blockedByLinks ? false : await waitUntilStored(noteId, SAVE_WAIT)
  const current = stored === false && !blockedByLinks ? liveDante(noteId) : null
  if (current && typeof current.writer.saveNote === `function`) {
    // Nothing was saved. If the editor was replaced since the write, the new one shows the stored
    // (empty) note and would save that over ours on the next keystroke: write into it too.
    if (current.dante !== target?.dante) {
      write(current)
      method += `+rewrite`
    }
    // Then save as the editor's own Ctrl+S does, and look again.
    const content = current.dante.clear?.getContent?.()
    current.writer.saveNote(article.title, typeof content === `string` ? content : undefined, true)
    method += `+save`
    stored = await waitUntilStored(noteId, SAVE_WAIT)
  }

  const live = liveDante(noteId)
  const saving = live?.dante.clear?.getContent?.()
  const bytes = new TextEncoder().encode(typeof saving === `string` ? saving : html).length
  const report: FillReport = {
    titleFilled,
    bodyLength: await waitForStable(() => elementTextChars(live?.dante.$editElem[0] ?? area)),
    expectedLength: countTextChars(body.textContent),
    method,
    draftSaved: stored !== false,
    ...(blockedByLinks ? { draftBlockedBy: `links` as const } : stored === false ? { draftBlockedBy: `unsaved` as const } : {}),
    ...imagesField(hosted.images),
    ...(links && (links.unwrapped > 0 || blockedByLinks) ? { links: { limit: NON_MEMBER_LINK_LIMIT, ...links } } : {}),
    // A paste loses headings, lists, quotes and code (see the top of this file).
    ...(method === `paste` ? { fallback: `paste` } : {}),
  }
  const tooLong = stored === false && !blockedByLinks && (bytes > NOTE_MAX_BYTES || pageMentions([TOO_LONG_NOTICE]))
  return { report, tooLong: tooLong ? bytes : null }
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

  const { report, tooLong } = editor instanceof HTMLTextAreaElement
    ? { report: await fillMarkdown(article, titleFilled), tooLong: null }
    : await fillRichText(article, titleFilled, fetchDeadline)
  if (tooLong !== null)
    return fail(`too-long`, `Jianshu refused the body as too long: ${tooLong} bytes of HTML, it keeps at most about ${NOTE_MAX_BYTES}`, report)
  if (report.bodyLength === 0 && report.expectedLength > 0)
    return fail(`fill-failed`, `Jianshu editor stayed empty (${report.method})`, report)
  return done(report)
}

export const jianshu: PlatformFiller = {
  steps: { prepare, fill },
}
