import type { MenuExtension } from './context-menu'
import { describe, expect, it, vi } from 'vitest'
import { AI_DAILY_MENU_ID, handleAiDailyMenuClick, inboxEntryFor, isNativePermissionChange, syncAiDailyMenu } from './context-menu'
import { AI_DAILY_HOST } from './native'

function ext(options: { granted?: boolean, reply?: unknown, fail?: boolean } = {}): MenuExtension {
  const { granted = true, reply = { ok: true, added: true, count: 1 }, fail = false } = options
  return {
    runtime: {
      id: `abc`,
      sendNativeMessage: vi.fn(async () => {
        if (fail)
          throw new Error(`Specified native messaging host not found.`)
        return reply
      }),
      getManifest: () => ({ optional_permissions: [`nativeMessaging`] }),
    },
    permissions: { contains: vi.fn(async () => granted), request: vi.fn(async () => granted) },
    contextMenus: { create: vi.fn(), remove: vi.fn(async () => {}) },
    action: { setBadgeText: vi.fn(async () => {}), setBadgeBackgroundColor: vi.fn(async () => {}) },
  }
}

describe(`inboxEntryFor`, () => {
  it(`prefers the link under the cursor and uses the selection as the note`, () => {
    expect(inboxEntryFor({ menuItemId: AI_DAILY_MENU_ID, linkUrl: `https://x.com/a/status/1`, pageUrl: `https://news.ycombinator.com/`, selectionText: `  new model  ` }, { title: `HN` }))
      .toEqual({ url: `https://x.com/a/status/1`, note: `new model` })
  })

  it(`falls back to the page, with the tab title as the note`, () => {
    expect(inboxEntryFor({ menuItemId: AI_DAILY_MENU_ID, pageUrl: `https://openai.com/index/x` }, { title: `Introducing X` }))
      .toEqual({ url: `https://openai.com/index/x`, note: `Introducing X` })
  })

  it(`ignores non-http pages`, () => {
    expect(inboxEntryFor({ menuItemId: AI_DAILY_MENU_ID, pageUrl: `chrome://extensions/` })).toBeNull()
  })
})

describe(`syncAiDailyMenu`, () => {
  it(`creates the menu only when nativeMessaging is granted`, async () => {
    const granted = ext()
    await syncAiDailyMenu(granted, `投喂到 AI 早报`)
    expect(granted.contextMenus!.create).toHaveBeenCalledWith({ id: AI_DAILY_MENU_ID, title: `投喂到 AI 早报`, contexts: [`page`, `link`, `selection`] })

    const denied = ext({ granted: false })
    await syncAiDailyMenu(denied, `x`)
    expect(denied.contextMenus!.remove).toHaveBeenCalledWith(AI_DAILY_MENU_ID)
    expect(denied.contextMenus!.create).not.toHaveBeenCalled()
  })
})

describe(`handleAiDailyMenuClick`, () => {
  it(`adds to the inbox and flashes a ✓ badge on the tab`, async () => {
    vi.useFakeTimers()
    const e = ext()
    const ok = await handleAiDailyMenuClick(e, { menuItemId: AI_DAILY_MENU_ID, pageUrl: `https://openai.com/x` }, { id: 7, title: `T` }, 100)
    expect(ok).toBe(true)
    expect(e.runtime!.sendNativeMessage).toHaveBeenCalledWith(AI_DAILY_HOST, { cmd: `inbox_add`, url: `https://openai.com/x`, note: `T` })
    expect(e.action!.setBadgeText).toHaveBeenCalledWith({ text: `✓`, tabId: 7 })
    vi.advanceTimersByTime(100)
    expect(e.action!.setBadgeText).toHaveBeenLastCalledWith({ text: ``, tabId: 7 })
    vi.useRealTimers()
  })

  it(`shows ! when the host is missing, and ignores other menu items`, async () => {
    const e = ext({ fail: true })
    expect(await handleAiDailyMenuClick(e, { menuItemId: AI_DAILY_MENU_ID, pageUrl: `https://a.io/` }, { id: 1 }, 0)).toBe(false)
    expect(e.action!.setBadgeText).toHaveBeenCalledWith({ text: `!`, tabId: 1 })
    expect(await handleAiDailyMenuClick(e, { menuItemId: `openSidePanel` }, { id: 1 })).toBe(false)
  })

  it(`recognises nativeMessaging permission changes`, () => {
    expect(isNativePermissionChange({ permissions: [`nativeMessaging`] })).toBe(true)
    expect(isNativePermissionChange({ permissions: [`cookies`] })).toBe(false)
    expect(isNativePermissionChange({})).toBe(false)
  })
})
