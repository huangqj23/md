// Note-creation flow follows doocs/cose and leaperone/MultiPost-Extension (both Apache-2.0).
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
import { done, editorMissing, fail, navigate } from '../result'

const MARKDOWN_EDITOR_SELECTORS = [`#arthur-editor`, `textarea._3swFR`]
const RICH_EDITOR_SELECTORS = [`.kalamu-area[contenteditable="true"]`, `[contenteditable="true"]`]
const TITLE_SELECTORS = [`input._24i7u`, `input[class*="title" i]`]

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

async function fill(article: AgentArticle): Promise<AgentResult> {
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

  let report: FillReport
  if (editor instanceof HTMLTextAreaElement) {
    setFieldValue(editor, article.markdown)
    report = {
      titleFilled,
      bodyLength: countTextChars(editor.value),
      expectedLength: countTextChars(article.markdown),
      method: `markdown`,
      draftSaved: true,
    }
  }
  else {
    editor.focus()
    dispatchPaste(editor, { html: article.html })
    report = {
      titleFilled,
      bodyLength: await waitForStable(() => elementTextChars(editor)),
      expectedLength: article.textLength,
      method: `rich-text`,
      draftSaved: true,
    }
  }

  if (report.bodyLength === 0 && report.expectedLength > 0)
    return fail(`fill-failed`, `Jianshu editor stayed empty`, report)
  return done(report)
}

export const jianshu: PlatformFiller = {
  steps: { prepare, fill },
}
