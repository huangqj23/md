// Markdown-editor flow follows doocs/cose (Apache-2.0).
// The body editor is StackEdit's cledit on `pre.editor__inner`; it watches its own DOM, so writing the
// text there reaches CSDN's store. The editor has three view tabs: Markdown, 比对 (the default) and 预览.
// 预览 hides the editor and sets it `contenteditable="false"`, and CSDN keeps the last tab in localStorage
// (`data/layoutSettings`), so one click on 预览 broke every later sync. The agent switches back to 比对
// first, as a user would. Read from the editor.csdn.net/md bundle (2026-10-12).
import type { AgentArticle, AgentResult } from '../protocol'
import type { PlatformFiller } from '../result'
import type { CodeMirrorLike } from './codemirror'
import {
  clickButtonWhenReady,
  countTextChars,
  describePage,
  findButton,
  isVisible,
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
/** The same editor while CSDN holds it read-only: the 预览 tab, a history version, its AI assistant answering. */
const READ_ONLY_EDITOR_SELECTORS = [`.editor__inner`, `.markdown-highlighting`]
/** View tabs that show the editor, CSDN's default first. */
const EDITING_TABS = [`比对`, `Markdown`]

function findEditor(): HTMLElement | CodeMirrorLike | null {
  return queryFirst<HTMLElement>(EDITOR_SELECTORS) ?? getCodeMirror(document.querySelector(`.CodeMirror`))
}

/** Clicks an editing tab when the editor is on the page but hidden, i.e. the 预览 tab is open. */
function leavePreview(): boolean {
  const editor = queryFirst(READ_ONLY_EDITOR_SELECTORS)
  if (!editor || isVisible(editor))
    return false
  const tab = EDITING_TABS.map(label => findButton(label)).find(Boolean)
  tab?.click()
  return Boolean(tab)
}

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

  let switchedTab = false
  const target = await waitFor(() => {
    const editor = findEditor()
    if (!editor && !switchedTab)
      switchedTab = leavePreview()
    return editor
  })
  if (!target) {
    if (!queryFirst(READ_ONLY_EDITOR_SELECTORS))
      return editorMissing(`CSDN Markdown body editor`)
    const tried = switchedTab ? `, even after switching to the 比对 tab` : ``
    return fail(`fill-failed`, `CSDN Markdown editor stayed read-only${tried} (预览 tab, a history version, or its AI assistant answering) — ${describePage()}`)
  }

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
