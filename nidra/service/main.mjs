#!/usr/bin/env node
// Nidra as a gardend service (docs/local-service-hosting.md in gardend).
//
// gardend starts this process when the profile enables it (`gardend service enable nidra`) and stops it on
// `disable` or when it exits itself. This process:
//  - brings up the engine (NVIDIA Auto Ontology's, on gardend) with `docker compose up -d`, the fork's
//    compose.nidra.yaml with compose.sophia.yaml over it, against THIS gardend: the catalog, the glossary and the
//    vector index live in one graph of the profile (NIDRA_GRAPH_ID, default `nidra`);
//  - answers gardend's proxy at /services/nidra: /health, the engine's API under /api (the caller's identity
//    added), and its own small API under /nidra (history, find, sources, ingest, settings);
//  - keeps every question asked, with its answer, SQL and rows, in that graph (never in a browser);
//  - is the engine's only door to gardend: a fence on loopback that forwards just the five calls the engine makes,
//    for that one graph, with credentials the containers never see;
//  - stops the containers when the service stops for good (disable, or gardend exits), not when it restarts.
//
// Credentials: gardend's own member token for this process (MNEMOSYNE_MCP_ACCESS_TOKEN: reads and `find`), and
// an rdf-admin client token kept in the service env as NIDRA_GARDEND_RDF_TOKEN (SPARQL update and RDF load,
// which a member may not do). Neither is printed, logged, or written anywhere by this file.
//
// Attached (NIDRA_ENGINE=attached, the one-container image `ghcr.io/sophia-labs/nidra`): the engine already runs
// beside gardend (its supervisor, nidra/oneliner/run.sh, started it at 127.0.0.1:NIDRA_ENGINE_PORT and talks to
// gardend directly); this process brings up nothing, keeps no fence and only proxies, records and reads. The model
// settings are the container's environment, so the Settings screen shows them and cannot change them.
//
//   node main.mjs                 the service (gardend runs this)
//   node main.mjs --reap <pid>    detached: stop the containers unless a new service process took over
import http from 'node:http'
import { spawn, spawnSync } from 'node:child_process'
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Ask, Asks, TOOL_BUDGET_MS, answerRpc, createSseReader } from './asks.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const env = process.env
const SERVICE_ID = env.GARDEN_SERVICE_ID || 'nidra'
const DATA_DIR = env.DATA_DIR || path.join(HERE, '.data')
// DATA_DIR is <profile>/services/<id>; the profile's settings for this service are <profile>/services/<id>.env.
const PROFILE = env.NIDRA_PROFILE_DIR || path.resolve(DATA_DIR, '..', '..')
const AUTOSTART_FILE = path.join(PROFILE, 'services', `${SERVICE_ID}.autostart`)
const PROJECT = env.NIDRA_PROJECT || 'sophia-nidra'
const COMPOSE_FILES = [path.join(HERE, 'engine', 'compose.nidra.yaml'), path.join(HERE, 'compose.sophia.yaml')]
const WRAPPER_PID = path.join(DATA_DIR, 'service.pid')
const REAPER_PID = path.join(DATA_DIR, 'reaper.pid')
const LOG_FILE = path.join(DATA_DIR, 'service.log')
const DOCKER_PATH = ['/usr/local/bin', '/opt/homebrew/bin', '/Applications/Docker.app/Contents/Resources/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']

const GRAPH = env.NIDRA_GRAPH_ID || 'nidra'
const ENGINE_PORT = Number(env.NIDRA_ENGINE_PORT || 7682)
const INGEST_PORT = Number(env.NIDRA_INGEST_PORT || 7683)
const FENCE_PORT = Number(env.NIDRA_FENCE_PORT || 7684)
const IMAGE = env.NIDRA_ENGINE_IMAGE || 'nidra-engine:latest'
const ATTACHED = env.NIDRA_ENGINE === 'attached'
const SOURCES = env.CONNECTION_STRINGS || 'sqlite:////data/chinook.sqlite'
const DEFAULT_ENDPOINT = 'https://openrouter.ai/api/v1'
const DEFAULT_MODEL = 'anthropic/claude-sonnet-5.5'

// The history vocabulary: one resource per question, in the graph's own RDF (the named graph gardend gives
// SPARQL updates, as the engine's writes use), so Garden, SPARQL and `find` see it like any other entity.
const NS = 'urn:nidra:'
const Q = `${NS}Question`
const P = name => `<${Q}#${name}>`
const XSD = 'http://www.w3.org/2001/XMLSchema#'
const RDF_JSON = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON'
const SKOS_LABEL = 'http://www.w3.org/2004/02/skos/core#prefLabel'
const dataGraph = () => `urn:mnemosyne:local:graph:${GRAPH}:user:rdf`

fs.mkdirSync(DATA_DIR, { recursive: true, mode: 0o700 })

function log(...words) {
  const line = `${new Date().toISOString()} ${words.join(' ')}\n`
  process.stderr.write(line)
  try { fs.appendFileSync(LOG_FILE, line, { mode: 0o600 }) } catch {}
}


// ── the service ─────────────────────────────────────────────────────────────────────────────────────────

