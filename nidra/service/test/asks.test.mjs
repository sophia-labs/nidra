// The service's ask reader and tool table, against a stream the real engine sent (fixtures/chinook-media-types.sse,
// captured 2026-11-03 from the Sophia.app engine, DeepSeek v4 Pro, Chinook). node --test nidra/service/test
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { Ask, Asks, ROWS_FOR_MODEL, TOOLS, answerRpc, createSseReader, rowsOf } from '../asks.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const CAPTURE = fs.readFileSync(path.join(HERE, '..', 'fixtures', 'chinook-media-types.sse'), 'utf8')

function clock() {
  let t = 1_000
  return { now: () => (t += 100) }
}

function replay(text, chunk = 37) {
  const { now } = clock()
  const ask = new Ask({ question: 'How many tracks does each media type have?', caller: 'service:choreograph', via: 'choreograph', context: { runId: 'wfr-x' }, now })
  const reader = createSseReader(event => (event.type === 'done' ? ask.finish() : ask.feed(event)))
  const bytes = new TextEncoder().encode(text)
  // Cut the bytes anywhere, even inside a multi-byte character.
  for (let i = 0; i < bytes.length; i += chunk) reader.push(bytes.slice(i, i + chunk))
  reader.end()
  return ask
}

test('a real stream, cut into odd chunks, comes to one answered record', () => {
  for (const chunk of [1, 7, 37, 4096]) {
    const ask = replay(CAPTURE, chunk)
    assert.equal(ask.done, true)
    const record = ask.record()
    assert.equal(record.ok, true)
    assert.match(record.sql, /FROM "MediaType"/)
    assert.equal(record.rowCount, 5)
    assert.deepEqual(Object.keys(record.rows[0]), ['MediaTypeName', 'TrackCount'])
    assert.equal(record.rows.find(row => row.MediaTypeName === 'MPEG audio file').TrackCount, 3034)
    assert.ok(record.sqlColumns.length >= 3)
    assert.equal(record.runId, 'wfr-x')
    assert.equal(record.via, 'choreograph')
  }
})

test('steps keep their order, labels and thoughts; the SQL the agent showed is kept as a draft', () => {
  const ask = replay(CAPTURE)
  const nodes = ask.steps.map(step => step.node)
  assert.equal(nodes[0], 'question_intent')
  assert.ok(nodes.includes('construct_sql_from_candidates'))
  assert.equal(nodes.at(-1), 'format_and_respond')
  assert.ok(ask.steps.every(step => step.endedAt !== null && step.endedAt >= step.startedAt))
  assert.match(ask.steps.find(step => step.node === 'construct_sql_from_candidates').thought, /MediaTypeId/)
  assert.equal(ask.steps.find(step => step.node === 'question_intent').label, 'Classifying the question')
  assert.equal(ask.sqlDrafts.length, 1)
})

test('what a model is handed: the answer, the SQL, at most 50 rows with the true count, the steps with seconds', () => {
  const out = replay(CAPTURE).forModel()
  assert.equal(out.status, 'answered')
  assert.deepEqual(out.columns, ['MediaTypeName', 'TrackCount'])
  assert.equal(out.rowsShown, 5)
  assert.ok(out.steps.every(step => typeof step.seconds === 'number' && step.done))
  assert.match(out.iri, /^urn:nidra:question\//)
  const many = rowsOf([JSON.stringify(Array.from({ length: 120 }, (_, i) => ({ i })))])
  assert.equal(many.length, 120)
  assert.equal(ROWS_FOR_MODEL, 50)
})

test('a stream cut before its result is a stopped question, not an answer', () => {
  const cut = CAPTURE.slice(0, CAPTURE.indexOf('"type": "result"'))
  const { now } = clock()
  const ask = new Ask({ question: 'q', now })
  const reader = createSseReader(event => ask.feed(event))
  reader.push(cut.slice(0, cut.lastIndexOf('\n\n') + 2))
  ask.finish({ stopped: true })
  const record = ask.record()
  assert.equal(record.ok, false)
  assert.equal(record.error, 'Stopped before an answer')
  assert.ok(record.sql === null)
  assert.equal(ask.forModel().status, 'failed')
})

test('the engine\'s "LLM failed" result is a failure, and an error event carries its partial answer', () => {
  const ask = new Ask({ question: 'q', ...clock() })
  ask.feed({ type: 'result', answer: { response: 'LLM failed to produce a response.', sql_code: '' } })
  ask.finish()
  assert.equal(ask.record().ok, false)
  assert.match(ask.record().error, /model did not answer/)
  const failed = new Ask({ question: 'q', ...clock() })
  failed.feed({ type: 'error', message: 'validation failed', partial_answer: { response: 'half', sql_code: 'SELECT 1' } })
  failed.finish()
  assert.equal(failed.record().error, 'validation failed')
  assert.equal(failed.record().sql, 'SELECT 1')
  assert.equal(failed.record().partialAnswer, 'half')
})

test('wait resolves at the end, or false after its time', async () => {
  const ask = new Ask({ question: 'q' })
  assert.equal(await ask.wait(5), false)
  setTimeout(() => ask.finish(), 5)
  assert.equal(await ask.wait(1000), true)
  const asks = new Asks()
  asks.add(ask)
  assert.equal(asks.get(ask.id), ask)
  assert.equal(asks.live().length, 0)
})

test('the lent tools: names under the prefix, read-only marked, writes refused to a caller that may not write', async () => {
  assert.ok(TOOLS.every(tool => tool.name.startsWith('nidra_')))
  assert.deepEqual(TOOLS.filter(tool => !tool.annotations.readOnlyHint).map(tool => tool.name), ['nidra_certify'])
  const listed = await answerRpc({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, {}, {})
  assert.equal(listed.result.tools.length, TOOLS.length)
  let called = 0
  const handlers = { certify: async () => { called++; return { ok: true } }, terms: async args => ({ terms: [], query: args.query ?? null }) }
  const refused = await answerRpc({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'nidra_certify', arguments: { term_id: 't', part: 'name', certified: true, note: 'n' } } }, handlers, { userId: 'token:abc', mayWrite: false })
  assert.equal(refused.result.isError, true)
  assert.equal(called, 0)
  const allowed = await answerRpc({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'nidra_certify', arguments: {} } }, handlers, { userId: 'service:choreograph', mayWrite: true })
  assert.equal(allowed.result.structuredContent.ok, true)
  const read = await answerRpc({ jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'nidra_terms', arguments: { query: 'genre' } } }, handlers, { userId: 'token:abc', mayWrite: false })
  assert.equal(read.result.structuredContent.query, 'genre')
  const unknown = await answerRpc({ jsonrpc: '2.0', id: 5, method: 'tools/call', params: { name: 'sparql_update' } }, handlers, {})
  assert.equal(unknown.error.code, -32602)
  assert.equal(await answerRpc({ jsonrpc: '2.0', method: 'notifications/initialized' }, handlers, {}), null)
})
