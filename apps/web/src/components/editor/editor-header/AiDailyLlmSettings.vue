<script setup lang="ts">
import type { LlmProvider, LlmRole, LlmRoleId, LlmTestResult, ProviderDraft } from '@/services/ai-daily/llm'
import { CircleCheck, CircleX, ExternalLink, KeyRound, Loader2, Plus, Trash2, TriangleAlert } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PasswordInput } from '@/components/ui/password-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { buildLlmUpdate, defaultModel, emptyDraft, LLM_ROLES, modelSuggestions } from '@/services/ai-daily/llm'
import { useAiDailyStore } from '@/stores/aiDaily'

const { t } = useI18n()
const aiDaily = useAiDailyStore()
const { llm } = storeToRefs(aiDaily)

const roles = ref<Record<LlmRoleId, LlmRole>>({ triage: { provider: ``, model: `` }, write: { provider: ``, model: `` } })
const drafts = ref<Record<string, ProviderDraft>>({})
const editingId = ref(``)
const fetched = ref<Record<string, string[]>>({})
const testModel = ref(``)
const testing = ref(false)
const testResult = ref<LlmTestResult | null>(null)
const saving = ref(false)
const adding = ref(false)
const newName = ref(``)
const newUrl = ref(``)
const newKey = ref(``)

const providers = computed(() => llm.value?.providers ?? [])
const editing = computed(() => providers.value.find(p => p.id === editingId.value))
const draft = computed(() => (editing.value ? ensureDraft(editing.value) : null))

function providerById(id: string): LlmProvider | undefined {
  return providers.value.find(p => p.id === id)
}

function ensureDraft(provider: LlmProvider): ProviderDraft {
  if (!drafts.value[provider.id])
    drafts.value[provider.id] = emptyDraft(provider)
  return drafts.value[provider.id]!
}

function suggestions(providerId: string): string[] {
  return modelSuggestions(providerById(providerId), fetched.value[providerId])
}

function resetFromView() {
  if (!llm.value)
    return
  roles.value = { triage: { ...llm.value.roles.triage }, write: { ...llm.value.roles.write } }
  drafts.value = {}
  if (!providerById(editingId.value))
    editingId.value = roles.value.triage.provider
  prepareTest()
}

function prepareTest() {
  testResult.value = null
  const used = LLM_ROLES.find(role => roles.value[role].provider === editingId.value)
  testModel.value = used ? roles.value[used].model : defaultModel(editing.value, `write`)
}

onMounted(async () => {
  await aiDaily.loadLlm()
  resetFromView()
})

watch(editingId, prepareTest)

function onRoleProvider(role: LlmRoleId, value: unknown) {
  if (typeof value !== `string`)
    return
  roles.value[role] = { provider: value, model: defaultModel(providerById(value), role) }
  editingId.value = value
}

function providerLabel(provider: LlmProvider): string {
  const ready = provider.has_key || !provider.requires_key
  return ready ? `${provider.name} ✓` : provider.name
}

async function test() {
  const provider = editing.value
  const d = draft.value
  if (!provider || !d)
    return
  testing.value = true
  testResult.value = null
  try {
    const request: { provider: string, model: string, api_key?: string, base_url?: string } = { provider: provider.id, model: testModel.value.trim() }
    if (d.apiKey.trim())
      request.api_key = d.apiKey.trim()
    if (d.baseUrl.trim() && d.baseUrl.trim() !== provider.base_url)
      request.base_url = d.baseUrl.trim()
    const result = await aiDaily.testLlm(request)
    testResult.value = result
    if (result?.models.length)
      fetched.value = { ...fetched.value, [provider.id]: result.models }
  }
  finally {
    testing.value = false
  }
}

async function save() {
  if (!llm.value)
    return
  saving.value = true
  try {
    if (await aiDaily.saveLlm(buildLlmUpdate(llm.value, drafts.value, roles.value))) {
      toast.success(t(`aiDaily.llm.saved`))
      resetFromView()
    }
  }
  finally {
    saving.value = false
  }
}

async function addCustom() {
  if (!newName.value.trim() || !newUrl.value.trim()) {
    toast.error(t(`aiDaily.llm.customMissing`))
    return
  }
  const id = await aiDaily.addCustomProvider(newName.value.trim(), newUrl.value.trim(), newKey.value.trim())
  if (!id)
    return
  adding.value = false
  newName.value = ``
  newUrl.value = ``
  newKey.value = ``
  editingId.value = id
}

async function removeCustom() {
  const provider = editing.value
  if (!provider?.custom || !(await aiDaily.removeCustomProvider(provider.id)))
    return
  resetFromView()
  editingId.value = roles.value.triage.provider
}
</script>

