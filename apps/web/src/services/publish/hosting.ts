import type { AgentArticle } from '@/publish-agent/protocol'
import type { UploadProviderId } from '@/services/upload/provider-registry'
import { dataUrlToBlob } from '@/publish-agent/platforms/wechat-images'

/**
 * Raster images embedded as data: URLs. SVG ones stay put: no platform takes an SVG upload, and
 * the formula pipelines already turn formulas into text or pictures of their own.
 */
const DATA_IMAGE = /data:image\/(?:png|jpe?g|gif|webp|bmp);base64,[\w+/=]+/g

/** Image hosts whose addresses every platform can fetch: not the shared default one, nor WeChat's own library. */
export function canHostImages(provider: UploadProviderId): boolean {
  return provider !== `default` && provider !== `mp`
}

export interface HostingReport {
  /** Distinct embedded images found so far. */
  total: number
  /** Distinct images whose upload has finished, failed ones included. */
  done: number
  /** Distinct images that stayed embedded because their upload failed. */
  failed: number
  reasons: string[]
}

export interface ImageHosting {
  /** The article with every embedded image swapped for its address on the image host. */
  hostArticle: (article: AgentArticle) => Promise<AgentArticle>
  report: () => HostingReport
}

export interface HostingOptions {
  /** Uploads in flight at once. */
  concurrency?: number
  /** Called whenever an image is found or its upload finishes. */
  onProgress?: (report: HostingReport) => void
}

/**
 * Uploads each distinct embedded image once per sync, a few at a time; `upload` turns a data URL
 * into a public address. An image whose upload fails keeps its data URL, so the platforms that
 * upload embedded images themselves still can.
 */
export function createImageHosting(upload: (dataUrl: string) => Promise<string>, options: HostingOptions = {}): ImageHosting {
  const { concurrency = 3, onProgress } = options
  const addresses = new Map<string, Promise<string | null>>()
  const failed = new Set<string>()
  const reasons = new Set<string>()
  const waiting: Array<() => void> = []
  let active = 0
  let done = 0
  const report = (): HostingReport => ({ total: addresses.size, done, failed: failed.size, reasons: [...reasons] })

  const limited = async <T>(work: () => Promise<T>): Promise<T> => {
    if (active < concurrency)
      active++
    else
      await new Promise<void>(resolve => waiting.push(resolve)) // the finishing upload hands over its slot
    try {
      return await work()
    }
    finally {
      const next = waiting.shift()
      if (next)
        next()
      else
        active--
    }
  }

  const host = (source: string) => {
    if (!addresses.has(source)) {
      addresses.set(source, limited(() => upload(source)).catch((error: unknown) => {
        failed.add(source)
        reasons.add(error instanceof Error ? error.message : String(error))
        return null
      }).finally(() => {
        done++
        onProgress?.(report())
      }))
      onProgress?.(report())
    }
    return addresses.get(source)!
  }

  const hostText = async (text: string): Promise<string> => {
    const sources = [...new Set(text.match(DATA_IMAGE) ?? [])]
    if (sources.length === 0)
      return text
    const hosted = new Map<string, string | null>()
    await Promise.all(sources.map(async source => hosted.set(source, await host(source))))
    return text.replace(DATA_IMAGE, source => hosted.get(source) ?? source)
  }

  return {
    async hostArticle(article) {
      const [markdown, html, wechatHtml] = await Promise.all([hostText(article.markdown), hostText(article.html), hostText(article.wechatHtml)])
      return { ...article, markdown, html, wechatHtml }
    },
    report,
  }
}

/**
 * Platforms that fetch a linked image onto their own host do not all take WebP (WeChat's library
 * does not), so WebP and BMP go up as PNG, or as JPEG when that is under half the size (photos).
 */
export async function toPortableImage(image: Blob): Promise<Blob> {
  if (/^image\/(?:png|jpe?g|gif)$/.test(image.type))
    return image
  const bitmap = await createImageBitmap(image)
  const canvas = document.createElement(`canvas`)
  canvas.width = bitmap.width
  canvas.height = bitmap.height
  const context = canvas.getContext(`2d`)!
  context.drawImage(bitmap, 0, 0)
  bitmap.close()
  const encode = (type: string, quality?: number) => new Promise<Blob | null>(resolve => canvas.toBlob(resolve, type, quality))
  const png = await encode(`image/png`)
  if (!png)
    throw new Error(`图片无法转换成 PNG`)
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height)
  for (let alpha = 3; alpha < data.length; alpha += 4) {
    if (data[alpha] < 255)
      return png
  }
  const jpeg = await encode(`image/jpeg`, 0.92)
  return jpeg && jpeg.size * 2 < png.size ? jpeg : png
}

/** The embedded image as a file the image host takes. */
export async function toUploadFile(dataUrl: string): Promise<File> {
  const blob = dataUrlToBlob(dataUrl)
  if (!blob)
    throw new Error(`内嵌图片无法解码`)
  const image = await toPortableImage(blob)
  const extension = (image.type.split(`/`)[1] ?? `png`).replace(`jpeg`, `jpg`)
  return new File([image], `image.${extension}`, { type: image.type })
}
