import { describe, expect, it } from 'vitest'
import { createPublishAgent } from './index'
import { AGENT_PROTOCOL_VERSION } from './protocol'

const article = { title: `t`, summary: ``, markdown: ``, html: ``, wechatHtml: ``, textLength: 0 }

describe(`createPublishAgent`, () => {
  const agent = createPublishAgent({
    zhihu: {
      steps: {
        fill: async () => ({ kind: `navigate`, url: `https://a.io/`, step: `fill` }),
        boom: async () => {
          throw new Error(`selector exploded`)
        },
      },
    },
  })

  it(`dispatches to the platform step`, async () => {
    await expect(agent.run({ version: AGENT_PROTOCOL_VERSION, platform: `zhihu`, step: `fill`, article }))
      .resolves
      .toEqual({ kind: `navigate`, url: `https://a.io/`, step: `fill` })
  })

  it(`rejects requests from another protocol version`, async () => {
    await expect(agent.run({ version: AGENT_PROTOCOL_VERSION + 1, platform: `zhihu`, step: `fill`, article }))
      .resolves
      .toMatchObject({ kind: `error`, code: `version-mismatch` })
  })

  it(`reports unknown steps and platforms`, async () => {
    await expect(agent.run({ version: AGENT_PROTOCOL_VERSION, platform: `zhihu`, step: `nope`, article }))
      .resolves
      .toMatchObject({ kind: `error`, code: `unknown-step` })
    await expect(agent.run({ version: AGENT_PROTOCOL_VERSION, platform: `csdn`, step: `fill`, article }))
      .resolves
      .toMatchObject({ kind: `error`, code: `unknown-step` })
  })

  it(`turns exceptions into structured errors`, async () => {
    await expect(agent.run({ version: AGENT_PROTOCOL_VERSION, platform: `zhihu`, step: `boom`, article }))
      .resolves
      .toEqual({ kind: `error`, code: `exception`, message: `selector exploded` })
  })
})