<template>
  <div v-if="!llm" class="flex items-center gap-2 p-4 text-sm text-muted-foreground">
    <Loader2 class="h-4 w-4 animate-spin" />
    {{ t('aiDaily.llm.loading') }}
  </div>

  <div v-else class="flex flex-col gap-4">
    <!-- Which model does what -->
    <section class="rounded-md border p-3 flex flex-col gap-3">
      <div>
        <p class="text-sm font-medium">
          {{ t('aiDaily.llm.rolesTitle') }}
        </p>
        <p class="text-xs text-muted-foreground">
          {{ t('aiDaily.llm.rolesHint') }}
        </p>
      </div>
      <div v-for="role in LLM_ROLES" :key="role" class="grid grid-cols-1 gap-2 sm:grid-cols-[5rem_minmax(0,1fr)_minmax(0,1fr)] sm:items-center">
        <Label>{{ t(`aiDaily.llm.role.${role}`) }}</Label>
        <Select :model-value="roles[role].provider" @update:model-value="value => onRoleProvider(role, value)">
          <SelectTrigger class="h-9 w-full">
            <SelectValue>{{ providerById(roles[role].provider)?.name ?? roles[role].provider }}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem v-for="provider in providers" :key="provider.id" :value="provider.id">
              {{ providerLabel(provider) }}
            </SelectItem>
          </SelectContent>
        </Select>
        <Input v-model="roles[role].model" :list="`ai-daily-models-${role}`" :placeholder="t('aiDaily.llm.modelPlaceholder')" class="h-9" />
        <datalist :id="`ai-daily-models-${role}`">
          <option v-for="model in suggestions(roles[role].provider)" :key="model" :value="model" />
        </datalist>
      </div>
    </section>

    <!-- One provider's endpoint and key -->
    <section class="rounded-md border p-3 flex flex-col gap-3">
      <div class="flex flex-wrap items-center gap-2">
        <KeyRound class="h-4 w-4 text-muted-foreground" />
        <span class="text-sm font-medium">{{ t('aiDaily.llm.providerTitle') }}</span>
        <Select v-model="editingId">
          <SelectTrigger class="h-8 w-56">
            <SelectValue>{{ editing?.name }}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem v-for="provider in providers" :key="provider.id" :value="provider.id">
              {{ providerLabel(provider) }}
            </SelectItem>
          </SelectContent>
        </Select>
        <a v-if="editing?.key_url" :href="editing.key_url" target="_blank" rel="noopener noreferrer" class="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline">
          {{ t('aiDaily.llm.getKey') }}
          <ExternalLink class="h-3 w-3" />
        </a>
      </div>

      <template v-if="editing && draft">
        <p v-if="editing.note" class="text-xs text-muted-foreground">
          {{ editing.note }}
        </p>
        <p v-if="editing.readonly" class="text-xs text-amber-700 dark:text-amber-400">
          {{ t('aiDaily.llm.envReadonly') }}
        </p>
        <template v-else>
          <div v-if="editing.custom" class="grid grid-cols-1 gap-1.5">
            <Label :for="`ai-daily-name-${editing.id}`">{{ t('aiDaily.llm.name') }}</Label>
            <Input :id="`ai-daily-name-${editing.id}`" v-model="draft.name" class="h-9" />
          </div>
          <div class="grid grid-cols-1 gap-1.5">
            <div class="flex items-center justify-between">
              <Label :for="`ai-daily-url-${editing.id}`">{{ t('aiDaily.llm.baseUrl') }}</Label>
              <button
                v-if="!editing.custom && draft.baseUrl !== editing.default_base_url"
                type="button"
                class="text-xs text-muted-foreground hover:text-foreground hover:underline"
                @click="draft.baseUrl = editing.default_base_url"
              >
                {{ t('aiDaily.llm.resetUrl') }}
              </button>
            </div>
            <Input :id="`ai-daily-url-${editing.id}`" v-model="draft.baseUrl" class="h-9 font-mono text-xs" />
          </div>
          <div class="grid grid-cols-1 gap-1.5">
            <Label>
              {{ t('aiDaily.llm.apiKey') }}
              <span v-if="!editing.requires_key" class="font-normal text-muted-foreground">{{ t('aiDaily.llm.keyOptional') }}</span>
            </Label>
            <PasswordInput
              v-model="draft.apiKey"
              class="h-9"
              :placeholder="editing.has_key ? t('aiDaily.llm.keySaved', { hint: editing.key_hint }) : t('aiDaily.llm.keyPlaceholder')"
            />
            <label v-if="editing.has_key" class="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input v-model="draft.clearKey" type="checkbox" class="h-3.5 w-3.5">
              {{ t('aiDaily.llm.clearKey') }}
            </label>
          </div>
        </template>

        <div class="flex flex-wrap items-center gap-2">
          <Input v-model="testModel" list="ai-daily-models-test" :placeholder="t('aiDaily.llm.testModel')" class="h-8 w-56" />
          <datalist id="ai-daily-models-test">
            <option v-for="model in suggestions(editing.id)" :key="model" :value="model" />
          </datalist>
          <Button size="sm" variant="outline" :disabled="testing" @click="test">
            <Loader2 v-if="testing" class="mr-1 h-3.5 w-3.5 animate-spin" />
            {{ t('aiDaily.llm.test') }}
          </Button>
          <Button v-if="editing.custom && !editing.readonly" size="sm" variant="ghost" class="ml-auto text-red-600 dark:text-red-400" @click="removeCustom">
            <Trash2 class="mr-1 h-3.5 w-3.5" />
            {{ t('aiDaily.llm.removeCustom') }}
          </Button>
        </div>
        <div v-if="testResult" class="rounded bg-muted/60 px-2 py-1.5 text-xs" aria-live="polite">
          <p class="flex items-center gap-1.5 font-medium">
            <CircleCheck v-if="testResult.success" class="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
            <CircleX v-else class="h-3.5 w-3.5 text-red-600 dark:text-red-400" />
            {{ testResult.success ? t('aiDaily.llm.testOk', { ms: testResult.latency_ms }) : t('aiDaily.llm.testFailed') }}
          </p>
          <p v-if="testResult.error" class="mt-0.5 break-all text-red-700 dark:text-red-400">
            {{ testResult.error }}
          </p>
          <p class="mt-0.5 text-muted-foreground">
            {{ testResult.models.length ? t('aiDaily.llm.modelsFound', { count: testResult.models.length }) : t('aiDaily.llm.modelsNone') }}
          </p>
        </div>
      </template>

      <div class="border-t pt-2">
        <button v-if="!adding" type="button" class="inline-flex items-center gap-1 text-xs text-primary hover:underline" @click="adding = true">
          <Plus class="h-3.5 w-3.5" />
          {{ t('aiDaily.llm.addCustom') }}
        </button>
        <div v-else class="flex flex-col gap-2">
          <p class="text-xs text-muted-foreground">
            {{ t('aiDaily.llm.addCustomHint') }}
          </p>
          <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Input v-model="newName" :placeholder="t('aiDaily.llm.name')" class="h-9" />
            <Input v-model="newUrl" placeholder="https://… /v1" class="h-9 font-mono text-xs" />
          </div>
          <PasswordInput v-model="newKey" class="h-9" :placeholder="t('aiDaily.llm.keyOptionalPlaceholder')" />
          <div class="flex gap-2">
            <Button size="sm" @click="addCustom">
              {{ t('aiDaily.llm.add') }}
            </Button>
            <Button size="sm" variant="ghost" @click="adding = false">
              {{ t('aiDaily.llm.cancel') }}
            </Button>
          </div>
        </div>
      </div>
    </section>

    <!-- Readiness and where the key lives -->
    <section class="flex flex-col gap-1 text-xs">
      <p v-if="llm.readiness.ready" class="flex items-center gap-1.5 text-green-700 dark:text-green-400">
        <CircleCheck class="h-3.5 w-3.5" />
        {{ t('aiDaily.llm.ready', { triage: llm.readiness.triage, write: llm.readiness.write }) }}
      </p>
      <template v-else>
        <p v-for="problem in llm.readiness.problems" :key="problem" class="flex items-center gap-1.5 text-amber-700 dark:text-amber-400">
          <TriangleAlert class="h-3.5 w-3.5" />
          {{ problem }}
        </p>
      </template>
      <p v-if="llm.source === 'env'" class="text-muted-foreground">
        {{ t('aiDaily.llm.sourceEnv') }}
      </p>
      <p class="text-muted-foreground">
        {{ t(llm.encryption === 'dpapi' ? 'aiDaily.llm.storageDpapi' : llm.encryption === 'keychain' ? 'aiDaily.llm.storageKeychain' : 'aiDaily.llm.storagePlain') }}
      </p>
    </section>

    <div class="flex justify-end">
      <Button :disabled="saving" @click="save">
        <Loader2 v-if="saving" class="mr-2 h-4 w-4 animate-spin" />
        {{ t('aiDaily.llm.save') }}
      </Button>
    </div>
  </div>
</template>
