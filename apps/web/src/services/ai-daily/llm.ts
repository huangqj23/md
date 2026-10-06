/**
 * Model settings for the AI daily pipeline. Keys are stored by the local ai-daily host (DPAPI on Windows, the login Keychain on macOS);
 * the extension only ever sends new keys and gets masked hints back.
 */

export type LlmRoleId = `triage` | `write`
export const LLM_ROLES: LlmRoleId[] = [`triage`, `write`]

export interface LlmProvider {
  id: string
  name: string
  api: `openai` | `anthropic`
  custom: boolean
  readonly?: boolean
  base_url: string
  default_base_url: string
  requires_key: boolean
  models: string[]
  triage: string
  write: string
  key_url: string
  note: string
  has_key: boolean
  key_hint: string
}

export interface LlmRole {
  provider: string
  model: string
}

export interface LlmReadiness {
  source: `file` | `env` | `none`
  ready: boolean
  problems: string[]
  triage: string | null
  write: string | null
}

export interface LlmView {
  source: `file` | `env` | `none`
  encryption: `dpapi` | `keychain` | `plain`
  providers: LlmProvider[]
  roles: Record<LlmRoleId, LlmRole>
  readiness: LlmReadiness
}

export interface LlmTestResult {
  success: boolean
  latency_ms: number
  models: string[]
  models_error: string
  error: string
  reply_ok: boolean | null
}

/** Unsaved edits for one provider; an empty `apiKey` means "keep the saved key". */
export interface ProviderDraft {
  apiKey: string
  clearKey: boolean
  baseUrl: string
  name: string
}

export interface LlmUpdate {
  providers: Array<{ id: string, api_key?: string, clear_key?: boolean, base_url?: string, name?: string }>
  roles: Record<LlmRoleId, LlmRole>
}

export function emptyDraft(provider: LlmProvider): ProviderDraft {
  return { apiKey: ``, clearKey: false, baseUrl: provider.base_url, name: provider.name }
}

/** Only what changed goes to the host; a preset whose URL is set back to its default is sent as `''` (reset). */
export function buildLlmUpdate(view: LlmView, drafts: Record<string, ProviderDraft>, roles: Record<LlmRoleId, LlmRole>): LlmUpdate {
  const providers: LlmUpdate[`providers`] = []
  for (const provider of view.providers) {
    const draft = drafts[provider.id]
    if (!draft || provider.readonly)
      continue
    const entry: LlmUpdate[`providers`][number] = { id: provider.id }
    const key = draft.apiKey.trim()
    if (key)
      entry.api_key = key
    else if (draft.clearKey && provider.has_key)
      entry.clear_key = true
    const baseUrl = draft.baseUrl.trim()
    if (baseUrl && baseUrl !== provider.base_url)
      entry.base_url = !provider.custom && baseUrl === provider.default_base_url ? `` : baseUrl
    const name = draft.name.trim()
    if (provider.custom && name && name !== provider.name)
      entry.name = name
    if (Object.keys(entry).length > 1)
      providers.push(entry)
  }
  return { providers, roles: { triage: { ...roles.triage }, write: { ...roles.write } } }
}

/** Suggestions for the model field: the preset's list, then anything fetched by "test connection". */
export function modelSuggestions(provider: LlmProvider | undefined, fetched: string[] = []): string[] {
  return [...new Set([...(provider?.models ?? []), ...fetched])]
}

/** Default model for a role when switching provider: that provider's default for the role, or its first suggestion. */
export function defaultModel(provider: LlmProvider | undefined, role: LlmRoleId): string {
  return provider?.[role] || provider?.models[0] || ``
}
