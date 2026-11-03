import * as THREE from 'three'

// High view: flat on the court. Low view: standing and facing the camera.
// The visible disc's bottom stays above the floor, including a lifted zone.
export function orientCourtMarkers(markers: THREE.Group | null, camera: THREE.Camera) {
  const direction = camera.getWorldDirection(new THREE.Vector3())
  const elevation = Math.atan2(-direction.y, Math.hypot(direction.x, direction.z))
  const amount = THREE.MathUtils.smoothstep(elevation, THREE.MathUtils.degToRad(10), THREE.MathUtils.degToRad(55))
  const tilt = amount * Math.PI / 2
  for (const marker of markers?.children ?? []) {
    marker.rotation.order = 'YXZ'
    marker.rotation.x = -tilt
    marker.rotation.y = Math.atan2(-direction.x, -direction.z)
    marker.rotation.z = 0
    const radius = (marker.userData.markerSize ?? 3.3) * 53 / 128
    marker.position.y = .17 + (marker.userData.floorHeight ?? 0) + radius * Math.cos(tilt)
  }
  return { elevation, tilt }
}
