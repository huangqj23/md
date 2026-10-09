// Column editor. Bilibili retired both earlier editors in 2026: the UEditor page
// (`article-text/home?newEditor=-1`) and the Quill one at `/read/editor/#/web` now only show
// "旧版编辑器已停止使用", whose 前往 button leads to `/york/read-editor`. That editor is Vue 3 + Tiptap:
// Tiptap hangs the instance on its `.ProseMirror` element and the page also sets `window.editor`; the
// title is a textarea; "保存为草稿" saves on demand (the page also autosaves every 60 s). The body goes
// in as a real paste, because the editor's image plugin then uploads each data: image to Bilibili's
// image host itself, the same as when a user pastes. Contract read from the york read-editor bundle
// (2026-10-08).
//
// What the plugin does not do for us, also from that bundle:
// - It uploads all pasted images at once and some of them fail. Each image's node view (on its
//   `.eva3-enhanced-image-wrapper`) has `handleRetry()`, the image's own 重新上传, which re-sends the
//   file the plugin cached; failed images go through it again one at a time, in rounds that wait
//   longer each time.
// - An image keeps its data: URL as `src` and gets the hosted one as `data-url`; the editor's
//   `getJSON()` swaps them only for uploaded images. A failed one would put its data: URL in the
//   draft, which Bilibili refuses ("保存失败，请尝试手动保存"), so the draft is not saved while any
//   image is unsent; the user re-uploads those in the editor, then saves.
// - Formulas are `latex` nodes (`img[data-type="latex"][data-formula]`, drawn by Bilibili's TeX
//   service), but a pasted `latex` image makes the plugin skip its paste handling, and with it every
//   upload. So formulas travel as plain-text tokens and become `latex` nodes after the paste.
// - It has no tables; the extension page sends them as images already (`tablesAsImages`).
// - It uploads data: images only, so images on an outside image host (md puts embedded images there
//   before syncing) are read back into data: URLs before the paste.
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import { clickButtonWhenReady, countTextChars, dispatchPaste, pageMentions, queryFirst, setFieldValue, sleep, waitFor } from '../dom'
import { FORMULA_DISPLAY_ATTR, FORMULA_TEX_ATTR } from '../protocol'
import { done, editorMissing, fail } from '../result'
import { ImageUploadError, readImage } from './platform-images'
import { toUploadableImage } from './wechat-images'

const TITLE_SELECTORS = [`.title textarea`, `textarea[placeholder*="标题"]`, `input[placeholder*="标题"]`]
const EDITOR_CONTAINER_SELECTOR = `.editor-container`
/** Footer tip the page rewrites after every save: "hh:mm:ss保存成功", or a `.save-fail` message. */
const SAVE_TIP_SELECTOR = `.save-tip`
const SAVE_DRAFT_LABEL = `保存为草稿`
/** Shown instead of an editor when a retired editor URL is opened. */
const RETIRED_EDITOR_NOTICE = `旧版编辑器已停止使用`
/** `status` attribute the editor's image plugin keeps on image nodes. */
const IMAGE_UPLOADING = 0
const IMAGE_UPLOADED = 1
const IMAGE_FAILED = 2
const IMAGE_WRAPPER_SELECTOR = `.eva3-enhanced-image-wrapper`
/** The platform's `stepTimeout` is 300 s; leave the save enough of it. */
const FILL_BUDGET = 270000
const SAVE_RESERVE = 25000
const FIRST_UPLOAD_WAIT = 120000
const RETRY_ROUNDS = 4
/** Pause before each later round, growing by this much, for an upload server that throttled the burst. */
const RETRY_BACKOFF = 3000
/** Pause between retried uploads, which lets the node view apply the result. */
const RETRY_GAP = 600

interface ProseNodeLike {
  type: { name: string }
  attrs: Record<string, unknown>
  isText?: boolean
  text?: string
  textContent?: string
}

interface TransactionLike {
  doc: { resolve: (pos: number) => { depth: number, parent: ProseNodeLike, before: () => number } }
  replaceWith: (from: number, to: number, node: unknown) => unknown
  insertText: (text: string, from: number, to: number) => unknown
  setNodeMarkup: (pos: number, type: undefined, attrs: Record<string, unknown>) => unknown
}

