export function canSendSelectedReports(records: { report_ready?: boolean }[]): boolean {
  return records.length > 0 && records.every(record => record.report_ready === true)
}
