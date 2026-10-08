import type { Check } from './center'
import type { PlatformRecord, PublishRecord } from './records'
import type { PlatformRun } from './runner'
import { describe, expect, it } from 'vitest'
import { buildPlatformViews, matchesFilter, outdatedViews, recordChecks } from './center'
import { getPublishPlatform, PUBLISH_PLATFORMS } from './platforms'
import { applyRun, beginSync, markPublished } from './records'

const report = { titleFilled: true, bodyLength: 98, expectedLength: 100, method: `test`, draftSaved: false }

function platformRecord(overrides: Partial<PlatformRecord> = {}): PlatformRecord {
  return {
    status: `success`,
    at: 1,
    version: `v-a`,
    report,
    warnings: [],
    unsupportedImages: 0,
    draft: { version: `v-a`, at: 1 },
    history: [],
    ...overrides,
  }
}

function sampleRecord(): PublishRecord {
  let record = beginSync(undefined, { postId: `p`, title: `t`, summary: ``, customTitle: false, customSummary: false, version: `v-a`, at: 1 })
  const ok = (id: PlatformRun[`id`], status: PlatformRun[`status`] = `success`, extra: Partial<PlatformRun> = {}): PlatformRun =>
    ({ id, status, warnings: [], report: { ...report, draftSaved: true }, ...extra })
  record = applyRun(record, ok(`wechat`), { version: `v-a`, at: 2, unsupportedImages: 0 })
  record = applyRun(record, ok(`csdn`), { version: `v-a`, at: 2, unsupportedImages: 0 })
  record = applyRun(record, ok(`zhihu`, `failed`, { report: undefined, errorCode: `fill-failed` }), { version: `v-a`, at: 2, unsupportedImages: 0 })
  record = applyRun(record, ok(`toutiao`, `warning`, { warnings: [`title-truncated`] }), { version: `v-a`, at: 2, unsupportedImages: 0 })
  record = beginSync(record, { postId: `p`, title: `t`, summary: ``, customTitle: false, customSummary: false, version: `v-b`, at: 3 })
  record = applyRun(record, ok(`juejin`), { version: `v-b`, at: 4, unsupportedImages: 0 })
  record = markPublished(record, `juejin`, 5)
  record = markPublished(record, `csdn`, 5)
  return record
}

describe(`recordChecks`, () => {
  const toutiao = getPublishPlatform(`toutiao`)
  const cnblogs = getPublishPlatform(`cnblogs`)

  it(`reports a truncated title with the platform limit`, () => {
    const checks = recordChecks(toutiao, platformRecord({ warnings: [`title-truncated`] }))
    expect(checks[0]).toEqual({ level: `warn`, key: `titleTruncated`, params: { limit: 30 } })
  })

  it(`reports how much of the body went in`, () => {
    expect(recordChecks(toutiao, platformRecord())[1]).toEqual({ level: `ok`, key: `bodyFilled`, params: { percent: 98 } })
    const partial = recordChecks(toutiao, platformRecord({ warnings: [`body-partial`], report: { ...report, bodyLength: 40 } }))
    expect(partial[1]).toEqual({ level: `warn`, key: `bodyPartial`, params: { percent: 40 } })
  })

  it(`prefers the platform's own image upload result over the article audit`, () => {
    const uploaded = recordChecks(getPublishPlatform(`wechat`), platformRecord({ unsupportedImages: 5, report: { ...report, images: { total: 5, failed: 0 } } }))
    expect(uploaded).toContainEqual({ level: `ok`, key: `imagesUploaded`, params: { total: 5 } })
    const unsupported = recordChecks(cnblogs, platformRecord({ unsupportedImages: 5 }))
    expect(unsupported).toContainEqual({ level: `warn`, key: `imagesUnsupported`, params: { count: 5 } })
  })

  it(`tells a saved draft from an autosaving platform from one that needs saving by hand`, () => {
    const last = (checks: Check[]) => checks[checks.length - 1]
    expect(last(recordChecks(cnblogs, platformRecord({ report: { ...report, draftSaved: true } }))).key).toBe(`draftSaved`)
    expect(last(recordChecks(toutiao, platformRecord())).key).toBe(`autosave`)
    expect(last(recordChecks(cnblogs, platformRecord()))).toEqual({ level: `warn`, key: `draftNotSaved` })
  })

  it(`reduces a failure to its error`, () => {
    expect(recordChecks(toutiao, platformRecord({ status: `failed`, errorCode: `fill-failed`, report: undefined })))
      .toEqual([{ level: `bad`, key: `error`, params: { code: `fill-failed` } }])
  })
})

