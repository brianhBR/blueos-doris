/**
 * Centralised download manager for the extension UI.
 *
 * Why this exists
 * ───────────────
 * The vanilla `<a download>` flow has zero feedback while the browser is
 * still talking to the backend, so big recordings looked like "the download
 * button does nothing" for many seconds. The backend now streams the file
 * directly (`FileResponse` -> actix_files), but the user still benefits from
 * an explicit progress affordance and from serializing bulk selections so
 * the browser doesn't silently cancel concurrent same-origin saves.
 *
 * Approach (mirrors the BR_exploreHD_DVR pattern)
 * ───────────────────────────────────────────────
 *  - phase=`preparing`: HEAD preflight in flight (verify file still exists,
 *    refresh size from disk).
 *  - phase=`starting`: HEAD ok, hidden <a download> click just fired.
 *  - phase=`streaming`: enough time has passed that the browser must have
 *    accepted the navigation; download tray now owns visible progress.
 *  - phase=`done`: auto-dismiss timer fired (or it was a quick file).
 *  - phase=`error`: HEAD failed (404 / 5xx / network).
 *
 * The composable is a singleton (module-level state) so any component can
 * call `enqueueDownload(...)` and the shared `<DownloadToast />` overlay
 * renders the queue regardless of which screen the user navigates to.
 */
import { reactive, readonly } from 'vue'

const API_BASE = '/api/v1'

export type DownloadPhase = 'preparing' | 'starting' | 'streaming' | 'done' | 'error'

export interface DownloadJob {
  id: string
  fileName: string
  filePath: string
  sizeBytes: number
  startedAt: number
  phase: DownloadPhase
  error: string
  /** Position in the bulk batch this job belongs to (1-based). */
  index: number
  /** Total jobs in the bulk batch this job belongs to. */
  total: number
}

interface State {
  jobs: DownloadJob[]
}

const state = reactive<State>({ jobs: [] })

let jobSeq = 0

function nextId(): string {
  jobSeq += 1
  return `dl-${Date.now()}-${jobSeq}`
}

function pruneOlderThan(ms: number) {
  const now = Date.now()
  state.jobs = state.jobs.filter(j => {
    if (j.phase === 'done') return now - j.startedAt < ms
    if (j.phase === 'error') return now - j.startedAt < ms
    return true
  })
}

async function preflight(filePath: string): Promise<{ size: number; name: string } | null> {
  const params = new URLSearchParams({ path: filePath }).toString()
  const url = `${API_BASE}/media/download?${params}`
  try {
    const res = await fetch(url, { method: 'HEAD' })
    if (!res.ok) return null
    const sizeStr = res.headers.get('X-Doris-File-Size') || res.headers.get('Content-Length') || '0'
    const nameStr = res.headers.get('X-Doris-File-Name') || ''
    const size = Number.parseInt(sizeStr, 10)
    return {
      size: Number.isFinite(size) && size > 0 ? size : 0,
      name: nameStr,
    }
  } catch {
    return null
  }
}

function triggerBrowserSave(filePath: string, fileName: string) {
  const params = new URLSearchParams({ path: filePath }).toString()
  const url = `${API_BASE}/media/download?${params}`
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  a.style.display = 'none'
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

async function runJob(job: DownloadJob): Promise<void> {
  const info = await preflight(job.filePath)
  if (!info) {
    job.phase = 'error'
    job.error = 'File not available (it may have been deleted or moved).'
    pruneOlderThan(15_000)
    return
  }
  if (info.size > 0) job.sizeBytes = info.size
  if (info.name) job.fileName = info.name

  job.phase = 'starting'
  triggerBrowserSave(job.filePath, job.fileName)

  // The browser owns the rest of the lifecycle. We can't observe its native
  // download tray progress, so flip to "streaming" once the navigation has
  // had time to land, and auto-collapse the toast a few seconds later. The
  // file keeps streaming in the background regardless of this UI state.
  await new Promise(r => setTimeout(r, 700))
  if (job.phase === 'starting') job.phase = 'streaming'
  await new Promise(r => setTimeout(r, 4_500))
  if (job.phase === 'streaming') job.phase = 'done'
  pruneOlderThan(15_000)
}

/**
 * Enqueue a single file. Returns a promise that resolves once the browser
 * has accepted the download (i.e. the toast has flipped to `streaming`),
 * NOT when the bytes finish landing on disk — the browser owns that.
 */
export async function enqueueDownload(
  filePath: string,
  fileName: string,
  sizeBytes: number,
  index = 1,
  total = 1,
): Promise<void> {
  pruneOlderThan(0)
  const job: DownloadJob = reactive({
    id: nextId(),
    fileName,
    filePath,
    sizeBytes,
    startedAt: Date.now(),
    phase: 'preparing',
    error: '',
    index,
    total,
  })
  state.jobs.push(job)
  await runJob(job)
}

/**
 * Bulk download. Drives the queue serially with a small gap between saves
 * so Safari/Chromium don't silently cancel the prior one when a new
 * navigation is initiated to the same origin.
 */
export async function enqueueBulkDownload(
  files: Array<{ filePath: string; fileName: string; sizeBytes: number }>,
): Promise<void> {
  const total = files.length
  for (let i = 0; i < files.length; i += 1) {
    const f = files[i]
    await enqueueDownload(f.filePath, f.fileName, f.sizeBytes, i + 1, total)
    if (i < files.length - 1) {
      await new Promise(r => setTimeout(r, 900))
    }
  }
}

export function dismissJob(id: string) {
  const idx = state.jobs.findIndex(j => j.id === id)
  if (idx >= 0) state.jobs.splice(idx, 1)
}

export function dismissFinishedJobs() {
  state.jobs = state.jobs.filter(j => j.phase !== 'done' && j.phase !== 'error')
}

export function useDownloadQueue() {
  return {
    jobs: readonly(state.jobs),
    enqueueDownload,
    enqueueBulkDownload,
    dismissJob,
    dismissFinishedJobs,
  }
}
