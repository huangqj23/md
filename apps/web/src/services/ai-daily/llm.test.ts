import type { LlmProvider, LlmView } from './llm'
import { describe, expect, it } from 'vitest'
import { buildLlmUpdate, defaultModel, emptyDraft, modelSuggestions } from './llm'

function provider(over: Partial<LlmProvider>): LlmProvider {
  return {
    id: `deepseek`,
    name: `DeepSeek`,
    api: `openai`,
    custom: false,
    base_url: `https://api.deepseek.com`,
    default_base_url: `https://api.deepseek.com`,
    requires_key: true,
    models: [`deepseek-flash`, `deepseek-v4-pro`],
    triage: `deepseek-flash`,
    write: `deepseek-v4-pro`,
    key_url: ``,
    note: ``,
    has_key: false,
    key_hint: ``,
    ...over,
  }
}

const roles = { triage: { provider: `deepseek`, model: `deepseek-flash` }, write: { provider: `anthropic`, model: `claude-opus-5` } }

function view(providers: LlmProvider[]): LlmView {
  return { source: `file`, encryption: `dpapi`, providers, roles, readiness: { source: `file`, ready: false, problems: [], triage: null, write: null } }
}

describe(`buildLlmUpdate`, () => {
  it(`sends nothing for untouched providers and always sends roles`, () => {
    const p = provider({})
    expect(buildLlmUpdate(view([p]), { deepseek: emptyDraft(p) }, roles)).toEqual({ providers: [], roles })
  })

  it(`sends a typed key trimmed, and never an empty key`, () => {
    const p = provider({ has_key: true })
    const update = buildLlmUpdate(view([p]), { deepseek: { ...emptyDraft(p), apiKey: `  sk-new  ` } }, roles)
    expect(update.providers).toEqual([{ id: `deepseek`, api_key: `sk-new` }])
  })

  it(`clears a saved key only when asked and only if one exists`, () => {
    const saved = provider({ has_key: true })
    expect(buildLlmUpdate(view([saved]), { deepseek: { ...emptyDraft(saved), clearKey: true } }, roles).providers)
      .toEqual([{ id: `deepseek`, clear_key: true }])
    const none = provider({ has_key: false })
    expect(buildLlmUpdate(view([none]), { deepseek: { ...emptyDraft(none), clearKey: true } }, roles).providers).toEqual([])
  })

  it(`resets a preset URL with '' and keeps custom URLs and names as typed`, () => {
    const overridden = provider({ base_url: `https://proxy.example.com/v1` })
    expect(buildLlmUpdate(view([overridden]), { deepseek: { ...emptyDraft(overridden), baseUrl: `https://api.deepseek.com` } }, roles).providers)
      .toEqual([{ id: `deepseek`, base_url: `` }])

    const custom = provider({ id: `custom-1`, name: `vLLM`, custom: true, base_url: `http://10.0.0.2:8000/v1`, default_base_url: `` })
    expect(buildLlmUpdate(view([custom]), { 'custom-1': { ...emptyDraft(custom), baseUrl: `http://10.0.0.3:8000/v1`, name: ` 4090 vLLM ` } }, roles).providers)
      .toEqual([{ id: `custom-1`, base_url: `http://10.0.0.3:8000/v1`, name: `4090 vLLM` }])
  })

  it(`skips read-only providers (the .env fallback)`, () => {
    const env = provider({ id: `env`, custom: true, readonly: true })
    expect(buildLlmUpdate(view([env]), { env: { ...emptyDraft(env), apiKey: `sk-x` } }, roles).providers).toEqual([])
  })
})

describe(`model helpers`, () => {
  it(`merges preset and fetched model names without duplicates`, () => {
    expect(modelSuggestions(provider({}), [`deepseek-v4-pro`, `deepseek-v4-pro-0813`])).toEqual([`deepseek-flash`, `deepseek-v4-pro`, `deepseek-v4-pro-0813`])
    expect(modelSuggestions(undefined)).toEqual([])
  })

  it(`picks the role default, then the first suggestion`, () => {
    expect(defaultModel(provider({}), `write`)).toBe(`deepseek-v4-pro`)
    expect(defaultModel(provider({ triage: ``, write: ``, models: [`m1`] }), `triage`)).toBe(`m1`)
    expect(defaultModel(provider({ triage: ``, write: ``, models: [] }), `triage`)).toBe(``)
  })
})
