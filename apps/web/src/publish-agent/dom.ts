/**
 * DOM helpers for the publish agent. They run inside third-party editors
 * (React, Vue, Draft.js, ProseMirror, CodeMirror, UEditor), so every write goes
 * through the same channels a user's typing or pasting would use.
 */

export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

export interface WaitOptions {
  timeout?: number
  interval?: number
}

/** Poll `probe` until it returns a truthy value; resolves `null` on timeout. */
export async function waitFor<T>(
  probe: () => T | null | undefined | false,
  { timeout = 15000, interval = 200 }: WaitOptions = {},
): Promise<T | null> {
  const deadline = Date.now() + timeout
  while (true) {
    const value = probe()
    if (value)
      return value
    if (Date.now() >= deadline)
      return null
    await sleep(interval)
  }
}

/** Non-whitespace character count: stable across editors that reflow whitespace. */
export function countTextChars(text: string | null | undefined): number {
  return (text ?? ``).replace(/\s+/g, ``).length
}

export function elementTextChars(el: Element | null | undefined): number {
  return countTextChars(el?.textContent)
}

/** Rendered on screen (layout boxes exist). */
export function isVisible(el: Element): boolean {
  return el.getClientRects().length > 0
}

/** First element matching any selector, preferring visible matches. */
export function queryFirst<T extends Element = HTMLElement>(
  selectors: readonly string[],
  root: ParentNode = document,
): T | null {
  let fallback: T | null = null
  for (const selector of selectors) {
    for (const el of Array.from(root.querySelectorAll<T>(selector))) {
      if (isVisible(el))
        return el
      fallback ??= el
    }
  }
  return fallback
}

function dispatchInput(el: Element, data?: string) {
  const event = typeof InputEvent === `function`
    ? new InputEvent(`input`, { bubbles: true, inputType: `insertText`, data: data ?? null })
    : new Event(`input`, { bubbles: true })
  el.dispatchEvent(event)
}

/**
 * Set an <input>/<textarea> value so React/Vue notice it. React tracks the value
 * on the instance, so assigning `el.value` directly is swallowed; the prototype
 * setter plus an `input` event is what its synthetic onChange listens for.
 */
export function setFieldValue(el: HTMLInputElement | HTMLTextAreaElement, value: string): boolean {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, `value`)?.set
  el.focus()
  if (setter)
    setter.call(el, value)
  else
    el.value = value
  dispatchInput(el, value)
  el.dispatchEvent(new Event(`change`, { bubbles: true }))
  return el.value === value
}

export function selectContents(el: Element) {
  const selection = el.ownerDocument.getSelection()
  if (!selection)
    return
  const range = el.ownerDocument.createRange()
  range.selectNodeContents(el)
  selection.removeAllRanges()
  selection.addRange(range)
}

/** `document.execCommand` that reports failure instead of throwing where it is missing. */
export function execCommand(doc: Document, command: string, value?: string): boolean {
  try {
    return typeof doc.execCommand === `function` && doc.execCommand(command, false, value)
  }
  catch {
    return false
  }
}

/** Empty a contenteditable through the editing pipeline so the editor model follows. */
export function clearEditable(el: HTMLElement) {
  el.focus()
  selectContents(el)
  if (!execCommand(el.ownerDocument, `delete`) || elementTextChars(el) > 0)
    el.innerHTML = ``
}

/**
 * Replace a contenteditable's text. `insertText` goes through beforeinput/input
 * like typing, which Draft.js and ProseMirror both observe; direct DOM writes
 * are only a fallback because those editors may revert them.
 */
export function replaceEditableText(el: HTMLElement, text: string): boolean {
  el.focus()
  selectContents(el)
  const inserted = execCommand(el.ownerDocument, `insertText`, text)
  if (!inserted || (el.textContent ?? ``).trim() !== text.trim()) {
    el.textContent = text
    dispatchInput(el, text)
  }
  return (el.textContent ?? ``).trim() === text.trim()
}

export function htmlToText(html: string): string {
  const doc = new DOMParser().parseFromString(html, `text/html`)
  return doc.body.textContent ?? ``
}

