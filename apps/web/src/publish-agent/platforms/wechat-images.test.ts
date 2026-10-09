// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { uploadHtmlImages } from './platform-images'
import { createWechatUploader, dataUrlToBlob, isWechatImage, toUploadableImage } from './wechat-images'

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

describe(`isWechatImage`, () => {
  it(`recognises WeChat's CDN only`, () => {
    expect(isWechatImage(`https://mmbiz.qpic.cn/mmbiz_png/abc/0?wx_fmt=png`)).toBe(true)
    expect(isWechatImage(`//mmbiz.qlogo.cn/x/0`)).toBe(true)
    expect(isWechatImage(`https://obisidian-1302473945.cos.ap-chengdu.myqcloud.com/md/a.png`)).toBe(false)
    expect(isWechatImage(`https://evilqpic.cn/a.png`)).toBe(false)
    expect(isWechatImage(`data:image/png;base64,AAAA`)).toBe(false)
    expect(isWechatImage(`images/a.png`)).toBe(false)
  })
})

describe(`uploading a WeChat article's images`, () => {
  it(`moves embedded and image-host images into the library once each, leaving WeChat's own and SVG alone`, async () => {
    const upload = vi.fn(async () => `https://mmbiz.qpic.cn/x/0?wx_fmt=png`)
    const fetchMock = vi.fn(async () => ({ ok: true, blob: async () => new Blob([`png`], { type: `image/png` }) }))
    vi.stubGlobal(`fetch`, fetchMock)
    const png = `data:image/png;base64,${btoa(`png`)}`
    const hosted = `https://obisidian-1302473945.cos.ap-chengdu.myqcloud.com/md/a.png`
    const html = `<p><span class="katex-inline" data-math-raw="$x$"><svg viewBox="0 0 1 1"><path d="M0 0"></path></svg></span></p>`
      + `<img src="${png}"><img src="${png}"><img src="${hosted}"><img src="https://mmbiz.qpic.cn/old/0">`

    const result = await uploadHtmlImages(html, upload, { isHosted: isWechatImage })

    expect(upload).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenCalledWith(hosted, { credentials: `omit` })
    expect(result).toMatchObject({ total: 3, failed: 0 })
    expect(result.text).toContain(`<svg viewBox="0 0 1 1"><path d="M0 0"></path></svg>`)
    expect(result.text).not.toContain(`myqcloud.com`)
    expect(result.text).toContain(`src="https://mmbiz.qpic.cn/old/0"`)
  })

  it(`keeps an image the page cannot read and says why`, async () => {
    vi.stubGlobal(`fetch`, vi.fn(async () => {
      throw new TypeError(`Failed to fetch`)
    }))
    const html = `<img src="https://obisidian-1302473945.cos.ap-chengdu.myqcloud.com/md/a.png">`

    const result = await uploadHtmlImages(html, vi.fn(), { isHosted: isWechatImage })

    expect(result).toEqual({ text: html, total: 1, failed: 1, reasons: [`读取不到图片（图床可能没有允许跨域读取）：obisidian-1302473945.cos.ap-chengdu.myqcloud.com`] })
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
