/** Supabase manages the tokens. This adapter chooses their lifetime, not passwords. */
export function createAuthStorage(local: Storage, session: Storage, storageKey: string) {
  const preferenceKey = `${storageKey}-remember`
  const remember = () => local.getItem(preferenceKey) === 'true'
  const ownedKeys = () => {
    const keys = new Set([storageKey, `${storageKey}-code-verifier`, `${storageKey}-user`])
    for (const store of [local, session]) {
      for (let i = 0; i < store.length; i++) {
        const key = store.key(i)
        if (key && key !== preferenceKey && (key === storageKey || key.startsWith(`${storageKey}-`))) keys.add(key)
      }
    }
    return keys
  }
  return {
    remember,
    setRemember(value: boolean) {
      const from = remember() ? local : session
      const to = value ? local : session
      const keys = ownedKeys()
      // Write first. A full/blocked destination must not silently lose the session.
      const written = new Map<string, string | null>()
      try {
        for (const key of keys) {
          const data = from.getItem(key) ?? (local.getItem(preferenceKey) === null ? local.getItem(key) : null)
          if (data !== null) { written.set(key, to.getItem(key)); to.setItem(key, data) }
        }
        local.setItem(preferenceKey, String(value))
      } catch (error) {
        for (const [key, previous] of written) {
          if (previous === null) to.removeItem(key)
          else to.setItem(key, previous)
        }
        throw error
      }
      const other = value ? session : local
      for (const key of keys) other.removeItem(key)
    },
    storage: {
      getItem(key: string) {
        const active = remember() ? local : session
        const value = active.getItem(key)
        if (value !== null) return value
        // Preserve pre-checkbox sessions on this tab, without keeping an old
        // refresh token permanently when the user has not opted in.
        if (!remember() && local.getItem(preferenceKey) === null) {
          const legacy = local.getItem(key)
          if (legacy !== null) { session.setItem(key, legacy); local.removeItem(key) }
          return legacy
        }
        return null
      },
      setItem(key: string, value: string) {
        const active = remember() ? local : session
        active.setItem(key, value)
        ;(remember() ? session : local).removeItem(key)
      },
      removeItem(key: string) { local.removeItem(key); session.removeItem(key) },
    },
  }
}