async function serve() {
  const port = Number(env.PORT)
  const host = env.GARDEN_SERVICE_HOST || '127.0.0.1'
  const gardend = (env.GARDEN_LOOPBACK_API_URL || '').replace(/\/+$/, '')
  const internal = env.MNEMOSYNE_INTERNAL_SERVICE_SECRET || ''
  if (!port || !gardend) {
    log('nidra: PORT and GARDEN_LOOPBACK_API_URL come from gardend; run this as a gardend service')
    process.exit(2)
  }
  const member = env.MNEMOSYNE_MCP_ACCESS_TOKEN || ''
  const rdfToken = env.NIDRA_GARDEND_RDF_TOKEN || ''
  const state = { state: 'starting', reason: 'starting', since: new Date().toISOString() }
  const set = (next, reason) => {
    if (state.state !== next || state.reason !== reason) log(`state ${next}: ${reason}`)
    Object.assign(state, { state: next, reason, since: new Date().toISOString() })
  }

  if (!ATTACHED) takeOver()
  const fence = ATTACHED ? null : await fenceSecret()
  const store = new Gardend(gardend, member, rdfToken)

  // The fence: the engine's containers reach this gardend only through it. Attached, the engine already talks to
  // gardend itself, inside the one container.
  const fenceServer = ATTACHED ? null : http.createServer((req, res) => fenceRequest(req, res, { gardend, member, rdfToken, fence, store }))
  fenceServer?.on('error', error => set('error', `the fence could not listen on 127.0.0.1:${FENCE_PORT} (${error.code || error.message})`))
  fenceServer?.listen(FENCE_PORT, '127.0.0.1')

  const server = http.createServer((req, res) => {
    route(req, res, { internal, state, store }).catch(error => {
      log('request failed:', req.method, req.url?.split('?')[0], String(error?.message || error))
      if (!res.headersSent) json(res, 500, { ok: false, error: String(error?.message || error) })
      else res.destroy()
    })
  })
  server.listen(port, host, () => log(`nidra: listening on ${host}:${port}; graph ${GRAPH}; engine on 127.0.0.1:${ENGINE_PORT}`))

  let stopping = false
  const stop = signal => {
    if (stopping) return
    stopping = true
    if (ATTACHED) {
      log(`nidra: ${signal}: stopping (the engine is its supervisor's to stop)`)
    } else {
      log(`nidra: ${signal}: stopping (the containers stop unless the service comes back)`)
      // Detached, so gardend's SIGKILL of this process group 8 s from now does not reach it.
      const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--reap', String(process.pid)], {
        detached: true, stdio: 'ignore', env: reaperEnv(),
      })
      child.unref()
    }
    server.close()
    fenceServer?.close()
    setTimeout(() => process.exit(0), 200)
  }
  process.on('SIGTERM', () => stop('SIGTERM'))
  process.on('SIGINT', () => stop('SIGINT'))

  // Bring the engine up, then watch it.
  const missing = []
  if (!member) missing.push('gardend gave this service no token (MNEMOSYNE_MCP_ACCESS_TOKEN)')
  if (!rdfToken) missing.push('NIDRA_GARDEND_RDF_TOKEN is not set: `gardend token create --label nidra-engine --grant rdf-admin --expires-days 365 --json`, its secret piped into `gardend service env set NIDRA_GARDEND_RDF_TOKEN --service nidra`')
  if (!ATTACHED && member) {
    set('starting', 'asking Sophia Compute how it routes Nidra')
    const routing = await learnRouting(gardend, member)
    log(`routing: ${routing.provider} ${routing.model} (from ${routing.from}${routing.reason ? `: ${routing.reason}` : ''})`)
  }
  if (!modelKey().value) missing.push(ATTACHED
    ? 'no model key: run Nidra again with -e OPENROUTER_API_KEY=sk-or-… (or the DEFAULT_MODELS_* triplet of any OpenAI-compatible endpoint)'
    : `no model key: Sophia Compute routes Nidra through ${ROUTING.provider}, whose key is ${EGRESS[ROUTING.provider]?.keyEnv || 'not one this service knows'}: \`gardend service env set ${EGRESS[ROUTING.provider]?.keyEnv || 'DEEPSEEK_API_KEY'} --profile ~/sophia\` (it reaches Compute and Nidra both)`)
  if (missing.length) return set('error', missing.join('; '))
  if (ATTACHED) return watchAttached(set, state, () => stopping)

  set('starting', 'checking Docker')
  const docker = await run('docker', ['info', '--format', '{{.ServerVersion}}'], { timeout: 20_000 })
  if (docker.status !== 0) return set('error', 'Docker is not running (Nidra’s engine runs in Docker: open Docker Desktop, then restart Nidra)')
  if ((await run('docker', ['image', 'inspect', '--format', '{{.Id}}', IMAGE], { timeout: 20_000 })).status !== 0)
    return set('error', `the engine image ${IMAGE} is not loaded (docker load -i nidra-engine-<date>.tar.gz, or build it from the Nidra checkout: docker compose -f compose.nidra.yaml build auto-ontology)`)
  set('starting', `checking graph ${GRAPH}`)
  try {
    if (!(await store.graphExists())) return set('error', `there is no graph ${GRAPH} in this Sophia yet (create it once: it holds Nidra’s catalog)`)
  } catch (error) {
    return set('error', `could not read graph ${GRAPH}: ${error.message}`)
  }

  const up = async () => {
    set('starting', 'starting the engine containers')
    for (let attempt = 1; attempt <= 3 && !stopping; attempt++) {
      const result = await compose(['up', '-d', '--no-build', '--pull', 'never', '--remove-orphans'], composeEnv(fence))
      if (result.status === 0) return true
      log(`compose up failed (attempt ${attempt}): ${tail(result.stderr)}`)
      if (attempt === 3) { set('error', `the engine containers did not start: ${tail(result.stderr, 300)}`); return false }
      await sleep(3000)
    }
    return false
  }
  if (!(await up())) return
  set('starting', 'waiting for the engine')
  let misses = 0
  const started = Date.now()
  while (!stopping) {
    const health = await engineHealth()
    if (health.ok) {
      misses = 0
      if (state.state !== 'running') {
        set('running', 'the engine answers')
        store.registerBeing(ROUTING.being).catch(error => log('could not register the being in the graph:', error.message))
      }
      state.engine = health.body
    } else if (state.state === 'running') {
      if (++misses >= 3) {
        set('starting', 'the engine stopped answering; starting it again')
        misses = 0
        if (!(await up())) return
      }
    } else if (Date.now() - started > 240_000 && state.state === 'starting') {
      set('error', `the engine did not answer within four minutes (${health.error}); see docker logs ${PROJECT}-engine`)
    }
    await sleep(state.state === 'running' ? 15_000 : 2_000)
  }
}

/** Attached: the engine is started and restarted by the container's supervisor; this only reports what it sees. */
async function watchAttached(set, state, stopping) {
  set('starting', 'waiting for the engine')
  while (!stopping()) {
    const health = await engineHealth()
    if (health.ok) {
      if (state.state !== 'running') set('running', 'the engine answers')
      state.engine = health.body
    } else if (state.state === 'running') {
      set('starting', `the engine stopped answering (${health.error})`)
    }
    await sleep(state.state === 'running' ? 15_000 : 2_000)
  }
}

/** Only one service process per profile: end a pending reaper (after checking it is ours), record ourselves. */
function takeOver() {
  const pid = readPid(REAPER_PID)
  if (pid && isOurs(pid, '--reap')) {
    try { process.kill(pid, 'SIGTERM'); log(`nidra: ended the pending stop (pid ${pid}): the service is back`) } catch {}
  }
  fs.writeFileSync(WRAPPER_PID, `${process.pid}\n`, { mode: 0o600 })
}

/** The secret the containers present to the fence: random, owner-only, stable across restarts so a restart does not recreate the containers. */
async function fenceSecret() {
  const file = path.join(DATA_DIR, 'fence.secret')
  try {
    const value = fs.readFileSync(file, 'utf8').trim()
    if (value.length >= 32) return value
  } catch {}
  const value = randomBytes(32).toString('hex')
  fs.writeFileSync(file, `${value}\n`, { mode: 0o600 })
  return value
}

// ── the model, as Sophia Compute routes Nidra ──────────────────────────────────────────────────────────────
//
// Nidra is a being of Sophia Compute (Choreograph's `nidra-chat-v1`): Compute names its provider and model and holds the
// key (DEEPSEEK_API_KEY for the deepseek egress, in the profile's services.env, which reaches every service). The engine
// answers with the same model through the same key, so there is one routing to read and one key to keep. This service
// asks Compute for the routing (GET /services/choreograph/api/workflows, nidra-chat-v1's host facts) before it starts the
// engine, and keeps nothing of its own: NIDRA_MODEL_* in this service's env is read only when Compute cannot be asked.
const EGRESS = {
  deepseek: { endpoint: 'https://api.deepseek.com/v1', keyEnv: 'DEEPSEEK_API_KEY' },
  openrouter: { endpoint: 'https://openrouter.ai/api/v1', keyEnv: 'OPENROUTER_API_KEY' },
}
const ROUTING = { provider: env.CHOREOGRAPH_NIDRA_PROVIDER || 'deepseek', model: env.CHOREOGRAPH_NIDRA_MODEL || 'deepseek-v4-pro', from: 'default', being: null, checkedAt: null }

/** Ask Compute how it routes Nidra; keep the answer (never a key: the host facts carry only whether one is set). */
async function learnRouting(gardend, member, { attempts = 10 } = {}) {
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const response = await fetch(`${gardend}/services/choreograph/api/workflows`, { headers: { authorization: `Bearer ${member}` }, signal: AbortSignal.timeout(10_000) })
      const body = await response.json().catch(() => null)
      const entry = (body?.workflows || []).find(workflow => workflow?.name === 'nidra-chat-v1')
      if (response.ok && entry?.host?.routing) {
        Object.assign(ROUTING, { provider: entry.host.routing.provider, model: entry.host.routing.model, from: 'compute', being: entry.host.being ?? null, keyConfigured: entry.host.routing.keyConfigured, checkedAt: new Date().toISOString() })
        return ROUTING
      }
      if (response.ok) { ROUTING.from = 'default'; ROUTING.reason = 'Sophia Compute here has no Nidra being (nidra-chat-v1)'; return ROUTING }
      ROUTING.reason = `Sophia Compute answered ${response.status}`
    } catch (error) {
      ROUTING.reason = `Sophia Compute did not answer (${error?.cause?.code || error?.message || error})`
    }
    await sleep(Math.min(2000 * attempt, 6000))
  }
  return ROUTING
}

