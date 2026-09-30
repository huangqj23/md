// UEditor flow follows doocs/cose (Apache-2.0): the legacy column editor
// (`newEditor=-1`) exposes UEditor, which accepts styled HTML.
import type { AgentArticle, AgentResult } from '../protocol'
import type { PlatformFiller } from '../result'
import { clickButtonWhenReady, countTextChars, queryFirst, setFieldValue, sleep, waitFor } from '../dom'
import { done, editorMissing, fail } from '../result'
import { findUEditor, isUEditorReady } from './ueditor'

const TITLE_SELECTORS = [`textarea[placeholder*="标题"]`, `input[placeholder*="标题"]`, `textarea`]

/** Logged-out visitors get a blank page, not a redirect; the CSRF cookie only exists after login. */
export function hasBilibiliSession(cookie: string = document.cookie): boolean {
  return /(?:^|;\s*)(?:bili_jct|DedeUserID)=[^;]/.test(cookie)
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  if (!hasBilibiliSession())
    return fail(`login-required`, `No Bilibili session cookie`)

  const editor = await waitFor(() => {
    const instance = findUEditor()
    return instance && isUEditorReady(instance) ? instance : null
  }, { timeout: 25000 })
  if (!editor)
    return editorMissing(`Bilibili column editor (UEditor)`)

  await sleep(500)
  const titleInput = queryFirst<HTMLTextAreaElement | HTMLInputElement>(TITLE_SELECTORS)
  const titleFilled = titleInput ? setFieldValue(titleInput, article.title) : false

  editor.setContent(``)
  editor.execCommand(`inserthtml`, article.wechatHtml || article.html)
  editor.fireEvent?.(`contentchange`)
  await sleep(500)

  const report = {
    titleFilled,
    bodyLength: countTextChars(editor.getContentTxt?.()),
    expectedLength: article.textLength,
    method: `ueditor`,
    draftSaved: false,
  }
  if (report.bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `Bilibili editor stayed empty`, report)

  report.draftSaved = await clickButtonWhenReady(label => label.includes(`存草稿`), { timeout: 4000 })
  if (report.draftSaved)
    await sleep(1500)
  return done(report)
}

export const bilibili: PlatformFiller = {
  steps: { fill },
}
