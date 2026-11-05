export const MAX_VIDEO_BYTES = 5 * 1024 * 1024 * 1024

export function videoSelectionError(files: readonly Pick<File, 'name' | 'size'>[]): string | null {
  if (files.length !== 1) return 'MP4 영상 한 개만 선택해 주세요.'
  const file = files[0]
  if (!file.name.toLowerCase().endsWith('.mp4')) return 'MP4 영상만 사용할 수 있습니다.'
  if (file.size === 0) return '비어 있는 파일은 사용할 수 없습니다.'
  if (file.size > MAX_VIDEO_BYTES) return '영상 파일은 최대 5GB까지 등록할 수 있습니다.'
  return null
}