function modelKey() {
  const egress = EGRESS[ROUTING.provider]
  if (egress && env[egress.keyEnv]) return { value: env[egress.keyEnv], from: egress.keyEnv }
  if (env.NIDRA_MODEL_KEY) return { value: env.NIDRA_MODEL_KEY, from: 'NIDRA_MODEL_KEY' }
  if (env.OPENROUTER_API_KEY) return { value: env.OPENROUTER_API_KEY, from: 'OPENROUTER_API_KEY' }
  if (ATTACHED && env.DEFAULT_MODELS_API_KEY) return { value: env.DEFAULT_MODELS_API_KEY, from: 'DEFAULT_MODELS_API_KEY' }
  return { value: '', from: null }
}

function modelSettings() {
  // Attached, the supervisor has already turned the container's environment into the engine's triplet.
  if (ATTACHED) return { endpoint: env.DEFAULT_MODELS_ENDPOINT || DEFAULT_ENDPOINT, model: env.DEFAULT_MODELS_MODEL || DEFAULT_MODEL }
  const key = modelKey()
  const egress = EGRESS[ROUTING.provider]
  // The key this engine uses decides where it goes: the routed egress's own key goes to that egress.
  if (egress && key.from === egress.keyEnv) return { endpoint: egress.endpoint, model: ROUTING.model }
  return { endpoint: env.NIDRA_MODEL_ENDPOINT || DEFAULT_ENDPOINT, model: env.NIDRA_MODEL || DEFAULT_MODEL }
}

/** What `docker compose` sees: no token of gardend's, nothing of this process's environment but these. */
function composeEnv(fence) {
  const { endpoint, model } = modelSettings()
  return {
    PATH: DOCKER_PATH.join(':'),
    HOME: env.HOME || '',
    ...(env.DOCKER_HOST ? { DOCKER_HOST: env.DOCKER_HOST } : {}),
    ...(env.DOCKER_CONTEXT ? { DOCKER_CONTEXT: env.DOCKER_CONTEXT } : {}),
    ...(env.DOCKER_CONFIG ? { DOCKER_CONFIG: env.DOCKER_CONFIG } : {}),
    GARDEND_URL: `http://host.docker.internal:${FENCE_PORT}`,
    GARDEND_TOKEN: fence,
    GARDEND_GRAPH_ID: GRAPH,
    CONNECTION_STRINGS: SOURCES,
    NIDRA_ENGINE_IMAGE: IMAGE,
    NIDRA_PROJECT: PROJECT,
    NIDRA_ENGINE_PORT: String(ENGINE_PORT),
    NIDRA_INGEST_PORT: String(INGEST_PORT),
    DEFAULT_MODELS_ENDPOINT: endpoint,
    DEFAULT_MODELS_API_KEY: modelKey().value,
    DEFAULT_MODELS_MODEL: model,
    ...(env.NIDRA_LLM_MAX_INFLIGHT ? { NIDRA_LLM_MAX_INFLIGHT: env.NIDRA_LLM_MAX_INFLIGHT } : {}),
  }
}

function reaperEnv() {
  return { PATH: DOCKER_PATH.join(':'), HOME: env.HOME || '', DATA_DIR, NIDRA_PROFILE_DIR: PROFILE, GARDEN_SERVICE_ID: SERVICE_ID, NIDRA_PROJECT: PROJECT,
    ...(env.DOCKER_HOST ? { DOCKER_HOST: env.DOCKER_HOST } : {}), ...(env.DOCKER_CONTEXT ? { DOCKER_CONTEXT: env.DOCKER_CONTEXT } : {}) }
}

function compose(args, composeEnvironment) {
  return run('docker', ['compose', '-p', PROJECT, ...COMPOSE_FILES.flatMap(file => ['-f', file]), ...args], { env: composeEnvironment, timeout: 180_000 })
}

/** A command, without blocking the event loop (gardend asks /health meanwhile). */
function run(command, args, { env: childEnv, timeout = 60_000 } = {}) {
  return new Promise(resolve => {
    let stdout = ''
    let stderr = ''
    let child
    try {
      child = spawn(command, args, { env: childEnv || { PATH: DOCKER_PATH.join(':'), HOME: env.HOME || '' }, stdio: ['ignore', 'pipe', 'pipe'] })
    } catch (error) {
      return resolve({ status: 1, stdout, stderr: String(error?.message || error) })
    }
    const timer = setTimeout(() => child.kill('SIGTERM'), timeout)
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', error => { stderr += String(error?.message || error) })
    child.on('close', status => { clearTimeout(timer); resolve({ status: status ?? 1, stdout, stderr }) })
  })
}

const tail = (text, max = 600) => String(text || '').trim().split('\n').slice(-4).join(' | ').slice(-max)
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

async function engineHealth() {
  try {
    const response = await fetch(`http://127.0.0.1:${ENGINE_PORT}/api/health`, { signal: AbortSignal.timeout(5000) })
    const body = await response.json().catch(() => null)
    return { ok: response.ok && body?.status === 'ok', body, error: `HTTP ${response.status}` }
  } catch (error) {
    return { ok: false, body: null, error: String(error?.cause?.code || error?.message || error) }
  }
}

// ── gardend's proxy → this service ──────────────────────────────────────────────────────────────────────

function believe(req, internal) {
  const given = Buffer.from(String(req.headers['x-internal-service'] || ''))
  const want = Buffer.from(internal)
  return internal.length > 0 && given.length === want.length && timingSafeEqual(given, want)
}

