// Upload endpoint and form layout follow leaperone/MultiPost-Extension (Apache-2.0).
// Runs inside the logged-in mp.weixin.qq.com tab, so requests are same-origin.

export interface WechatSession {
  token: string
  /** `wx.commonData.ticket`; the upload endpoint wants it alongside the token. */
  ticket: string
  /** `wx.commonData.user_name`, sent as `ticket_id`. */
  userName: string
}

export type ImageUploader = (image: Blob) => Promise<string | null>

export interface EmbeddedImageUpload {
  html: string
  total: number
  failed: number
}

/** Decode a `data:` URL without fetch (the page CSP may not allow data: fetches). */
export function dataUrlToBlob(dataUrl: string): Blob | null {
  const comma = dataUrl.indexOf(`,`)
  const header = dataUrl.slice(0, comma)
  if (comma === -1 || !header.startsWith(`data:`))
    return null
  const [mime = ``, ...params] = header.slice(`data:`.length).split(`;`)
  const payload = dataUrl.slice(comma + 1)
  try {
    const bytes = params.includes(`base64`)
      ? Uint8Array.from(atob(payload), char => char.charCodeAt(0))
      : new TextEncoder().encode(decodeURIComponent(payload))
    return new Blob([bytes], { type: mime || `application/octet-stream` })
  }
  catch {
    return null
  }
}

/** WeChat's image library takes JPEG, PNG, GIF and BMP; re-encode anything else (WebP) as PNG. */
export async function toUploadableImage(image: Blob): Promise<Blob> {
  if (/^image\/(?:png|jpe?g|gif|bmp)$/.test(image.type))
    return image
  try {
    const bitmap = await createImageBitmap(image)
    const canvas = document.createElement(`canvas`)
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    canvas.getContext(`2d`)!.drawImage(bitmap, 0, 0)
    bitmap.close()
    const png = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, `image/png`))
    return png ?? image
  }
  catch {
    return image
  }
}

/** Uploads into the account's image library, like the editor's own "upload image" does. */
export function createWechatUploader(session: WechatSession): ImageUploader {
  return async (image) => {
    const extension = (image.type.split(`/`)[1] ?? `png`).replace(`jpeg`, `jpg`)
    const name = `md-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${extension}`
    const form = new FormData()
    form.append(`type`, image.type)
    form.append(`id`, String(Date.now()))
    form.append(`name`, name)
    form.append(`lastModifiedDate`, new Date().toString())
    form.append(`size`, String(image.size))
    form.append(`file`, image, name)

    const url = new URL(`/cgi-bin/filetransfer`, location.origin)
    const params: Record<string, string> = {
      action: `upload_material`,
      f: `json`,
      scene: `8`,
      writetype: `doublewrite`,
      groupid: `1`,
      ticket_id: session.userName,
      ticket: session.ticket,
      svr_time: String(Math.floor(Date.now() / 1000)),
      token: session.token,
      lang: `zh_CN`,
      seq: String(Date.now()),
      t: String(Math.random()),
    }
    for (const [key, value] of Object.entries(params))
      url.searchParams.set(key, value)

    try {
      const response = await fetch(url.toString(), { method: `POST`, body: form, credentials: `include` })
      const result = await response.json() as { base_resp?: { ret?: number, err_msg?: string }, cdn_url?: string }
      const ok = result.base_resp?.ret === 0 || result.base_resp?.err_msg === `ok`
      return ok && typeof result.cdn_url === `string` && result.cdn_url ? result.cdn_url : null
    }
    catch {
      return null
    }
  }
}

/**
 * Replace every embedded (`data:`) image with an uploaded copy. The HTML is
 * edited as an inert template so formula SVGs and inline styles survive;
 * images that fail to upload keep their data URL and are counted.
 */
export async function uploadEmbeddedImages(html: string, upload: ImageUploader): Promise<EmbeddedImageUpload> {
  const template = document.createElement(`template`)
  template.innerHTML = html
  const images = Array.from(template.content.querySelectorAll<HTMLImageElement>(`img[src^="data:"]`))
  if (images.length === 0)
    return { html, total: 0, failed: 0 }

  const uploaded = new Map<string, string | null>()
  for (const img of images) {
    const source = img.getAttribute(`src`)!
    if (!uploaded.has(source)) {
      const blob = dataUrlToBlob(source)
      uploaded.set(source, blob ? await upload(await toUploadableImage(blob)) : null)
    }
    const url = uploaded.get(source)
    if (url)
      img.setAttribute(`src`, url)
  }
  const failed = images.filter(img => img.getAttribute(`src`)!.startsWith(`data:`)).length
  return { html: template.innerHTML, total: images.length, failed }
}
