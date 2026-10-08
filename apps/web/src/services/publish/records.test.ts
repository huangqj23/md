import type { PublishRecord, SyncStart } from './records'
import type { PlatformRun } from './runner'
import { describe, expect, it } from 'vitest'
import {
  applyRun,
  beginSync,
  contentHash,
  HISTORY_LIMIT,
  markPublished,
  normalizePublishedUrl,
  unmarkPublished,
  versionNumber,
} from './records'

const start: SyncStart = {
  postId: `post-1`,
  title: `Llama 3`,
  summary: `s`,
  customTitle: false,
  customSummary: false,
  version: `v-a`,
  at: 1000,
}

function run(overrides: Partial<PlatformRun> & Pick<PlatformRun, `id` | `status`>): PlatformRun {
  return { warnings: [], ...overrides }
}

const filled: PlatformRun = run({
  id: `csdn`,
  status: `success`,
  tabId: 42,
  report: { titleFilled: true, bodyLength: 10, expectedLength: 10, method: `codemirror`, draftSaved: true },
})

describe(`contentHash`, () => {
  it(`is stable and notices any change`, () => {
    expect(contentHash(`# Title\n\nbody`)).toBe(contentHash(`# Title\n\nbody`))
    expect(contentHash(`# Title\n\nbody`)).not.toBe(contentHash(`# Title\n\nbody.`))
    expect(contentHash(``)).toMatch(/^[0-9a-z]+$/)
  })
})

describe(`beginSync`, () => {
  it(`creates a record and numbers each new content version once`, () => {
    const first = beginSync(undefined, start)
    expect(first.versions).toEqual([`v-a`])
    const again = beginSync(first, { ...start, at: 2000 })
    expect(again.versions).toEqual([`v-a`])
    const changed = beginSync(again, { ...start, version: `v-b`, title: `Llama 3 (edited)`, customTitle: true, at: 3000 })
    expect(changed.versions).toEqual([`v-a`, `v-b`])
    expect(changed).toMatchObject({ title: `Llama 3 (edited)`, customTitle: true, updatedAt: 3000 })
    expect(versionNumber(changed, `v-b`)).toBe(2)
    expect(versionNumber(changed, `unknown`)).toBeNull()
  })

  it(`keeps the platforms recorded by earlier syncs`, () => {
    const record = applyRun(beginSync(undefined, start), filled, { version: `v-a`, at: 1100, unsupportedImages: 0 })
    expect(beginSync(record, { ...start, version: `v-b` }).platforms.csdn?.draft).toEqual({ version: `v-a`, at: 1100 })
  })
})

