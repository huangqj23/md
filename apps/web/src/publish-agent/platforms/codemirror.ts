export interface CodeMirrorLike {
  setValue: (value: string) => void
  getValue: () => string
  refresh?: () => void
}

/**
 * CodeMirror 5 stores its instance on the wrapper element (`wrapper.CodeMirror`).
 * That expando only exists in the page's MAIN world, which is why the agent runs there.
 */
export function getCodeMirror(el: Element | null | undefined): CodeMirrorLike | null {
  const candidate = (el as (Element & { CodeMirror?: Partial<CodeMirrorLike> }) | null | undefined)?.CodeMirror
  if (candidate && typeof candidate.setValue === `function` && typeof candidate.getValue === `function`)
    return candidate as CodeMirrorLike
  return null
}
