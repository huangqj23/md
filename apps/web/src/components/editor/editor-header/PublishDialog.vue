<script setup lang="ts">
import type { PublishPlatformId } from '@/publish-agent/protocol'
import type { ContentFormat } from '@/services/publish/platforms'
import type { PlatformRun, RunErrorCode, RunStatus, RunWarning } from '@/services/publish/runner'
import { Circle, CircleCheck, CircleSlash, CircleX, ImageOff, Loader2, LogIn, RefreshCw, ShieldCheck, TriangleAlert } from '@lucide/vue'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { auditImages, extractMarkdownTitle, extractSummary } from '@/services/publish/article'
import { PUBLISH_PLATFORMS } from '@/services/publish/platforms'
import { useEditorStore } from '@/stores/editor'
import { usePostStore } from '@/stores/post'
import { usePublishStore } from '@/stores/publish'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t, locale } = useI18n()
const publishStore = usePublishStore()
const { selectedIds, loginStates, checkingLogin, runs, running, cancelRequested } = storeToRefs(publishStore)

const dialogOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit(`update:open`, value),
})

const title = ref(``)
const summary = ref(``)
const permissionGranted = ref<boolean | null>(null)
const unsupportedImages = ref(0)

const runById = computed(() => {
  const map: Partial<Record<PublishPlatformId, PlatformRun>> = {}
  for (const run of runs.value)
    map[run.id] = run
  return map
})

const STATUS_KEY: Record<RunStatus, string> = {
  'queued': `queued`,
  'opening': `opening`,
  'filling': `filling`,
  'success': `success`,
  'warning': `warning`,
  'login-required': `loginRequired`,
  'failed': `failed`,
  'cancelled': `cancelled`,
}

const WARNING_KEY: Record<RunWarning, string> = {
  'title-truncated': `titleTruncated`,
  'title-not-filled': `titleNotFilled`,
  'body-partial': `bodyPartial`,
  'images-not-uploaded': `imagesNotUploaded`,
  'draft-not-saved': `draftNotSaved`,
}

const FORMAT_KEY: Record<ContentFormat, string> = {
  'markdown': `markdown`,
  'html': `html`,
  'wechat-html': `wechatHtml`,
}

function prefill() {
  const markdown = useEditorStore().getContent()
  title.value = extractMarkdownTitle(markdown) ?? usePostStore().currentPost?.title ?? ``
  const output = document.querySelector(`#output`)
  summary.value = output ? extractSummary(output) : ``
  const audit = output ? auditImages(output) : null
  unsupportedImages.value = audit ? audit.embedded + audit.local : 0
}

onMounted(async () => {
  prefill()
  permissionGranted.value = await publishStore.hasPermissions()
  if (permissionGranted.value && Object.keys(loginStates.value).length === 0)
    void publishStore.refreshLoginStates()
})

function isSelected(id: PublishPlatformId) {
  return selectedIds.value.includes(id)
}

function selectAll(selected: boolean) {
  for (const platform of PUBLISH_PLATFORMS)
    publishStore.setSelected(platform.id, selected)
}

function loginLabel(id: PublishPlatformId): string {
  const info = loginStates.value[id]
  if (!info)
    return checkingLogin.value ? t(`publish.login.checking`) : ``
  if (info.state === `logged-in`)
    return info.name ? t(`publish.login.loggedInAs`, { name: info.name }) : t(`publish.login.loggedIn`)
  return info.state === `logged-out` ? t(`publish.login.loggedOut`) : t(`publish.login.unknown`)
}

function errorMessage(code?: RunErrorCode): string {
  switch (code) {
    case `login-required`:
      return t(`publish.errors.loginRequired`)
    case `editor-not-found`:
      return t(`publish.errors.editorNotFound`)
    case `fill-failed`:
      return t(`publish.errors.fillFailed`)
    case `page-timeout`:
      return t(`publish.errors.pageTimeout`)
    case `agent-timeout`:
      return t(`publish.errors.agentTimeout`)
    default:
      return t(`publish.errors.generic`)
  }
}

