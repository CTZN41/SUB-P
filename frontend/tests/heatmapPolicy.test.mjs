import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { zoneTarget, zoneTone } from '../src/utils/heatmapPolicy.ts'
import { COURT_ZONES } from '../src/utils/courtZones.ts'

test('all 14 zones use the new paint, two-point and three-point goals', () => {
  for (const zone of COURT_ZONES) {
    const expected = zone.code === 'restricted' || zone.code.startsWith('paint_') ? 90 : zone.code.startsWith('mid_') ? 50 : 40
    assert.equal(zoneTarget(zone.code), expected)
  }
})

test('fewer than five shots remain gray even at 100 percent; the fifth enables goal colors', () => {
  for (const zone of COURT_ZONES) {
    const target = zoneTarget(zone.code)
    assert.equal(zoneTone(undefined, zone.code), 'empty')
    for (const attempts of [0, 1, 4]) {
      assert.equal(zoneTone({ attempts, success_rate: 100 }, zone.code), 'empty')
      assert.equal(zoneTone({ attempts, success_rate: 0 }, zone.code), 'empty')
    }
    assert.equal(zoneTone({ attempts: 5, success_rate: target }, zone.code), 'hot')
    assert.equal(zoneTone({ attempts: 5, success_rate: target - .01 }, zone.code), 'cold')
    assert.equal(zoneTone({ attempts: 5, success_rate: null }, zone.code), 'neutral')
  }
})

test('web and PDF targets and colors agree for every zone at the sample boundary', () => {
  const cases = COURT_ZONES.flatMap(({ code }) => [0, 4, 5, 6].flatMap(attempts =>
    [0, zoneTarget(code), 100].map(success_rate => ({ code, attempts, success_rate }))))
  const backend = fileURLToPath(new URL('../../backend/', import.meta.url))
  const python = fileURLToPath(new URL(process.platform === 'win32' ? '../../backend/.venv/Scripts/python.exe' : '../../backend/.venv/bin/python', import.meta.url))
  const script = `import json,sys
from app.services.practice_report_pdf import zone_rows,EMPTY,HOT
out=[]
for case in json.load(sys.stdin):
 s={'attempts':case['attempts'],'success_rate':case['success_rate'],'made':0,'missed':0,'unresolved':0}
 r=next(r for r in zone_rows({'rows':[{'key':'zone/'+case['code'],'current':s}]}) if r['code']==case['code'])
 out.append({'target':r['target'],'tone':'empty' if r['tone']==EMPTY else 'hot' if r['tone']==HOT else 'cold'})
print(json.dumps(out))`
  const result = spawnSync(python, ['-c', script], { cwd: backend, input: JSON.stringify(cases), encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  const actual = JSON.parse(result.stdout)
  cases.forEach((s, i) => assert.deepEqual(actual[i], { target: zoneTarget(s.code), tone: zoneTone(s, s.code) }))
})
