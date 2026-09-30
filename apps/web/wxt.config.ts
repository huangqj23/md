import type { ConfigEnv } from 'vite'
import fs from 'node:fs'
import path from 'node:path'
import { loadEnv } from 'vite'
import { defineConfig } from 'wxt'
import { PUBLISH_API_PERMISSIONS } from './src/services/publish/permissions'
import { PUBLISH_PLATFORMS } from './src/services/publish/platforms'
import ViteConfig from './vite.config'

function getRootPackageVersion() {
  let dir = __dirname
  while (dir !== path.parse(dir).root) {
    const pkgPath = path.join(dir, `package.json`)
    if (fs.existsSync(pkgPath)) {
      const pkg = JSON.parse(fs.readFileSync(pkgPath, `utf-8`))
      if (pkg.version)
        return pkg.version
    }
    dir = path.dirname(dir)
  }
  return `0.0.0`
}

const version = getRootPackageVersion()

function getApiHostPermissions(mode: string): string[] {
  const env = loadEnv(mode, __dirname, [`VITE_`])
  const apiUrl = (env.VITE_MD_API_URL || env.VITE_SYNC_API_URL || ``).replace(/\/$/, ``)
  if (!apiUrl)
    return []
  try {
    const { protocol, hostname } = new URL(apiUrl)
    if (protocol !== `https:` && protocol !== `http:`)
      return []
    return [`${protocol}//${hostname}/*`]
  }
  catch {
    return []
  }
}

/**
 * Multi-platform publishing asks for these only when first used, so installing
 * or updating the extension requests nothing new. MV2 (Firefox) has no
 * optional_host_permissions, so hosts go into optional_permissions there.
 */
function getPublishPermissions(browser: string, manifestVersion: 2 | 3) {
  const hosts = [...new Set(PUBLISH_PLATFORMS.flatMap(platform => platform.hostPermissions))]
  const apis = PUBLISH_API_PERMISSIONS.filter(permission => permission !== `tabGroups` || browser === `chrome` || browser === `edge`)
  return manifestVersion === 2
    ? { optional_permissions: [...apis, ...hosts] }
    : { optional_permissions: [...apis], optional_host_permissions: hosts }
}

export default defineConfig({
  srcDir: `src`,
  modulesDir: `src/modules`,
  manifest: ({ mode, browser, manifestVersion }) => ({
    name: `公众号内容编辑器`,
    version,
    icons: {
      256: mode === `development` ? `/mpmd/icon-256-gray.png` : `/mpmd/icon-256.png`,
    },
    permissions: [`storage`, `activeTab`, `sidePanel`, `contextMenus`, `identity`],
    host_permissions: [
      ...getApiHostPermissions(mode),
      `https://*.github.com/*`,
      `https://*.githubusercontent.com/*`,
      `https://*.gitee.com/*`,
      `https://*.weixin.qq.com/*`,
      // WeChat public account images (qpic CDN)
      `https://*.qpic.cn/*`,
      `https://www.plantuml.com/*`,
    ],
    ...getPublishPermissions(browser, manifestVersion),
    web_accessible_resources: [
      {
        resources: [`*.png`, `*.svg`, `injected.js`],
        matches: [`<all_urls>`],
      },
    ],
    side_panel: browser === `chrome`
      ? {
          default_path: `sidepanel.html`,
        }
      : undefined,
    sidebar_action: browser === `firefox`
      ? {
          default_panel: `sidepanel.html`,
          default_icon: {
            256: `mpmd/icon-256.png`,
          },
          default_title: `MD 公众号编辑器`,
        }
      : undefined,
    commands: {
      _execute_sidebar_action: {
        description: `Open MD Editor Side Panel`,
        suggested_key: {
          default: `Ctrl+Shift+Y`,
        },
      },
    },
  }),
  zip: {
    excludeSources: [
      `dist/**`,
      `docker/**`,
      `docs/**`,
      `scripts/**`,
    ],
  },
  analysis: {
    open: true,
  },
  vite: ({ mode }) => {
    const config = ViteConfig({ mode } as ConfigEnv)

    return {
      ...config,
      plugins: config.plugins!.filter((plugin) => {
        if (typeof plugin === `object` && plugin != null && `name` in plugin && plugin?.name === `vite-plugin-Radar`) {
          return false
        }
        return true
      }),
      define: undefined,
      build: undefined,
      base: `/`,
    }
  },
})
