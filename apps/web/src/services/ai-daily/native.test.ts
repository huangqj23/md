import type { AiDailyError, NativeExtension } from './native'
import { describe, expect, it, vi } from 'vitest'
import { AI_DAILY_HOST, callHost, classifyNativeError, hasNativePermission, installCommand, isAiDailyDeclared, isWindows, localDate, readEmbed, requestNativePermission, resolveLoadTarget } from './native'

function ext(send?: (host: string, message: any) => Promise<unknown>, declared = true): NativeExtension {
  return {
    runtime: {
      id: `abc`,
      sendNativeMessage: send,
      getManifest: () => ({ permissions: [`storage`], optional_permissions: declared ? [`nativeMessaging`] : [] }),
    },
    permissions: { contains: vi.fn(async () => true), request: vi.fn(async () => true) },
  }
}

async function kindOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise
    return `resolved`
  }
  catch (error) {
    return (error as AiDailyError).kind
  }
}

describe(`classifyNativeError`, () => {
  it.each([
    [`Specified native messaging host not found.`, `not-installed`],
    [`Access to the specified native messaging host is forbidden.`, `forbidden`],
    [`Native host has exited.`, `host-error`],
    [`Error when communicating with the native messaging host.`, `host-error`],
  ])(`%s → %s`, (message, kind) => {
    expect(classifyNativeError(message)).toBe(kind)
  })
})

describe(`callHost`, () => {
  it(`sends to the ai-daily host and returns ok replies`, async () => {
    const send = vi.fn(async () => ({ ok: true, version: 1 }))
    expect(await callHost(ext(send), { cmd: `ping` })).toEqual({ ok: true, version: 1 })
    expect(send).toHaveBeenCalledWith(AI_DAILY_HOST, { cmd: `ping` })
  })

  it(`surfaces host refusals as command errors with the host's message`, async () => {
    const e = ext(async () => ({ ok: false, error: `当天还没有草稿` }))
    await expect(callHost(e, { cmd: `publish` })).rejects.toThrow(`当天还没有草稿`)
    expect(await kindOf(callHost(e, { cmd: `publish` }))).toBe(`command`)
  })

  it(`tells missing permission, missing declaration, missing host and bad replies apart`, async () => {
    expect(await kindOf(callHost(ext(undefined), { cmd: `ping` }))).toBe(`no-permission`)
    expect(await kindOf(callHost(ext(async () => ({}), false), { cmd: `ping` }))).toBe(`unsupported`)
    expect(await kindOf(callHost(null, { cmd: `ping` }))).toBe(`unsupported`)
    expect(await kindOf(callHost(ext(async () => {
      throw new Error(`Specified native messaging host not found.`)
    }), { cmd: `ping` }))).toBe(`not-installed`)
    expect(await kindOf(callHost(ext(async () => `garbage`), { cmd: `ping` }))).toBe(`host-error`)
  })
})

describe(`readEmbed`, () => {
  it(`stitches chunked replies in order`, async () => {
    const pieces = [`# 标题\n`, `正文`, `！`]
    const send = vi.fn(async (_host: string, message: any) => ({ ok: true, title: `标题`, chunks: 3, index: message.chunk, content: pieces[message.chunk] }))
    expect(await readEmbed(ext(send), `2026-09-30`)).toEqual({ title: `标题`, content: `# 标题\n正文！` })
    expect(send).toHaveBeenCalledTimes(3)
    expect(send).toHaveBeenLastCalledWith(AI_DAILY_HOST, { cmd: `read_embed`, date: `2026-09-30`, chunk: 2 })
  })
})

describe(`helpers`, () => {
  it(`detects the declared permission and wraps the permissions API`, async () => {
    const e = ext(async () => ({ ok: true }))
    expect(isAiDailyDeclared(e)).toBe(true)
    expect(isAiDailyDeclared(ext(undefined, false))).toBe(false)
    expect(await hasNativePermission(e)).toBe(true)
    expect(await requestNativePermission(e)).toBe(true)
    expect(e.permissions!.request).toHaveBeenCalledWith({ permissions: [`nativeMessaging`] })
    expect(await hasNativePermission({})).toBe(false)
  })

  it(`formats the local date and the install command`, () => {
    expect(localDate(new Date(2026, 8, 3, 23, 59))).toBe(`2026-09-03`)
    expect(installCommand(`abc`, true)).toBe(`.venv\\Scripts\\ai-daily install-host --extension-id abc`)
    expect(installCommand(`abc`, false)).toBe(`.venv/bin/ai-daily install-host --extension-id abc`)
    expect(isWindows(`Mozilla/5.0 (Windows NT 10.0; Win64; x64)`)).toBe(true)
    expect(isWindows(`Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)`)).toBe(false)
  })
})

describe(`resolveLoadTarget`, () => {
  it(`reuses the post created for that day while it still exists`, () => {
    const map = { '2026-09-30': `p1` }
    expect(resolveLoadTarget(map, `2026-09-30`, id => id === `p1`)).toEqual({ postId: `p1` })
    expect(resolveLoadTarget(map, `2026-09-30`, () => false)).toBeNull()
    expect(resolveLoadTarget(map, `2026-10-01`, () => true)).toBeNull()
  })
})