function runMessages(run: PlatformRun | undefined): string[] {
  if (!run)
    return []
  if (run.status === `warning`) {
    return run.warnings.map((warning) => {
      if (warning === `body-partial` && run.report) {
        const percent = Math.round(run.report.bodyLength / Math.max(run.report.expectedLength, 1) * 100)
        return t(`publish.warnings.bodyPartial`, { percent })
      }
      if (warning === `images-not-uploaded` && run.report?.images)
        return t(`publish.warnings.imagesNotUploaded`, run.report.images)
      return t(`publish.warnings.${WARNING_KEY[warning]}`)
    })
  }
  if (run.status === `login-required` || run.status === `failed`)
    return [errorMessage(run.errorCode)]
  return []
}

async function grantPermission() {
  const granted = await publishStore.requestPermissions()
  permissionGranted.value = granted
  if (granted)
    await publishStore.refreshLoginStates()
  else
    toast.error(t(`publish.permission.denied`))
}

async function checkLogin() {
  if (!permissionGranted.value) {
    await grantPermission()
    return
  }
  await publishStore.refreshLoginStates()
}

async function start() {
  if (selectedIds.value.length === 0) {
    toast.error(t(`publish.noPlatform`))
    return
  }
  if (!title.value.trim()) {
    toast.error(t(`publish.noTitle`))
    return
  }
  // First await in the click handler: the permission prompt needs the user gesture.
  const granted = await publishStore.requestPermissions()
  permissionGranted.value = granted
  if (!granted) {
    toast.error(t(`publish.permission.denied`))
    return
  }

  const time = new Date().toLocaleTimeString(locale.value, { hour: `2-digit`, minute: `2-digit` })
  const results = await publishStore.publish({
    title: title.value,
    summary: summary.value,
    groupTitle: t(`publish.groupTitle`, { time }),
  })
  if (results.length === 0)
    return
  const count = (statuses: RunStatus[]) => results.filter(run => statuses.includes(run.status)).length
  toast.success(t(`publish.result`, {
    success: count([`success`]),
    warning: count([`warning`]),
    failed: count([`failed`, `login-required`, `cancelled`]),
  }))
}
</script>

