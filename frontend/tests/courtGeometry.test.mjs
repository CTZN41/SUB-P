import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import * as THREE from 'three'
import { COURT_ZONES, ZONE_POLYGONS, classifyCourtZone } from '../src/utils/courtZones.ts'
import { addCourtZones } from '../src/components/courtZoneMeshes.ts'

const points = []
for (let x = .113; x < 50; x += .71) for (let y = .217; y < 47; y += .67) points.push([x, y])
// Both sides AND the exact value of every straight threshold.
for (const x of [0, 3, 17, 19, 17 + 16 / 3, 33 - 16 / 3, 31, 33, 47, 50]) {
  for (const y of [0, 5.25, 10, 13, 14, 19, 23, 30, 47]) {
    for (const delta of [-.0001, 0, .0001]) if (x + delta >= 0 && x + delta <= 50) points.push([x + delta, y])
  }
}
for (const radius of [4, 23.75]) for (let angle = 0; angle < 360; angle += 3) {
  for (const delta of [-.001, 0, .001]) {
    const x = 25 + (radius + delta) * Math.cos(angle * Math.PI / 180)
    const y = 5.25 + (radius + delta) * Math.sin(angle * Math.PI / 180)
    if (x >= 0 && x <= 50 && y >= 0 && y <= 47) points.push([x, y])
  }
}
const backend = fileURLToPath(new URL('../../backend/', import.meta.url))
const python = fileURLToPath(new URL('../../backend/.venv/Scripts/python.exe', import.meta.url))
const result = spawnSync(python, ['-c', 'import sys,json;from app.services.court_zones import classify_zone;print(json.dumps([classify_zone(x,y).value for x,y in json.load(sys.stdin)]))'], { cwd: backend, input: JSON.stringify(points), encoding: 'utf8' })
assert.equal(result.status, 0, result.stderr)
const expected = JSON.parse(result.stdout)

test('frontend classification matches backend across court, exact ties and both sides', () => {
  for (let i = 0; i < points.length; i++) assert.equal(classifyCourtZone(...points[i]), expected[i], JSON.stringify(points[i]))
  assert.equal(classifyCourtZone(-.001, 20), null)
  assert.equal(classifyCourtZone(50.001, 20), null)
})

test('2D paths use exactly the polygon rings used by 3D', () => {
  assert.equal(COURT_ZONES.length, 14)
  for (const zone of COURT_ZONES) {
    assert.ok(ZONE_POLYGONS[zone.code].length)
    assert.equal((zone.path.match(/M/g) ?? []).length, ZONE_POLYGONS[zone.code].reduce((sum, rings) => sum + rings.length, 0))
  }
})

test('real 3D triangles match backend classification including near-boundary probes', () => {
  const scene = new THREE.Scene()
  const zones = addCourtZones(scene)
  scene.updateMatrixWorld(true)
  const ray = new THREE.Raycaster()
  const objects = [...zones.values()]
  let checked = 0
  for (let i = 0; i < points.length; i += 2) {
    const [x, y] = points[i]
    const boundaries = [x, 50 - x, y, 47 - y, ...[3, 17, 19, 17 + 16 / 3, 33 - 16 / 3, 31, 33, 47].map(value => x - value), ...[13, 14, 19].map(value => y - value), Math.hypot(x - 25, y - 5.25) - 4, Math.hypot(x - 25, y - 5.25) - 23.75]
    // GPU positions are Float32; ties are resolved analytically by the click handler.
    if (boundaries.some(value => Math.abs(value) < .00005)) continue
    ray.set(new THREE.Vector3(x - 25, 3, y - 23.5), new THREE.Vector3(0, -1, 0))
    const hits = ray.intersectObjects(objects, true).filter(hit => hit.object instanceof THREE.Mesh)
    // Exact boundaries belong to adjacent closed polygons; classifier resolves ties.
    assert.ok(hits.some(hit => hit.object.userData.zoneCode === expected[i]), `${x},${y}: expected ${expected[i]}, got ${hits.map(hit => hit.object.userData.zoneCode)}`)
    checked++
  }
  assert.ok(checked > 2000)
})
