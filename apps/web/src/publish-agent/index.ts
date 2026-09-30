import type { AgentRequest, AgentResult, PublishAgent, PublishPlatformId } from './protocol'
import type { PlatformFiller } from './result'
import { baijiahao } from './platforms/baijiahao'
import { bilibili } from './platforms/bilibili'
import { cnblogs } from './platforms/cnblogs'
import { csdn } from './platforms/csdn'
import { jianshu } from './platforms/jianshu'
import { juejin } from './platforms/juejin'
import { toutiao } from './platforms/toutiao'
import { wechat } from './platforms/wechat'
import { zhihu } from './platforms/zhihu'
import { AGENT_PROTOCOL_VERSION } from './protocol'
import { fail } from './result'

export const PLATFORM_FILLERS: Record<PublishPlatformId, PlatformFiller> = {
  wechat,
  zhihu,
  csdn,
  juejin,
  toutiao,
  baijiahao,
  jianshu,
  bilibili,
  cnblogs,
}

export function createPublishAgent(fillers: Record<string, PlatformFiller> = PLATFORM_FILLERS): PublishAgent {
  return {
    version: AGENT_PROTOCOL_VERSION,
    async run(request: AgentRequest): Promise<AgentResult> {
      if (request.version !== AGENT_PROTOCOL_VERSION)
        return fail(`version-mismatch`, `Agent speaks protocol v${AGENT_PROTOCOL_VERSION}, request is v${request.version}`)
      const step = fillers[request.platform]?.steps[request.step]
      if (!step)
        return fail(`unknown-step`, `No step "${request.step}" for ${request.platform}`)
      try {
        return await step(request.article)
      }
      catch (error) {
        return fail(`exception`, error instanceof Error ? error.message : String(error))
      }
    },
  }
}
