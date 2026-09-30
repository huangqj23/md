// Markdown-paste flow follows doocs/cose (Apache-2.0); the HTML-paste fallback
// follows leaperone/MultiPost-Extension (Apache-2.0).
import type { AgentArticle, AgentResult } from '../protocol'
import type { PlatformFiller } from '../result'
import {
  clearEditable,
  clickButtonWhenReady,
  dispatchPaste,
  elementTextChars,
  pageMentions,
  queryFirst,
  setFieldValue,
  simulateClick,
  sleep,
  waitFor,
  waitForStable,
} from '../dom'
import { done, editorMissing, fail } from '../result'

const TITLE_SELECTORS = [`textarea[placeholder*="标题"]`, `.WriteIndex-titleInput textarea`]
const EDITOR_SELECTORS = [
  `.public-DraftEditor-content[contenteditable="true"]`,
  `.DraftEditor-root [contenteditable="true"]`,
  `.WriteIndex [contenteditable="true"]`,
]
const LOGIN_PHRASES = [`验证码登录`, `密码登录`, `扫码登录`]

/**
 * A line that only reads as Markdown syntax (heading, list, emphasis, link).
 * If it shows up verbatim in the editor, the paste was not parsed.
 */
export function markdownSignature(markdown: string): string | null {
  for (const raw of markdown.split(`\n`)) {
    const line = raw.trim()
    if (/^#{1,6}\s+\S/.test(line) || /\*\*[^*]+\*\*/.test(line) || /\]\(\S+\)/.test(line) || /^[-*+]\s+\S/.test(line))
      return line
  }
  return null
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
  clearEditable(editor)
  await sleep(200)

  let method = `markdown`
  dispatchPaste(editor, { text: article.markdown })
  const parsed = await clickButtonWhenReady(label => label.includes(`确认并解析`), { timeout: 6000 })
  if (parsed) {
    await clickButtonWhenReady(`确认`, { timeout: 8000 })
  }
  else {
    const signature = markdownSignature(article.markdown)
    const rawMarkdownLeft = elementTextChars(editor) === 0 || (signature !== null && (editor.textContent ?? ``).includes(signature))
    if (rawMarkdownLeft) {
      method = `html`
      clearEditable(editor)
      await sleep(200)
      dispatchPaste(editor, { html: article.html })
    }
  }

  const bodyLength = await waitForStable(() => elementTextChars(editor), { timeout: 15000 })
  const report = { titleFilled, bodyLength, expectedLength: article.textLength, method, draftSaved: true }
  if (bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `Zhihu editor stayed empty after pasting`, report)
  return done(report)
}

export const zhihu: PlatformFiller = {
  steps: { fill },
}
