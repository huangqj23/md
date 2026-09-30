import type { PublishPlatformId } from '@/publish-agent/protocol'

/** How a platform receives the article (shown to the user). */
export type ContentFormat = `markdown` | `html` | `wechat-html`

export type LoginState = `logged-in` | `logged-out` | `unknown`

export interface LoginInfo {
  state: LoginState
  name?: string
  avatar?: string
}

export interface DetectContext {
  /** Credentialed GET from the extension page; the platform's own cookies are sent. */
  fetchJson: (url: string) => Promise<{ status: number, data: unknown }>
  fetchText: (url: string) => Promise<{ status: number, url: string, text: string }>
  /** Cookie lookup; null when the cookies permission is unavailable. */
  getCookie: ((url: string, name: string) => Promise<string | null>) | null
}

export interface PublishPlatform {
  id: PublishPlatformId
  /** Opened by the "log in / open" link. */
  homeUrl: string
  /** First page the publisher opens; the agent starts with `startStep` there. */
  startUrl: string
  startStep: `fill` | `prepare`
  /** Match patterns covering every page and endpoint this platform touches. */
  hostPermissions: string[]
  /** Endpoint probed by `detectLogin` (kept here so permission coverage is testable). */
  loginCheckUrl: string
  /** A tab that lands on a URL like this was bounced to a login page. */
  loginUrlPattern: RegExp
  format: ContentFormat
  /** The platform rejects or trims longer titles. */
  titleMaxLength?: number
  /** Saves the draft on its own once content is in the editor. */
  autosave: boolean
  detectLogin: (ctx: DetectContext) => Promise<LoginInfo>
}

type Json = Record<string, any>

function asJson(data: unknown): Json {
  return data && typeof data === `object` ? data as Json : {}
}

function loggedIn(name?: unknown, avatar?: unknown): LoginInfo {
  return {
    state: `logged-in`,
    name: typeof name === `string` && name ? name : undefined,
    avatar: typeof avatar === `string` && avatar ? avatar : undefined,
  }
}

const LOGGED_OUT: LoginInfo = { state: `logged-out` }

