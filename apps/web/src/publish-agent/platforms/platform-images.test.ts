// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createBaijiahaoUploader, createJianshuUploader, ImageUploadError, isBaiduImage, readImage, uploadDataImages, uploadHtmlImages } from './platform-images'

afterEach(() => {
  vi.unstubAllGlobals()
})

const png = (bytes: string) => `data:image/png;base64,${btoa(bytes)}`

describe(`uploadDataImages`, () => {
  it(`replaces data: images in Markdown and raw <img> tags, uploading each distinct one once`, async () => {
    const markdown = [
      `# Title`,
      `<img alt="a" src="${png(`one`)}" style="max-width:100%;" />`,
      `![b](${png(`two`)})`,
      `<img alt="again" src="${png(`one`)}" />`,
      `![remote](https://a.io/x.png)`,
    ].join(`\n\n`)
    const upload = vi.fn(async (image: Blob) => `https://cdn.example/${await image.text()}.png`)

    const result = await uploadDataImages(markdown, upload)

    expect(upload).toHaveBeenCalledTimes(2)
    expect(result).toMatchObject({ total: 3, failed: 0 })
    expect(result.text).toContain(`<img alt="a" src="https://cdn.example/one.png" style="max-width:100%;" />`)
    expect(result.text).toContain(`![b](https://cdn.example/two.png)`)
    expect(result.text).toContain(`<img alt="again" src="https://cdn.example/one.png" />`)
    expect(result.text).toContain(`![remote](https://a.io/x.png)`)
    expect(result.text).not.toContain(`data:`)
  })

  it(`keeps images that fail to upload and counts them`, async () => {
    const markdown = `![a](${png(`one`)})\n\n![b](${png(`two`)})`
    const result = await uploadDataImages(markdown, async image => (await image.text()) === `one` ? `https://cdn.example/one.png` : null)
    expect(result).toMatchObject({ total: 2, failed: 1 })
    expect(result.text).toContain(`![a](https://cdn.example/one.png)`)
    expect(result.text).toContain(`![b](${png(`two`)})`)
  })

  it(`uploads several at once`, async () => {
    let running = 0
    let peak = 0
    const upload = async (image: Blob) => {
      running++
      peak = Math.max(peak, running)
      await new Promise(resolve => setTimeout(resolve, 10))
      running--
      return `https://cdn.example/${await image.text()}.png`
    }
    const markdown = [`a`, `b`, `c`, `d`, `e`].map(name => `![](${png(name)})`).join(`\n`)
    const result = await uploadDataImages(markdown, upload, { concurrency: 3 })
    expect(peak).toBe(3)
    expect(result).toMatchObject({ total: 5, failed: 0 })
  })

  it(`stops at the budget and leaves the rest embedded, so the body can still go in`, async () => {
    vi.useFakeTimers()
    try {
      const upload = async (image: Blob) => {
        const name = await image.text()
        await new Promise(resolve => setTimeout(resolve, name === `slow` ? 60000 : 1000))
        return `https://cdn.example/${name}.png`
      }
      const markdown = [`fast`, `slow`, `late`].map(name => `![](${png(name)})`).join(`\n`)
      const pending = uploadDataImages(markdown, upload, { budget: 5000 })
      await vi.advanceTimersByTimeAsync(5000)
      const result = await pending
      expect(result).toMatchObject({ total: 3, failed: 2 })
      expect(result.text).toContain(`![](https://cdn.example/fast.png)`)
      expect(result.text).toContain(`![](${png(`slow`)})`)
      expect(result.text).toContain(`![](${png(`late`)})`)
    }
    finally {
      vi.useRealTimers()
    }
  })

  it(`reports the uploader's reasons for the images that still failed`, async () => {
    const markdown = [`a`, `b`, `c`].map(name => `![](${png(name)})`).join(`\n`)
    const upload = async (image: Blob) => {
      const name = await image.text()
      if (name === `a`)
        return `https://cdn.example/a.png`
      throw new ImageUploadError(name === `b` ? `HTTP 429` : `too large`)
    }
    const result = await uploadDataImages(markdown, upload)
    expect(result).toMatchObject({ total: 3, failed: 2, reasons: [`HTTP 429`, `too large`] })
  })

  it(`retries failed images one at a time, after a pause`, async () => {
    vi.useFakeTimers()
    try {
      const attempts = new Map<string, number>()
      const upload = async (image: Blob) => {
        const name = await image.text()
        const attempt = (attempts.get(name) ?? 0) + 1
        attempts.set(name, attempt)
        if (name === `flaky` && attempt < 3)
          throw new ImageUploadError(`HTTP 429`)
        return name === `broken` ? null : `https://cdn.example/${name}.png`
      }
      const markdown = [`ok`, `flaky`, `broken`].map(name => `![](${png(name)})`).join(`\n`)
      const pending = uploadDataImages(markdown, upload, { retries: 2 })
      await vi.runAllTimersAsync()
      const result = await pending
      expect(result).toMatchObject({ total: 3, failed: 1 })
      expect(result.reasons).toBeUndefined()
      expect(result.text).toContain(`![](https://cdn.example/flaky.png)`)
      expect(Object.fromEntries(attempts)).toEqual({ ok: 1, flaky: 3, broken: 3 })
    }
    finally {
      vi.useRealTimers()
    }
  })

  it(`leaves Markdown without embedded images untouched`, async () => {
    const upload = vi.fn()
    expect(await uploadDataImages(`![x](images/a.png)`, upload)).toEqual({ text: `![x](images/a.png)`, total: 0, failed: 0 })
    expect(upload).not.toHaveBeenCalled()
  })
})

