// Nidra's questions, as this service runs them: one ask of the engine's agent, read as it streams, kept in the graph,
// and lent to gardend's /mcp as tools (service.json `mcp`), so a Choreograph being (Nidra) can ask the catalog, read the
// glossary and certify it the way a person does on the Glossary screen.
//
// Pure parts (the stream reader, the record, the tool table and the JSON-RPC dispatch) live here so they can be tested
// against a real captured stream (fixtures/); main.mjs supplies the engine, the graph store and who is calling.
import { randomUUID } from 'node:crypto'

/** The rows an answer carries to a model: enough to read, not a table dump. The record keeps up to ROW_KEEP. */
export const ROWS_FOR_MODEL = 50
export const ROW_KEEP = 1000
/**
 * How long a tool call waits for the engine before it answers "running" (and the question goes on in the background):
 * under the shortest caller's patience. gardend gives a lent tool up to 120 s; Sophia Compute's MCP proxy gives a call
 * 60 s (MNEMOSYNE_REQUEST_TIMEOUT_MS).
 */
export const TOOL_BUDGET_MS = Number(process.env.NIDRA_TOOL_BUDGET_MS || 45_000)
/** A finished ask stays in memory this long (for a tool or a page that asks after it), then lives only in the graph. */
const KEEP_FINISHED_MS = 30 * 60_000

/** Reads the engine's SSE (`POST /api/chat/completions`) incrementally: chunks may split anything anywhere. */
export function createSseReader(onEvent) {
  const decoder = new TextDecoder()
  let buffer = ''
  const flush = block => {
    const data = block.split('\n').map(line => line.replace(/\r$/, '')).filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).replace(/^ /, '')).join('\n')
    if (!data) return
    if (data.trim() === '[DONE]') return onEvent({ type: 'done' })
    try { onEvent(JSON.parse(data)) } catch {}
  }
  return {
    push(chunk) {
      buffer += typeof chunk === 'string' ? chunk : decoder.decode(chunk, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let cut
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        flush(buffer.slice(0, cut))
        buffer = buffer.slice(cut + 2)
      }
    },
    end() {
      buffer += decoder.decode()
      if (buffer.trim()) flush(buffer)
      buffer = ''
    },
  }
}

/** `sql_response_from_db` is a list of JSON strings, each a list of rows; the rows, at most ROW_KEEP of them. */
export function rowsOf(value) {
  if (!Array.isArray(value)) return null
  const rows = []
  for (const part of value) {
    let parsed = part
    if (typeof part === 'string') { try { parsed = JSON.parse(part) } catch { continue } }
    if (Array.isArray(parsed)) rows.push(...parsed.filter(row => row && typeof row === 'object'))
  }
  return rows
}

const MODEL_FAILED = /^LLM failed to produce a response/

/**
 * One question to the engine. `feed` takes the engine's events; `steps` are the agent's nodes as they ran (a node that
 * runs twice is two steps); `sqlDrafts` every SQL the agent showed before it ran one. Times are the service's clock.
 */
export class Ask {
  constructor({ question, targetDb = null, caller = 'owner', via = null, context = {}, now = Date.now }) {
    this.id = randomUUID()
    this.question = String(question)
    this.targetDb = targetDb || null
    this.caller = caller
    /** Who relayed it: null for a person's own page, else e.g. `choreograph` (a being's tool call). */
    this.via = via
    /** What the caller said it belongs to: a Choreograph run, session, being (agent id). */
    this.context = context
    this.now = now
    this.startedAt = now()
    this.endedAt = null
    this.steps = []
    this.sqlDrafts = []
    this.result = null
    this.error = null
    this.stopped = false
    this.waiters = []
  }

  get done() {
    return this.endedAt !== null
  }

