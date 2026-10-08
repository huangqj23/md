// The body goes in as a rich-text paste of the plain HTML, which Zhihu's editor imports in the page.
// Pasting Markdown no longer works for our articles: Zhihu now sends any paste whose plain text looks
// like Markdown or HTML to `/editor/paste/type`, keeps it out of the editor, and offers "确认并解析",
// which posts the whole text to `/editor/paste/parse` and aborts after 5 s. An article that carries its
// images inline never parses in time, so both halves of a split article stayed empty. A paste whose
// text/plain part is empty skips that check (its `handlePastedText` returns early), and the HTML import
// keeps headings, lists, code (with its language), tables and links. Formulas go in as Zhihu's own
// markup, `<img eeimg alt="TeX">`, which the import turns into formulas. Read from Zhihu's column.app
// and editor bundles (2026-10-08).
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import {
  countTextChars,
  dispatchPaste,
  elementTextChars,
  pageMentions,
  queryFirst,
  selectContents,
  setFieldValue,
  simulateClick,
  sleep,
  waitFor,
  waitForStable,
} from '../dom'
import { FORMULA_DISPLAY_ATTR, FORMULA_TEX_ATTR } from '../protocol'
import { done, editorMissing, fail } from '../result'

const TITLE_SELECTORS = [`textarea[placeholder*="标题"]`, `.WriteIndex-titleInput textarea`]
const EDITOR_SELECTORS = [
  `.public-DraftEditor-content[contenteditable="true"]`,
  `.DraftEditor-root [contenteditable="true"]`,
  `.WriteIndex [contenteditable="true"]`,
]
const LOGIN_PHRASES = [`验证码登录`, `密码登录`, `扫码登录`]
/** Zhihu's counter under an article that is too long, e.g. "已超过 7973 个字". */
const OVER_LIMIT_PATTERN = /已超过\s*(\d+)\s*个?字/

/** How many characters Zhihu says the article is over its length limit, or null. */
export function overLimitBy(text: string): number | null {
  const match = text.match(OVER_LIMIT_PATTERN)
  return match ? Number(match[1]) : null
}

function pageOverLimitBy(): number | null {
  // eslint-disable-next-line unicorn/prefer-dom-node-text-content -- visible text only
  return overLimitBy(document.body.innerText ?? document.body.textContent ?? ``)
}

/**
 * Rewrites formulas as Zhihu's formula markup (`eeimg` 2 is display style). Also returns how much
 * `$…$` text they held, which a formula no longer shows as text in the editor.
 */
export function withZhihuFormulas(html: string): { html: string, formulaChars: number } {
  const body = new DOMParser().parseFromString(`<body>${html}</body>`, `text/html`).body
  const holders = Array.from(body.querySelectorAll(`[${FORMULA_TEX_ATTR}]`))
  let formulaChars = 0
  for (const holder of holders) {
    const tex = holder.getAttribute(FORMULA_TEX_ATTR) ?? ``
    const display = holder.hasAttribute(FORMULA_DISPLAY_ATTR)
    formulaChars += countTextChars(holder.textContent)
    const image = body.ownerDocument.createElement(`img`)
    image.setAttribute(`eeimg`, display ? `2` : `1`)
    image.setAttribute(`alt`, tex)
    image.setAttribute(`src`, `https://www.zhihu.com/equation?tex=${encodeURIComponent(tex)}`)
    if (display) {
      // Keep the paragraph, so the formula stays on a line of its own.
      holder.removeAttribute(FORMULA_TEX_ATTR)
      holder.removeAttribute(FORMULA_DISPLAY_ATTR)
      holder.replaceChildren(image)
    }
    else {
      holder.replaceWith(image)
    }
  }
  return { html: holders.length ? body.innerHTML : html, formulaChars }
}

/** Body length once the editor settles; stops early when Zhihu flags the article as too long. */
async function settledBodyLength(editor: HTMLElement): Promise<number> {
  await waitFor(() => pageOverLimitBy() !== null || elementTextChars(editor) > 0, { timeout: 15000 })
  if (pageOverLimitBy() !== null)
    return elementTextChars(editor)
  return waitForStable(() => elementTextChars(editor), { timeout: 15000 })
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const titleInput = await waitFor(() => queryFirst<HTMLTextAreaElement>(TITLE_SELECTORS), { timeout: 20000 })
  if (!titleInput)
    return pageMentions(LOGIN_PHRASES) ? fail(`login-required`, `Zhihu shows its login form`) : editorMissing(`Zhihu title field`)

  // Let the draft loader finish, otherwise it overwrites what we type.
  await sleep(1500)
  const titleFilled = setFieldValue(titleInput, article.title)

  const editor = await waitFor(() => queryFirst<HTMLElement>(EDITOR_SELECTORS))
  if (!editor)
    return editorMissing(`Zhihu body editor`)

  simulateClick(editor)
  // Draft.js holds the content in React state, so emptying the DOM by hand breaks the editor; select
  // whatever is there and let the paste replace it.
  if (elementTextChars(editor) > 0)
    selectContents(editor)
  await sleep(200)

  const { html, formulaChars } = withZhihuFormulas(article.html)
  // The empty text/plain part keeps Zhihu's Markdown detection out of the way (see above).
  dispatchPaste(editor, { html, text: `` })

  const bodyLength = await settledBodyLength(editor)
  const report: FillReport = {
    titleFilled,
    bodyLength,
    expectedLength: Math.max(0, article.textLength - formulaChars),
    method: `html`,
    draftSaved: true,
  }
  const over = pageOverLimitBy()
  if (over !== null)
    return fail(`too-long`, `Zhihu says the article is ${over} characters over its length limit`, report)
  if (bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `Zhihu editor stayed empty after pasting the article as rich text`, report)
  return done(report)
}

export const zhihu: PlatformFiller = {
  steps: { fill },
}
