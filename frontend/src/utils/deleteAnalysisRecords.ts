export type DeleteFailure = { id: string; message: string }
export type DeleteResult = { removed: string[]; failed: DeleteFailure[] }

/** Submit one batch and update the visible list once its complete result arrives. */
export async function deleteAnalysisRecords(
  ids: string[],
  removeMany: (ids: string[]) => Promise<DeleteResult>,
  onRemoved: (ids: string[]) => void,
): Promise<DeleteResult> {
  const uniqueIds = [...new Set(ids)]
  if (!uniqueIds.length) return { removed: [], failed: [] }
  const result = await removeMany(uniqueIds)
  const requested = new Set(uniqueIds)
  const reported = [...result.removed, ...result.failed.map(item => item.id)]
  if (reported.length !== uniqueIds.length || new Set(reported).size !== uniqueIds.length
    || reported.some(id => !requested.has(id))) {
    throw new Error('삭제 결과를 확인하지 못했습니다. 기록 목록을 새로고침해 주세요.')
  }
  if (result.removed.length) onRemoved(result.removed)
  return result
}