  feed(event) {
    if (!event || typeof event !== 'object' || this.done) return
    const at = this.now()
    if (event.type === 'step') {
      const node = String(event.node || 'step')
      const label = typeof event.label === 'string' && event.label.trim() ? event.label : node.replace(/_/g, ' ')
      const open = [...this.steps].reverse().find(step => step.node === node && step.endedAt === null)
      if (event.phase === 'end') {
        if (open) {
          open.endedAt = at
          if (typeof event.thought === 'string' && event.thought.trim()) open.thought = event.thought
        } else this.steps.push({ node, label, startedAt: at, endedAt: at, thought: typeof event.thought === 'string' ? event.thought : null })
      } else if (!open) this.steps.push({ node, label, startedAt: at, endedAt: null, thought: null })
    } else if (event.type === 'sql' && typeof event.sql === 'string') {
      this.sqlDrafts.push({ node: String(event.node || ''), sql: event.sql, at })
    } else if (event.type === 'result') {
      this.result = event.answer && typeof event.answer === 'object' ? event.answer : event
    } else if (event.type === 'error') {
      const detail = event.detail && typeof event.detail === 'object' ? event.detail : null
      this.error = String(event.message || event.error || (typeof event.detail === 'string' ? event.detail : '') || detail?.message || 'the engine reported an error')
      if (event.partial_answer && typeof event.partial_answer === 'object') this.partial = event.partial_answer
    }
  }

  /** The stream ended (or was cut): close what is open, wake whoever waits. */
  finish({ stopped = false } = {}) {
    if (this.done) return
    this.stopped = stopped
    this.endedAt = this.now()
    for (const step of this.steps) if (step.endedAt === null) step.endedAt = this.endedAt
    for (const wake of this.waiters.splice(0)) wake()
  }

  /** Resolves when the ask is done, or after `ms`, whichever is first. */
  wait(ms) {
    if (this.done) return Promise.resolve(true)
    return new Promise(resolve => {
      const timer = setTimeout(() => { this.waiters = this.waiters.filter(w => w !== wake); resolve(false) }, Math.max(0, ms))
      const wake = () => { clearTimeout(timer); resolve(true) }
      this.waiters.push(wake)
    })
  }

  /** The record as the graph keeps it (and `history` reads back). */
  record() {
    const answer = this.result || {}
    const response = typeof answer.response === 'string' ? answer.response : null
    const modelFailed = MODEL_FAILED.test(String(response || ''))
    const partial = this.partial || null
    return {
      id: this.id,
      question: this.question,
      targetDb: this.targetDb,
      answer: response,
      sql: typeof answer.sql_code === 'string' && answer.sql_code ? answer.sql_code : (partial && typeof partial.sql_code === 'string' ? partial.sql_code : null),
      sqlColumns: Array.isArray(answer.sql_columns) ? answer.sql_columns.map(String) : null,
      rows: (rowsOf(answer.sql_response_from_db) || null)?.slice(0, ROW_KEEP) ?? null,
      rowCount: rowsOf(answer.sql_response_from_db)?.length ?? null,
      thoughts: typeof answer.thoughts === 'string' ? answer.thoughts : null,
      steps: this.steps.map(step => ({ ...step })),
      sqlDrafts: this.sqlDrafts.map(draft => ({ ...draft })),
      askedBy: this.caller,
      via: this.via,
      runId: this.context.runId || null,
      sessionId: this.context.sessionId || null,
      agentId: this.context.agentId || null,
      askedAt: new Date(this.startedAt).toISOString(),
      seconds: this.endedAt === null ? null : Math.round((this.endedAt - this.startedAt) / 100) / 10,
      ok: Boolean(this.result) && !this.error && !modelFailed,
      error: this.error || (!this.result ? (this.stopped ? 'Stopped before an answer' : 'No answer came back')
        : modelFailed ? 'The model did not answer (every call to it failed)' : null),
      partialAnswer: partial && typeof partial.response === 'string' ? partial.response : null,
    }
  }

