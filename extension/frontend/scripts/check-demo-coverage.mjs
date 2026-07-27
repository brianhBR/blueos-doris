/**
 * Cross-checks every `/api/v1` path the frontend can call against the demo
 * route table, so gaps surface here instead of as a blank panel during a
 * design review.
 *
 * Run: node scripts/check-demo-coverage.mjs
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..')
const srcDir = join(root, 'src')
const demoApi = join(srcDir, 'demo', 'api.ts')

function walk(dir) {
  return readdirSync(dir).flatMap(name => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full) : [full]
  })
}

const files = walk(srcDir).filter(f => /\.(ts|vue)$/.test(f) && !f.includes(`${join('src', 'demo')}`))

// ── Collect call sites ──────────────────────────────────────────────

/** `${...}` interpolations stand in for path params. */
const normalize = p => p.replace(/\$\{[^}]*\}/g, 'X').split('?')[0].replace(/\/+$/, '') || '/'

const calls = new Map() // "METHOD /path" -> Set(source files)
const looseCalls = new Map() // same, from the method-less bare-string scan

function record(method, path, file, target = calls) {
  const norm = normalize(path)
  // `${API_BASE}${endpoint}` inside the fetchApi/postApi/deleteApi helpers
  // themselves, not a real endpoint.
  if (norm === 'X' || norm === '/') return
  const key = `${method} ${norm}`
  if (!target.has(key)) target.set(key, new Set())
  target.get(key).add(relative(root, file))
}

const helperMethod = { fetchApi: 'GET', postApi: 'POST', deleteApi: 'DELETE' }

for (const file of files) {
  const src = readFileSync(file, 'utf8')

  // fetchApi<T>('/x') | postApi<T>(`/x`) | deleteApi<T>('/x')
  for (const m of src.matchAll(/\b(fetchApi|postApi|deleteApi)\s*(?:<[^>]*>)?\s*\(\s*[`'"]([^`'"]+)[`'"]/g)) {
    record(helperMethod[m[1]], m[2], file)
  }

  // Direct fetch() against an absolute /api/v1 path, or via an
  // ${API_BASE}/${API_V1} prefix constant.
  for (const m of src.matchAll(/fetch\s*\(\s*[`'"]([^`'"]*\/api\/v1[^`'"]*)[`'"]([^)]*)\)/g)) {
    const path = m[1].replace(/^.*\/api\/v1/, '')
    const method = /method\s*:\s*['"](\w+)['"]/.exec(m[2] || '')?.[1]?.toUpperCase() ?? 'GET'
    record(method, path || '/', file)
  }
  for (const m of src.matchAll(/fetch\s*\(\s*`\$\{(?:API_BASE|API_V1)\}([^`]*)`([^)]*)\)/g)) {
    const method = /method\s*:\s*['"](\w+)['"]/.exec(m[2] || '')?.[1]?.toUpperCase() ?? 'GET'
    record(method, m[1], file)
  }

  // URL builders that never reach fetch() directly (href/src bindings).
  // The method is unknowable here, so these are provisional GETs and are
  // discarded below if the same path was seen with a real method.
  for (const m of src.matchAll(/[`'"]\/api\/v1(\/[^`'"$]*)/g)) {
    record('GET', m[1], file, looseCalls)
  }
}

const knownPaths = new Set([...calls.keys()].map(k => k.split(' ')[1]))
for (const [key, sources] of looseCalls) {
  if (!knownPaths.has(key.split(' ')[1])) calls.set(key, sources)
}

// ── Collect demo routes ─────────────────────────────────────────────

const apiSrc = readFileSync(demoApi, 'utf8')
const routes = [...apiSrc.matchAll(/method:\s*'(\w+)',\s*pattern:\s*\/(.+?)\/,/g)].map(m => ({
  method: m[1],
  regex: new RegExp(m[2]),
}))

// Non-JSON endpoints handled ahead of the table.
const binary = [
  { method: 'GET', regex: /^\/camera\/snapshot$/ },
  { method: 'GET', regex: /^.*\/export\/scientific\.csv$/ },
  { method: 'GET', regex: /^\/media\/download$/ },
]
const all = [...routes, ...binary]

// ── Report ──────────────────────────────────────────────────────────

const missing = []
for (const [key, sources] of [...calls.entries()].sort()) {
  const [method, path] = key.split(' ')
  const covered = all.some(r => r.method === method && r.regex.test(path))
  if (!covered) missing.push({ method, path, sources: [...sources] })
}

console.log(`Call sites found:  ${calls.size}`)
console.log(`Demo routes:       ${all.length}`)
console.log(`Uncovered:         ${missing.length}\n`)

if (missing.length) {
  console.log('Endpoints with no demo route (will hit the generic fallback):\n')
  for (const m of missing) {
    console.log(`  ${m.method.padEnd(6)} ${m.path.padEnd(46)} ${m.sources.join(', ')}`)
  }
  process.exitCode = 1
} else {
  console.log('Every discovered endpoint has an explicit demo route.')
}