/**
 * Dispatch a synthetic paste. Editors read `event.clipboardData`, so a
 * DataTransfer we fill ourselves is enough; no real clipboard access happens.
 * Returns true when the editor handled it (called preventDefault).
 */
export function dispatchPaste(target: Element, data: { html?: string, text?: string }): boolean {
  const transfer = new DataTransfer()
  if (data.html)
    transfer.setData(`text/html`, data.html)
  transfer.setData(`text/plain`, data.text ?? (data.html ? htmlToText(data.html) : ``))

  const event = new ClipboardEvent(`paste`, { bubbles: true, cancelable: true, clipboardData: transfer })
  // Older engines ignore the `clipboardData` init member.
  if (!event.clipboardData)
    Object.defineProperty(event, `clipboardData`, { value: transfer })
  target.dispatchEvent(event)
  return event.defaultPrevented
}

/** Mouse sequence some editors need before they accept focus and input. */
export function simulateClick(el: Element) {
  const rect = el.getBoundingClientRect()
  const init: MouseEventInit = {
    bubbles: true,
    cancelable: true,
    clientX: rect.left + rect.width / 2,
    clientY: rect.top + Math.min(rect.height / 2, 20),
    button: 0,
  }
  for (const type of [`mousedown`, `mouseup`, `click`])
    el.dispatchEvent(new MouseEvent(type, init))
}

function normalizeLabel(text: string | null | undefined): string {
  return (text ?? ``).replace(/\s+/g, ``)
}

/** Find a button-like element by its label, preferring visible, enabled ones. */
export function findButton(
  match: string | ((label: string) => boolean),
  root: ParentNode = document,
): HTMLElement | null {
  const test = typeof match === `string`
    ? (label: string) => label === normalizeLabel(match)
    : match
  let fallback: HTMLElement | null = null
  for (const el of Array.from(root.querySelectorAll<HTMLElement>(`button, [role="button"], a`))) {
    if (!test(normalizeLabel(el.textContent)))
      continue
    if ((el as HTMLButtonElement).disabled || el.getAttribute(`aria-disabled`) === `true`)
      continue
    if (isVisible(el))
      return el
    fallback ??= el
  }
  return fallback
}

export async function clickButtonWhenReady(
  match: string | ((label: string) => boolean),
  options?: WaitOptions,
): Promise<boolean> {
  const button = await waitFor(() => findButton(match), options)
  if (!button)
    return false
  button.click()
  return true
}

/** Resolve once `measure` stops changing, so async editor rendering can settle. */
export async function waitForStable(
  measure: () => number,
  { timeout = 8000, interval = 300, quietPeriod = 900 }: WaitOptions & { quietPeriod?: number } = {},
): Promise<number> {
  const deadline = Date.now() + timeout
  let last = measure()
  let stableSince = Date.now()
  while (Date.now() < deadline) {
    await sleep(interval)
    const current = measure()
    if (current !== last) {
      last = current
      stableSince = Date.now()
    }
    else if (Date.now() - stableSince >= quietPeriod) {
      break
    }
  }
  return last
}

/**
 * True when the page visibly shows any of the given phrases, e.g. a login prompt.
 * `innerText` skips hidden markup; editors ship hidden dialogs (QR verification
 * and the like) whose text would otherwise read as a login wall.
 */
export function pageMentions(phrases: readonly string[], root: HTMLElement | null = document.body): boolean {
  // eslint-disable-next-line unicorn/prefer-dom-node-text-content -- visible text only, see above
  const text = root?.innerText ?? root?.textContent ?? ``
  return phrases.some(phrase => text.includes(phrase))
}

/** One-line page summary for error details: path (no query, it may hold tokens), title, editor surfaces. */
export function describePage(doc: Document = document): string {
  const count = (selector: string) => doc.querySelectorAll(selector).length
  return `${doc.location?.pathname ?? ``} "${doc.title}" editable=${count(`[contenteditable="true"]`)} prosemirror=${count(`.ProseMirror`)} codemirror=${count(`.CodeMirror`)} iframe=${count(`iframe`)} textarea=${count(`textarea`)}`
}
