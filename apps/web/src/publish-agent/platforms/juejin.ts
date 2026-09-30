// CodeMirror flow follows doocs/cose (Apache-2.0).
import type { AgentArticle, AgentResult } from '../protocol'
import type { PlatformFiller } from '../result'
import { countTextChars, pageMentions, queryFirst, setFieldValue, sleep, waitFor } from '../dom'
import { done, editorMissing, fail } from '../result'
import { getCodeMirror } from './codemirror'

const TITLE_SELECTORS = [`input.title-input`, `input[placeholder*="标题"]`]
const LOGIN_PHRASES = [`登录掘金`, `手机登录`, `密码登录`]

async function fill(article: AgentArticle): Promise<AgentResult> {
  const titleInput = await waitFor(() => queryFirst<HTMLInputElement>(TITLE_SELECTORS), { timeout: 20000 })
  if (!titleInput)
    return pageMentions(LOGIN_PHRASES) ? fail(`login-required`, `Juejin shows its login form`) : editorMissing(`Juejin title field`)

  const cm = await waitFor(() => getCodeMirror(document.querySelector(`.bytemd .CodeMirror`) ?? document.querySelector(`.CodeMirror`)))
  if (!cm)
    return editorMissing(`Juejin Markdown editor (CodeMirror)`)

  await sleep(500)
  const titleFilled = setFieldValue(titleInput, article.title)
  cm.setValue(article.markdown)
  cm.refresh?.()

  const report = {
    titleFilled,
    bodyLength: countTextChars(cm.getValue()),
    expectedLength: countTextChars(article.markdown),
    method: `codemirror`,
    // Juejin autosaves; the URL switches from /drafts/new to /drafts/<id> once it has.
    draftSaved: false,
  }
  if (report.bodyLength === 0 && report.expectedLength > 0)
    return fail(`fill-failed`, `Juejin editor stayed empty`, report)

  report.draftSaved = Boolean(await waitFor(() => !/\/drafts\/new\b/.test(location.pathname), { timeout: 8000 }))
  return done(report)
}

export const juejin: PlatformFiller = {
  steps: { fill },
}