interface TiptapLike {
  view: { dom: HTMLElement, dispatch?: (tr: TransactionLike) => void }
  state: {
    doc: { descendants: (visit: (node: ProseNodeLike, pos: number) => boolean | void) => void }
    tr?: TransactionLike
  }
  schema?: { nodes: Record<string, { create: (attrs: Record<string, unknown>) => unknown } | undefined> }
  commands: {
    focus: (position?: string) => boolean
    insertContent: (value: string) => boolean
  }
  getText: () => string
}

interface ImageNodeView {
  node?: ProseNodeLike
  handleRetry?: () => Promise<void>
}

interface ImageCount {
  total: number
  uploading: number
  failed: number
  reasons: string[]
}

interface Formula {
  tex: string
  display: boolean
  /** The `$…$` source, kept where no formula node can be made. */
  raw: string
}

/** Logged-out visitors get a blank page, not a redirect; the CSRF cookie only exists after login. */
export function hasBilibiliSession(cookie: string = document.cookie): boolean {
  return /(?:^|;\s*)(?:bili_jct|DedeUserID)=[^;]/.test(cookie)
}

function isTiptap(value: unknown): value is TiptapLike {
  const candidate = value as Partial<TiptapLike> | null | undefined
  return !!candidate?.view?.dom && !!candidate.state?.doc
    && typeof candidate.commands?.insertContent === `function` && typeof candidate.getText === `function`
}

export function findBilibiliEditor(): TiptapLike | null {
  const dom = document.querySelector(`${EDITOR_CONTAINER_SELECTOR} .ProseMirror`) as (Element & { editor?: unknown }) | null
  const attached = dom?.editor
  if (isTiptap(attached))
    return attached
  const global = (window as unknown as { editor?: unknown }).editor
  return isTiptap(global) && global.view.dom.isConnected ? global : null
}

function isImageNode(node: ProseNodeLike): boolean {
  return node.type.name === `image` || node.type.name === `enhancedImage`
}

/** Uploads still running, and images that failed or never reached Bilibili's image host. */
function countImages(editor: TiptapLike): ImageCount {
  const count: ImageCount = { total: 0, uploading: 0, failed: 0, reasons: [] }
  editor.state.doc.descendants((node) => {
    if (!isImageNode(node))
      return
    count.total++
    const { status, src, message } = node.attrs
    if (status === IMAGE_UPLOADING) {
      count.uploading++
    }
    else if (status === IMAGE_FAILED || (status !== IMAGE_UPLOADED && typeof src === `string` && src.startsWith(`data:`))) {
      count.failed++
      if (typeof message === `string` && message && !count.reasons.includes(message))
        count.reasons.push(message)
    }
  })
  return count
}

/** Sends each failed image again, one at a time, through its node view's 重新上传. */
async function retryFailedImages(editor: TiptapLike, deadline: number) {
  const wrappers = Array.from(editor.view.dom.querySelectorAll(IMAGE_WRAPPER_SELECTOR)) as Array<Element & { __nodeView?: ImageNodeView }>
  for (const wrapper of wrappers) {
    const view = wrapper.__nodeView
    if (view?.node?.attrs.status !== IMAGE_FAILED || typeof view.handleRetry !== `function`)
      continue
    if (Date.now() > deadline)
      return
    try {
      await view.handleRetry()
    }
    catch {}
    await sleep(RETRY_GAP)
  }
}

const FORMULA_TOKEN = /⟦md-formula-(\d+)⟧/g

/** Takes the formulas out of the HTML, leaving a plain-text token where each one was. */
export function tokenizeFormulas(html: string): { html: string, formulas: Formula[] } {
  const body = new DOMParser().parseFromString(`<body>${html}</body>`, `text/html`).body
  const formulas: Formula[] = []
  for (const holder of Array.from(body.querySelectorAll(`[${FORMULA_TEX_ATTR}]`))) {
    formulas.push({
      tex: holder.getAttribute(FORMULA_TEX_ATTR) ?? ``,
      display: holder.hasAttribute(FORMULA_DISPLAY_ATTR),
      raw: holder.textContent ?? ``,
    })
    holder.removeAttribute(FORMULA_TEX_ATTR)
    holder.removeAttribute(FORMULA_DISPLAY_ATTR)
    holder.textContent = `⟦md-formula-${formulas.length - 1}⟧`
  }
  return formulas.length ? { html: body.innerHTML, formulas } : { html, formulas }
}

