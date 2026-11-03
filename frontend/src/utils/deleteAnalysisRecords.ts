export type DeleteFailure = { id: string; message: string }

/** Keep successful removals visible even if a later record cannot be deleted. */
export async function deleteAnalysisRecords(
  ids: string[],
  removeOne: (id: string) => Promise<void>,
  onRemoved: (id: string) => void,
): Promise<{ removed: string[]; failed: DeleteFailure[] }> {
  const removed: string[] = []
  const failed: DeleteFailure[] = []
  for (const id of new Set(ids)) {
    try {
      await removeOne(id)
    } catch (error) {
      failed.push({ id, message: error instanceof Error ? error.message : '기록을 삭제하지 못했습니다.' })
      continue
    }
    removed.push(id)
    onRemoved(id)
  }
  return { removed, failed }
}
