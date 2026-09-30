import type { PublishTabsApi, TabSnapshot } from './extension-api'
import type { PlatformRun, RunnerTiming } from './runner'
import type { AgentArticle, AgentRequest, AgentResult, FillReport } from '@/publish-agent/protocol'
import { describe, expect, it, vi } from 'vitest'
import { wechatEditorUrl } from '@/publish-agent/platforms/wechat'
import { getPublishPlatform } from './platforms'
import { classifyReport, runPublish } from './runner'

const FAST: Partial<RunnerTiming> = { pageLoadTimeout: 300, stepTimeout: 300, settleDelay: 0, pollInterval: 1 }

const article: AgentArticle = {
  title: `论文学习笔记：No Train Yet Gain: Towards Generic Multi-Object Tracking`,
  summary: `s`,
  markdown: `## x`,
  html: `<h2>x</h2>`,
  wechatHtml: ``,
  textLength: 100,
}

const goodReport: FillReport = { titleFilled: true, bodyLength: 100, expectedLength: 100, method: `test`, draftSaved: true }

interface FakeOptions {
  agent?: (request: AgentRequest) => AgentResult | Promise<AgentResult> | undefined
  /** Final URL after load; undefined models a redirect to a host we cannot read. */
  landOn?: (url: string) => string | undefined
  neverLoads?: boolean
  openFails?: (url: string) => boolean
}

function createFakeApi(options: FakeOptions = {}) {
  const tabs = new Map<number, TabSnapshot>()
  const requests: AgentRequest[] = []
  let nextId = 100
  const landOn = options.landOn ?? ((url: string) => url)
  const api: PublishTabsApi = {
    currentTabId: vi.fn(async () => 1),
    openTab: vi.fn(async (url: string) => {
      if (options.openFails?.(url))
        throw new Error(`tabs.create failed`)
      const id = nextId++
      tabs.set(id, { url: landOn(url), status: `loading` })
      return id
    }),
    getTab: vi.fn(async (tabId: number) => {
      const tab = tabs.get(tabId)
      if (!tab)
        return null
      const snapshot = { ...tab }
      // Each read lets the page make progress, like a real load.
      if (tab.status === `loading` && !options.neverLoads)
        tab.status = `complete`
      return snapshot
    }),
    navigate: vi.fn(async (tabId: number, url: string) => {
      tabs.set(tabId, { url: landOn(url), status: `loading` })
    }),
    focusTab: vi.fn(async () => {}),
    injectAgent: vi.fn(async () => {}),
    runAgent: vi.fn(async (_tabId: number, request: AgentRequest): Promise<AgentResult | undefined> => {
      requests.push(request)
      return options.agent ? options.agent(request) : { kind: `done`, report: goodReport }
    }),
    groupTabs: vi.fn(async (_ids: number[], _title: string, groupId: number | null) => groupId ?? 7),
  }
  return { api, requests, tabs }
}

async function run(platformIds: Parameters<typeof getPublishPlatform>[0][], fake: ReturnType<typeof createFakeApi>, extra: { shouldCancel?: () => boolean } = {}) {
  const updates: PlatformRun[] = []
  const runs = await runPublish(platformIds.map(getPublishPlatform), article, {
    api: fake.api,
    onUpdate: update => updates.push(update),
    timing: FAST,
    groupTitle: `md test`,
    ...extra,
  })
  return { runs, updates }
}

