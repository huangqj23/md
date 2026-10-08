// Embedded (data:) images re-uploaded to the image host of a platform other than WeChat, which
// cannot save a draft that still holds them. Upload endpoints follow
// leaperone/MultiPost-Extension (Apache-2.0); each uploader runs inside the platform's logged-in tab.
import type { ImageUploader } from './wechat-images'
import { sleep } from '../dom'
import { dataUrlToBlob, toUploadableImage } from './wechat-images'

export interface DataImageUpload {
  text: string
  total: number
  failed: number
  /** The platform's distinct reasons for the images that still failed, from `ImageUploadError`s. */
  reasons?: string[]
}

export interface DataImageUploadOptions {
  /** Uploads in flight at once. */
  concurrency?: number
  /**
   * Milliseconds to spend uploading, so the body still goes in before the step times out;
   * images not uploaded by then keep their data: URL and count as failed.
   */
  budget?: number
  /**
   * Further rounds for the images that failed, one upload at a time and each round after a longer
   * pause, for an image host that throttles a burst.
   */
  retries?: number
}

/** Thrown by an uploader with the platform's own words for why it refused an image. */
export class ImageUploadError extends Error {}

/** Pause before retry round n is n times this. */
const RETRY_BACKOFF = 3000

/**
 * For a fill step given 300 s (`stepTimeout` in services/publish/platforms.ts): stop uploading in
 * time to find the editor and put the body in, since a slow upload must not cost the whole body.
 */
export const UPLOAD_WITHIN_STEP: DataImageUploadOptions = { budget: 200000, concurrency: 3 }

function uploadName(image: Blob): string {
  const extension = (image.type.split(`/`)[1] ?? `png`).replace(`jpeg`, `jpg`)
  return `md-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${extension}`
}

/** The message in a Jianshu (`{ error: [{ message }] }`) or Qiniu (`{ error }`) error body, if any. */
export function errorMessageOf(body: unknown): string | null {
  const { error, message } = (body ?? {}) as { error?: unknown, message?: unknown }
  const first = Array.isArray(error) ? (error[0] as { message?: unknown } | undefined)?.message : error
  const text = typeof first === `string` ? first : typeof message === `string` ? message : ``
  return text.trim() || null
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  }
  catch {
    return null
  }
}

/**
 * Jianshu hands out a Qiniu token per file; the image goes straight to Qiniu, which returns its CDN
 * URL. The writer's own uploader makes the same two requests; failures throw `ImageUploadError`
 * saying which request failed and why, which the publish center shows.
 */
export function createJianshuUploader(): ImageUploader {
  return async (image) => {
    const name = uploadName(image)
    let step = `token.json`
    try {
      const tokenResponse = await fetch(`/upload_images/token.json?${new URLSearchParams({ filename: name })}`, {
        credentials: `include`,
        headers: { Accept: `application/json` },
      })
      const config = await readJson(tokenResponse) as { token?: string, key?: string } | null
      if (!config?.token || !config.key)
        throw new ImageUploadError(`${step}: ${errorMessageOf(config) ?? `HTTP ${tokenResponse.status}`}`)

      step = `Qiniu`
      const form = new FormData()
      form.append(`token`, config.token)
      form.append(`key`, config.key)
      form.append(`x:protocol`, `https`)
      form.append(`file`, image, name)
      const response = await fetch(`https://upload.qiniup.com/`, { method: `POST`, body: form })
      const result = await readJson(response) as { url?: string } | null
      if (typeof result?.url === `string` && result.url)
        return result.url
      throw new ImageUploadError(`${step}: ${errorMessageOf(result) ?? `HTTP ${response.status}`}`)
    }
    catch (error) {
      if (error instanceof ImageUploadError)
        throw error
      throw new ImageUploadError(`${step}: ${error instanceof Error ? error.message : String(error)}`)
    }
  }
}

/**
 * Baijiahao's material library, as its editor's own image upload uses it; the request carries the
 * `edit-token` the editor keeps in localStorage. Form fields are the ones MultiPost sends.
 */
