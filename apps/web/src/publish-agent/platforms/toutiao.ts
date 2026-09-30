// Paste flow follows leaperone/MultiPost-Extension (Apache-2.0); field
// handling follows doocs/cose (Apache-2.0).
import type { AgentArticle, AgentResult } from '../protocol'
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

const TITLE_SELECTORS = [`textarea[placeholder*="标题"]`, `input[placeholder*="标题"]`]
const EDITOR_SELECTORS = [`.ProseMirror[contenteditable="true"]`, `.ProseMirror`]

async function fill(article: AgentArticle): Promise<AgentResult> {
  const titleInput = await waitFor(() => queryFirst<HTMLTextAreaElement>(TITLE_SELECTORS), { timeout: 20000 })
  if (!titleInput)
    return editorMissing(`Toutiao title field`)

  const editor = await waitFor(() => queryFirst<HTMLElement>(EDITOR_SELECTORS))
  if (!editor)
    return editorMissing(`Toutiao body editor (ProseMirror)`)

  await sleep(800)
  const titleFilled = setFieldValue(titleInput, article.title)

  simulateClick(editor)
  clearEditable(editor)
  await sleep(200)

  let method = `paste`
  dispatchPaste(editor, { html: article.html })
  let bodyLength = await waitForStable(() => elementTextChars(editor))
  if (bodyLength < article.textLength * 0.3) {
    method = `insertHTML`
    clearEditable(editor)
    editor.focus()
    execCommand(document, `insertHTML`, article.html)
    bodyLength = await waitForStable(() => elementTextChars(editor))
  }

  const report = { titleFilled, bodyLength, expectedLength: article.textLength, method, draftSaved: true }
  if (bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `Toutiao editor stayed empty`, report)
  return done(report)
}

export const toutiao: PlatformFiller = {
  steps: { fill },
}