describe(`buildPlatformViews`, () => {
  const record = sampleRecord()
  const byId = (views: ReturnType<typeof buildPlatformViews>) => Object.fromEntries(views.map(view => [view.platform.id, view]))

  it(`derives each platform's state`, () => {
    const views = byId(buildPlatformViews(PUBLISH_PLATFORMS, record, `v-b`))
    expect(views.wechat.state).toBe(`draft`)
    expect(views.zhihu.state).toBe(`failed`)
    expect(views.toutiao.state).toBe(`attention`)
    expect(views.juejin.state).toBe(`published`)
    expect(views.bilibili.state).toBe(`idle`)
  })

  it(`flags what the platforms hold when the article changed since`, () => {
    const views = byId(buildPlatformViews(PUBLISH_PLATFORMS, record, `v-b`))
    expect(views.wechat).toMatchObject({ outdated: true, heldVersion: 1 })
    expect(views.csdn).toMatchObject({ state: `published`, outdated: true, heldVersion: 1 })
    expect(views.juejin).toMatchObject({ outdated: false, heldVersion: 2 })
    expect(views.zhihu.outdated).toBe(false)

    const { drafts, published } = outdatedViews(Object.values(views))
    expect(drafts.map(view => view.platform.id)).toEqual([`wechat`, `toutiao`])
    expect(published.map(view => view.platform.id)).toEqual([`csdn`])
  })

  it(`treats unsynced edits as a version no platform holds`, () => {
    const views = buildPlatformViews(PUBLISH_PLATFORMS, record, `edited`)
    expect(views.filter(view => view.outdated).map(view => view.platform.id)).toEqual([`wechat`, `csdn`, `juejin`, `toutiao`])
  })

  it(`shows a sync in progress over the stored result`, () => {
    const live: PlatformRun[] = [{ id: `zhihu`, status: `filling`, warnings: [] }, { id: `csdn`, status: `queued`, warnings: [] }, { id: `wechat`, status: `cancelled`, warnings: [] }]
    const views = byId(buildPlatformViews(PUBLISH_PLATFORMS, record, `v-b`, live))
    expect(views.zhihu.state).toBe(`syncing`)
    expect(views.csdn.state).toBe(`queued`)
    expect(views.wechat.state).toBe(`draft`)
  })

  it(`shows every platform as not synced without a record`, () => {
    expect(buildPlatformViews(PUBLISH_PLATFORMS, undefined, `v-a`).every(view => view.state === `idle` && !view.outdated)).toBe(true)
  })
})

describe(`matchesFilter`, () => {
  const views = buildPlatformViews(PUBLISH_PLATFORMS, sampleRecord(), `v-b`)
  const ids = (filter: Parameters<typeof matchesFilter>[1]) => views.filter(view => matchesFilter(view, filter)).map(view => view.platform.id)

  it(`groups platforms by what the user has to do`, () => {
    expect(ids(`all`)).toHaveLength(PUBLISH_PLATFORMS.length)
    expect(ids(`todo`)).toEqual([`wechat`, `zhihu`, `csdn`, `toutiao`])
    expect(ids(`pending`)).toEqual([`wechat`, `toutiao`])
    expect(ids(`published`)).toEqual([`csdn`, `juejin`])
    expect(ids(`idle`)).toEqual([`baijiahao`, `jianshu`, `bilibili`, `cnblogs`])
  })
})