  /** What a tool call hands back: the answer, the SQL, the first rows, the steps with their seconds. */
  forModel() {
    const record = this.record()
    const rows = record.rows || []
    const seconds = step => (step.endedAt === null ? null : Math.round((step.endedAt - step.startedAt) / 100) / 10)
    return {
      status: this.done ? (record.ok ? 'answered' : 'failed') : 'running',
      questionId: record.id,
      question: record.question,
      answer: record.answer,
      sql: record.sql,
      columns: rows.length ? Object.keys(rows[0]) : [],
      rows: rows.slice(0, ROWS_FOR_MODEL),
      rowCount: record.rowCount,
      rowsShown: Math.min(rows.length, ROWS_FOR_MODEL),
      sqlColumns: record.sqlColumns,
      steps: record.steps.map(step => ({ node: step.node, label: step.label, seconds: seconds(step), thought: step.thought, done: step.endedAt !== null })),
      seconds: record.seconds ?? Math.round((this.now() - this.startedAt) / 100) / 10,
      error: this.done ? record.error : null,
      partialAnswer: record.partialAnswer,
      iri: `urn:nidra:question/${record.id}`,
    }
  }
}

/** The asks this process is running or ran lately, by id. */
export class Asks {
  constructor() {
    this.byId = new Map()
  }
  add(ask) {
    this.byId.set(ask.id, ask)
    this.sweep()
    return ask
  }
  get(id) {
    return this.byId.get(id) || null
  }
  live() {
    return [...this.byId.values()].filter(ask => !ask.done)
  }
  recent() {
    return [...this.byId.values()].sort((a, b) => b.startedAt - a.startedAt)
  }
  sweep() {
    const cutoff = Date.now() - KEEP_FINISHED_MS
    for (const [id, ask] of this.byId) if (ask.done && ask.endedAt < cutoff) this.byId.delete(id)
  }
}

// ── the tools Nidra lends (gardend lists them in /mcp beside its own; service.json `mcp.toolPrefix` is `nidra_`) ──────

const TERM_ID = { type: 'string', description: 'A glossary term id, as nidra_terms lists it.' }

