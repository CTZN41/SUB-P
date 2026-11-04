import test from 'node:test'
import assert from 'node:assert/strict'
import { registerVideo } from '../src/utils/videoUpload.ts'

function inputs() {
  const file = new File(['video'], 'practice.mp4', { type: 'video/mp4' })
  const form = new FormData()
  for (const [key, value] of Object.entries({ title: 'Practice', play_type: 'solo', recorded_date: '2026-11-04', contains_others: 'false', consent_confirmed: 'false' })) form.set(key, value)
  form.set('video', file)
  return { file, form }
}

test('normal registration validates metadata then streams the File and finalizes', async t => {
  const calls = []
  const { file, form } = inputs()
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    calls.push({ path, options })
    return Response.json(calls.length === 1 ? { id: 'upload-id' } : calls.length === 2 ? { state: 'received' } : { id: 'analysis-id' }, { status: calls.length === 1 ? 201 : 200 })
  })
  assert.deepEqual(await registerVideo(form, file), { id: 'analysis-id' })
  assert.deepEqual(calls.map(c => c.path), ['/api/uploads', '/api/uploads/upload-id', '/api/uploads/upload-id/complete'])
  assert.equal(JSON.parse(calls[0].options.body).file_size, file.size)
  assert.equal(calls[1].options.body, file)
  assert.equal(calls[1].options.method, 'PUT')
})

test('preflight failure sends no video and a transfer failure never finalizes', async t => {
  const { file, form } = inputs()
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => { calls++; return Response.json({ detail: { message: 'Too large' } }, { status: 413 }) })
  await assert.rejects(registerVideo(form, file), { message: 'Too large' })
  assert.equal(calls, 1)
  calls = 0
  globalThis.fetch = async () => { calls++; return calls === 1 ? Response.json({ id: 'u' }) : new Response('Internal Server Error', { status: 500 }) }
  await assert.rejects(registerVideo(form, file), { message: '영상 전송을 완료하지 못했습니다.' })
  assert.equal(calls, 2)
})

test('older running backend uses the existing multipart registration route', async t => {
  const { file, form } = inputs()
  const calls = []
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    calls.push({ path, options })
    return calls.length === 1 ? new Response('missing', { status: 404 }) : Response.json({ id: 'legacy-id' })
  })
  assert.deepEqual(await registerVideo(form, file), { id: 'legacy-id' })
  assert.equal(calls[1].path, '/api/analyses')
  assert.equal(calls[1].options.body, form)
})

test('retry after a lost finalize response reuses the upload and skips the file transfer', async t => {
  const { file, form } = inputs()
  const attempt = { current: null }
  const paths = []
  let finishes = 0
  t.mock.method(globalThis, 'fetch', async path => {
    paths.push(path)
    if (path.endsWith('/complete')) {
      finishes++
      return finishes === 1 ? new Response('database unavailable', { status: 500 }) : Response.json({ id: 'same-id' })
    }
    return Response.json(path === '/api/uploads' ? { id: 'same-id' } : { state: 'received' })
  })
  await assert.rejects(registerVideo(form, file, attempt))
  assert.deepEqual(await registerVideo(form, file, attempt), { id: 'same-id' })
  assert.deepEqual(paths, ['/api/uploads', '/api/uploads/same-id', '/api/uploads/same-id/complete', '/api/uploads/same-id/complete'])
  assert.equal(attempt.current, null)
})