/**
 * Swaps each token for a `latex` node (a display formula gets `\displaystyle` and a centered
 * paragraph of its own); falls back to the `$…$` source where no node fits. Returns how many
 * formulas became nodes.
 */
export function placeFormulas(editor: TiptapLike, formulas: Formula[]): number {
  const tr = editor.state.tr
  if (!formulas.length || !tr || !editor.view.dispatch)
    return 0
  const hits: Array<{ from: number, to: number, token: string, formula: Formula }> = []
  editor.state.doc.descendants((node, pos) => {
    if (!node.isText || !node.text)
      return
    for (const match of node.text.matchAll(FORMULA_TOKEN)) {
      const formula = formulas[Number(match[1])]
      if (formula)
        hits.push({ from: pos + match.index!, to: pos + match.index! + match[0].length, token: match[0], formula })
    }
  })
  const latex = editor.schema?.nodes.latex
  let placed = 0
  // From the end, so earlier positions stay valid.
  for (const { from, to, token, formula } of hits.reverse()) {
    try {
      if (!latex)
        throw new Error(`no latex node`)
      if (formula.display) {
        const $from = tr.doc.resolve(from)
        const paragraph = $from.parent
        if ($from.depth > 0 && `textAlign` in paragraph.attrs && paragraph.textContent?.trim() === token)
          tr.setNodeMarkup($from.before(), undefined, { ...paragraph.attrs, textAlign: `center` })
      }
      tr.replaceWith(from, to, latex.create({ formula: formula.display ? `\\displaystyle ${formula.tex}` : formula.tex }))
      placed++
    }
    catch {
      tr.insertText(formula.raw, from, to)
    }
  }
  editor.view.dispatch(tr)
  return placed
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

const BILIBILI_IMAGE_HOST = /(?:^|\.)(?:hdslb\.com|biliimg\.com)$/i
const WEB_ADDRESS = /^(?:https?:)?\/\//i

function isBilibiliImage(src: string): boolean {
  try {
    return BILIBILI_IMAGE_HOST.test(new URL(src, location.href).hostname)
  }
  catch {
    return false
  }
}

interface PastableImages {
  html: string
  /** Linked images that could not be read and went in as links, which the plugin does not upload. */
  unread: number
  reasons: string[]
}

/**
 * Gets every image ready for the image plugin: linked ones from an outside host are read into data:
 * URLs, and WebP is re-encoded as PNG. The editor's client accepts WebP, but nothing shows
 * Bilibili's upload endpoint does (WeChat's does not); PNG is safe on both counts.
 */
export async function withPastableImages(html: string): Promise<PastableImages> {
  const body = new DOMParser().parseFromString(`<body>${html}</body>`, `text/html`).body
  const converted = new Map<string, Promise<string | null>>()
  const reasons = new Set<string>()
  let unread = 0
  for (const image of Array.from(body.querySelectorAll(`img[src]`))) {
    const src = image.getAttribute(`src`)!
    const linked = WEB_ADDRESS.test(src)
    if (linked ? isBilibiliImage(src) : !src.startsWith(`data:image/webp`))
      continue
    if (!converted.has(src)) {
      converted.set(src, readImage(src).then(toUploadableImage).then(
        image => linked || image.type === `image/png` ? blobToDataUrl(image) : null,
        (error: unknown) => {
          if (linked && error instanceof ImageUploadError)
            reasons.add(error.message)
          return null
        },
      ))
    }
    const dataUrl = await converted.get(src)!
    if (dataUrl)
      image.setAttribute(`src`, dataUrl)
    else if (linked)
      unread++
  }
  return { html: body.innerHTML, unread, reasons: [...reasons] }
}

/** Clicks 保存为草稿, then waits for the footer tip to report a new successful save. */
async function saveDraft(): Promise<boolean> {
  const before = document.querySelector(SAVE_TIP_SELECTOR)?.textContent ?? ``
  if (!(await clickButtonWhenReady(SAVE_DRAFT_LABEL, { timeout: 5000 })))
    return false
  return !!(await waitFor(() => {
    const tip = document.querySelector(SAVE_TIP_SELECTOR)
    const text = tip?.textContent ?? ``
    return !!tip && !tip.classList.contains(`save-fail`) && text.includes(`保存成功`) && text !== before
  }, { timeout: 20000, interval: 500 }))
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const deadline = Date.now() + FILL_BUDGET - SAVE_RESERVE
  if (!hasBilibiliSession())
    return fail(`login-required`, `No Bilibili session cookie`)

  const editor = await waitFor(findBilibiliEditor, { timeout: 25000 })
  if (!editor) {
    if (pageMentions([RETIRED_EDITOR_NOTICE]))
      return fail(`editor-not-found`, `Bilibili showed its retired-editor notice; the column editor now lives at /york/read-editor`)
    return editorMissing(`Bilibili column editor (Tiptap)`)
  }

  await sleep(500)
  const titleInput = queryFirst<HTMLTextAreaElement | HTMLInputElement>(TITLE_SELECTORS)
  const titleFilled = titleInput ? setFieldValue(titleInput, article.title) : false

  // Structure-only HTML: the editor keeps headings, lists, quotes, code, links and images.
  const tokenized = tokenizeFormulas(article.html || article.wechatHtml)
  const pastable = await withPastableImages(tokenized.html)
  const { html } = pastable
  const { formulas } = tokenized
  const bodyLength = () => countTextChars(editor.getText())
  editor.commands.focus(`end`)
  dispatchPaste(editor.view.dom, { html })
  let fallback: string | undefined
  if (!(await waitFor(() => bodyLength() > 0, { timeout: 5000 })) && article.textLength > 0) {
    // Inserted directly, data: images stay in the document un-uploaded and are reported as failed.
    editor.commands.insertContent(html)
    fallback = `paste-ignored`
  }
  await sleep(500)

  // Each pasted image is uploaded in the background; saving before that finishes drops it.
  const settled = () => countImages(editor).uploading === 0
  await waitFor(settled, { timeout: Math.max(0, Math.min(FIRST_UPLOAD_WAIT, deadline - Date.now())), interval: 500 })
  for (let round = 0; round < RETRY_ROUNDS && countImages(editor).failed > 0 && Date.now() < deadline; round++) {
    if (round > 0)
      await sleep(Math.min(RETRY_BACKOFF * round, Math.max(0, deadline - Date.now())))
    await retryFailedImages(editor, deadline)
    await waitFor(settled, { timeout: Math.max(0, deadline - Date.now()), interval: 500 })
  }
  const images = countImages(editor)
  // Linked images that could not be read are in the editor as links nothing uploads.
  images.failed += pastable.unread
  images.reasons.push(...pastable.reasons.filter(reason => !images.reasons.includes(reason)))
  const placed = placeFormulas(editor, formulas)

  const report: FillReport = {
    titleFilled,
    bodyLength: bodyLength(),
    // Formula nodes hold no text, so their `$…$` source is not expected in the body.
    expectedLength: Math.max(0, article.textLength - formulas.reduce((sum, formula) => sum + countTextChars(formula.raw), 0)),
    method: fallback ? `tiptap-insert` : `paste`,
    draftSaved: false,
    ...(images.total > 0
      ? { images: { total: images.total, failed: images.failed + images.uploading, ...(images.reasons.length ? { reasons: images.reasons } : {}) } }
      : {}),
    ...(formulas.length ? { formulas: { total: formulas.length, placed } } : {}),
    ...(fallback ? { fallback } : {}),
  }
  if (report.bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `Bilibili editor stayed empty`, report)

  if (images.failed + images.uploading > 0)
    report.draftBlockedBy = `images`
  else
    report.draftSaved = await saveDraft()
  return done(report)
}

export const bilibili: PlatformFiller = {
  steps: { fill },
}