describe(`createJianshuUploader`, () => {
  it(`asks Jianshu for a Qiniu token, then posts the file to Qiniu`, async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: `tok`, key: `upload_images/k.png` })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ url: `https://upload-images.jianshu.io/upload_images/k.png` })))
    vi.stubGlobal(`fetch`, fetchMock)

    const url = await createJianshuUploader()(new Blob([`x`], { type: `image/png` }))

    expect(url).toBe(`https://upload-images.jianshu.io/upload_images/k.png`)
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/^\/upload_images\/token\.json\?filename=md-\d+-\w+\.png$/)
    expect(fetchMock.mock.calls[1][0]).toBe(`https://upload.qiniup.com/`)
    const form = fetchMock.mock.calls[1][1].body as FormData
    expect(form.get(`token`)).toBe(`tok`)
    expect(form.get(`key`)).toBe(`upload_images/k.png`)
    expect(form.get(`x:protocol`)).toBe(`https`)
    expect(form.get(`file`)).toBeInstanceOf(Blob)
  })

  it(`says which request failed and what Jianshu or Qiniu answered`, async () => {
    const image = new Blob([`x`], { type: `image/png` })
    vi.stubGlobal(`fetch`, vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: [{ message: `请先绑定手机号` }] }), { status: 422 })))
    await expect(createJianshuUploader()(image)).rejects.toThrow(new ImageUploadError(`token.json: 请先绑定手机号`))

    vi.stubGlobal(`fetch`, vi.fn().mockResolvedValue(new Response(`Too Many Requests`, { status: 429 })))
    await expect(createJianshuUploader()(image)).rejects.toThrow(`token.json: HTTP 429`)

    vi.stubGlobal(`fetch`, vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: `tok`, key: `k.png` })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: `file exceeds maximum allowed size` }), { status: 413 })))
    await expect(createJianshuUploader()(image)).rejects.toThrow(`Qiniu: file exceeds maximum allowed size`)

    vi.stubGlobal(`fetch`, vi.fn().mockRejectedValue(new TypeError(`Failed to fetch`)))
    await expect(createJianshuUploader()(image)).rejects.toBeInstanceOf(ImageUploadError)
  })
})

describe(`createBaijiahaoUploader`, () => {
  it(`posts the image to the material library with the editor's edit-token`, async () => {
    localStorage.setItem(`edit-token`, `"tok-123"`)
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ret: { https_url: `https://pic.rmb.bdstatic.com/a.png` } })))
    vi.stubGlobal(`fetch`, fetchMock)

    const url = await createBaijiahaoUploader()(new Blob([`x`], { type: `image/png` }))

    expect(url).toBe(`https://pic.rmb.bdstatic.com/a.png`)
    const [endpoint, init] = fetchMock.mock.calls[0]
    expect(endpoint).toBe(`/materialui/picture/uploadProxy`)
    expect(init.headers).toEqual({ Token: `tok-123` })
    const form = init.body as FormData
    expect(form.get(`type`)).toBe(`image`)
    expect(form.get(`article_type`)).toBe(`news`)
    expect(form.get(`media`)).toBeInstanceOf(File)
    expect((form.get(`media`) as File).name).toMatch(/^md-\d+-\w+\.png$/)
    localStorage.removeItem(`edit-token`)
  })

  it(`fails with the library's message when it answers without a URL`, async () => {
    vi.stubGlobal(`fetch`, vi.fn().mockResolvedValue(new Response(JSON.stringify({ errno: 1, errmsg: `token invalid` }))))
    await expect(createBaijiahaoUploader()(new Blob([`x`], { type: `image/png` }))).rejects.toThrow(new ImageUploadError(`uploadProxy: token invalid`))

    vi.stubGlobal(`fetch`, vi.fn().mockResolvedValue(new Response(`<html>`, { status: 502 })))
    await expect(createBaijiahaoUploader()(new Blob([`x`], { type: `image/png` }))).rejects.toThrow(`uploadProxy: HTTP 502`)
  })
})