describe(`applyRun`, () => {
  const base = beginSync(undefined, start)

  it(`records a filled draft with its version, tab and report`, () => {
    const record = applyRun(base, filled, { version: `v-a`, at: 1100, unsupportedImages: 2 })
    expect(record.platforms.csdn).toMatchObject({
      status: `success`,
      version: `v-a`,
      tabId: 42,
      unsupportedImages: 2,
      draft: { version: `v-a`, at: 1100 },
      history: [{ at: 1100, kind: `synced`, version: `v-a` }],
    })
    expect(record.updatedAt).toBe(1100)
  })

  it(`keeps the previous draft when a later attempt fails`, () => {
    const synced = applyRun(base, filled, { version: `v-a`, at: 1100, unsupportedImages: 0 })
    const failed = applyRun(synced, run({ id: `csdn`, status: `failed`, errorCode: `fill-failed`, detail: `empty` }), { version: `v-b`, at: 1200, unsupportedImages: 0 })
    expect(failed.platforms.csdn).toMatchObject({ status: `failed`, errorCode: `fill-failed`, draft: { version: `v-a` } })
    expect(failed.platforms.csdn?.history.map(event => event.kind)).toEqual([`failed`, `synced`])
    expect(failed.platforms.csdn?.history[0].errorCode).toBe(`fill-failed`)
  })

  it(`keeps the parts of a split article, and their draft even if one part failed`, () => {
    const split = run({
      id: `zhihu`,
      status: `failed`,
      errorCode: `fill-failed`,
      parts: [
        { title: `T（上）`, status: `success`, warnings: [], tabId: 1 },
        { title: `T（下）`, status: `failed`, warnings: [], errorCode: `fill-failed` },
      ],
    })
    const record = applyRun(base, split, { version: `v-a`, at: 1100, unsupportedImages: 0 })
    expect(record.platforms.zhihu?.parts?.map(part => part.status)).toEqual([`success`, `failed`])
    expect(record.platforms.zhihu?.draft).toEqual({ version: `v-a`, at: 1100 })
  })

  it(`ignores runs that never touched the platform`, () => {
    expect(applyRun(base, run({ id: `zhihu`, status: `cancelled` }), { version: `v-a`, at: 1100, unsupportedImages: 0 })).toBe(base)
    expect(applyRun(base, run({ id: `zhihu`, status: `filling` }), { version: `v-a`, at: 1100, unsupportedImages: 0 })).toBe(base)
  })

  it(`caps the history`, () => {
    let record = base
    for (let i = 0; i < HISTORY_LIMIT + 5; i++)
      record = applyRun(record, filled, { version: `v-a`, at: 2000 + i, unsupportedImages: 0 })
    expect(record.platforms.csdn?.history).toHaveLength(HISTORY_LIMIT)
    expect(record.platforms.csdn?.history[0].at).toBe(2000 + HISTORY_LIMIT + 4)
  })

  it(`keeps a published mark across later syncs`, () => {
    const published = markPublished(applyRun(base, filled, { version: `v-a`, at: 1100, unsupportedImages: 0 }), `csdn`, 1200)
    const resynced = applyRun(published, filled, { version: `v-b`, at: 1300, unsupportedImages: 0 })
    expect(resynced.platforms.csdn).toMatchObject({ published: { version: `v-a`, at: 1200 }, draft: { version: `v-b` } })
  })
})

describe(`markPublished / unmarkPublished`, () => {
  const synced: PublishRecord = applyRun(beginSync(undefined, start), filled, { version: `v-a`, at: 1100, unsupportedImages: 0 })

  it(`marks the version of the last draft, with an optional link`, () => {
    const record = markPublished(synced, `csdn`, 1500, `https://blog.csdn.net/u/article/details/1`)
    expect(record.platforms.csdn?.published).toEqual({ version: `v-a`, at: 1500, url: `https://blog.csdn.net/u/article/details/1` })
    expect(record.platforms.csdn?.history[0]).toEqual({ at: 1500, kind: `published`, version: `v-a` })
    expect(markPublished(synced, `csdn`, 1500).platforms.csdn?.published).toEqual({ version: `v-a`, at: 1500 })
  })

  it(`does nothing for a platform without a draft`, () => {
    expect(markPublished(synced, `zhihu`, 1500)).toBe(synced)
  })

  it(`can be undone`, () => {
    const record = unmarkPublished(markPublished(synced, `csdn`, 1500), `csdn`, 1600)
    expect(record.platforms.csdn?.published).toBeUndefined()
    expect(record.platforms.csdn?.history[0]).toEqual({ at: 1600, kind: `unpublished` })
    expect(unmarkPublished(synced, `csdn`, 1600)).toBe(synced)
  })
})

describe(`normalizePublishedUrl`, () => {
  it(`accepts http(s) links only`, () => {
    expect(normalizePublishedUrl(`  https://juejin.cn/post/1 `)).toBe(`https://juejin.cn/post/1`)
    expect(normalizePublishedUrl(`javascript:alert(1)`)).toBeNull()
    expect(normalizePublishedUrl(`juejin.cn/post/1`)).toBeNull()
    expect(normalizePublishedUrl(``)).toBeNull()
  })
})
