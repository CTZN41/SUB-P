import test from 'node:test'
import assert from 'node:assert/strict'
import { ANALYSIS_STAGES, analysisStageIndex, analysisStageLabel } from '../src/utils/analysisStages.ts'

test('eight display stages follow real worker phases', () => {
  assert.equal(ANALYSIS_STAGES.length, 8)
  for (const [stage, expected] of [
    ['영상 준비', 0], ['코트와 골대 확인', 1], ['선수·공 추적', 2], ['슛 후보 찾기', 3],
    ['성공·실패와 슛 종류 판단 · 슛 여부 확인 2/8', 4],
    ['성공·실패와 슛 종류 판단 · 슛 여부 확인·추가 재탐지', 4],
    ['성공·실패와 슛 종류 판단 · 카메라 구도 확인', 5],
    ['성공·실패와 슛 종류 판단 · 상세 AI 판정 3/8', 5],
    ['성공·실패와 슛 종류 판단', 6], ['완료', 7],
  ]) assert.equal(analysisStageIndex(stage), expected, stage)
  assert.equal(analysisStageLabel('성공·실패와 슛 종류 판단 · 상세 AI 판정 3/8'), '심층 분석 3/8')
  assert.equal(analysisStageLabel('성공·실패와 슛 종류 판단 · 슛 여부 확인 5/5'), '후보 판단 5/5 · 후속 검증 중')
  assert.equal(analysisStageLabel('성공·실패와 슛 종류 판단 · 슛 여부 확인·추가 재탐지'), '후보 판단 · 추가 후보 탐지')
  assert.equal(analysisStageLabel(null), '시작 전')
  assert.equal(analysisStageLabel('분석 실패'), '분석 실패')
  assert.equal(analysisStageIndex('취소됨'), -1)
})

test('follow-up counters and grouped concurrent detail activities stay visible', () => {
  assert.equal(analysisStageIndex('심층 분석 5/31 · 위치 재검토'), 5)
  assert.equal(analysisStageLabel('심층 분석 5/31 · 기본 판정·위치 재검토'), '심층 분석 5/31 · 기본 판정·위치 재검토')
  assert.equal(analysisStageLabel('후보 판단 · 발 추적 보정 4/21'), '후보 판단 4/21 · 발 추적 보정')
  assert.equal(analysisStageLabel('후보 판단 · 추가 후보 탐지 1/3'), '후보 판단 1/3 · 추가 후보 탐지')
  assert.equal(analysisStageLabel('심층 분석 31/31'), '심층 분석 31/31 · 결과 정리 중')
})
