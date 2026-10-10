<script setup lang="ts">
import { CircleCheck, CircleX, Copy, ExternalLink, FileText, Inbox, Loader2, PlugZap, RefreshCw, ShieldCheck, TriangleAlert } from '@lucide/vue'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatLocalDateTime } from '@/i18n/translate'
import { installCommand, localDate } from '@/services/ai-daily/native'
import { useAiDailyStore } from '@/stores/aiDaily'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const aiDaily = useAiDailyStore()
const { date, connection, connectionDetail, status, inbox, loading, job, extensionId } = storeToRefs(aiDaily)

const dialogOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit(`update:open`, value),
})

const skipLinks = ref(false)
const problems = ref<string[]>([])
const showLog = ref(false)
const tab = ref<`today` | `models`>(`today`)
const AiDailyLlmSettings = defineAsyncComponent(() => import('./AiDailyLlmSettings.vue'))
const inboxUrl = ref(``)
const inboxNote = ref(``)
const copied = ref(false)

const article = computed(() => status.value?.article ?? null)
const jobRunning = computed(() => job.value.state === `running`)
const jobKind = computed(() => t(`aiDaily.job.kindPublish`))
const blocking = computed(() => article.value?.problems ?? [])
const canLoad = computed(() => !!status.value?.embed && !status.value.embed.stale)
const command = computed(() => installCommand(extensionId.value))
const hostProblem = computed(() => [`not-installed`, `forbidden`, `host-error`].includes(connection.value))

onMounted(() => {
  void aiDaily.refresh()
})

watch(date, () => {
  problems.value = []
  void aiDaily.refresh()
})

watch(() => article.value, (value) => {
  if (!value)
    problems.value = []
})

function formatTime(value?: string | null) {
  return value ? formatLocalDateTime(value) : ``
}

