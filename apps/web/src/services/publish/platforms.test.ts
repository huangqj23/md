import type { DetectContext } from './platforms'
import { describe, expect, it } from 'vitest'
import { PLATFORM_FILLERS } from '@/publish-agent'
import { wechatEditorUrl } from '@/publish-agent/platforms/wechat'
import { PUBLISH_PLATFORM_IDS } from '@/publish-agent/protocol'
import { matchesPattern } from './permissions'
import { getPublishPlatform, PUBLISH_PLATFORMS } from './platforms'

/** Pages the publisher reaches after the start URL (agent navigations). */
const EXTRA_URLS: Record<string, string[]> = {
  wechat: [wechatEditorUrl(`123`)],
  jianshu: [`https://www.jianshu.com/writer#/notebooks/1/notes/2`, `https://www.jianshu.com/author/notes`],
}

describe(`publish platform registry`, () => {
  it(`covers exactly the platforms the agent can fill, in order`, () => {
    expect(PUBLISH_PLATFORMS.map(p => p.id)).toEqual([...PUBLISH_PLATFORM_IDS])
    expect(Object.keys(PLATFORM_FILLERS).sort()).toEqual([...PUBLISH_PLATFORM_IDS].sort())
  })

  it.each(PUBLISH_PLATFORMS.map(p => [p.id, p] as const))(`%s: every URL it touches is inside its host permissions`, (id, platform) => {
    for (const url of [platform.startUrl, platform.homeUrl, platform.loginCheckUrl, ...(EXTRA_URLS[id] ?? [])])
      expect(platform.hostPermissions.some(pattern => matchesPattern(url, pattern)), url).toBe(true)
  })

  it.each(PUBLISH_PLATFORMS.map(p => [p.id, p] as const))(`%s: the start step exists and the start URL is not a login page`, (id, platform) => {
    expect(PLATFORM_FILLERS[id].steps[platform.startStep]).toBeTypeOf(`function`)
    expect(platform.loginUrlPattern.test(platform.startUrl)).toBe(false)
    expect(platform.startUrl.startsWith(`https://`)).toBe(true)
  })

  it(`getPublishPlatform looks platforms up by id`, () => {
    expect(getPublishPlatform(`zhihu`).startUrl).toBe(`https://zhuanlan.zhihu.com/write`)
  })
})

function ctx(overrides: Partial<DetectContext>): DetectContext {
  return {
    fetchJson: async () => ({ status: 500, data: null }),
    fetchText: async () => ({ status: 500, url: ``, text: `` }),
    getCookie: null,
    ...overrides,
  }
}

const json = (status: number, data: unknown): Partial<DetectContext> => ({ fetchJson: async () => ({ status, data }) })

describe(`login probes`, () => {
  it(`zhihu`, async () => {
    const zhihu = getPublishPlatform(`zhihu`)
    expect(await zhihu.detectLogin(ctx(json(200, { id: `u1`, name: `Hollis`, avatar_url: `a.png` })))).toEqual({ state: `logged-in`, name: `Hollis`, avatar: `a.png` })
    expect(await zhihu.detectLogin(ctx(json(401, { error: {} })))).toEqual({ state: `logged-out` })
    expect(await zhihu.detectLogin(ctx(json(500, null)))).toEqual({ state: `unknown` })
  })

  it(`juejin`, async () => {
    const juejin = getPublishPlatform(`juejin`)
    expect((await juejin.detectLogin(ctx(json(200, { err_no: 0, data: { user_id: `1`, user_name: `h` } })))).state).toBe(`logged-in`)
    expect((await juejin.detectLogin(ctx(json(200, { err_no: 403 })))).state).toBe(`logged-out`)
  })

  it(`toutiao, baijiahao and bilibili read their API status codes`, async () => {
    expect((await getPublishPlatform(`toutiao`).detectLogin(ctx(json(200, { code: 0, name: `h` })))).name).toBe(`h`)
    expect((await getPublishPlatform(`toutiao`).detectLogin(ctx(json(200, { code: 1 })))).state).toBe(`logged-out`)
    expect((await getPublishPlatform(`baijiahao`).detectLogin(ctx(json(200, { errno: 0, data: { user: { name: `b` } } })))).name).toBe(`b`)
    expect((await getPublishPlatform(`baijiahao`).detectLogin(ctx(json(200, { errno: 1 })))).state).toBe(`logged-out`)
    expect((await getPublishPlatform(`bilibili`).detectLogin(ctx(json(200, { code: 0, data: { isLogin: true, uname: `u` } })))).name).toBe(`u`)
    expect((await getPublishPlatform(`bilibili`).detectLogin(ctx(json(200, { code: -101, data: { isLogin: false } })))).state).toBe(`logged-out`)
  })

  it(`jianshu and cnblogs`, async () => {
    expect((await getPublishPlatform(`jianshu`).detectLogin(ctx(json(200, { data: { nickname: `j` } })))).name).toBe(`j`)
    expect((await getPublishPlatform(`jianshu`).detectLogin(ctx(json(401, null)))).state).toBe(`logged-out`)
    expect(await getPublishPlatform(`cnblogs`).detectLogin(ctx(json(200, { spaceUserId: 1, displayName: `c`, iconName: `//pic.cnblogs.com/a.png` }))))
      .toEqual({ state: `logged-in`, name: `c`, avatar: `https://pic.cnblogs.com/a.png` })
    expect((await getPublishPlatform(`cnblogs`).detectLogin(ctx(json(200, {})))).state).toBe(`logged-out`)
  })

  it(`csdn relies on session cookies and is unknown without the cookies API`, async () => {
    const csdn = getPublishPlatform(`csdn`)
    expect((await csdn.detectLogin(ctx({}))).state).toBe(`unknown`)
    const cookies: Record<string, string> = { UserName: `hollis23`, UserNick: encodeURIComponent(`霍利斯`) }
    expect(await csdn.detectLogin(ctx({ getCookie: async (_url, name) => cookies[name] ?? null }))).toEqual({ state: `logged-in`, name: `霍利斯`, avatar: undefined })
    expect((await csdn.detectLogin(ctx({ getCookie: async () => null }))).state).toBe(`logged-out`)
  })

  it(`wechat parses the logged-in home page or the QR login page`, async () => {
    const wechat = getPublishPlatform(`wechat`)
    const home = { fetchText: async () => ({ status: 200, url: `https://mp.weixin.qq.com/cgi-bin/home?t=home/index&token=42`, text: `nick_name: "hollis23", head_img: "https://x/y.png"` }) }
    expect(await wechat.detectLogin(ctx(home))).toEqual({ state: `logged-in`, name: `hollis23`, avatar: `https://x/y.png` })
    const login = { fetchText: async () => ({ status: 200, url: `https://mp.weixin.qq.com/`, text: `<div>使用账号登录</div>` }) }
    expect((await wechat.detectLogin(ctx(login))).state).toBe(`logged-out`)
  })
})
