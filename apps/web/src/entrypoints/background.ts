import type { MenuExtension } from '@/services/ai-daily/context-menu'
import { browser, defineBackground } from '#imports'
import { detectInitialLocale } from '@/i18n/detect'
import aiDailyEnUS from '@/i18n/messages/en-US/aiDaily'
import enUS from '@/i18n/messages/en-US/store'
import aiDailyJaJP from '@/i18n/messages/ja-JP/aiDaily'
import jaJP from '@/i18n/messages/ja-JP/store'
import aiDailyZhCN from '@/i18n/messages/zh-CN/aiDaily'
import zhCN from '@/i18n/messages/zh-CN/store'
import aiDailyZhTW from '@/i18n/messages/zh-TW/aiDaily'
import zhTW from '@/i18n/messages/zh-TW/store'
import { handleAiDailyMenuClick, isNativePermissionChange, syncAiDailyMenu } from '@/services/ai-daily/context-menu'

const EXTENSION_TITLE_BY_LOCALE = {
  'zh-CN': zhCN.store.extension.editorTitle,
  'zh-TW': zhTW.store.extension.editorTitle,
  'en-US': enUS.store.extension.editorTitle,
  'ja-JP': jaJP.store.extension.editorTitle,
} as const

function getExtensionTitle(): string {
  return EXTENSION_TITLE_BY_LOCALE[detectInitialLocale()]
}

const AI_DAILY_MENU_TITLE_BY_LOCALE = {
  'zh-CN': aiDailyZhCN.aiDaily.contextMenu,
  'zh-TW': aiDailyZhTW.aiDaily.contextMenu,
  'en-US': aiDailyEnUS.aiDaily.contextMenu,
  'ja-JP': aiDailyJaJP.aiDaily.contextMenu,
} as const

function syncAiDaily() {
  void syncAiDailyMenu(browser as unknown as MenuExtension, AI_DAILY_MENU_TITLE_BY_LOCALE[detectInitialLocale()])
}

export default defineBackground({
  type: `module`,
  main() {
    browser.runtime.onInstalled.addListener((detail) => {
      if (import.meta.env.COMMAND === `serve`) {
        browser.runtime.openOptionsPage()
        return
      }
      if (detail.reason === `install`) {
        browser.tabs.create({ url: `https://md-pages.doocs.org/welcome` })
      }
      else if (detail.reason === `update`) {
        browser.runtime.openOptionsPage()
      }
    })

    browser.runtime.onInstalled.addListener(() => {
      if (typeof browser.sidePanel === `undefined`)
        return
      browser.contextMenus.create({
        id: `openSidePanel`,
        title: getExtensionTitle(),
        documentUrlPatterns: [`https://mp.weixin.qq.com/cgi-bin/appmsg*`],
        contexts: [`all`],
      })
    })

    browser.contextMenus.onClicked.addListener((info, tab) => {
      if (info.menuItemId === `openSidePanel` && tab?.id)
        browser.sidePanel.open({ tabId: tab.id })
      else
        void handleAiDailyMenuClick(browser as unknown as MenuExtension, info, tab)
    })

    // The inbox menu only exists while nativeMessaging is granted (it is optional, requested from the AI daily panel).
    browser.runtime.onInstalled.addListener(syncAiDaily)
    browser.permissions?.onAdded.addListener((permissions) => {
      if (isNativePermissionChange(permissions))
        syncAiDaily()
    })
    browser.permissions?.onRemoved.addListener((permissions) => {
      if (isNativePermissionChange(permissions))
        syncAiDaily()
    })
  },
})
