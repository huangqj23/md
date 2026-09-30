import type { ExtensionGlobal } from './extension-api'
import { describe, expect, it, vi } from 'vitest'
import { hasPublishPermissions, matchesPattern, originsFor, requestableApiPermissions, requestPublishPermissions } from './permissions'
import { getPublishPlatform } from './platforms'

describe(`matchesPattern`, () => {
  it.each([
    [`https://zhuanlan.zhihu.com/write`, `https://*.zhihu.com/*`, true],
    [`https://zhihu.com/`, `https://*.zhihu.com/*`, true],
    [`https://evilzhihu.com/`, `https://*.zhihu.com/*`, false],
    [`https://zhihu.com.evil.io/`, `https://*.zhihu.com/*`, false],
    [`http://www.zhihu.com/`, `https://*.zhihu.com/*`, false],
    [`https://baijiahao.baidu.com/builder/rc/edit?type=news`, `https://baijiahao.baidu.com/*`, true],
    [`https://passport.baidu.com/`, `https://baijiahao.baidu.com/*`, false],
    [`https://a.io/x?y=1`, `*://a.io/x*`, true],
    [`not a url`, `https://*/*`, false],
    [`https://a.io/`, `bogus`, false],
  ])(`%s ~ %s → %s`, (url, pattern, expected) => {
    expect(matchesPattern(url, pattern)).toBe(expected)
  })
})

describe(`permission helpers`, () => {
  const platforms = [getPublishPlatform(`zhihu`), getPublishPlatform(`juejin`), getPublishPlatform(`zhihu`)]

  function ext(declared: string[], granted = true): ExtensionGlobal {
    return {
      runtime: { id: `x`, getManifest: () => ({ permissions: [`storage`], optional_permissions: declared }) },
      permissions: { contains: vi.fn(async () => granted), request: vi.fn(async () => granted) },
    }
  }

  it(`originsFor de-duplicates host patterns`, () => {
    expect(originsFor(platforms)).toEqual([`https://*.zhihu.com/*`, `https://*.juejin.cn/*`])
  })

  it(`only requests API permissions the manifest declares`, () => {
    expect(requestableApiPermissions(ext([`scripting`, `cookies`, `tabGroups`]))).toEqual([`scripting`, `cookies`, `tabGroups`])
    expect(requestableApiPermissions(ext([`scripting`, `cookies`]))).toEqual([`scripting`, `cookies`])
  })

  it(`asks the browser for hosts and APIs together`, async () => {
    const e = ext([`scripting`, `cookies`])
    expect(await requestPublishPermissions(e, platforms)).toBe(true)
    expect(e.permissions!.request).toHaveBeenCalledWith({ origins: [`https://*.zhihu.com/*`, `https://*.juejin.cn/*`], permissions: [`scripting`, `cookies`] })
    expect(await hasPublishPermissions(e, platforms)).toBe(true)
  })

  it(`treats a missing or failing permissions API as not granted`, async () => {
    expect(await hasPublishPermissions({}, platforms)).toBe(false)
    expect(await requestPublishPermissions({}, platforms)).toBe(false)
    const failing: ExtensionGlobal = { permissions: { contains: async () => { throw new Error(`nope`) }, request: async () => false } }
    expect(await hasPublishPermissions(failing, platforms)).toBe(false)
  })
})