async function route(req, res, { internal, state, store }) {
  const url = new URL(req.url || '/', 'http://nidra.local')
  if (url.pathname === '/health') {
    return json(res, 200, { ok: state.state === 'running', state: state.state, reason: state.reason, since: state.since, graphId: GRAPH, engine: state.engine ?? null })
  }
  // Everything else comes through gardend's proxy, which alone knows the per-process secret.
  if (!believe(req, internal)) return json(res, 403, { ok: false, error: 'reach Nidra through gardend (/services/nidra)' })
  const caller = String(req.headers['x-user-id'] || 'owner').slice(0, 200)
  const role = String(req.headers['x-sophia-caller-role'] || 'viewer')
  // Who may change the glossary: the owner, and Sophia Compute's beings (Choreograph's own token, which gardend names
  // `service:choreograph`). Other members read. gardend lets members reach this service at all (`memberAccess: all`)
  // only so Compute's beings can call the write tool; this is the door that decides.
  const who = { userId: caller, role, mayWrite: role === 'owner' || caller === 'service:choreograph' }

  if (url.pathname === '/mcp' && req.method === 'POST') {
    const message = await readJson(req, 256 * 1024)
    if (!message) return json(res, 400, { jsonrpc: '2.0', id: null, error: { code: -32700, message: 'one JSON-RPC message, please' } })
    const reply = await answerRpc(message, toolHandlers(state, store), who)
    if (!reply) { res.writeHead(202); return res.end() }
    return json(res, 200, reply)
  }
  if (url.pathname === '/nidra/asks' && req.method === 'GET') {
    // The questions in flight (and the ones just finished), each with its steps so far: the page draws a being's
    // nidra_ask as it runs from here, since a tool call says nothing until it returns.
    return json(res, 200, { asks: ASKS.recent().slice(0, 20).map(ask => ask.forModel()) })
  }
  const one = url.pathname.match(/^\/nidra\/questions\/([0-9a-f-]{36})$/)
  if (one && req.method === 'GET') {
    const live = ASKS.get(one[1])
    if (live) return json(res, 200, { question: live.record(), live: !live.done })
    const found = await store.question(one[1])
    return found ? json(res, 200, { question: found, live: false }) : json(res, 404, { ok: false, error: 'no such question' })
  }
  const link = url.pathname.match(/^\/nidra\/questions\/([0-9a-f-]{36})\/link$/)
  if (link && req.method === 'POST') {
    // Which Compute run and session asked this question (the page reads it off the session's record, where the
    // nidra_ask call's receipt names the question): added to the question once, never changed.
    const body = await readJson(req)
    const runId = /^wfr-[a-z0-9-]{1,64}$/.test(String(body?.runId || '')) ? body.runId : null
    const sessionId = /^ags_[0-9a-f]{20,32}$/.test(String(body?.sessionId || '')) ? body.sessionId : null
    if (!runId && !sessionId) return json(res, 400, { ok: false, error: 'send {runId, sessionId}' })
    return json(res, 200, await store.link(link[1], { runId, sessionId }))
  }
  if (url.pathname === '/nidra/certify' && req.method === 'POST') {
    if (!who.mayWrite) return json(res, 403, { ok: false, error: 'only the owner may certify' })
    if (state.state !== 'running') return json(res, 503, { ok: false, error: `Nidra's engine is ${state.state}: ${state.reason}` })
    try {
      return json(res, 200, await certify(await readJson(req), who, store))
    } catch (error) {
      return json(res, 400, { ok: false, error: String(error?.message || error) })
    }
  }
  if (url.pathname === '/nidra/whoami' && req.method === 'GET') return json(res, 200, { userId: caller, role, mayWrite: who.mayWrite })

  if (url.pathname.startsWith('/api/')) {
    if (state.state !== 'running') return json(res, 503, { ok: false, error: `Nidra's engine is ${state.state}: ${state.reason}` })
    return engineProxy(req, res, url, caller, store)
  }
  if (url.pathname === '/nidra/history' && req.method === 'GET') {
    const limit = Math.max(1, Math.min(200, Number(url.searchParams.get('limit')) || 50))
    return json(res, 200, { items: await store.history(limit) })
  }
  if (url.pathname === '/nidra/find' && req.method === 'GET') {
    const query = (url.searchParams.get('q') || '').trim()
    if (!query) return json(res, 200, { hits: [] })
    const types = (url.searchParams.get('types') || '').split(',').map(s => s.trim()).filter(Boolean)
    const k = Math.max(1, Math.min(25, Number(url.searchParams.get('k')) || 25))
    return json(res, 200, await store.find(query, types, k))
  }
  if (url.pathname === '/nidra/sources' && req.method === 'GET') {
    return json(res, 200, { graphId: GRAPH, envSources: envSources(), databases: await store.databases() })
  }
  if (url.pathname === '/nidra/ingest' && req.method === 'POST') {
    const body = await readJson(req)
    const database = String(body?.database || '').trim()
    if (!database) return json(res, 400, { ok: false, error: 'name the database to ingest' })
    const response = await fetch(`http://127.0.0.1:${INGEST_PORT}/ingest`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ database }), signal: AbortSignal.timeout(15_000),
    })
    return json(res, response.status, await response.json().catch(() => ({ status: response.statusText })))
  }
  if (url.pathname === '/nidra/settings' && req.method === 'GET') {
    // What the engine runs on, as this service started it: Compute's routing for the being, the egress, and whether a
    // key was there (never the key, never where in a file it lives beyond the setting's name).
    const key = modelKey()
    const { endpoint, model } = modelSettings()
    return json(res, 200, {
      engine: { endpoint, model, keySet: Boolean(key.value), keyFrom: key.from },
      routing: { provider: ROUTING.provider, model: ROUTING.model, from: ROUTING.from, reason: ROUTING.reason ?? null, checkedAt: ROUTING.checkedAt, keyEnv: EGRESS[ROUTING.provider]?.keyEnv ?? null },
      being: ROUTING.being,
      // Retired: the model is Compute's to route now (round 2). PUT answers 410.
      editable: false,
    })
  }
  if (url.pathname === '/nidra/settings' && req.method === 'PUT') {
    return json(res, 410, { ok: false, error: 'Nidra’s model is routed by Sophia Compute now: its being (nidra-chat-v1) names the provider and model, and the key is DEEPSEEK_API_KEY in the profile (gardend service env set DEEPSEEK_API_KEY --profile ~/sophia).' })
  }
  return json(res, 404, { ok: false, error: `no ${req.method} ${url.pathname} here` })
}

/** The engine's API, as it is, with the caller gardend vouched for as the engine's user. Questions are recorded. */
async function engineProxy(req, res, url, caller, store) {
  const body = ['GET', 'HEAD'].includes(req.method || 'GET') ? undefined : await readBody(req)
  const headers = { 'x-auto-ontology-user-id': caller }
  for (const name of ['content-type', 'accept', 'last-event-id']) if (req.headers[name]) headers[name] = String(req.headers[name])
  const abort = new AbortController()
  res.on('close', () => { if (!res.writableFinished) abort.abort() })
  const asking = url.pathname === '/api/chat/completions' && req.method === 'POST'
  let question = null
  if (asking) { try { question = JSON.parse(body.toString('utf8')) } catch {} }
  let upstream
  try {
    upstream = await fetch(`http://127.0.0.1:${ENGINE_PORT}${url.pathname}${url.search}`, { method: req.method, headers, body, signal: abort.signal, duplex: 'half' })
  } catch (error) {
    return json(res, 502, { ok: false, error: `the engine did not answer: ${error?.cause?.code || error.message}` })
  }
  const out = {}
  for (const [name, value] of upstream.headers) if (!['content-length', 'content-encoding', 'transfer-encoding', 'connection'].includes(name)) out[name] = value
  res.writeHead(upstream.status, out)
  if (!upstream.body) return res.end()
  const ask = asking && typeof question?.question === 'string' && upstream.ok
    ? ASKS.add(new Ask({ question: question.question, targetDb: typeof question.target_db === 'string' ? question.target_db : null, caller }))
    : null
  const reader = upstream.body.getReader()
  const sse = ask ? createSseReader(event => (event.type === 'done' ? null : ask.feed(event))) : null
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      sse?.push(value)
      res.write(value)
    }
    sse?.end()
    ask?.finish()
    res.end()
  } catch (error) {
    ask?.finish({ stopped: abort.signal.aborted })
    res.destroy()
  } finally {
    if (ask) store.record(ask.record()).catch(error => log('could not record a question:', error.message))
  }
}

// ── questions a being asks, and the tools gardend lends it (asks.mjs) ──────────────────────────────────────────

const ASKS = new Asks()

/**
 * The engine answers one question at a time: a second one sent while it works comes back at once as an empty stream.
 * So asks wait their turn here, in order, and a waiting ask says so (a "Waiting for the engine" step).
 */
let engineTurn = Promise.resolve()
let engineWaiting = 0

