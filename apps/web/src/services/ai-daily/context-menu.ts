import type { NativeExtension } from './native'
import { callHost, hasNativePermission, NATIVE_PERMISSION } from './native'

/** Right-click "send to AI daily": adds the link (or the page) to the ai-daily inbox. */
export const AI_DAILY_MENU_ID = `aiDailyInbox`

interface MenuClickInfo {
  menuItemId: string | number
  linkUrl?: string
  pageUrl?: string
  selectionText?: string
}

interface MenuTab {
  id?: number
  title?: string
  url?: string
}

export interface MenuExtension extends NativeExtension {
  contextMenus?: {
    create: (props: { id: string, title: string, contexts: string[] }) => void
    remove: (id: string) => Promise<void> | void
  }
  action?: {
    setBadgeText: (details: { text: string, tabId?: number }) => Promise<void> | void
    setBadgeBackgroundColor?: (details: { color: string, tabId?: number }) => Promise<void> | void
  }
}

/** The link under the cursor wins; otherwise the page itself. Selected text becomes the note. */
export function inboxEntryFor(info: MenuClickInfo, tab?: MenuTab): { url: string, note: string } | null {
  const url = info.linkUrl || info.pageUrl || tab?.url || ``
  if (!/^https?:\/\//i.test(url))
    return null
  const note = info.selectionText?.trim() || (info.linkUrl ? `` : tab?.title ?? ``)
  return { url, note: note.slice(0, 200) }
}

/** Only offer the menu once the native messaging permission is granted, so it never fails for new users. */
export async function syncAiDailyMenu(ext: MenuExtension, title: string): Promise<void> {
  if (!ext.contextMenus)
    return
  try {
    await ext.contextMenus.remove(AI_DAILY_MENU_ID)
  }
  catch {
    // Not created yet.
  }
  if (await hasNativePermission(ext))
    ext.contextMenus.create({ id: AI_DAILY_MENU_ID, title, contexts: [`page`, `link`, `selection`] })
}

/** Returns whether the entry was added; shows ✓ / ! on the toolbar badge for a moment. */
export async function handleAiDailyMenuClick(ext: MenuExtension, info: MenuClickInfo, tab?: MenuTab, badgeMs = 2500): Promise<boolean> {
  if (info.menuItemId !== AI_DAILY_MENU_ID)
    return false
  const entry = inboxEntryFor(info, tab)
  let ok = false
  if (entry) {
    try {
      await callHost(ext, { cmd: `inbox_add`, url: entry.url, note: entry.note })
      ok = true
    }
    catch (error) {
      console.warn(`[ai-daily] inbox_add failed`, error)
    }
  }
  const tabId = tab?.id
  await ext.action?.setBadgeBackgroundColor?.({ color: ok ? `#16a34a` : `#dc2626`, tabId })
  await ext.action?.setBadgeText({ text: ok ? `✓` : `!`, tabId })
  setTimeout(() => void ext.action?.setBadgeText({ text: ``, tabId }), badgeMs)
  return ok
}

export function isNativePermissionChange(permissions: { permissions?: string[] }): boolean {
  return permissions.permissions?.includes(NATIVE_PERMISSION) ?? false
}
