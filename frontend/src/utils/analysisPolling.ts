export async function readAnalysisResponse<T>(response: Response, fallback: string): Promise<T> {
  // A proxy or database failure may return plain text/HTML, not JSON.
  // Never show the raw response (or a JSON parser error) in the product.
  if (response.status >= 500) throw new Error(fallback)
  let payload: unknown
  try { payload = JSON.parse(await response.text()) }
  catch { throw new Error(fallback) }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
    const message = detail && typeof detail === 'object' && 'message' in detail ? detail.message : null
    throw new Error(typeof message === 'string' ? message : fallback)
  }
  if (!payload || typeof payload !== 'object') throw new Error(fallback)
  return payload as T
}

type PollOptions<T> = {
  request: () => Promise<T>
  success: (value: T) => void
  failure: () => void
  keepPolling: (value: T) => boolean
  schedule?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  unschedule?: (handle: ReturnType<typeof setTimeout>) => void
}

export function startAnalysisPolling<T>(options: PollOptions<T>) {
  const schedule = options.schedule ?? setTimeout
  const unschedule = options.unschedule ?? clearTimeout
  let active = true
  let failures = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const tick = async () => {
    if (!active) return
    try {
      const value = await options.request()
      if (!active) return
      failures = 0
      options.success(value)
      if (!options.keepPolling(value)) return
    } catch {
      if (!active) return
      failures += 1
      options.failure()
    }
    // Schedule after the request settles: slow DB queries cannot overlap.
    if (active) timer = schedule(() => { void tick() }, Math.min(15000, 2000 * 2 ** failures))
  }
  void tick()
  return () => { active = false; if (timer !== undefined) unschedule(timer) }
}
