// Editor detection follows doocs/cose and leaperone/MultiPost-Extension (both Apache-2.0).
// Cnblogs renders whichever editor the account has chosen in its settings.
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import { countTextChars, queryFirst, setFieldValue, sleep, waitFor } from '../dom'
import { done, editorMissing, fail } from '../result'
import { getCodeMirror } from './codemirror'

interface TinyMceEditor {
  setContent: (html: string) => void
  getContent: (options?: { format?: string }) => string
}

const TITLE_SELECTORS = [`#post-title`, `input[placeholder*="标题"]`]

function tinymceEditor(): TinyMceEditor | null {
  const editor = (window as Window & { tinymce?: { activeEditor?: TinyMceEditor | null } }).tinymce?.activeEditor
  return editor && typeof editor.setContent === `function` ? editor : null
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const titleInput = await waitFor(() => queryFirst<HTMLInputElement>(TITLE_SELECTORS), { timeout: 20000 })
  if (!titleInput)
    return editorMissing(`Cnblogs title field`)

  const body = await waitFor(
    () => document.querySelector<HTMLTextAreaElement>(`textarea#md-editor`)
      ?? getCodeMirror(document.querySelector(`.CodeMirror`))
      ?? tinymceEditor(),
    { timeout: 20000 },
  )
  if (!body)
    return editorMissing(`Cnblogs body editor`)

  await sleep(500)
  const titleFilled = setFieldValue(titleInput, article.title)
  const summary = document.querySelector<HTMLTextAreaElement>(`textarea#summary`)
  if (summary && article.summary)
    setFieldValue(summary, article.summary)

  let report: FillReport
  if (body instanceof HTMLTextAreaElement) {
    setFieldValue(body, article.markdown)
    report = { titleFilled, bodyLength: countTextChars(body.value), expectedLength: countTextChars(article.markdown), method: `markdown`, draftSaved: false }
  }
  else if (`getValue` in body) {
    body.setValue(article.markdown)
    report = { titleFilled, bodyLength: countTextChars(body.getValue()), expectedLength: countTextChars(article.markdown), method: `codemirror`, draftSaved: false }
  }
  else {
    body.setContent(article.html)
    report = { titleFilled, bodyLength: countTextChars(body.getContent({ format: `text` })), expectedLength: article.textLength, method: `tinymce`, draftSaved: false }
  }

  if (report.bodyLength === 0 && report.expectedLength > 0)
    return fail(`fill-failed`, `Cnblogs editor stayed empty`, report)
  return done(report)
}

export const cnblogs: PlatformFiller = {
  steps: { fill },
}