describe(`runPublish`, () => {
  it(`fills each platform in its own grouped tab and returns to the editor`, async () => {
    const fake = createFakeApi()
    const { runs, updates } = await run([`zhihu`, `juejin`], fake)

    expect(runs.map(r => r.status)).toEqual([`success`, `success`])
    expect(updates.filter(u => u.id === `zhihu`).map(u => u.status)).toEqual(expect.arrayContaining([`queued`, `opening`, `filling`, `success`]))
    expect(fake.api.openTab).toHaveBeenNthCalledWith(1, `https://zhuanlan.zhihu.com/write`, 1)
    expect(fake.api.groupTabs).toHaveBeenNthCalledWith(1, [100], `md test`, null)
    expect(fake.api.groupTabs).toHaveBeenNthCalledWith(2, [101], `md test`, 7)
    expect(fake.api.injectAgent).toHaveBeenCalledTimes(2)
    expect(fake.api.focusTab).toHaveBeenLastCalledWith(1)
    expect(fake.requests.map(r => [r.platform, r.step])).toEqual([[`zhihu`, `fill`], [`juejin`, `fill`]])
    expect(runs[0].tabId).toBe(100)
  })

  it(`stops at a login redirect without injecting anything`, async () => {
    const fake = createFakeApi({ landOn: () => `https://www.zhihu.com/signin?next=%2Fwrite` })
    const { runs } = await run([`zhihu`], fake)
    expect(runs[0]).toMatchObject({ status: `login-required`, errorCode: `login-required` })
    expect(fake.api.injectAgent).not.toHaveBeenCalled()
  })

  it(`treats a redirect to an unreadable host as a login wall`, async () => {
    const fake = createFakeApi({ landOn: () => undefined })
    const { runs } = await run([`baijiahao`], fake)
    expect(runs[0]).toMatchObject({ status: `login-required`, detail: `redirected to another site` })
  })

  it(`follows agent navigations to the next step`, async () => {
    const fake = createFakeApi({
      agent: request => request.step === `prepare`
        ? { kind: `navigate`, url: wechatEditorUrl(`9`), step: `fill` }
        : { kind: `done`, report: goodReport },
    })
    const { runs } = await run([`wechat`], fake)
    expect(fake.requests.map(r => r.step)).toEqual([`prepare`, `fill`])
    expect(fake.api.navigate).toHaveBeenCalledWith(100, wechatEditorUrl(`9`))
    expect(runs[0].status).toBe(`success`)
  })

  it(`maps agent errors to statuses`, async () => {
    const fake = createFakeApi({
      agent: request => request.platform === `csdn`
        ? { kind: `error`, code: `editor-not-found`, message: `no editor` }
        : { kind: `error`, code: `login-required`, message: `login form` },
    })
    const { runs } = await run([`csdn`, `juejin`], fake)
    expect(runs[0]).toMatchObject({ status: `failed`, errorCode: `editor-not-found`, detail: `no editor` })
    expect(runs[1]).toMatchObject({ status: `login-required`, errorCode: `login-required` })
  })

  it(`times out a hung agent and a page that never loads`, async () => {
    const hung = await run([`zhihu`], createFakeApi({ agent: () => new Promise<AgentResult>(() => {}) }))
    expect(hung.runs[0]).toMatchObject({ status: `failed`, errorCode: `agent-timeout` })

    const stuck = await run([`zhihu`], createFakeApi({ neverLoads: true }))
    expect(stuck.runs[0]).toMatchObject({ status: `failed`, errorCode: `page-timeout` })
  })

  it(`marks an empty agent answer and endless navigation as failures`, async () => {
    const empty = await run([`zhihu`], createFakeApi({ agent: () => undefined }))
    expect(empty.runs[0]).toMatchObject({ status: `failed`, errorCode: `no-result` })

    const loop = await run([`zhihu`], createFakeApi({ agent: () => ({ kind: `navigate`, url: `https://zhuanlan.zhihu.com/write`, step: `fill` }) }))
    expect(loop.runs[0]).toMatchObject({ status: `failed`, errorCode: `too-many-steps` })
  })

  it(`reports login-required when a single-page app redirects to login after load`, async () => {
    const fake = createFakeApi()
    fake.api.runAgent = vi.fn(async (tabId: number) => {
      // Juejin swaps to its login route client-side, after the page reported "complete".
      fake.tabs.set(tabId, { url: `https://juejin.cn/login?to=%2Feditor%2Fdrafts%2Fnew`, status: `complete` })
      return { kind: `error`, code: `editor-not-found`, message: `Juejin title field not found` } as AgentResult
    })
    const { runs } = await run([`juejin`], fake)
    expect(runs[0]).toMatchObject({ status: `login-required`, detail: `https://juejin.cn/login?to=%2Feditor%2Fdrafts%2Fnew` })
  })

  it(`treats a step interrupted by navigation as login-required only when it landed on a login page`, async () => {
    const toLogin = createFakeApi()
    toLogin.api.runAgent = vi.fn(async (tabId: number) => {
      toLogin.tabs.set(tabId, { url: `https://passport.csdn.net/login`, status: `complete` })
      throw new Error(`Frame with ID 0 was removed.`)
    })
    expect((await run([`csdn`], toLogin)).runs[0]).toMatchObject({ status: `login-required` })

    const elsewhere = createFakeApi()
    elsewhere.api.runAgent = vi.fn(async () => {
      throw new Error(`Frame with ID 0 was removed.`)
    })
    expect((await run([`csdn`], elsewhere)).runs[0]).toMatchObject({ status: `failed`, errorCode: `browser-error`, detail: `Frame with ID 0 was removed.` })
  })

  it(`keeps going after a browser error on one platform`, async () => {
    const fake = createFakeApi({ openFails: url => url.includes(`zhihu`) })
    const { runs } = await run([`zhihu`, `juejin`], fake)
    expect(runs[0]).toMatchObject({ status: `failed`, errorCode: `browser-error`, detail: `tabs.create failed` })
    expect(runs[1].status).toBe(`success`)
  })

  it(`cancels platforms that have not started yet`, async () => {
    let started = 0
    const fake = createFakeApi({
      agent: () => {
        started++
        return { kind: `done`, report: goodReport }
      },
    })
    const { runs } = await run([`zhihu`, `juejin`, `csdn`], fake, { shouldCancel: () => started >= 1 })
    expect(runs.map(r => r.status)).toEqual([`success`, `cancelled`, `cancelled`])
  })

  it(`trims titles to the platform limit and flags it`, async () => {
    const fake = createFakeApi()
    const { runs } = await run([`toutiao`], fake)
    expect(Array.from(fake.requests[0].article.title)).toHaveLength(30)
    expect(runs[0]).toMatchObject({ status: `warning`, warnings: [`title-truncated`] })
  })
})

describe(`classifyReport`, () => {
  const autosaving = { autosave: true }

  it(`accepts a complete fill`, () => {
    expect(classifyReport(goodReport, autosaving, false)).toEqual({ status: `success`, warnings: [] })
  })

  it(`flags a partial body, a missing title and an unsaved draft`, () => {
    const report = { ...goodReport, titleFilled: false, bodyLength: 30, draftSaved: false }
    expect(classifyReport(report, { autosave: false }, false)).toEqual({
      status: `warning`,
      warnings: [`title-not-filled`, `body-partial`, `draft-not-saved`],
    })
  })

  it(`flags embedded images that could not be uploaded`, () => {
    const report = { ...goodReport, images: { total: 8, failed: 3 } }
    expect(classifyReport(report, autosaving, false)).toEqual({ status: `warning`, warnings: [`images-not-uploaded`] })
    expect(classifyReport({ ...goodReport, images: { total: 8, failed: 0 } }, autosaving, false).status).toBe(`success`)
  })

  it(`does not ask to save drafts on platforms that autosave`, () => {
    expect(classifyReport({ ...goodReport, draftSaved: false }, autosaving, false).status).toBe(`success`)
  })
})