describe(`isBaiduImage`, () => {
  it(`recognizes the hosts Baijiahao serves images from`, () => {
    expect(isBaiduImage(`https://pic.rmb.bdstatic.com/bjh/abc.png`)).toBe(true)
    expect(isBaiduImage(`//t10.baidu.com/it/u=1.jpg`)).toBe(true)
    expect(isBaiduImage(`https://md-1250000000.cos.ap-guangzhou.myqcloud.com/a.png`)).toBe(false)
    expect(isBaiduImage(`images/fig1.png`)).toBe(false)
    expect(isBaiduImage(png(`x`))).toBe(false)
  })
})

describe(`readImage`, () => {
  it(`decodes data: URLs and fetches web addresses without credentials`, async () => {
    expect(await (await readImage(png(`one`))).text()).toBe(`one`)

    const fetchMock = vi.fn().mockResolvedValue(new Response(`bytes`))
    vi.stubGlobal(`fetch`, fetchMock)
    expect(await (await readImage(`https://cdn.example/a.png`)).text()).toBe(`bytes`)
    expect(fetchMock).toHaveBeenCalledWith(`https://cdn.example/a.png`, { credentials: `omit` })
  })

  it(`says why an image cannot be read`, async () => {
    await expect(readImage(`images/fig1.png`)).rejects.toThrow(`本地图片，先上传到图床再同步：images/fig1.png`)

    vi.stubGlobal(`fetch`, vi.fn().mockRejectedValue(new TypeError(`Failed to fetch`)))
    await expect(readImage(`https://cos.example/a.png`)).rejects.toThrow(`读取不到图片（图床可能没有允许跨域读取）：cos.example`)

    vi.stubGlobal(`fetch`, vi.fn().mockResolvedValue(new Response(``, { status: 403 })))
    await expect(readImage(`https://cos.example/a.png`)).rejects.toThrow(`读取不到图片（HTTP 403）：cos.example`)
  })
})

describe(`uploadHtmlImages`, () => {
  const isHosted = (src: string) => src.startsWith(`https://own.example/`)

  it(`uploads embedded and outside images once each, leaving the platform's own`, async () => {
    vi.stubGlobal(`fetch`, vi.fn().mockResolvedValue(new Response(`remote`)))
    const html = `<figure><img src="https://cos.example/fig1.png" alt="图 1"><figcaption>图 1</figcaption></figure>`
      + `<p><img src="${png(`table`)}"></p><p><img src="https://own.example/kept.png"></p><img src="https://cos.example/fig1.png">`
    const upload = vi.fn(async (image: Blob) => `https://own.example/${await image.text()}.png`)

    const result = await uploadHtmlImages(html, upload, { isHosted })

    expect(upload).toHaveBeenCalledTimes(2)
    expect(result).toMatchObject({ total: 3, failed: 0 })
    expect(result.text).toBe(
      `<figure><img src="https://own.example/remote.png" alt="图 1"><figcaption>图 1</figcaption></figure>`
      + `<p><img src="https://own.example/table.png"></p><p><img src="https://own.example/kept.png"></p><img src="https://own.example/remote.png">`,
    )
  })

  it(`keeps what it could not upload and reports why`, async () => {
    const html = `<img src="images/fig1.png"><img src="images/fig2.png"><img src="${png(`ok`)}">`
    const result = await uploadHtmlImages(html, async () => `https://own.example/ok.png`, { isHosted })
    expect(result).toEqual({
      text: `<img src="images/fig1.png"><img src="images/fig2.png"><img src="https://own.example/ok.png">`,
      total: 3,
      failed: 2,
      reasons: [`本地图片，先上传到图床再同步：images/fig1.png`, `本地图片，先上传到图床再同步：images/fig2.png`],
    })
  })

  it(`leaves HTML without foreign images untouched`, async () => {
    const upload = vi.fn()
    const html = `<p>x</p><img src="https://own.example/a.png">`
    expect(await uploadHtmlImages(html, upload, { isHosted })).toEqual({ text: html, total: 0, failed: 0 })
    expect(upload).not.toHaveBeenCalled()
  })
})
