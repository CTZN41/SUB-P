import test from 'node:test'
import assert from 'node:assert/strict'
import { MAX_VIDEO_BYTES, videoSelectionError } from '../src/utils/videoSelection.ts'

test('picker and drop share single MP4 validation, including empty and oversized files', () => {
  assert.equal(videoSelectionError([{ name: 'practice.MP4', size: MAX_VIDEO_BYTES }]), null)
  assert.match(videoSelectionError([]), /한 개/)
  assert.match(videoSelectionError([{ name: 'a.mp4', size: 1 }, { name: 'b.mp4', size: 1 }]), /한 개/)
  assert.match(videoSelectionError([{ name: 'clip.mov', size: 1 }]), /MP4/)
  assert.match(videoSelectionError([{ name: 'empty.mp4', size: 0 }]), /비어/)
  assert.match(videoSelectionError([{ name: 'large.mp4', size: MAX_VIDEO_BYTES + 1 }]), /5GB/)
})