/** Ask the engine in the background; the ask is recorded in the graph when the stream ends, however it ends. */
function startAsk({ question, targetDb, caller, via, context }, store) {
  const ask = ASKS.add(new Ask({ question, targetDb, caller, via, context }))
  const body = JSON.stringify({ question, ...(targetDb ? { target_db: targetDb } : {}) })
  const previous = engineTurn
  let release
  engineTurn = new Promise(resolve => { release = resolve })
  const ahead = ASKS.live().filter(other => other !== ask).length
  if (ahead > 0) ask.feed({ type: 'step', phase: 'start', node: 'queue', label: `Waiting for the engine (${ahead} ${ahead === 1 ? 'question' : 'questions'} ahead)` })
  engineWaiting++
  ;(async () => {
    await previous
    engineWaiting--
    if (ahead > 0) ask.feed({ type: 'step', phase: 'end', node: 'queue', label: 'Waiting for the engine', thought: null })
    let events = 0
    try {
      const response = await fetch(`http://127.0.0.1:${ENGINE_PORT}/api/chat/completions`, {
        method: 'POST', headers: { 'content-type': 'application/json', accept: 'text/event-stream', 'x-auto-ontology-user-id': caller }, body,
        // The engine's own budget ends a question; this only keeps a wedged stream from living forever.
        signal: AbortSignal.timeout(15 * 60_000),
      })
      if (!response.ok || !response.body) {
        const text = await response.text().catch(() => '')
        ask.feed({ type: 'error', message: `the engine answered ${response.status}: ${text.slice(0, 300)}` })
        return ask.finish()
      }
      const sse = createSseReader(event => { if (event.type !== 'done') { events++; ask.feed(event) } })
      const reader = response.body.getReader()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        sse.push(value)
      }
      sse.end()
      if (events === 0) ask.feed({ type: 'error', message: 'the engine ended the question without a word (it was busy with another, or its worker stopped): ask again' })
      ask.finish()
    } catch (error) {
      ask.feed({ type: 'error', message: `the engine stream broke: ${error?.cause?.code || error?.message || error}` })
      ask.finish({ stopped: true })
    } finally {
      release()
      store.record(ask.record()).catch(error => log('could not record a question:', error.message))
    }
  })()
  return ask
}

const text = value => (typeof value === 'string' && value.trim() ? value.trim() : null)
const AGENT_ID = /^agent-[0-9a-f]{16}$/

function toolHandlers(state, store) {
  const ready = () => { if (state.state !== 'running') throw new Error(`Nidra's engine is ${state.state}: ${state.reason}`) }
  const engineGet = async pathname => {
    const response = await fetch(`http://127.0.0.1:${ENGINE_PORT}/api${pathname}`, { headers: { 'x-auto-ontology-user-id': 'service:nidra' }, signal: AbortSignal.timeout(30_000) })
    const body = await response.json().catch(() => null)
    if (!response.ok) throw new Error(`the engine answered ${response.status} for ${pathname}: ${JSON.stringify(body?.detail ?? body).slice(0, 300)}`)
    return body
  }
  return {
    async ask(args, who) {
      ready()
      const question = text(args.question)
      if (!question) throw new Error('nidra_ask needs a question')
      const context = { runId: text(args.run_id), sessionId: text(args.session_id), agentId: AGENT_ID.test(String(args.agent_id || '')) ? args.agent_id : null }
      const ask = startAsk({ question, targetDb: text(args.target_db), caller: who.userId, via: who.userId.startsWith('service:') ? who.userId.slice(8) : null, context }, store)
      await ask.wait(TOOL_BUDGET_MS)
      return ask.forModel()
    },
    async question(args) {
      const id = String(args.question_id || '').trim()
      const live = ASKS.get(id)
      if (live) {
        await live.wait(Math.min(TOOL_BUDGET_MS / 1000, Math.max(0, Number(args.wait_seconds ?? TOOL_BUDGET_MS / 1000))) * 1000)
        return live.forModel()
      }
      const found = await store.question(id)
      if (!found) throw new Error(`no question ${id}`)
      return { status: found.ok ? 'answered' : 'failed', questionId: id, ...found, rows: (found.rows || []).slice(0, 50) }
    },
    async terms(args) {
      ready()
      const params = new URLSearchParams({ skip: '0', limit: String(Math.max(1, Math.min(200, Number(args.limit) || 100))) })
      if (text(args.query)) params.set('query', text(args.query))
      const page = await engineGet(`/terms?${params}`)
      const counts = new Map((page.column_attribute_counts || []).map(row => [row.term_id, row.count]))
      return {
        total: page.total,
        terms: (page.terms || []).map(term => ({
          id: term.id, name: term.name, description: term.description ?? null, synonyms: term.synonyms ?? [],
          name_certified: Boolean(term.name_certified), description_certified: Boolean(term.description_certified),
          certification: term.certification ?? null, column_attributes: counts.get(term.id) ?? 0,
        })),
      }
    },
    async term(args) {
      ready()
      const id = encodeURIComponent(String(args.term_id || '').trim())
      if (!id) throw new Error('nidra_term needs a term_id')
      const [term, columns, sql] = await Promise.all([engineGet(`/terms/${id}`), engineGet(`/terms/${id}/column-attributes`), engineGet(`/terms/${id}/sql-attributes`)])
      return { term: term.data ?? term, column_attributes: columns.data ?? [], sql_attributes: sql.data ?? [] }
    },
    async certify(args, who) {
      ready()
      return certify(args, who, store)
    },
  }
}

/**
 * Certify (or withdraw) one part of a term, through the engine's own PATCH (it refreshes what depends on it), and keep
 * the act as a row in the graph: who, when, which part, the value it set and the note. Testimony in Emporium is written
 * by the witness itself (a person from the Glossary page, a being through emporium_write): this row is the service's
 * own receipt of the engine write.
 */
