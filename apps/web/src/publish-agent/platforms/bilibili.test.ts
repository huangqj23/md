// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withPastableImages } from './bilibili'

afterEach(() => {
  vi.unstubAllGlobals()
})

const HOSTED = `https://obisidian-1302473945.cos.ap-chengdu.myqcloud.com/md/a.png`

describe(`withPastableImages`, () => {
  it(`reads images from an outside host into data: URLs the image plugin uploads, once each`, async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, blob: async () => new Blob([`png`], { type: `image/png` }) }))
    vi.stubGlobal(`fetch`, fetchMock)
    const own = `https://i0.hdslb.com/bfs/new_dyn/x.png`
    const embedded = `data:image/png;base64,${btoa(`png`)}`

    const result = await withPastableImages(`<img src="${HOSTED}"><img src="${HOSTED}"><img src="${own}"><img src="${embedded}">`)

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(result.unread).toBe(0)
    expect(result.html).not.toContain(`myqcloud.com`)
    expect(result.html.match(new RegExp(`src="data:image/png;base64,${btoa(`png`)}"`, `g`))).toHaveLength(3)
    expect(result.html).toContain(`src="${own}"`)
  })

  it(`reports a linked image it cannot read, which then goes in as a link`, async () => {
    vi.stubGlobal(`fetch`, vi.fn(async () => ({ ok: false, status: 403 })))

    const result = await withPastableImages(`<p>图</p><img src="${HOSTED}">`)

    expect(result.html).toContain(`src="${HOSTED}"`)
    expect(result).toMatchObject({ unread: 1, reasons: [`读取不到图片（HTTP 403）：obisidian-1302473945.cos.ap-chengdu.myqcloud.com`] })
  })
})