export const TOOLS = [
  {
    name: 'nidra_ask',
    title: 'Ask the catalog',
    description: 'Ask the databases Nidra catalogues one question in plain words. Nidra\'s engine (NVIDIA Auto Ontology\'s text-to-SQL agent) '
      + 'classifies it, finds the tables and terms, writes and validates SQL, runs it read-only and answers. Returns the answer, the SQL it ran, '
      + 'the first rows with the true row count, the catalog columns the SQL used (sqlColumns) and every step the engine took with its seconds. '
      + 'A question that takes longer than about 45 s comes back with status "running" and a questionId: call nidra_question with it (again, while it says running) to wait for the rest; never ask the same question twice. '
      + 'Each question is recorded in the graph as urn:nidra:question/<questionId>.',
    inputSchema: {
      type: 'object',
      required: ['question'],
      additionalProperties: false,
      properties: {
        question: { type: 'string', minLength: 1, maxLength: 4000, description: 'The question, in the words of the person who asked it (clarify, never change its meaning).' },
        target_db: { type: 'string', description: 'Ask one database only (its name, as nidra_terms or the catalog names it). Omit for all.' },
        run_id: { type: 'string', description: 'The Choreograph run this question belongs to, when you know it.' },
        session_id: { type: 'string', description: 'The Choreograph session this question belongs to, when you know it.' },
        agent_id: { type: 'string', description: 'The being that asks (agent- and 16 hex digits). Sophia Compute fills it in for its beings.' },
      },
    },
    annotations: { readOnlyHint: true, openWorldHint: false },
  },
  {
    name: 'nidra_question',
    title: 'A question asked before',
    description: 'Read one question Nidra was asked (by its questionId): still running (with the steps so far, waiting up to wait_seconds for it to end) or its answer, SQL, rows and steps.',
    inputSchema: {
      type: 'object',
      required: ['question_id'],
      additionalProperties: false,
      properties: {
        question_id: { type: 'string', minLength: 1 },
        wait_seconds: { type: 'number', minimum: 0, maximum: 45, description: 'How long to wait for a running question to end (default and most: 45).' },
      },
    },
    annotations: { readOnlyHint: true, openWorldHint: false },
  },
  {
    name: 'nidra_terms',
    title: 'Glossary terms',
    description: 'List the glossary: the business terms Nidra compiled from the catalog, each with its description, synonyms and certification '
      + '(name_certified, description_certified, and the rollup "certification"). query filters by words in the name.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        query: { type: 'string', description: 'Words in the term name; omit for all.' },
        limit: { type: 'integer', minimum: 1, maximum: 200, description: 'Default 100.' },
      },
    },
    annotations: { readOnlyHint: true, openWorldHint: false },
  },
  {
    name: 'nidra_term',
    title: 'One glossary term',
    description: 'One glossary term in full: its description and certification, its column attributes (each mapped to a catalog table and column, '
      + 'with sample values) and its SQL attributes (a computed measure: the SQL expression, its source and whether it is certified).',
    inputSchema: { type: 'object', required: ['term_id'], additionalProperties: false, properties: { term_id: TERM_ID } },
    annotations: { readOnlyHint: true, openWorldHint: false },
  },
  {
    name: 'nidra_certify',
    title: 'Certify or uncertify',
    description: 'Certify (certified: true) or withdraw certification (false) of one part of a glossary term: its name, its description, '
      + 'one column attribute or one SQL attribute. This changes the engine\'s glossary, which the engine uses for every later question, and records '
      + 'who did it and when as testimony in the graph. Only do it when the person asked you to, or with the evidence in the note.',
    inputSchema: {
      type: 'object',
      required: ['term_id', 'part', 'certified', 'note'],
      additionalProperties: false,
      properties: {
        term_id: TERM_ID,
        part: { type: 'string', enum: ['name', 'description', 'column_attribute', 'sql_attribute'] },
        attribute_id: { type: 'string', description: 'For column_attribute and sql_attribute: the attribute id nidra_term lists.' },
        certified: { type: 'boolean' },
        note: { type: 'string', minLength: 1, maxLength: 2000, description: 'Why, in one or two sentences (the evidence: a question, a run, what the person said).' },
        agent_id: { type: 'string', description: 'The being that certifies, the witness of the act (Sophia Compute fills it in for its beings).' },
        run_id: { type: 'string' },
      },
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
]

const TOOL_NAMES = new Set(TOOLS.map(tool => tool.name))

/** A tool result: the structured value, and the same as text for a client that reads only text. */
export function toolResult(value, isError = false) {
  return { content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }], structuredContent: typeof value === 'string' ? { message: value } : value, ...(isError ? { isError: true } : {}) }
}

/**
 * Answer one JSON-RPC message (gardend sends `tools/list` and `tools/call`; `initialize` for completeness).
 * `handlers` are main.mjs's: { ask, question, terms, term, certify } each (args, caller) → value (throw to report).
 */
export async function answerRpc(message, handlers, caller) {
  const id = message?.id ?? null
  const reply = result => ({ jsonrpc: '2.0', id, result })
  const fail = (code, text, data) => ({ jsonrpc: '2.0', id, error: { code, message: text, ...(data ? { data } : {}) } })
  const method = String(message?.method || '')
  if (method === 'initialize') return reply({ protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'nidra', version: '2' } })
  if (method.startsWith('notifications/')) return null
  if (method === 'tools/list') return reply({ tools: TOOLS })
  if (method !== 'tools/call') return fail(-32601, `no method ${method}`)
  const name = String(message?.params?.name || '')
  if (!TOOL_NAMES.has(name)) return fail(-32602, `unknown tool ${name}`)
  const args = message?.params?.arguments && typeof message.params.arguments === 'object' ? message.params.arguments : {}
  const tool = TOOLS.find(t => t.name === name)
  if (!tool.annotations.readOnlyHint && !caller.mayWrite) {
    return reply(toolResult(`${name} changes the glossary; ${caller.userId} may not (only the owner and Sophia Compute's beings may)`, true))
  }
  const handler = handlers[name.slice('nidra_'.length)]
  try {
    return reply(toolResult(await handler(args, caller)))
  } catch (error) {
    return reply(toolResult(String(error?.message || error), true))
  }
}
