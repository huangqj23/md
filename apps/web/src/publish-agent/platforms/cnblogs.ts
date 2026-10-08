// Editor detection follows doocs/cose and leaperone/MultiPost-Extension (both Apache-2.0).
// Cnblogs renders whichever editor the account has chosen in its settings.
//
// Formulas only render when the account has 启用数学公式支持 on (`isEnableMathFormula` in
// `/api/preferences`, next to the engine choice). It is a blog-wide preference, not a post option:
// with it off, the Markdown renderer also eats the backslashes inside formulas (`\,` → `,`) and the blog
// loads no formula engine. The agent reads it and reports formulas that will show as source; it does
// not switch it on, since that changes how every post on the blog renders. Read from the i.cnblogs.com
// bundles (2026-10-08).
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import { countTextChars, queryFirst, setFieldValue, sleep, waitFor } from '../dom'
import { FORMULA_TEX_ATTR } from '../protocol'
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

function countFormulas(html: string): number {
  return new DOMParser().parseFromString(`<body>${html}</body>`, `text/html`).body.querySelectorAll(`[${FORMULA_TEX_ATTR}]`).length
}

/** The account's 启用数学公式支持, or null when the preferences could not be read. */
export async function mathFormulasEnabled(): Promise<boolean | null> {
  try {
    const response = await fetch(`/api/preferences`, { credentials: `include`, headers: { accept: `application/json` } })
    if (!response.ok)
      return null
    const enabled = (await response.json() as { isEnableMathFormula?: unknown }).isEnableMathFormula
    return typeof enabled === `boolean` ? enabled : null
  }
  catch {
    return null
  }
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

  const formulas = countFormulas(article.html)
  if (formulas > 0) {
    const enabled = await mathFormulasEnabled()
    if (enabled !== null)
      report.formulas = enabled ? { total: formulas, placed: formulas } : { total: formulas, placed: 0, needsSetting: true }
  }
  return done(report)
}

export const cnblogs: PlatformFiller = {
  steps: { fill },
}
