// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createWechatUploader, dataUrlToBlob, toUploadableImage, uploadEmbeddedImages } from './wechat-images'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe(`dataUrlToBlob`, () => {
  it(`decodes base64 and percent-encoded payloads`, async () => {
    const base64 = dataUrlToBlob(`data:image/webp;base64,${btoa(`webp-bytes`)}`)!
    expect(base64.type).toBe(`image/webp`)
    expect(await base64.text()).toBe(`webp-bytes`)

    const svg = dataUrlToBlob(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg/>`)}`)!
    expect(svg.type).toBe(`image/svg+xml`)
    expect(await svg.text()).toBe(`<svg/>`)
  })

  it(`rejects anything that is not a data URL`, () => {
    expect(dataUrlToBlob(`https://a.io/x.png`)).toBeNull()
    expect(dataUrlToBlob(`data:image/png;base64`)).toBeNull()
    expect(dataUrlToBlob(`data:image/png;base64,***`)).toBeNull()
  })
})

describe(`toUploadableImage`, () => {
  it(`passes through formats WeChat accepts`, async () => {
    const png = new Blob([`x`], { type: `image/png` })
    expect(await toUploadableImage(png)).toBe(png)
  })

  it(`falls back to the original when it cannot re-encode`, async () => {
    // jsdom has no createImageBitmap, like any environment where decoding fails.
    const webp = new Blob([`x`], { type: `image/webp` })
    expect(await toUploadableImage(webp)).toBe(webp)
  })
})

describe(`uploadEmbeddedImages`, () => {
  it(`uploads each distinct data URL once and leaves remote images and SVG alone`, async () => {
    const upload = vi.fn(async () => `https://mmbiz.qpic.cn/x/0?wx_fmt=png`)
    const png = `data:image/png;base64,${btoa(`png`)}`
    const html = `<p><span class="katex-inline" data-math-raw="$x$"><svg viewBox="0 0 1 1"><path d="M0 0"></path></svg></span></p>`
      + `<img src="${png}"><img src="${png}"><img src="https://cdn.example.com/a.png">`

    const result = await uploadEmbeddedImages(html, upload)

    expect(upload).toHaveBeenCalledOnce()
    expect(result.total).toBe(2)
    expect(result.failed).toBe(0)
    expect(result.html).toContain(`<svg viewBox="0 0 1 1"><path d="M0 0"></path></svg>`)
    expect(result.html).toContain(`src="https://cdn.example.com/a.png"`)
    expect(result.html.match(/mmbiz\.qpic\.cn/g)).toHaveLength(2)
  })

  it(`returns the HTML untouched when there is nothing embedded`, async () => {
    const upload = vi.fn()
    const html = `<p>plain</p>`
    expect(await uploadEmbeddedImages(html, upload)).toEqual({ html, total: 0, failed: 0 })
    expect(upload).not.toHaveBeenCalled()
  })
})

describe(`createWechatUploader`, () => {
  it(`posts the file to the image library with the session parameters`, async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      new Response(JSON.stringify({ base_resp: { ret: 0, err_msg: `ok` }, cdn_url: `https://mmbiz.qpic.cn/y/0` })))
    vi.stubGlobal(`fetch`, fetchMock)

    const url = await createWechatUploader({ token: `42`, ticket: `tk`, userName: `gh_x` })(new Blob([`png`], { type: `image/png` }))

    expect(url).toBe(`https://mmbiz.qpic.cn/y/0`)
    const [requestUrl, init] = fetchMock.mock.calls[0]
    const params = new URL(requestUrl).searchParams
    expect(Object.fromEntries([`action`, `scene`, `writetype`, `token`, `ticket`, `ticket_id`].map(key => [key, params.get(key)])))
      .toEqual({ action: `upload_material`, scene: `8`, writetype: `doublewrite`, token: `42`, ticket: `tk`, ticket_id: `gh_x` })
    const form = init.body as FormData
    expect(form.get(`type`)).toBe(`image/png`)
    expect((form.get(`file`) as File).name).toMatch(/\.png$/)
  })

  it(`returns null on a rejected upload or a network error`, async () => {
    vi.stubGlobal(`fetch`, vi.fn(async () => new Response(JSON.stringify({ base_resp: { ret: 200002, err_msg: `invalid args` } }))))
    const uploader = createWechatUploader({ token: `1`, ticket: ``, userName: `` })
    expect(await uploader(new Blob([`x`], { type: `image/png` }))).toBeNull()

    vi.stubGlobal(`fetch`, vi.fn(async () => {
      throw new TypeError(`Failed to fetch`)
    }))
    expect(await uploader(new Blob([`x`], { type: `image/png` }))).toBeNull()
  })
})
