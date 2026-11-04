import test from 'node:test'
import assert from 'node:assert/strict'
import { createAuthStorage } from '../src/auth/sessionStorage.ts'

class MemoryStorage {
  data = new Map()
  get length() { return this.data.size }
  key(i) { return [...this.data.keys()][i] ?? null }
  getItem(key) { return this.data.get(key) ?? null }
  setItem(key, value) { this.data.set(key, value) }
  removeItem(key) { this.data.delete(key) }
}
const key = 'sb-test-auth-token'

test('unchecked login survives reload/OAuth callback in the same tab but not a fresh session', () => {
  const local = new MemoryStorage(), tab = new MemoryStorage()
  const first = createAuthStorage(local, tab, key)
  first.setRemember(false)
  first.storage.setItem(key, 'test-session')
  first.storage.setItem(`${key}-code-verifier`, 'test-verifier')
  const callback = createAuthStorage(local, tab, key)
  assert.equal(callback.storage.getItem(key), 'test-session')
  assert.equal(callback.storage.getItem(`${key}-code-verifier`), 'test-verifier')
  assert.equal(local.getItem(key), null)
  const newBrowser = createAuthStorage(local, new MemoryStorage(), key)
  assert.equal(newBrowser.storage.getItem(key), null)
})

test('checked login survives a fresh browser session, including the OAuth verifier', () => {
  const local = new MemoryStorage(), tab = new MemoryStorage()
  const first = createAuthStorage(local, tab, key)
  first.setRemember(true)
  first.storage.setItem(key, 'test-session')
  first.storage.setItem(`${key}-code-verifier`, 'test-verifier')
  const reopened = createAuthStorage(local, new MemoryStorage(), key)
  assert.equal(reopened.remember(), true)
  assert.equal(reopened.storage.getItem(key), 'test-session')
  assert.equal(reopened.storage.getItem(`${key}-code-verifier`), 'test-verifier')
  assert.equal(tab.getItem(key), null)
})

test('changing the selection moves tokens without leaving old persistent copies', () => {
  const local = new MemoryStorage(), tab = new MemoryStorage()
  const adapter = createAuthStorage(local, tab, key)
  adapter.storage.setItem(key, 'test-session')
  adapter.storage.setItem(`${key}-code-verifier`, 'test-verifier')
  adapter.setRemember(true)
  assert.equal(tab.getItem(key), null)
  assert.equal(local.getItem(key), 'test-session')
  adapter.setRemember(false)
  assert.equal(local.getItem(key), null)
  assert.equal(local.getItem(`${key}-code-verifier`), null)
  assert.equal(tab.getItem(key), 'test-session')
  assert.equal(tab.getItem(`${key}-code-verifier`), 'test-verifier')
  adapter.storage.removeItem(key)
  assert.equal(local.getItem(key), null)
  assert.equal(tab.getItem(key), null)
})

test('legacy sessions migrate to the current tab and unrelated project state remains intact', () => {
  const local = new MemoryStorage(), tab = new MemoryStorage()
  local.setItem(key, 'legacy-test-session')
  local.setItem('another-project-token', 'unrelated')
  const adapter = createAuthStorage(local, tab, key)
  assert.equal(adapter.storage.getItem(key), 'legacy-test-session')
  assert.equal(local.getItem(key), null)
  adapter.setRemember(false)
  assert.equal(local.getItem('another-project-token'), 'unrelated')
})

test('failed persistent writes never silently discard the current temporary session', () => {
  const local = new MemoryStorage(), tab = new MemoryStorage()
  const adapter = createAuthStorage(local, tab, key)
  adapter.storage.setItem(key, 'test-session')
  local.setItem = () => { throw new Error('Storage unavailable') }
  assert.throws(() => adapter.setRemember(true), /unavailable/)
  assert.equal(tab.getItem(key), 'test-session')
})

test('partial migration failure rolls back persistent copies and keeps the prior selection', () => {
  const local = new MemoryStorage(), tab = new MemoryStorage()
  const adapter = createAuthStorage(local, tab, key)
  adapter.setRemember(false)
  adapter.storage.setItem(key, 'test-session')
  adapter.storage.setItem(`${key}-code-verifier`, 'test-verifier')
  const original = local.setItem.bind(local)
  local.setItem = (name, value) => { if (name.endsWith('-code-verifier')) throw new Error('Full'); original(name, value) }
  assert.throws(() => adapter.setRemember(true), /Full/)
  assert.equal(adapter.remember(), false)
  assert.equal(local.getItem(key), null)
  assert.equal(tab.getItem(key), 'test-session')
  assert.equal(tab.getItem(`${key}-code-verifier`), 'test-verifier')
})