async function certify(args, who, store) {
  const termId = text(args?.term_id)
  const part = String(args?.part || '')
  const attributeId = text(args?.attribute_id)
  if (!termId) throw new Error('certify needs a term_id')
  if (typeof args?.certified !== 'boolean') throw new Error('certified must be true or false')
  let pathname
  let patch
  if (part === 'name' || part === 'description') { pathname = `/api/terms/${encodeURIComponent(termId)}`; patch = { [`${part}_certified`]: args.certified } }
  else if (part === 'column_attribute' && attributeId) { pathname = `/api/terms/${encodeURIComponent(termId)}/column-attributes/${encodeURIComponent(attributeId)}`; patch = { certified: args.certified } }
  else if (part === 'sql_attribute' && attributeId) { pathname = `/api/sql-attributes/${encodeURIComponent(attributeId)}`; patch = { certified: args.certified } }
  else throw new Error('part is name, description, column_attribute or sql_attribute (the last two with attribute_id)')
  const response = await fetch(`http://127.0.0.1:${ENGINE_PORT}${pathname}`, {
    method: 'PATCH', headers: { 'content-type': 'application/json', 'x-auto-ontology-user-id': who.userId }, body: JSON.stringify(patch), signal: AbortSignal.timeout(60_000),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(`the engine refused the certification (${response.status}): ${JSON.stringify(body?.detail ?? body).slice(0, 300)}`)
  const act = {
    id: randomUUID(), termId, part, attributeId, certified: args.certified, note: text(args.note), by: who.userId,
    agentId: AGENT_ID.test(String(args.agent_id || '')) ? args.agent_id : null, runId: text(args.run_id), at: new Date().toISOString(),
  }
  await store.certification(act).catch(error => log('could not record a certification:', error.message))
  // A being's certification is its testimony too, in its own memory (gardend admits this service's memory writes
  // only under an agent observer: service.json `agentMemory`). A person's testimony is written by the person, from
  // the Glossary page, with their own key.
  let testimony = null
  if (act.agentId) testimony = await store.testify(act).catch(error => { log('could not write the being\'s testimony:', error.message); return null })
  return { ok: true, certification: act, iri: `urn:nidra:certification/${act.id}`, testimony }
}

function envSources() {
  return SOURCES.split(',').map(s => s.trim()).filter(Boolean).map(raw => {
    const dialect = (raw.match(/^([a-z0-9+]+):/i)?.[1] || 'unknown').split('+')[0]
    const redacted = raw.replace(/(\/\/)([^@/]+)@/, (_, slashes, auth) => `${slashes}${auth.split(':')[0]}:•••@`)
    const label = dialect === 'sqlite' ? path.basename(raw.replace(/^sqlite:\/+/, '/')) : (raw.match(/\/([^/?]+)(\?|$)/)?.[1] || dialect)
    return { label, dialect, redacted }
  })
}

// ── stopping for good ───────────────────────────────────────────────────────────────────────────────────

async function reap(spawner) {
  fs.writeFileSync(REAPER_PID, `${process.pid}\n`, { mode: 0o600 })
  let cancelled = false
  process.on('SIGTERM', () => { cancelled = true; process.exit(0) })
  // `disable` writes `off` before it stops the service: stop the containers now. Otherwise (a restart, or gardend
  // going away) wait: a service process that comes back ends this one first.
  const disabled = (() => { try { return fs.readFileSync(AUTOSTART_FILE, 'utf8').trim() === 'off' } catch { return false } })()
  if (!disabled) await sleep(20_000)
  const current = readPid(WRAPPER_PID)
  if (cancelled || (current && current !== spawner && isOurs(current, 'main.mjs'))) return cleanup()
  log(`nidra: the service is ${disabled ? 'disabled' : 'gone'}; stopping the engine containers`)
  const result = spawnSync('docker', ['compose', '-p', PROJECT, 'stop', '-t', '10'], { env: process.env, encoding: 'utf8', timeout: 120_000 })
  log(`nidra: containers ${result.status === 0 ? 'stopped' : `not stopped (${tail(result.stderr, 200)})`}`)
  cleanup()

  function cleanup() {
    try { if (readPid(REAPER_PID) === process.pid) fs.unlinkSync(REAPER_PID) } catch {}
  }
}

function readPid(file) {
  try { const pid = Number(fs.readFileSync(file, 'utf8').trim()); return Number.isInteger(pid) && pid > 1 ? pid : null } catch { return null }
}

/** Is `pid` a live process running this file (with `marker` in its command line)? Never signal a pid we did not check. */
function isOurs(pid, marker) {
  try { process.kill(pid, 0) } catch { return false }
  const result = spawnSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8' })
  const command = (result.stdout || '').trim()
  return command.includes(fileURLToPath(import.meta.url)) && command.includes(marker)
}

// ── the fence: the engine's only door to gardend ────────────────────────────────────────────────────────

async function fenceRequest(req, res, { gardend, member, rdfToken, fence, store }) {
  try {
    const given = Buffer.from(String(req.headers.authorization || '').replace(/^Bearer\s+/i, ''))
    const want = Buffer.from(fence)
    if (given.length !== want.length || !timingSafeEqual(given, want)) return json(res, 401, { ok: false, error: 'not the Nidra engine' })
    const url = new URL(req.url || '/', 'http://fence.local')
    if (req.method !== 'POST') return json(res, 405, { ok: false, error: 'the fence forwards POSTs only' })
    const raw = await readBody(req, 64 * 1024 * 1024)
    let body
    try { body = JSON.parse(raw.toString('utf8')) } catch { return json(res, 400, { ok: false, error: 'JSON bodies only' }) }

    if (url.pathname === '/mcp') {
      const calls = Array.isArray(body) ? body : [body]
      for (const call of calls) {
        const method = String(call?.method || '')
        const allowed = method === 'initialize' || method.startsWith('notifications/')
          || (method === 'tools/call' && call?.params?.name === 'find' && call?.params?.arguments?.graphId === GRAPH)
        if (!allowed) return json(res, 403, { jsonrpc: '2.0', id: call?.id ?? null, error: { code: -32003, message: `the Nidra fence forwards only find on graph ${GRAPH}` } })
      }
      const headers = { authorization: `Bearer ${member}`, 'content-type': 'application/json', accept: String(req.headers.accept || 'application/json, text/event-stream') }
      if (req.headers['mcp-session-id']) headers['mcp-session-id'] = String(req.headers['mcp-session-id'])
      return forward(res, `${gardend}/mcp`, headers, raw)
    }
    if (body?.graphId !== GRAPH) return json(res, 403, { ok: false, error: `the Nidra fence forwards graph ${GRAPH} only` })
    if (url.pathname === '/api/graphs') {
      // The engine's ensure_graph: the graph is created once at install; say it exists (the words its client expects).
      return (await store.graphExists())
        ? json(res, 409, { ok: false, error: `graph ${GRAPH} already exists` })
        : json(res, 403, { ok: false, error: `graph ${GRAPH} does not exist; create it in Sophia (the engine may not)` })
    }
    if (!['/api/sparql/query', '/api/sparql/update', '/api/rdf/load'].includes(url.pathname))
      return json(res, 403, { ok: false, error: `the Nidra fence does not forward ${url.pathname}` })
    return forward(res, `${gardend}${url.pathname}`, { authorization: `Bearer ${rdfToken}`, 'content-type': 'application/json' }, raw)
  } catch (error) {
    log('fence: request failed:', String(error?.message || error))
    if (!res.headersSent) json(res, 502, { ok: false, error: 'the fence could not reach gardend' })
  }
}

async function forward(res, target, headers, body) {
  const upstream = await fetch(target, { method: 'POST', headers, body })
  const out = {}
  for (const name of ['content-type', 'mcp-session-id', 'cache-control']) {
    const value = upstream.headers.get(name)
    if (value) out[name] = value
  }
  res.writeHead(upstream.status, out)
  if (!upstream.body) return res.end()
  const reader = upstream.body.getReader()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    res.write(value)
  }
  res.end()
}

// ── this service's own reads and writes of the graph ────────────────────────────────────────────────────

class Gardend {
  constructor(base, member, rdfToken) {
    this.base = base
    this.member = member
    this.rdfToken = rdfToken
    this.session = null
  }
  async post(pathname, body, token) {
    const response = await fetch(`${this.base}${pathname}`, {
      method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(60_000),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || payload?.ok === false) throw new Error(`${pathname}: HTTP ${response.status}: ${String(payload?.error || payload?.message || '').slice(0, 300)}`)
    return payload
  }
  async select(sparql) {
    const payload = await this.post('/api/sparql/query', { graphId: GRAPH, sparql }, this.rdfToken || this.member)
    return (payload.rows || []).map(row => Object.fromEntries(Object.entries(row).map(([key, term]) => [key, parseTerm(term)])))
  }
  async update(sparql) {
    await this.post('/api/sparql/update', { graphId: GRAPH, sparql }, this.rdfToken)
  }
  async graphExists() {
    const response = await fetch(`${this.base}/api/graphs`, { headers: { authorization: `Bearer ${this.member}` }, signal: AbortSignal.timeout(20_000) })
    const payload = await response.json().catch(() => null)
    if (!response.ok) throw new Error(`/api/graphs: HTTP ${response.status}`)
    const list = Array.isArray(payload) ? payload : payload?.graphs || payload?.data || []
    return list.some(graph => (graph?.graphId || graph?.graph_id || graph?.id) === GRAPH)
  }
  async record(item) {
    const s = `<${NS}question/${item.id}>`
    const triples = [
      `${s} a <${Q}>`,
      `${s} <${SKOS_LABEL}> ${lit(item.question)}`,
      `${s} ${P('question')} ${lit(item.question)}`,
      `${s} ${P('askedBy')} ${lit(item.askedBy)}`,
      `${s} ${P('askedAt')} "${item.askedAt}"^^<${XSD}dateTime>`,
      `${s} ${P('ok')} "${item.ok}"^^<${XSD}boolean>`,
    ]
    if (item.seconds !== null) triples.push(`${s} ${P('seconds')} "${item.seconds}"^^<${XSD}decimal>`)
    if (item.rowCount !== null && item.rowCount !== undefined) triples.push(`${s} ${P('rowCount')} "${item.rowCount}"^^<${XSD}integer>`)
    for (const field of ['answer', 'sql', 'thoughts', 'error', 'targetDb', 'via', 'runId', 'sessionId', 'partialAnswer']) if (item[field]) triples.push(`${s} ${P(field)} ${lit(item[field])}`)
    // The being that asked (a Choreograph agent id), as the agent IRI gardend's memory uses for a witness.
    if (item.agentId) triples.push(`${s} ${P('askedByAgent')} <urn:sophia:agent:${item.agentId}>`)
    for (const field of ['rows', 'steps', 'sqlColumns', 'sqlDrafts']) if (item[field] && item[field].length) triples.push(`${s} ${P(field)} ${lit(JSON.stringify(item[field]))}^^<${RDF_JSON}>`)
    for (const column of item.sqlColumns || []) if (/^[0-9a-f-]{36}$/.test(column)) triples.push(`${s} ${P('column')} <urn:ao:catalog_column/id=${column}>`)
    await this.update(`INSERT DATA { GRAPH <${dataGraph()}> { ${triples.join(' .\n')} . } }`)
    log(`history: recorded a question (${item.ok ? 'answered' : 'unanswered'}, ${item.seconds}s${item.via ? `, via ${item.via}` : ''})`)
  }
  static FIELDS = ['question', 'answer', 'sql', 'rows', 'thoughts', 'targetDb', 'askedBy', 'askedAt', 'ok', 'error', 'seconds', 'steps', 'sqlColumns', 'rowCount', 'via', 'runId', 'sessionId', 'partialAnswer', 'askedByAgent']
  item(id, row) {
    return {
      id,
      question: row.question ?? '', answer: row.answer ?? null, sql: row.sql ?? null,
      rows: Array.isArray(row.rows) ? row.rows : null, thoughts: row.thoughts ?? null, targetDb: row.targetDb ?? null,
      askedBy: row.askedBy ?? null, askedAt: row.askedAt instanceof Date ? row.askedAt.toISOString() : String(row.askedAt ?? ''),
      ok: row.ok === true, error: row.error ?? null, seconds: typeof row.seconds === 'number' ? row.seconds : null,
      steps: Array.isArray(row.steps) ? row.steps : null, sqlColumns: Array.isArray(row.sqlColumns) ? row.sqlColumns : null,
      rowCount: typeof row.rowCount === 'number' ? row.rowCount : (Array.isArray(row.rows) ? row.rows.length : null),
      via: row.via ?? null, runId: row.runId ?? null, sessionId: row.sessionId ?? null, partialAnswer: row.partialAnswer ?? null,
      agentId: typeof row.askedByAgent === 'string' ? row.askedByAgent.replace(/^urn:sophia:agent:/, '') : null,
    }
  }
  async history(limit) {
    const fields = Gardend.FIELDS
    const rows = await this.select(`SELECT ?s ${fields.map(f => `?${f}`).join(' ')} WHERE { GRAPH ?g { ?s a <${Q}> ; ${P('question')} ?question ; ${P('askedAt')} ?askedAt .
      ${fields.filter(f => !['question', 'askedAt'].includes(f)).map(f => `OPTIONAL { ?s ${P(f)} ?${f} }`).join('\n      ')} } } ORDER BY DESC(?askedAt) LIMIT ${limit}`)
    return rows.map(row => this.item(String(row.s).slice(`${NS}question/`.length), row))
  }
  async question(id) {
    if (!/^[0-9a-f-]{36}$/.test(id)) return null
    const fields = Gardend.FIELDS
    const s = `<${NS}question/${id}>`
    const rows = await this.select(`SELECT ${fields.map(f => `?${f}`).join(' ')} WHERE { GRAPH ?g { ${s} ${P('question')} ?question ; ${P('askedAt')} ?askedAt .
      ${fields.filter(f => !['question', 'askedAt'].includes(f)).map(f => `OPTIONAL { ${s} ${P(f)} ?${f} }`).join('\n      ')} } } LIMIT 1`)
    return rows.length ? this.item(id, rows[0]) : null
  }
  /**
   * Nidra in its graph's cast: the being Compute runs (its id, name and face, from Compute's host facts) as an agt:Agent
   * in the graph's own RDF, which the Compute surface (/choreograph › Agents) and agent_status read. Added once, never
   * replaced: a later run finds it there and writes nothing. Choreograph has no being directory of its own.
   */
  async registerBeing(being) {
    if (!being?.agentId || !/^agent-[0-9a-f]{16}$/.test(being.agentId)) return
    const s = `<urn:sophia:agent:${being.agentId}>`
    const AGT = 'http://mnemosyne.dev/agent#'
    const known = await this.select(`SELECT ?label WHERE { GRAPH ?g { ${s} a <${AGT}Agent> ; <http://www.w3.org/2000/01/rdf-schema#label> ?label } } LIMIT 1`)
    if (known.length) return
    const triples = [
      `${s} a <${AGT}Agent>`,
      `${s} <http://www.w3.org/2000/01/rdf-schema#label> ${lit(being.label || 'Nidra')}`,
      `${s} <${AGT}agentId> ${lit(being.agentId)}`,
      `${s} <${AGT}kind> "nidra"`,
      `${s} <${AGT}homeGraph> ${lit(being.home || GRAPH)}`,
      `${s} <http://purl.org/dc/terms/created> "${new Date().toISOString()}"^^<${XSD}dateTime>`,
    ]
    if (being.registeredName) triples.push(`${s} <${AGT}registeredName> ${lit(being.registeredName)}`)
    if (being.face) triples.push(`${s} <${AGT}face> <${being.face}>`)
    if (being.witness?.memory) triples.push(`${s} <${AGT}memoryWitness> <${being.witness.memory}>`)
    await this.update(`INSERT DATA { GRAPH <${dataGraph()}> { ${triples.join(' .\n')} . } }`)
    log(`cast: registered ${being.label || 'Nidra'} (${being.agentId}) in graph ${GRAPH}`)
  }
  /** A being's certification as its own memory record (sophia-memory-core, through `remember`, the being as witness). */
  async testify(act) {
    const what = act.part === 'name' ? 'name' : act.part === 'description' ? 'description' : act.part === 'column_attribute' ? `column attribute ${act.attributeId}` : `SQL attribute ${act.attributeId}`
    const content = `${act.certified ? 'Certified' : 'Withdrew the certification of'} the ${what} of glossary term ${act.termId}${act.note ? `: ${act.note}` : '.'}`
    const result = await this.tool('remember', {
      graph_id: GRAPH, observer_agent_id: act.agentId, content, kind: 'ClaimMemory', scope: 'graph', content_orientation: 'knowledge',
      visibility: 'shared', status: 'active', tags: ['nidra', 'certification', act.certified ? 'certified' : 'uncertified', `part:${act.part}`, `term:${act.termId}`],
      source_refs: [{ sourceKind: 'ArtifactReference', externalUri: `urn:ao:term/id=${act.termId}`, label: `term ${act.termId}` },
        { sourceKind: 'ToolCall', externalUri: `urn:nidra:certification/${act.id}`, label: 'nidra_certify' }],
    })
    log(`glossary: the being's testimony for a certification is ${result?.subject ? 'written' : 'not written'}`)
    return result?.subject ?? null
  }
  async tool(name, args) {
    if (!this.session) {
      await this.mcp({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'nidra-service', version: '2' } } })
      await this.mcp({ jsonrpc: '2.0', method: 'notifications/initialized' })
    }
    const { payload, status } = await this.mcp({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name, arguments: args } })
    if (!payload || payload.error) throw new Error(`${name}: ${payload?.error?.message || `HTTP ${status}`}`)
    if (payload.result?.isError) throw new Error(`${name}: ${(payload.result.content || []).map(c => c.text).join(' ').slice(0, 300)}`)
    let found = payload.result?.structuredContent
    if (!found) for (const item of payload.result?.content || []) if (item.type === 'text') { try { found = JSON.parse(item.text) } catch {} }
    return found ?? null
  }
  async link(id, { runId, sessionId }) {
    const s = `<${NS}question/${id}>`
    const known = await this.select(`SELECT ?runId ?sessionId WHERE { GRAPH ?g { ${s} ${P('question')} ?q . OPTIONAL { ${s} ${P('runId')} ?runId } OPTIONAL { ${s} ${P('sessionId')} ?sessionId } } } LIMIT 1`)
    if (!known.length) return { ok: false, error: 'no such question' }
    const triples = []
    if (runId && !known[0].runId) triples.push(`${s} ${P('runId')} ${lit(runId)}`, `${s} ${P('run')} <urn:sophia:wf-run:${runId}>`)
    if (sessionId && !known[0].sessionId) triples.push(`${s} ${P('sessionId')} ${lit(sessionId)}`)
    if (triples.length) await this.update(`INSERT DATA { GRAPH <${dataGraph()}> { ${triples.join(' .\n')} . } }`)
    return { ok: true, added: triples.length }
  }
  async certification(act) {
    const C = `${NS}Certification`
    const s = `<${NS}certification/${act.id}>`
    const term = `<urn:ao:term/id=${act.termId}>`
    const triples = [
      `${s} a <${C}>`,
      `${s} <${C}#term> ${term}`,
      `${s} <${C}#part> ${lit(act.part)}`,
      `${s} <${C}#certified> "${act.certified}"^^<${XSD}boolean>`,
      `${s} <${C}#by> ${lit(act.by)}`,
      `${s} <${C}#at> "${act.at}"^^<${XSD}dateTime>`,
    ]
    if (act.attributeId) triples.push(`${s} <${C}#attribute> ${lit(act.attributeId)}`)
    if (act.note) triples.push(`${s} <${C}#note> ${lit(act.note)}`)
    if (act.agentId) triples.push(`${s} <${C}#byAgent> <urn:sophia:agent:${act.agentId}>`)
    if (act.runId) triples.push(`${s} <${C}#runId> ${lit(act.runId)}`)
    await this.update(`INSERT DATA { GRAPH <${dataGraph()}> { ${triples.join(' .\n')} . } }`)
    log(`glossary: recorded a certification (${act.part}, ${act.certified}, by ${act.by})`)
  }
  async databases() {
    const T = name => `<urn:ao:${name}>`
    const rows = await this.select(`SELECT ?db ?name ?dialect ?pulled (COUNT(DISTINCT ?t) AS ?tables) (COUNT(DISTINCT ?c) AS ?columns) WHERE { GRAPH ?g {
      ?db a ${T('catalog_database')} ; <urn:ao:catalog_database#name> ?name .
      OPTIONAL { ?db <urn:ao:catalog_database#dialect> ?dialect }
      OPTIONAL { ?db <urn:ao:catalog_database#pulled> ?pulled }
      OPTIONAL { ?s <urn:ao:catalog_schema#ref-database_id> ?db . ?t <urn:ao:catalog_table#ref-schema_id> ?s .
                 OPTIONAL { ?c <urn:ao:catalog_column#ref-table_id> ?t } } } }
      GROUP BY ?db ?name ?dialect ?pulled ORDER BY ?name`)
    return rows.map(row => ({
      name: row.name, dialect: row.dialect ?? null,
      pulled: row.pulled instanceof Date ? row.pulled.toISOString() : row.pulled ? String(row.pulled) : null,
      tables: Number(row.tables) || 0, columns: Number(row.columns) || 0,
    }))
  }
  async mcp(body) {
    const headers = { authorization: `Bearer ${this.member}`, 'content-type': 'application/json', accept: 'application/json, text/event-stream' }
    if (this.session) headers['mcp-session-id'] = this.session
    const response = await fetch(`${this.base}/mcp`, { method: 'POST', headers, body: JSON.stringify(body), signal: AbortSignal.timeout(60_000) })
    const session = response.headers.get('mcp-session-id')
    if (session) this.session = session
    const text = await response.text()
    let payload = null
    for (const line of text.split('\n')) {
      const candidate = line.replace(/^data:\s*/, '').trim()
      if (candidate.startsWith('{')) { try { payload = JSON.parse(candidate) } catch {} }
    }
    return { status: response.status, payload }
  }
  async find(query, types, k) {
    if (!this.session) {
      await this.mcp({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'nidra-service', version: '1' } } })
      await this.mcp({ jsonrpc: '2.0', method: 'notifications/initialized' })
    }
    const args = { graphId: GRAPH, query, k, ...(types.length ? { types } : {}) }
    let { payload, status } = await this.mcp({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'find', arguments: args } })
    if (status === 404 || status === 400) { // a session gardend forgot: start again once
      this.session = null
      return this.find(query, types, k)
    }
    if (!payload || payload.error) throw new Error(`find: ${payload?.error?.message || `HTTP ${status}`}`)
    const result = payload.result || {}
    if (result.isError) throw new Error(`find: ${(result.content || []).map(c => c.text).join(' ').slice(0, 300)}`)
    let found = result.structuredContent
    if (!found) for (const item of result.content || []) if (item.type === 'text') { try { found = JSON.parse(item.text) } catch {} }
    return { hits: Array.isArray(found?.hits) ? found.hits : [] }
  }
}

