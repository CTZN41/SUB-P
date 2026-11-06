type HistoryRecord = { is_demo?: boolean }

export function visibleHistoryRecords<T extends HistoryRecord>(records: T[], recent: boolean): T[] {
  const real = records.filter(record => !record.is_demo)
  return recent ? real.slice(0, 3) : [...real, ...records.filter(record => record.is_demo)]
}
