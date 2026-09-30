import type { ExtensionGlobal } from './extension-api'
import type { PublishPlatform } from './platforms'

/**
 * API permissions the publisher asks for on first use, next to the platform
 * hosts. They are optional in the manifest so the base install asks for nothing new.
 */
export const PUBLISH_API_PERMISSIONS = [`scripting`, `cookies`, `tabGroups`] as const

function escapeRegExp(text: string): string {
  return text.replace(/[.+?^${}()|[\]\\]/g, `\\$&`)
}

/** Chrome match-pattern test (`https://*.example.com/*`), enough for host permissions. */
export function matchesPattern(url: string, pattern: string): boolean {
  const parts = pattern.match(/^(\*|https?):\/\/(\*|\*\.[^/*]+|[^/*]+)(\/.*)$/)
  if (!parts)
    return false
  let parsed: URL
  try {
    parsed = new URL(url)
  }
  catch {
    return false
  }
  const [, scheme, host, path] = parts
  const protocolOk = scheme === `*` ? parsed.protocol === `http:` || parsed.protocol === `https:` : parsed.protocol === `${scheme}:`
  if (!protocolOk)
    return false
  if (host.startsWith(`*.`)) {
    const base = host.slice(2)
    if (parsed.hostname !== base && !parsed.hostname.endsWith(`.${base}`))
      return false
  }
  else if (host !== `*` && parsed.hostname !== host) {
    return false
  }
  const pathPattern = new RegExp(`^${path.split(`*`).map(escapeRegExp).join(`.*`)}$`)
  return pathPattern.test(parsed.pathname + parsed.search)
}

export function originsFor(platforms: readonly PublishPlatform[]): string[] {
  return [...new Set(platforms.flatMap(p => p.hostPermissions))]
}

/** Only request what the built manifest declares (Firefox has no tabGroups, for example). */
export function requestableApiPermissions(ext: ExtensionGlobal): string[] {
  const manifest = ext.runtime?.getManifest?.()
  const declared = new Set([...(manifest?.optional_permissions ?? []), ...(manifest?.permissions ?? [])])
  return PUBLISH_API_PERMISSIONS.filter(permission => declared.has(permission))
}

export async function hasPublishPermissions(ext: ExtensionGlobal, platforms: readonly PublishPlatform[]): Promise<boolean> {
  if (!ext.permissions)
    return false
  try {
    return await ext.permissions.contains({ origins: originsFor(platforms), permissions: requestableApiPermissions(ext) })
  }
  catch {
    return false
  }
}

/** Must run inside a click handler: browsers only show the prompt for a user gesture. */
export async function requestPublishPermissions(ext: ExtensionGlobal, platforms: readonly PublishPlatform[]): Promise<boolean> {
  if (!ext.permissions)
    return false
  return ext.permissions.request({ origins: originsFor(platforms), permissions: requestableApiPermissions(ext) })
}