// ── small parts ─────────────────────────────────────────────────────────────────────────────────────────

const ESCAPES = { '\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t' }
function lit(value) {
  return `"${String(value).replace(/[\\"\n\r\t]/g, ch => ESCAPES[ch])}"`
}

const TERM = /^(?:<([^>]*)>|(_:\S+)|"((?:[^"\\]|\\.)*)"(?:\^\^<([^>]*)>|@([A-Za-z0-9-]+))?)$/s
const UNESCAPE = { n: '\n', r: '\r', t: '\t', '"': '"', '\\': '\\', b: '\b', f: '\f' }
function parseTerm(term) {
  if (term === null || term === undefined) return null
  const match = TERM.exec(String(term))
  if (!match) return term
  if (match[1] !== undefined) return match[1]
  if (match[2] !== undefined) return match[2]
  const lexical = match[3].replace(/\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)/g, (_, code) =>
    code.length > 1 ? String.fromCodePoint(parseInt(code.slice(1), 16)) : UNESCAPE[code] ?? code)
  const datatype = match[4]
  if (!datatype) return lexical
  if (datatype === `${XSD}boolean`) return lexical === 'true'
  if (/#(integer|int|long|short|nonNegativeInteger|decimal|double|float)$/.test(datatype)) return Number(lexical)
  if (datatype === `${XSD}dateTime`) return new Date(lexical)
  if (datatype === RDF_JSON) { try { return JSON.parse(lexical) } catch { return lexical } }
  return lexical
}

function readBody(req, max = 4 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', chunk => {
      size += chunk.length
      if (size > max) { reject(new Error('request too large')); req.destroy() } else chunks.push(chunk)
    })
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

async function readJson(req, max = 64 * 1024) {
  try { return JSON.parse((await readBody(req, max)).toString('utf8')) } catch { return null }
}

function json(res, status, value) {
  const body = JSON.stringify(value)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
  res.end(body)
}

// Last, so every class and constant above is initialized before any of it runs.
const mode = process.argv[2]
if (mode === '--reap') await reap(Number(process.argv[3]))
else await serve()