export function createBaijiahaoUploader(): ImageUploader {
  return async (image) => {
    const name = uploadName(image)
    const form = new FormData()
    form.append(`org_file_name`, name)
    form.append(`type`, `image`)
    form.append(`app_id`, ``)
    form.append(`is_waterlog`, `1`)
    form.append(`save_material`, `1`)
    form.append(`no_compress`, `0`)
    form.append(`is_events`, ``)
    form.append(`article_type`, `news`)
    form.append(`media`, new File([image], name, { type: image.type }))
    let response: Response
    try {
      response = await fetch(`/materialui/picture/uploadProxy`, {
        method: `POST`,
        body: form,
        credentials: `include`,
        headers: { Token: (localStorage.getItem(`edit-token`) ?? ``).replace(/"/g, ``) },
      })
    }
    catch (error) {
      throw new ImageUploadError(`uploadProxy: ${error instanceof Error ? error.message : String(error)}`)
    }
    const result = await readJson(response) as { ret?: { https_url?: string }, errmsg?: unknown, errno?: unknown } | null
    const url = result?.ret?.https_url
    if (typeof url === `string` && url)
      return url
    const message = typeof result?.errmsg === `string` && result.errmsg.trim() ? result.errmsg.trim() : errorMessageOf(result)
    throw new ImageUploadError(`uploadProxy: ${message ?? `HTTP ${response.status}${result?.errno !== undefined ? `, errno ${String(result.errno)}` : ``}`}`)
  }
}

const BAIDU_IMAGE_HOST = /(?:^|\.)(?:bdstatic\.com|baidu\.com|bdimg\.com|bcebos\.com|baidubce\.com)$/i

/** Whether Baijiahao serves this image itself (its material library answers with bdstatic addresses). */
export function isBaiduImage(src: string): boolean {
  if (!/^(?:https?:)?\/\//i.test(src))
    return false
  try {
    return BAIDU_IMAGE_HOST.test(new URL(src, `https://baijiahao.baidu.com/`).hostname)
  }
  catch {
    return false
  }
}

const DATA_IMAGE = /data:image\/[\w.+-]+;base64,[\w+/=]+/g

/** `work`, or null once `deadline` (epoch ms) passes first. */
export function beforeDeadline<T>(work: Promise<T>, deadline: number): Promise<T | null> {
  const left = deadline - Date.now()
  if (!Number.isFinite(left))
    return work
  return Promise.race([work, new Promise<null>(resolve => setTimeout(resolve, Math.max(0, left), null))])
}

/**
 * Replaces every data: image in Markdown or HTML, whether in `![](…)` or in an `<img src>` (the
 * embedded drafts use the latter), with an uploaded copy; each distinct image is uploaded once.
 */
interface UploadOutcome {
  /** Uploaded address by source. */
  uploaded: Map<string, string>
  /** The platform's or the reader's reasons for the sources that still failed, deduplicated. */
  reasons: string[]
}

/**
 * Uploads each distinct source a few at a time within the budget, then retries the failed ones;
 * `read` turns a source into the image to upload and throws `ImageUploadError` when it cannot.
 */
async function uploadSources(
  distinct: readonly string[],
  read: (source: string) => Promise<Blob>,
  upload: ImageUploader,
  options: DataImageUploadOptions,
): Promise<UploadOutcome> {
  const deadline = Date.now() + (options.budget ?? Number.POSITIVE_INFINITY)
  const uploaded = new Map<string, string>()
  const reasons = new Map<string, string>()
  const attempt = async (source: string) => {
    try {
      const url = await beforeDeadline(read(source).then(toUploadableImage).then(upload), deadline)
      if (url)
        uploaded.set(source, url)
    }
    catch (error) {
      if (error instanceof ImageUploadError && error.message)
        reasons.set(source, error.message)
    }
  }
  const queue = [...distinct]
  const worker = async () => {
    for (let source = queue.shift(); source && Date.now() < deadline; source = queue.shift())
      await attempt(source)
  }
  await Promise.all(Array.from({ length: Math.max(1, options.concurrency ?? 1) }, worker))

  for (let round = 1; round <= (options.retries ?? 0); round++) {
    const failed = distinct.filter(source => !uploaded.has(source))
    if (failed.length === 0 || Date.now() >= deadline)
      break
    await sleep(Math.min(RETRY_BACKOFF * round, deadline - Date.now()))
    for (const source of failed) {
      if (Date.now() >= deadline)
        break
      await attempt(source)
    }
  }

  const why = distinct.filter(source => !uploaded.has(source)).map(source => reasons.get(source)).filter(Boolean) as string[]
  return { uploaded, reasons: [...new Set(why)] }
}

async function readDataImage(source: string): Promise<Blob> {
  const blob = dataUrlToBlob(source)
  if (!blob)
    throw new ImageUploadError(`内嵌图片无法解码`)
  return blob
}

/**
 * Replaces every data: image in Markdown or HTML, whether in `![](…)` or in an `<img src>` (the
 * embedded drafts use the latter), with an uploaded copy; each distinct image is uploaded once.
 */
export async function uploadDataImages(text: string, upload: ImageUploader, options: DataImageUploadOptions = {}): Promise<DataImageUpload> {
  const sources = text.match(DATA_IMAGE) ?? []
  if (sources.length === 0)
    return { text, total: 0, failed: 0 }

  const { uploaded, reasons } = await uploadSources([...new Set(sources)], readDataImage, upload, options)
  return {
    text: text.replace(DATA_IMAGE, source => uploaded.get(source) ?? source),
    total: sources.length,
    failed: sources.filter(source => !uploaded.has(source)).length,
    ...(reasons.length ? { reasons } : {}),
  }
}

const WEB_ADDRESS = /^(?:https?:)?\/\//i

/**
 * Reads an image the platform's page can reach: a data: URL, or a web address whose host allows
 * cross-origin reads (an image host's CORS rule). A local path such as `images/fig.png` never
 * reached the web, so it is reported as such.
 */
export async function readImage(source: string): Promise<Blob> {
  if (source.startsWith(`data:`))
    return readDataImage(source)
  if (!WEB_ADDRESS.test(source))
    throw new ImageUploadError(`本地图片，先上传到图床再同步：${source.slice(0, 80)}`)
  const host = new URL(source, location.href).hostname
  let response: Response
  try {
    response = await fetch(source, { credentials: `omit` })
  }
  catch {
    throw new ImageUploadError(`读取不到图片（图床可能没有允许跨域读取）：${host}`)
  }
  if (!response.ok)
    throw new ImageUploadError(`读取不到图片（HTTP ${response.status}）：${host}`)
  return response.blob()
}

export interface HtmlImageUploadOptions extends DataImageUploadOptions {
  /** Images the platform already serves itself, left as they are. */
  isHosted: (src: string) => boolean
}

/**
 * Puts every image of an HTML body that the platform does not serve itself on the platform's
 * image host: embedded ones, ones on an outside image host, and (reported as failed) local paths.
 * Editors such as Baijiahao's show only their own images; an outside one goes in blank.
 */
export async function uploadHtmlImages(html: string, upload: ImageUploader, options: HtmlImageUploadOptions): Promise<DataImageUpload> {
  const template = document.createElement(`template`)
  template.innerHTML = html
  const images = Array.from(template.content.querySelectorAll<HTMLImageElement>(`img[src]`))
    .filter(image => !options.isHosted(image.getAttribute(`src`)!))
  if (images.length === 0)
    return { text: html, total: 0, failed: 0 }

  const sources = images.map(image => image.getAttribute(`src`)!)
  const { uploaded, reasons } = await uploadSources([...new Set(sources)], readImage, upload, options)
  for (const image of images) {
    const address = uploaded.get(image.getAttribute(`src`)!)
    if (address)
      image.setAttribute(`src`, address)
  }
  return {
    text: template.innerHTML,
    total: images.length,
    failed: sources.filter(source => !uploaded.has(source)).length,
    ...(reasons.length ? { reasons } : {}),
  }
}
