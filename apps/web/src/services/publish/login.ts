import type { ExtensionGlobal } from './extension-api'
import type { DetectContext, LoginInfo, PublishPlatform } from './platforms'
import type { PublishPlatformId } from '@/publish-agent/protocol'

const DETECT_TIMEOUT_MS = 8000

async function fetchWithTimeout(url: string, accept: string): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), DETECT_TIMEOUT_MS)
  try {
    // Runs in the extension page (a document), where the browser attaches the
    // platform's cookies for hosts we hold permission for.
    return await fetch(url, { credentials: `include`, headers: { Accept: accept }, signal: controller.signal })
  }
  finally {
    clearTimeout(timer)
  }
}

export function createDetectContext(ext: ExtensionGlobal | null): DetectContext {
  const cookies = ext?.cookies
  return {
    async fetchJson(url) {
      const response = await fetchWithTimeout(url, `application/json`)
      let data: unknown = null
      try {
        data = await response.json()
      }
      catch {
        // Login walls often answer with HTML; the probes treat that as "no data".
      }
      return { status: response.status, data }
    },
    async fetchText(url) {
      const response = await fetchWithTimeout(url, `text/html`)
      return { status: response.status, url: response.url, text: await response.text() }
    },
    getCookie: cookies
      ? async (url, name) => (await cookies.get({ url, name }))?.value ?? null
      : null,
  }
}

/** Probe every platform in parallel; a probe that errors or hangs reports `unknown`. */
export async function detectLoginStates(
  platforms: readonly PublishPlatform[],
  ctx: DetectContext,
  onResult?: (id: PublishPlatformId, info: LoginInfo) => void,
): Promise<Partial<Record<PublishPlatformId, LoginInfo>>> {
  const results: Partial<Record<PublishPlatformId, LoginInfo>> = {}
  await Promise.all(platforms.map(async (platform) => {
    let info: LoginInfo
    try {
      info = await Promise.race([
        platform.detectLogin(ctx),
        new Promise<LoginInfo>(resolve => setTimeout(resolve, DETECT_TIMEOUT_MS + 1000, { state: `unknown` })),
      ])
    }
    catch {
      info = { state: `unknown` }
    }
    results[platform.id] = info
    onResult?.(platform.id, info)
  }))
  return results
}
