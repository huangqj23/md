// UEditor flow follows doocs/cose (Apache-2.0).
import type { AgentArticle, AgentResult, FillReport } from '../protocol'
import type { PlatformFiller } from '../result'
import {
  clickButtonWhenReady,
  countTextChars,
  dispatchPaste,
  elementTextChars,
  queryFirst,
  replaceEditableText,
  setFieldValue,
  sleep,
  waitFor,
  waitForStable,
} from '../dom'
import { done, editorMissing, fail } from '../result'
import { createBaijiahaoUploader, isBaiduImage, UPLOAD_WITHIN_STEP, uploadHtmlImages } from './platform-images'
import { findUEditor, isUEditorReady } from './ueditor'

const TITLE_EDITABLE_SELECTORS = [
  `.client_components_titleInput [contenteditable="true"]`,
  `.client_pages_edit_components_titleInput [contenteditable="true"]`,
  `[class*="titleInput"] [contenteditable="true"]`,
]
const TITLE_FIELD_SELECTORS = [`textarea[placeholder*="标题"]`, `input[placeholder*="标题"]`]

function editorFrameBody(): HTMLElement | null {
  for (const frame of Array.from(document.querySelectorAll<HTMLIFrameElement>(`iframe`))) {
    try {
      const body = frame.contentDocument?.body
      if (body?.isContentEditable)
        return body
    }
    catch {
      // Cross-origin frames (ads, login widgets) are not the editor.
    }
  }
  return null
}

const LIST_MARKER = /^\s*(?:\d+\.|•)\s/

/**
 * md writes each list item's marker into its text ("4. ", "• ") and hides the real one with
 * `list-style: none`, which WeChat keeps; Baijiahao drops that style and numbers the list itself,
 * so the text markers would show twice.
 */
export function dropListMarkers(html: string): string {
  const template = document.createElement(`template`)
  template.innerHTML = html
  for (const item of Array.from(template.content.querySelectorAll(`li`))) {
    const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent?.trim())
        continue
      node.textContent = node.textContent.replace(LIST_MARKER, ``)
      break
    }
  }
  return template.innerHTML
}

function fillTitle(title: string): boolean {
  const editable = queryFirst<HTMLElement>(TITLE_EDITABLE_SELECTORS)
  if (editable)
    return replaceEditableText(editable, title)
  const field = queryFirst<HTMLInputElement | HTMLTextAreaElement>(TITLE_FIELD_SELECTORS)
  return field ? setFieldValue(field, title) : false
}

async function fill(article: AgentArticle): Promise<AgentResult> {
  const ready = await waitFor(() => {
    const editor = findUEditor()
    if (editor && isUEditorReady(editor))
      return editor
    return editorFrameBody()
  }, { timeout: 25000 })
  if (!ready)
    return editorMissing(`Baijiahao editor`)

  await sleep(1000)
  const titleFilled = fillTitle(article.title)
  // Every image goes to Baijiahao's material library first: a draft holding embedded (data:) ones
  // is megabytes and cannot be saved, and the editor leaves outside-hosted ones blank.
  const images = await uploadHtmlImages(article.wechatHtml || article.html, createBaijiahaoUploader(), { ...UPLOAD_WITHIN_STEP, isHosted: isBaiduImage })
  const html = dropListMarkers(images.text)

  const editor = findUEditor()
  let method: string
  let bodyLength: number
  if (editor) {
    method = `ueditor`
    editor.setContent(html)
    editor.fireEvent?.(`contentchange`)
    await sleep(500)
    bodyLength = countTextChars(editor.getContentTxt?.() ?? editorFrameBody()?.textContent)
  }
  else {
    method = `paste`
    const body = editorFrameBody()!
    body.focus()
    dispatchPaste(body, { html })
    bodyLength = await waitForStable(() => elementTextChars(editorFrameBody()))
  }

  const report: FillReport = {
    titleFilled,
    bodyLength,
    expectedLength: article.textLength,
    method,
    draftSaved: false,
    ...(images.total ? { images: { total: images.total, failed: images.failed, ...(images.reasons ? { reasons: images.reasons } : {}) } } : {}),
  }
  if (bodyLength === 0 && article.textLength > 0)
    return fail(`fill-failed`, `Baijiahao editor stayed empty`, report)

  report.draftSaved = await clickButtonWhenReady(label => label.includes(`存草稿`), { timeout: 4000 })
  if (report.draftSaved)
    await sleep(1500)
  return done(report)
}

export const baijiahao: PlatformFiller = {
  steps: { fill },
}