<template>
  <Dialog v-model:open="dialogOpen">
    <DialogContent class="!max-w-[95vw] !w-[min(680px,95vw)] max-h-[88vh] flex flex-col overflow-hidden">
      <DialogHeader>
        <DialogTitle>{{ t('publish.title') }}</DialogTitle>
        <DialogDescription class="text-left leading-relaxed">
          {{ t('publish.description') }}
          <span class="mt-1 block text-xs">{{ t('publish.hint') }}</span>
        </DialogDescription>
      </DialogHeader>

      <div class="flex-1 overflow-y-auto p-1 flex flex-col gap-4">
        <Alert v-if="permissionGranted === false">
          <ShieldCheck class="h-4 w-4" />
          <AlertTitle>{{ t('publish.permission.title') }}</AlertTitle>
          <AlertDescription class="flex flex-col items-start gap-2">
            <span>{{ t('publish.permission.description') }}</span>
            <Button size="sm" @click="grantPermission">
              {{ t('publish.permission.grant') }}
            </Button>
          </AlertDescription>
        </Alert>

        <Alert v-if="unsupportedImages > 0">
          <ImageOff class="h-4 w-4" />
          <AlertTitle>{{ t('publish.images.title', { count: unsupportedImages }) }}</AlertTitle>
          <AlertDescription>{{ t('publish.images.description') }}</AlertDescription>
        </Alert>

        <div class="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(4.5rem,auto)_1fr] sm:items-center sm:gap-x-4">
          <Label for="publish-title" class="sm:text-end">
            {{ t('publish.titleLabel') }}
          </Label>
          <Input id="publish-title" v-model="title" :placeholder="t('publish.titlePlaceholder')" :disabled="running" class="min-w-0" />
          <Label for="publish-summary" class="sm:self-start sm:pt-2 sm:text-end">
            {{ t('publish.summaryLabel') }}
          </Label>
          <Textarea id="publish-summary" v-model="summary" :rows="2" :placeholder="t('publish.summaryPlaceholder')" :disabled="running" class="min-w-0" />
        </div>

        <section class="flex flex-col gap-2" :aria-label="t('publish.platformsLabel')">
          <div class="flex items-center justify-between">
            <span class="text-sm font-medium">{{ t('publish.platformsLabel') }}</span>
            <div class="flex items-center gap-3 text-xs">
              <button type="button" class="text-muted-foreground hover:text-foreground hover:underline disabled:opacity-50" :disabled="running" @click="selectAll(true)">
                {{ t('publish.selectAll') }}
              </button>
              <button type="button" class="text-muted-foreground hover:text-foreground hover:underline disabled:opacity-50" :disabled="running" @click="selectAll(false)">
                {{ t('publish.selectNone') }}
              </button>
            </div>
          </div>

          <ul class="divide-y rounded-md border" aria-live="polite">
            <li v-for="platform in PUBLISH_PLATFORMS" :key="platform.id" class="flex items-start gap-3 px-3 py-2">
              <Checkbox
                :id="`publish-platform-${platform.id}`"
                class="mt-0.5"
                :model-value="isSelected(platform.id)"
                :disabled="running"
                @update:model-value="value => publishStore.setSelected(platform.id, value === true)"
              />
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <label :for="`publish-platform-${platform.id}`" class="cursor-pointer text-sm font-medium">
                    {{ t(`publish.platforms.${platform.id}`) }}
                  </label>
                  <span class="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                    {{ t(`publish.format.${FORMAT_KEY[platform.format]}`) }}
                  </span>
                </div>
                <p v-for="message in runMessages(runById[platform.id])" :key="message" class="mt-1 text-xs text-muted-foreground">
                  {{ message }}
                </p>
                <p
                  v-if="runById[platform.id]?.detail && (runById[platform.id]?.status === 'failed' || runById[platform.id]?.status === 'login-required')"
                  class="mt-0.5 break-all text-[11px] text-muted-foreground/80"
                >
                  {{ runById[platform.id]?.detail }}
                </p>
              </div>

              <div class="flex shrink-0 items-center gap-2 text-xs">
                <template v-if="runById[platform.id]">
                  <span class="inline-flex items-center gap-1">
                    <Loader2 v-if="runById[platform.id]?.status === 'opening' || runById[platform.id]?.status === 'filling'" class="h-3.5 w-3.5 animate-spin" />
                    <CircleCheck v-else-if="runById[platform.id]?.status === 'success'" class="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
                    <TriangleAlert v-else-if="runById[platform.id]?.status === 'warning'" class="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                    <LogIn v-else-if="runById[platform.id]?.status === 'login-required'" class="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                    <CircleX v-else-if="runById[platform.id]?.status === 'failed'" class="h-3.5 w-3.5 text-red-600 dark:text-red-400" />
                    <CircleSlash v-else-if="runById[platform.id]?.status === 'cancelled'" class="h-3.5 w-3.5 text-muted-foreground" />
                    <Circle v-else class="h-3.5 w-3.5 text-muted-foreground" />
                    {{ t(`publish.status.${STATUS_KEY[runById[platform.id]!.status]}`) }}
                  </span>
                  <Button
                    v-if="runById[platform.id]?.tabId !== undefined"
                    variant="ghost"
                    size="sm"
                    class="h-7 px-2"
                    @click="publishStore.focusTab(runById[platform.id]!.tabId!)"
                  >
                    {{ t('publish.viewTab') }}
                  </Button>
                </template>
                <template v-else>
                  <span
                    class="max-w-[12rem] truncate"
                    :class="loginStates[platform.id]?.state === 'logged-in' ? 'text-green-700 dark:text-green-400' : 'text-muted-foreground'"
                  >
                    {{ loginLabel(platform.id) }}
                  </span>
                  <a
                    v-if="loginStates[platform.id]?.state !== 'logged-in'"
                    :href="platform.homeUrl"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="text-primary hover:underline"
                  >
                    {{ t('publish.login.open') }}
                  </a>
                </template>
              </div>
            </li>
          </ul>
        </section>
      </div>

      <DialogFooter class="gap-2 sm:gap-2">
        <Button variant="outline" :disabled="running || checkingLogin" @click="checkLogin">
          <RefreshCw class="mr-2 h-4 w-4" :class="{ 'animate-spin': checkingLogin }" />
          {{ t('publish.login.refresh') }}
        </Button>
        <Button v-if="running" variant="outline" :disabled="cancelRequested" @click="publishStore.cancel()">
          {{ cancelRequested ? t('publish.stopping') : t('publish.stop') }}
        </Button>
        <Button :disabled="running || selectedIds.length === 0" @click="start">
          <Loader2 v-if="running" class="mr-2 h-4 w-4 animate-spin" />
          {{ t('publish.start') }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