async function copyCommand() {
  await navigator.clipboard.writeText(command.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

async function buildEmbed() {
  const result = await aiDaily.startPublish({ skipLinks: skipLinks.value })
  if (result === null)
    return
  problems.value = result
  if (result.length > 0)
    toast.error(t(`aiDaily.actions.publishBlocked`))
  else
    showLog.value = true
}

async function load() {
  if (await aiDaily.loadIntoEditor()) {
    toast.success(t(`aiDaily.actions.loaded`))
    dialogOpen.value = false
  }
}

async function addInbox() {
  const url = inboxUrl.value.trim()
  if (!/^https?:\/\/\S+$/i.test(url)) {
    toast.error(t(`aiDaily.inbox.invalidUrl`))
    return
  }
  if (await aiDaily.addToInbox(url, inboxNote.value.trim())) {
    inboxUrl.value = ``
    inboxNote.value = ``
  }
}
</script>

<template>
  <Dialog v-model:open="dialogOpen">
    <DialogContent class="!max-w-[95vw] !w-[min(680px,95vw)] max-h-[88vh] flex flex-col overflow-hidden">
      <DialogHeader>
        <DialogTitle>{{ t('aiDaily.title') }}</DialogTitle>
        <DialogDescription class="text-left leading-relaxed">
          {{ t('aiDaily.description') }}
        </DialogDescription>
      </DialogHeader>

      <div class="flex-1 overflow-y-auto p-1 flex flex-col gap-4">
        <Alert v-if="connection === 'unsupported'">
          <TriangleAlert class="h-4 w-4" />
          <AlertTitle>{{ t('aiDaily.unsupported.title') }}</AlertTitle>
          <AlertDescription>{{ t('aiDaily.unsupported.description') }}</AlertDescription>
        </Alert>

        <Alert v-else-if="connection === 'no-permission'">
          <ShieldCheck class="h-4 w-4" />
          <AlertTitle>{{ t('aiDaily.permission.title') }}</AlertTitle>
          <AlertDescription class="flex flex-col items-start gap-2">
            <span>{{ t('aiDaily.permission.description') }}</span>
            <Button size="sm" @click="aiDaily.grant()">
              {{ t('aiDaily.permission.grant') }}
            </Button>
          </AlertDescription>
        </Alert>

        <Alert v-else-if="hostProblem">
          <PlugZap class="h-4 w-4" />
          <AlertTitle>
            {{ connection === 'forbidden' ? t('aiDaily.host.forbiddenTitle') : connection === 'host-error' ? t('aiDaily.host.errorTitle') : t('aiDaily.host.notInstalledTitle') }}
          </AlertTitle>
          <AlertDescription class="flex flex-col items-start gap-2">
            <span>{{ t('aiDaily.host.steps') }}</span>
            <code class="block w-full break-all rounded bg-muted px-2 py-1 text-xs">{{ command }}</code>
            <span class="text-xs text-muted-foreground">{{ t('aiDaily.host.extensionId', { id: extensionId }) }}</span>
            <span v-if="connectionDetail" class="text-xs text-muted-foreground break-all">{{ connectionDetail }}</span>
            <div class="flex gap-2">
              <Button size="sm" variant="outline" @click="copyCommand">
                <Copy class="mr-2 h-3.5 w-3.5" />
                {{ copied ? t('aiDaily.host.copied') : t('aiDaily.host.copy') }}
              </Button>
              <Button size="sm" @click="aiDaily.refresh()">
                {{ t('aiDaily.host.retry') }}
              </Button>
            </div>
          </AlertDescription>
        </Alert>

        <Tabs v-if="connection === 'ok' || connection === 'unknown'" v-model="tab" class="gap-3">
          <TabsList class="grid w-full grid-cols-2">
            <TabsTrigger value="today">
              {{ t('aiDaily.tabs.today') }}
            </TabsTrigger>
            <TabsTrigger value="models">
              {{ t('aiDaily.tabs.models') }}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="models" class="mt-0">
            <AiDailyLlmSettings v-if="tab === 'models'" />
          </TabsContent>

          <TabsContent value="today" class="mt-0 flex flex-col gap-4">
            <div class="flex flex-wrap items-center gap-2">
              <Label for="ai-daily-date">{{ t('aiDaily.date') }}</Label>
              <Input id="ai-daily-date" v-model="date" type="date" class="h-8 w-40" />
              <Button variant="ghost" size="sm" class="h-8" @click="date = localDate()">
                {{ t('aiDaily.today') }}
              </Button>
              <Button variant="ghost" size="sm" class="h-8 ml-auto" :disabled="loading" @click="aiDaily.refresh()">
                <RefreshCw class="mr-1 h-3.5 w-3.5" :class="{ 'animate-spin': loading }" />
                {{ t('aiDaily.refresh') }}
              </Button>
            </div>

            <!-- Draft -->
            <section class="rounded-md border p-3 flex flex-col gap-2">
              <div class="flex flex-wrap items-center gap-2">
                <FileText class="h-4 w-4 text-muted-foreground" />
                <span class="text-sm font-medium">{{ t('aiDaily.draft.label') }}</span>
                <span v-if="article" class="text-xs text-muted-foreground">{{ t('aiDaily.draft.modified', { time: formatTime(article.modified) }) }}</span>
              </div>
              <p v-if="!article" class="text-sm text-muted-foreground">
                {{ t('aiDaily.draft.none') }}
              </p>
              <template v-else>
                <p class="text-sm font-medium break-words">
                  {{ article.title }}
                </p>
                <ul class="flex flex-col gap-1 text-sm">
                  <li class="flex items-center gap-2">
                    <CircleCheck v-if="article.opinion_written" class="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                    <CircleX v-else class="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                    {{ t('aiDaily.checks.opinion') }}：{{ article.opinion_written ? t('aiDaily.checks.opinionDone') : t('aiDaily.checks.opinionTodo') }}
                  </li>
                  <li class="flex items-start gap-2">
                    <CircleCheck v-if="article.flags.length === 0" class="mt-0.5 h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                    <TriangleAlert v-else class="mt-0.5 h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                    <div class="min-w-0">
                      {{ article.flags.length === 0 ? t('aiDaily.checks.flagsNone') : t('aiDaily.checks.flags', { count: article.flags.length }) }}
                      <ul v-if="article.flags.length" class="mt-1 flex flex-col gap-0.5 text-xs text-muted-foreground">
                        <li v-for="(flag, i) in article.flags" :key="i" class="break-words">
                          <span class="font-medium text-foreground">{{ flag.section }}</span>：{{ flag.text }}
                        </li>
                      </ul>
                    </div>
                  </li>
                  <li class="flex items-center gap-2">
                    <CircleCheck v-if="canLoad" class="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                    <CircleX v-else class="h-3.5 w-3.5 text-muted-foreground" />
                    {{ t('aiDaily.checks.embed') }}：{{ !status?.embed ? t('aiDaily.checks.embedNone') : status.embed.stale ? t('aiDaily.checks.embedStale') : t('aiDaily.checks.embedReady') }}
                  </li>
                  <li class="flex items-center gap-2 text-muted-foreground">
                    <CircleCheck v-if="status?.cover" class="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                    <CircleX v-else class="h-3.5 w-3.5" />
                    {{ t('aiDaily.checks.cover') }}：{{ status?.cover ? t('aiDaily.checks.coverReady') : t('aiDaily.checks.coverNone') }}
                  </li>
                  <li v-if="status?.published" class="flex items-center gap-2 text-muted-foreground">
                    <CircleCheck class="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                    {{ t('aiDaily.checks.published') }}
                  </li>
                </ul>
                <div v-if="(problems.length ? problems : blocking).length" class="rounded bg-muted/60 px-2 py-1.5 text-xs">
                  <p class="font-medium">
                    {{ t('aiDaily.checks.problems') }}
                  </p>
                  <ul class="mt-0.5 list-disc pl-4">
                    <li v-for="problem in (problems.length ? problems : blocking)" :key="problem">
                      {{ problem }}
                    </li>
                  </ul>
                </div>
                <div class="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" @click="aiDaily.open('article')">
                    <ExternalLink class="mr-1 h-3.5 w-3.5" />
                    {{ t('aiDaily.draft.openArticle') }}
                  </Button>
                  <Button v-if="status?.review" variant="outline" size="sm" @click="aiDaily.open('review')">
                    {{ t('aiDaily.draft.openReview') }}
                  </Button>
                </div>
              </template>
            </section>

            <!-- Job: only the embed build runs here; older draft-generation records are not shown -->
            <section v-if="job.state !== 'none' && job.kind === 'publish'" class="rounded-md border p-3 flex flex-col gap-1" aria-live="polite">
              <div class="flex items-center gap-2 text-sm">
                <Loader2 v-if="jobRunning" class="h-3.5 w-3.5 animate-spin" />
                <CircleCheck v-else-if="job.state === 'done'" class="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                <CircleX v-else class="h-3.5 w-3.5 text-red-600 dark:text-red-400" />
                <span class="font-medium">
                  {{ jobRunning ? t('aiDaily.job.running', { kind: jobKind }) : job.state === 'done' ? t('aiDaily.job.done', { kind: jobKind }) : t('aiDaily.job.failed', { kind: jobKind }) }}
                </span>
                <span class="text-xs text-muted-foreground">{{ t('aiDaily.job.started', { time: formatTime(job.started) }) }}</span>
                <button type="button" class="ml-auto text-xs text-muted-foreground hover:text-foreground hover:underline" @click="showLog = !showLog">
                  {{ showLog ? t('aiDaily.job.hideLog') : t('aiDaily.job.showLog') }}
                </button>
              </div>
              <p v-if="job.error" class="text-xs text-red-600 dark:text-red-400">
                {{ job.error }}
              </p>
              <pre v-if="showLog" class="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-muted px-2 py-1 text-[11px] leading-relaxed">{{ job.log_tail }}</pre>
            </section>

            <!-- Inbox -->
            <section class="rounded-md border p-3 flex flex-col gap-2">
              <div class="flex items-center gap-2">
                <Inbox class="h-4 w-4 text-muted-foreground" />
                <span class="text-sm font-medium">{{ t('aiDaily.inbox.title') }}</span>
                <button type="button" class="ml-auto text-xs text-muted-foreground hover:text-foreground hover:underline" @click="aiDaily.open('inbox')">
                  {{ t('aiDaily.draft.openInbox') }}
                </button>
              </div>
              <p class="text-xs text-muted-foreground">
                {{ t('aiDaily.inbox.hint') }}
              </p>
              <div class="flex flex-col gap-2 sm:flex-row">
                <Input v-model="inboxUrl" :placeholder="t('aiDaily.inbox.urlPlaceholder')" class="h-8 min-w-0 sm:flex-[3]" @keydown.enter="addInbox" />
                <Input v-model="inboxNote" :placeholder="t('aiDaily.inbox.notePlaceholder')" class="h-8 min-w-0 sm:flex-[2]" @keydown.enter="addInbox" />
                <Button size="sm" class="h-8" @click="addInbox">
                  {{ t('aiDaily.inbox.add') }}
                </Button>
              </div>
              <div v-if="inbox" class="text-xs text-muted-foreground">
                <p v-if="inbox.count === 0">
                  {{ t('aiDaily.inbox.empty') }}
                </p>
                <template v-else>
                  <p>{{ t('aiDaily.inbox.recent', { count: inbox.count }) }}</p>
                  <ul class="mt-0.5 flex flex-col gap-0.5">
                    <li v-for="line in inbox.recent.slice(-5).reverse()" :key="line" class="truncate">
                      {{ line }}
                    </li>
                  </ul>
                </template>
              </div>
            </section>
          </TabsContent>
        </Tabs>
      </div>

      <DialogFooter v-if="connection === 'ok' && tab === 'today'" class="flex-col gap-2 sm:flex-col sm:items-stretch">
        <label v-if="article" class="flex items-center gap-1.5 text-xs">
          <Checkbox v-model="skipLinks" :disabled="jobRunning" />
          {{ t('aiDaily.actions.skipLinks') }}
        </label>
        <div class="flex flex-wrap justify-end gap-2">
          <Button variant="outline" :disabled="jobRunning || !article" @click="buildEmbed">
            <Loader2 v-if="jobRunning && job.kind === 'publish'" class="mr-2 h-4 w-4 animate-spin" />
            {{ t('aiDaily.actions.publish') }}
          </Button>
          <Button :disabled="jobRunning || !canLoad" @click="load">
            {{ t('aiDaily.actions.load') }}
          </Button>
        </div>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
