import test from 'node:test'
import assert from 'node:assert/strict'
import { readAnalysisResponse, startAnalysisPolling } from '../src/utils/analysisPolling.ts'

const settle = () => new Promise(resolve => setImmediate(resolve))

test('plain text/HTML server errors show a safe message, never JSON parser text', async () => {
  for (const body of ['Internal Server Error', '<html>proxy failure</html>', '{"detail":{"message":"internal database details"}}']) {
    await assert.rejects(readAnalysisResponse(new Response(body, { status: 500 }), 'retry status'), { message: 'retry status' })
  }
  await assert.rejects(readAnalysisResponse(new Response('bad JSON'), 'retry status'), { message: 'retry status' })
  await assert.rejects(readAnalysisResponse(new Response('null'), 'retry status'), { message: 'retry status' })
})

test('valid status and actionable structured client errors remain readable', async () => {
  assert.deepEqual(await readAnalysisResponse(new Response('{"id":"job","status":"running"}'), 'fallback'), { id: 'job', status: 'running' })
  await assert.rejects(readAnalysisResponse(new Response('{"detail":{"message":"Please sign in"}}', { status: 401 }), 'fallback'), { message: 'Please sign in' })
})

test('transient errors retry with backoff; recovery clears the warning and resumes the normal interval', async () => {
  const timers = [], notices = []
  let requests = 0
  const stop = startAnalysisPolling({
    request: async () => { requests++; if (requests <= 2) throw new Error('connection'); return { status: requests === 3 ? 'running' : 'succeeded' } },
    success: value => notices.push(value.status), failure: () => notices.push('retrying'),
    keepPolling: value => value.status === 'running',
    schedule: (callback, delay) => { timers.push({ callback, delay }); return timers.length }, unschedule: () => {},
  })
  await settle(); assert.equal(timers[0].delay, 4000)
  timers[0].callback(); await settle(); assert.equal(timers[1].delay, 8000)
  timers[1].callback(); await settle(); assert.equal(timers[2].delay, 2000)
  timers[2].callback(); await settle()
  assert.deepEqual(notices, ['retrying', 'retrying', 'running', 'succeeded'])
  assert.equal(timers.length, 3)
  stop()
})

test('slow requests never overlap; leaving the page ignores late responses', async () => {
  let finish, calls = 0, updates = 0
  const timers = []
  const stop = startAnalysisPolling({ request: () => { calls++; return new Promise(resolve => { finish = resolve }) },
    success: () => updates++, failure: () => updates++, keepPolling: () => true,
    schedule: callback => { timers.push(callback); return timers.length }, unschedule: () => {},
  })
  await settle(); assert.equal(calls, 1); assert.equal(timers.length, 0)
  stop(); finish({ status: 'running' }); await settle()
  assert.equal(updates, 0); assert.equal(timers.length, 0)
})
