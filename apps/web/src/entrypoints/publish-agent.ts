import { defineUnlistedScript } from '#imports'
import { createPublishAgent } from '@/publish-agent'
import { AGENT_GLOBAL } from '@/publish-agent/protocol'

/**
 * Injected with `scripting.executeScript({ world: 'MAIN' })` into a platform's
 * editor tab by the multi-platform publisher. MAIN world is required to reach
 * page objects such as CodeMirror instances, UEditor and WeChat's editor JSAPI.
 */
export default defineUnlistedScript(() => {
  ;(window as unknown as Record<string, unknown>)[AGENT_GLOBAL] = createPublishAgent()
})
