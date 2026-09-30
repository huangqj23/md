// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import {
  clickButtonWhenReady,
  countTextChars,
  describePage,
  dispatchPaste,
  findButton,
  pageMentions,
  queryFirst,
  replaceEditableText,
  setFieldValue,
  waitFor,
  waitForStable,
} from './dom'

beforeAll(() => {
  // jsdom lacks the clipboard classes; the helpers only need their data surface.
  if (typeof globalThis.DataTransfer === `undefined`) {
    class DataTransferStub {
      private store = new Map<string, string>()
      setData(type: string, value: string) { this.store.set(type, value) }
      getData(type: string) { return this.store.get(type) ?? `` }
      get types() { return [...this.store.keys()] }
    }
    ;(globalThis as Record<string, unknown>).DataTransfer = DataTransferStub
  }
  if (typeof globalThis.ClipboardEvent === `undefined`) {
    class ClipboardEventStub extends Event {
      clipboardData: DataTransfer | null
      constructor(type: string, init: ClipboardEventInit = {}) {
        super(type, init)
        this.clipboardData = init.clipboardData ?? null
      }
    }
    ;(globalThis as Record<string, unknown>).ClipboardEvent = ClipboardEventStub
  }
})

afterEach(() => {
  document.body.innerHTML = ``
  vi.useRealTimers()
})

describe(`countTextChars`, () => {
  it(`ignores all whitespace`, () => {
    expect(countTextChars(` a b\n\tc  `)).toBe(3)
    expect(countTextChars(null)).toBe(0)
  })
})

describe(`waitFor`, () => {
  it(`resolves with the first truthy probe value`, async () => {
    let calls = 0
    const value = await waitFor(() => (++calls >= 3 ? `ready` : null), { timeout: 1000, interval: 1 })
    expect(value).toBe(`ready`)
    expect(calls).toBe(3)
  })

  it(`resolves null after the timeout`, async () => {
    expect(await waitFor(() => false, { timeout: 20, interval: 5 })).toBeNull()
  })
})

describe(`queryFirst`, () => {
  it(`tries selectors in order`, () => {
    document.body.innerHTML = `<input class="b"><textarea class="a"></textarea>`
    expect(queryFirst([`.a`, `.b`])?.tagName).toBe(`TEXTAREA`)
    expect(queryFirst([`.missing`, `.b`])?.tagName).toBe(`INPUT`)
    expect(queryFirst([`.missing`])).toBeNull()
  })
})

describe(`setFieldValue`, () => {
  it(`writes through the prototype setter so React-style value trackers see a change`, () => {
    document.body.innerHTML = `<textarea></textarea>`
    const textarea = document.querySelector(`textarea`)!
    // React shadows `value` on the instance to remember the last value it rendered.
    let trackedValue = ``
    const proto = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, `value`)!
    Object.defineProperty(textarea, `value`, {
      configurable: true,
      get() { return proto.get!.call(this) },
      set(next: string) {
        trackedValue = next
        proto.set!.call(this, next)
      },
    })
    const inputs: string[] = []
    textarea.addEventListener(`input`, () => inputs.push(textarea.value))

    expect(setFieldValue(textarea, `Hello`)).toBe(true)
    expect(textarea.value).toBe(`Hello`)
    expect(trackedValue).toBe(``)
    expect(inputs).toEqual([`Hello`])
  })

  it(`works for inputs and fires change`, () => {
    document.body.innerHTML = `<input>`
    const input = document.querySelector(`input`)!
    const onChange = vi.fn()
    input.addEventListener(`change`, onChange)
    setFieldValue(input, `Title`)
    expect(input.value).toBe(`Title`)
    expect(onChange).toHaveBeenCalledOnce()
  })
})

describe(`replaceEditableText`, () => {
  it(`falls back to writing text when execCommand is unavailable`, () => {
    document.body.innerHTML = `<div contenteditable="true"><p>old</p></div>`
    const editable = document.querySelector<HTMLElement>(`[contenteditable]`)!
    const onInput = vi.fn()
    editable.addEventListener(`input`, onInput)
    expect(replaceEditableText(editable, `new title`)).toBe(true)
    expect(editable.textContent).toBe(`new title`)
    expect(onInput).toHaveBeenCalled()
  })
})

describe(`dispatchPaste`, () => {
  it(`delivers html and derived plain text through clipboardData`, () => {
    document.body.innerHTML = `<div contenteditable="true"></div>`
    const editor = document.querySelector(`div`)!
    let seen: { html: string, text: string } | null = null
    editor.addEventListener(`paste`, (event) => {
      const data = (event as ClipboardEvent).clipboardData!
      seen = { html: data.getData(`text/html`), text: data.getData(`text/plain`) }
      event.preventDefault()
    })
    expect(dispatchPaste(editor, { html: `<p>Hi <b>there</b></p>` })).toBe(true)
    expect(seen).toEqual({ html: `<p>Hi <b>there</b></p>`, text: `Hi there` })
  })

  it(`reports false when nobody handles the paste`, () => {
    document.body.innerHTML = `<div></div>`
    expect(dispatchPaste(document.querySelector(`div`)!, { text: `plain` })).toBe(false)
  })
})

describe(`findButton`, () => {
  it(`matches exact labels ignoring whitespace and skips disabled buttons`, () => {
    document.body.innerHTML = `
      <button disabled>确认</button>
      <button id="parse"> 确认并解析 </button>
      <button id="ok">确 认</button>`
    expect(findButton(`确认`)?.id).toBe(`ok`)
    expect(findButton(label => label.includes(`解析`))?.id).toBe(`parse`)
    expect(findButton(`发布`)).toBeNull()
  })

  it(`clickButtonWhenReady waits for the button to appear`, async () => {
    const onClick = vi.fn()
    setTimeout(() => {
      document.body.innerHTML = `<button>保存为草稿</button>`
      document.querySelector(`button`)!.addEventListener(`click`, onClick)
    }, 10)
    expect(await clickButtonWhenReady(`保存为草稿`, { timeout: 500, interval: 5 })).toBe(true)
    expect(onClick).toHaveBeenCalledOnce()
  })
})

describe(`pageMentions`, () => {
  it(`reads only visible text when the browser provides innerText`, () => {
    document.body.innerHTML = `<p>写文章</p><div style="display:none">请使用微信扫描二维码</div>`
    // jsdom has no layout, so stand in for innerText the way a browser computes it.
    Object.defineProperty(document.body, `innerText`, { configurable: true, get: () => `写文章` })
    expect(pageMentions([`请使用微信扫描`])).toBe(false)
    expect(pageMentions([`写文章`])).toBe(true)
    Reflect.deleteProperty(document.body, `innerText`)
  })
})

describe(`describePage`, () => {
  it(`summarises the page without the query string`, () => {
    window.history.replaceState({}, ``, `/write?token=secret`)
    document.title = `编辑`
    document.body.innerHTML = `<div class="ProseMirror" contenteditable="true"></div><textarea></textarea><iframe></iframe>`
    expect(describePage()).toBe(`/write "编辑" editable=1 prosemirror=1 codemirror=0 iframe=1 textarea=1`)
    window.history.replaceState({}, ``, `/`)
  })
})

describe(`waitForStable`, () => {
  it(`returns once the measurement stops changing`, async () => {
    let value = 0
    const timer = setInterval(() => {
      if (value < 3)
        value++
    }, 5)
    const result = await waitForStable(() => value, { timeout: 2000, interval: 5, quietPeriod: 40 })
    clearInterval(timer)
    expect(result).toBe(3)
  })
})
