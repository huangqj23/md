// @vitest-environment jsdom
import type { ExtensionGlobal } from './extension-api'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AGENT_GLOBAL, AGENT_PROTOCOL_VERSION } from '@/publish-agent/protocol'
import { AGENT_SCRIPT_PATH, createPublishTabsApi, getExtensionGlobal, runAgentInPage } from './extension-api'

afterEach(() => {
  delete (window as unknown as Record<string, unknown>)[AGENT_GLOBAL]
  delete (globalThis as Record<string, unknown>).chrome
  delete (globalThis as Record<string, unknown>).browser
})

const request = {
  version: AGENT_PROTOCOL_VERSION,
  platform: `zhihu` as const,
  step: `fill`,
  article: { title: `t`, summary: ``, markdown: ``, html: ``, wechatHtml: ``, textLength: 0 },
}

describe(`runAgentInPage`, () => {
  it(`is self-contained and looks up the agent under AGENT_GLOBAL`, () => {
    // executeScript ships the function source; it must name the global literally.
    expect(runAgentInPage.toString()).toContain(AGENT_GLOBAL)
  })

  it(`delegates to the injected agent`, async () => {
    const run = vi.fn(async () => ({ kind: `navigate` as const, url: `u`, step: `s` }))
    ;(window as unknown as Record<string, unknown>)[AGENT_GLOBAL] = { version: AGENT_PROTOCOL_VERSION, run }
    await expect(runAgentInPage(request)).resolves.toEqual({ kind: `navigate`, url: `u`, step: `s` })
    expect(run).toHaveBeenCalledWith(request)
  })

  it(`reports a structured error when the agent is missing`, async () => {
    await expect(runAgentInPage(request)).resolves.toMatchObject({ kind: `error`, code: `exception` })
  })
})

describe(`getExtensionGlobal`, () => {
  it(`prefers browser, falls back to chrome, and needs a runtime id`, () => {
    expect(getExtensionGlobal()).toBeNull()
    const chrome = { runtime: { id: `c` } }
    ;(globalThis as Record<string, unknown>).chrome = chrome
    expect(getExtensionGlobal()).toBe(chrome)
    const browser = { runtime: { id: `b` } }
    ;(globalThis as Record<string, unknown>).browser = browser
    expect(getExtensionGlobal()).toBe(browser)
  })
})

describe(`createPublishTabsApi`, () => {
  function fakeExt(currentUrl: string) {
    const tabs = {
      create: vi.fn(async (props: { url: string }) => ({ id: 5, url: props.url })),
      get: vi.fn(async () => ({ id: 5, url: currentUrl, status: `complete` })),
      update: vi.fn(async () => undefined),
      reload: vi.fn(async () => {}),
      group: vi.fn(async () => 11),
    }
    const scripting = { executeScript: vi.fn(async () => [{ result: { kind: `done` } }]) }
    const tabGroups = { update: vi.fn(async () => ({})) }
    return { ext: { tabs, scripting, tabGroups } as unknown as ExtensionGlobal, tabs, scripting, tabGroups }
  }

  it(`reloads after a hash-only navigation so the SPA boots on the new route`, async () => {
    const { ext, tabs } = fakeExt(`https://www.jianshu.com/writer`)
    await createPublishTabsApi(ext).navigate(5, `https://www.jianshu.com/writer#/notebooks/1/notes/2`)
    expect(tabs.update).toHaveBeenCalledWith(5, { url: `https://www.jianshu.com/writer#/notebooks/1/notes/2` })
    expect(tabs.reload).toHaveBeenCalledWith(5)
  })

  it(`does not reload after a normal navigation`, async () => {
    const { ext, tabs } = fakeExt(`https://mp.weixin.qq.com/cgi-bin/home?token=1`)
    await createPublishTabsApi(ext).navigate(5, `https://mp.weixin.qq.com/cgi-bin/appmsg?token=1`)
    expect(tabs.reload).not.toHaveBeenCalled()
  })

  it(`injects the agent bundle and runs it in the MAIN world`, async () => {
    const { ext, scripting } = fakeExt(``)
    const api = createPublishTabsApi(ext)
    await api.injectAgent(5)
    expect(scripting.executeScript).toHaveBeenCalledWith({ target: { tabId: 5 }, files: [AGENT_SCRIPT_PATH], world: `MAIN` })
    await expect(api.runAgent(5, request)).resolves.toEqual({ kind: `done` })
    expect(scripting.executeScript).toHaveBeenLastCalledWith(expect.objectContaining({ func: runAgentInPage, args: [request], world: `MAIN` }))
  })

  it(`opens tabs next to the editor and names the tab group once`, async () => {
    const { ext, tabs, tabGroups } = fakeExt(``)
    const api = createPublishTabsApi(ext)
    expect(await api.openTab(`https://a.io/`, 3)).toBe(5)
    expect(tabs.create).toHaveBeenCalledWith({ url: `https://a.io/`, active: true, openerTabId: 3 })
    expect(await api.groupTabs([5], `md`, null)).toBe(11)
    expect(await api.groupTabs([6], `md`, 11)).toBe(11)
    expect(tabs.group).toHaveBeenLastCalledWith({ tabIds: [6], groupId: 11 })
    expect(tabGroups.update).toHaveBeenCalledOnce()
  })

  it(`degrades gracefully where tab groups are unsupported`, async () => {
    const api = createPublishTabsApi({ tabs: { create: vi.fn(), get: vi.fn(), update: vi.fn(), reload: vi.fn() } })
    expect(await api.groupTabs([1], `md`, null)).toBeNull()
    await expect(api.injectAgent(1)).rejects.toThrow(/scripting API unavailable/)
  })
})
