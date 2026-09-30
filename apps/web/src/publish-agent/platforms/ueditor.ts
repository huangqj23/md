/** Subset of the UEditor API used by Baijiahao and the Bilibili column editor. */
export interface UEditorLike {
  isReady?: boolean | number
  setContent: (html: string, append?: boolean) => void
  execCommand: (command: string, ...args: unknown[]) => unknown
  getContentTxt?: () => string
  fireEvent?: (event: string) => void
}

interface UEditorRegistry {
  instants?: Record<string, UEditorLike | undefined>
}

/** First UEditor instance registered on the page (`UE_V2` on Baijiahao, `UE` elsewhere). */
export function findUEditor(win: Window = window): UEditorLike | null {
  const globals = win as Window & { UE_V2?: UEditorRegistry, UE?: UEditorRegistry }
  for (const registry of [globals.UE_V2, globals.UE]) {
    const instance = registry?.instants?.ueditorInstant0
    if (instance && typeof instance.setContent === `function`)
      return instance
  }
  return null
}

export function isUEditorReady(editor: UEditorLike): boolean {
  return editor.isReady === undefined || Boolean(editor.isReady)
}
