// Markdown-editor flow follows doocs/cose (Apache-2.0).
import type { AgentArticle, AgentResult } from '../protocol'
import type { PlatformFiller } from '../result'
import {
  clickButtonWhenReady,
  countTextChars,
  queryFirst,
  setFieldValue,
  sleep,
  waitFor,
} from '../dom'
import { done, editorMissing, fail } from '../result'
import { getCodeMirror } from './codemirror'

const TITLE_SELECTORS = [`.article-bar__title input`, `input.article-bar__title--input`, `input[placeholder*="标题"]`]
const EDITOR_SELECTORS = [
  `pre.editor__inner[contenteditable="true"]`,
  `.editor__inner[contenteditable="true"]`,
  `[contenteditable="true"].markdown-highlighting`,
]

function setEditorMarkdown(editor: HTMLElement, markdown: string) {
  editor.focus()
  // The editor re-derives its model from the element text on `input`.
  editor.textContent = markdown
  editor.dispatchEvent(new InputEvent(`input`, { bubbles: true, inputType: `insertText`, data: null }))
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const titleInput = await waitFor(() => queryFirst<HTMLInputElement>(TITLE_SELECTORS), { timeout: 20000 })
  if (!titleInput)
    return editorMissing(`CSDN Markdown editor title field`)
  await sleep(800)
  const titleFilled = setFieldValue(titleInput, article.title)

  const target = await waitFor(() => queryFirst<HTMLElement>(EDITOR_SELECTORS) ?? getCodeMirror(document.querySelector(`.CodeMirror`)))
  if (!target)
    return editorMissing(`CSDN Markdown body editor`)

  let method: string
  let bodyText: string
  if (target instanceof HTMLElement) {
    method = `contenteditable`
    setEditorMarkdown(target, article.markdown)
    await sleep(500)
    bodyText = target.textContent ?? ``
  }
  else {
    method = `codemirror`
    target.setValue(article.markdown)
    bodyText = target.getValue()
  }

  const report = {
    titleFilled,
    bodyLength: countTextChars(bodyText),
    expectedLength: countTextChars(article.markdown),
    method,
    draftSaved: false,
  }
  if (report.bodyLength === 0 && report.expectedLength > 0)
    return fail(`fill-failed`, `CSDN editor stayed empty`, report)

  report.draftSaved = await clickButtonWhenReady(label => label.includes(`保存草稿`), { timeout: 4000 })
  if (report.draftSaved)
    await sleep(1500)
  return done(report)
}

export const csdn: PlatformFiller = {
  steps: { fill },
}
