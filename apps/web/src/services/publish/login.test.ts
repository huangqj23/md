import type { DetectContext, LoginInfo, PublishPlatform } from './platforms'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDetectContext, detectLoginStates } from './login'
import { getPublishPlatform } from './platforms'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function withProbe(id: `zhihu` | `juejin` | `csdn`, detectLogin: (ctx: DetectContext) => Promise<LoginInfo>): PublishPlatform {
  return { ...getPublishPlatform(id), detectLogin }
}

describe(`detectLoginStates`, () => {
  it(`collects results and turns failures into unknown`, async () => {
    const seen: string[] = []
    const results = await detectLoginStates([
      withProbe(`zhihu`, async () => ({ state: `logged-in`, name: `z` })),
      withProbe(`juejin`, async () => {
        throw new Error(`network`)
      }),
    ], createDetectContext(null), id => seen.push(id))

    expect(results).toEqual({ zhihu: { state: `logged-in`, name: `z` }, juejin: { state: `unknown` } })
    expect(seen.sort()).toEqual([`juejin`, `zhihu`])
  })

  it(`gives up on a probe that hangs`, async () => {
    vi.useFakeTimers()
    const pending = detectLoginStates([withProbe(`csdn`, () => new Promise(() => {}))], createDetectContext(null))
    await vi.advanceTimersByTimeAsync(10000)
    await expect(pending).resolves.toEqual({ csdn: { state: `unknown` } })
  })
})

describe(`createDetectContext`, () => {
  it(`sends credentialed requests and tolerates non-JSON answers`, async () => {
    const fetchMock = vi.fn(async () => new Response(`<html>login</html>`, { status: 302 }))
    vi.stubGlobal(`fetch`, fetchMock)
    const ctx = createDetectContext(null)

    await expect(ctx.fetchJson(`https://www.zhihu.com/api/v4/me`)).resolves.toEqual({ status: 302, data: null })
    expect(fetchMock).toHaveBeenCalledWith(`https://www.zhihu.com/api/v4/me`, expect.objectContaining({ credentials: `include` }))
    expect(ctx.getCookie).toBeNull()
  })

  it(`reads cookies through the cookies API when available`, async () => {
    const get = vi.fn(async () => ({ value: `v` }))
    const ctx = createDetectContext({ cookies: { get } })
    await expect(ctx.getCookie!(`https://www.csdn.net/`, `UserName`)).resolves.toBe(`v`)
    expect(get).toHaveBeenCalledWith({ url: `https://www.csdn.net/`, name: `UserName` })
  })
})