// Login probes mirror doocs/cose `packages/detection` (Apache-2.0).
export const PUBLISH_PLATFORMS: readonly PublishPlatform[] = [
  {
    id: `wechat`,
    homeUrl: `https://mp.weixin.qq.com/`,
    startUrl: `https://mp.weixin.qq.com/`,
    startStep: `prepare`,
    hostPermissions: [`https://mp.weixin.qq.com/*`],
    loginCheckUrl: `https://mp.weixin.qq.com/`,
    loginUrlPattern: /mp\.weixin\.qq\.com\/cgi-bin\/(?:loginpage|bizlogin)/,
    format: `wechat-html`,
    titleMaxLength: 64,
    autosave: false,
    async detectLogin(ctx) {
      const { url, text } = await ctx.fetchText(this.loginCheckUrl)
      if (/[?&]token=\d+/.test(url) || /nick_name\s*[:=]/.test(text))
        return loggedIn(text.match(/nick_name\s*[:=]\s*["']([^"']+)["']/)?.[1], text.match(/head_img\s*[:=]\s*["']([^"']+)["']/)?.[1])
      if (/扫码登录|请使用微信扫描|使用账号登录/.test(text))
        return LOGGED_OUT
      return { state: `unknown` }
    },
  },
  {
    id: `zhihu`,
    homeUrl: `https://www.zhihu.com/signin`,
    startUrl: `https://zhuanlan.zhihu.com/write`,
    startStep: `fill`,
    hostPermissions: [`https://*.zhihu.com/*`],
    loginCheckUrl: `https://www.zhihu.com/api/v4/me`,
    loginUrlPattern: /zhihu\.com\/(?:signin|signup)/,
    format: `markdown`,
    titleMaxLength: 100,
    autosave: true,
    async detectLogin(ctx) {
      const { status, data } = await ctx.fetchJson(this.loginCheckUrl)
      const me = asJson(data)
      if (status === 200 && me.id)
        return loggedIn(me.name, me.avatar_url)
      return status === 401 || status === 403 ? LOGGED_OUT : { state: `unknown` }
    },
  },
  {
    id: `csdn`,
    homeUrl: `https://passport.csdn.net/login`,
    startUrl: `https://editor.csdn.net/md/`,
    startStep: `fill`,
    hostPermissions: [`https://*.csdn.net/*`],
    loginCheckUrl: `https://www.csdn.net/`,
    loginUrlPattern: /passport\.csdn\.net/,
    format: `markdown`,
    titleMaxLength: 100,
    autosave: false,
    async detectLogin(ctx) {
      // CSDN has no simple unsigned "me" endpoint; its session cookies are the signal.
      if (!ctx.getCookie)
        return { state: `unknown` }
      const userName = await ctx.getCookie(this.loginCheckUrl, `UserName`)
      if (!userName)
        return LOGGED_OUT
      const nick = await ctx.getCookie(this.loginCheckUrl, `UserNick`)
      return loggedIn(nick ? decodeURIComponent(nick) : userName)
    },
  },
  {
    id: `juejin`,
    homeUrl: `https://juejin.cn/login`,
    startUrl: `https://juejin.cn/editor/drafts/new?v=2`,
    startStep: `fill`,
    hostPermissions: [`https://*.juejin.cn/*`],
    loginCheckUrl: `https://api.juejin.cn/user_api/v1/user/get`,
    loginUrlPattern: /juejin\.cn\/login/,
    format: `markdown`,
    titleMaxLength: 100,
    autosave: true,
    async detectLogin(ctx) {
      const { data } = await ctx.fetchJson(this.loginCheckUrl)
      const body = asJson(data)
      if (body.err_no === 0 && body.data?.user_id)
        return loggedIn(body.data.user_name, body.data.avatar_large)
      return typeof body.err_no === `number` ? LOGGED_OUT : { state: `unknown` }
    },
  },
  {
    id: `toutiao`,
    homeUrl: `https://mp.toutiao.com/`,
    startUrl: `https://mp.toutiao.com/profile_v4/graphic/publish`,
    startStep: `fill`,
    hostPermissions: [`https://*.toutiao.com/*`],
    loginCheckUrl: `https://mp.toutiao.com/mp/agw/creator_center/user_info?app_id=1231`,
    loginUrlPattern: /toutiao\.com\/auth\/page\/login|sso\.toutiao\.com/,
    format: `html`,
    titleMaxLength: 30,
    autosave: true,
    async detectLogin(ctx) {
      const { data } = await ctx.fetchJson(this.loginCheckUrl)
      const body = asJson(data)
      if (body.code === 0 && body.name)
        return loggedIn(body.name, body.avatar_url)
      return typeof body.code === `number` ? LOGGED_OUT : { state: `unknown` }
    },
  },
  {
    id: `baijiahao`,
    homeUrl: `https://baijiahao.baidu.com/`,
    startUrl: `https://baijiahao.baidu.com/builder/rc/edit?type=news`,
    startStep: `fill`,
    hostPermissions: [`https://baijiahao.baidu.com/*`],
    loginCheckUrl: `https://baijiahao.baidu.com/builder/app/appinfo`,
    loginUrlPattern: /baijiahao\.baidu\.com\/builder\/theme\/bjh\/login|passport\.baidu\.com/,
    format: `wechat-html`,
    titleMaxLength: 64,
    autosave: false,
    async detectLogin(ctx) {
      const { data } = await ctx.fetchJson(this.loginCheckUrl)
      const body = asJson(data)
      if (body.errno === 0 && body.data?.user?.name)
        return loggedIn(body.data.user.name, body.data.user.avatar)
      return typeof body.errno === `number` ? LOGGED_OUT : { state: `unknown` }
    },
  },
  {
    id: `jianshu`,
    homeUrl: `https://www.jianshu.com/sign_in`,
    startUrl: `https://www.jianshu.com/writer`,
    startStep: `prepare`,
    hostPermissions: [`https://*.jianshu.com/*`],
    loginCheckUrl: `https://www.jianshu.com/settings/basic.json`,
    loginUrlPattern: /jianshu\.com\/sign_in/,
    format: `markdown`,
    autosave: true,
    async detectLogin(ctx) {
      const { status, data } = await ctx.fetchJson(this.loginCheckUrl)
      const body = asJson(data)
      if (status === 200 && body.data)
        return loggedIn(body.data.nickname, body.data.avatar)
      return status === 401 || status === 403 ? LOGGED_OUT : { state: `unknown` }
    },
  },
  {
    id: `bilibili`,
    homeUrl: `https://passport.bilibili.com/login`,
    startUrl: `https://member.bilibili.com/article-text/home?newEditor=-1`,
    startStep: `fill`,
    hostPermissions: [`https://*.bilibili.com/*`],
    loginCheckUrl: `https://api.bilibili.com/x/web-interface/nav`,
    loginUrlPattern: /passport\.bilibili\.com/,
    format: `wechat-html`,
    titleMaxLength: 40,
    autosave: false,
    async detectLogin(ctx) {
      const { data } = await ctx.fetchJson(this.loginCheckUrl)
      const body = asJson(data)
      if (body.code === 0 && body.data?.isLogin)
        return loggedIn(body.data.uname, body.data.face)
      return typeof body.code === `number` ? LOGGED_OUT : { state: `unknown` }
    },
  },
  {
    id: `cnblogs`,
    homeUrl: `https://account.cnblogs.com/signin`,
    startUrl: `https://i.cnblogs.com/posts/edit`,
    startStep: `fill`,
    hostPermissions: [`https://*.cnblogs.com/*`],
    loginCheckUrl: `https://account.cnblogs.com/user/userinfo`,
    loginUrlPattern: /account\.cnblogs\.com\/signin|passport\.cnblogs\.com/,
    format: `markdown`,
    autosave: false,
    async detectLogin(ctx) {
      const { status, data } = await ctx.fetchJson(this.loginCheckUrl)
      const body = asJson(data)
      if (status === 200 && body.spaceUserId)
        return loggedIn(body.displayName, typeof body.iconName === `string` && body.iconName.startsWith(`//`) ? `https:${body.iconName}` : body.iconName)
      return status === 401 || status === 403 || status === 200 ? LOGGED_OUT : { state: `unknown` }
    },
  },
]

export function getPublishPlatform(id: PublishPlatformId): PublishPlatform {
  const platform = PUBLISH_PLATFORMS.find(p => p.id === id)
  if (!platform)
    throw new Error(`Unknown publish platform: ${id}`)
  return platform
}
