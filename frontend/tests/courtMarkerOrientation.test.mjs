import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
import { orientCourtMarkers } from '../src/components/courtMarkerOrientation.ts'

test('markers continuously rise from flat to upright as camera lowers', () => {
  const group = new THREE.Group()
  const marker = new THREE.Mesh(new THREE.PlaneGeometry(3.3, 3.3))
  marker.position.set(-10, 0, 5)
  marker.userData.markerSize = 3.3
  marker.userData.floorHeight = 1.6
  group.add(marker)
  const camera = new THREE.PerspectiveCamera()
  let lastHeight = -Infinity
  for (let degrees = 80; degrees >= 5; degrees--) {
    const radians = THREE.MathUtils.degToRad(degrees)
    camera.position.set(0, 80 * Math.sin(radians), 80 * Math.cos(radians))
    camera.lookAt(0, 0, 0)
    orientCourtMarkers(group, camera)
    if (degrees >= 55) assert.ok(Math.abs(marker.rotation.x + Math.PI / 2) < 1e-12)
    if (degrees <= 10) assert.ok(Math.abs(marker.rotation.x) < 1e-12)
    assert.ok(marker.position.y >= lastHeight - 1e-10)
    assert.equal(marker.position.x, -10)
    assert.equal(marker.position.z, 5)
    assert.ok(marker.position.y >= 1.77 - 1e-10)
    lastHeight = marker.position.y
  }
  const height = marker.position.y
  camera.position.set(-80, 7, -20)
  camera.lookAt(0, 0, 0)
  orientCourtMarkers(group, camera)
  assert.ok(Math.abs(marker.rotation.y - Math.atan2(-80, -20)) < 1e-12)
  assert.ok(Math.abs(marker.position.y - height) < 1e-10)
})
