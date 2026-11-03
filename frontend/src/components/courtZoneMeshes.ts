import * as THREE from 'three'
import { COURT_ZONES, ZONE_POLYGONS } from '../utils/courtZones.ts'

export function addCourtZones(scene: THREE.Scene): Map<string, THREE.Group> {
  const zones = new Map<string, THREE.Group>()
  COURT_ZONES.forEach((zone, index) => {
    const group = new THREE.Group()
    group.userData.zoneCode = zone.code
    group.userData.baseColor = index % 2 ? '#6b6d6f' : '#77797b'
    for (const rings of ZONE_POLYGONS[zone.code]) {
      const vectors = (ring: number[][]) => ring.map(([x, y]) => new THREE.Vector2(x * 10, y * 10))
      const shape = new THREE.Shape(vectors(rings[0]))
      shape.holes = rings.slice(1).map(ring => new THREE.Path(vectors(ring)))
      const geometry = new THREE.ExtrudeGeometry(shape, { depth: 8, bevelEnabled: false, curveSegments: 48 })
      geometry.scale(.1, .1, .1)
      geometry.rotateX(Math.PI / 2)
      geometry.translate(-25, .065, -23.5)
      const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: group.userData.baseColor, transparent: true, opacity: .2, roughness: .85, depthWrite: false, side: THREE.DoubleSide }))
      mesh.userData.zoneCode = zone.code
      mesh.renderOrder = 1
      group.add(mesh)
      for (const ring of rings) {
        const edge = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(ring.map(([x, y]) => new THREE.Vector3(x - 25, .09, y - 23.5))), new THREE.LineBasicMaterial({ color: '#ffffff', transparent: true, opacity: 1 }))
        group.add(edge)
      }
    }
    scene.add(group)
    zones.set(zone.code, group)
  })
  return zones
}

export function styleCourtZones(zones: Map<string, THREE.Group>, selected: string | null) {
  for (const [code, group] of zones) group.traverse(item => {
    if (item instanceof THREE.Mesh) {
      const material = item.material as THREE.MeshStandardMaterial
      material.color.set(code === selected ? '#d8ff4f' : group.userData.baseColor)
      material.opacity = code === selected ? .9 : selected ? .1 : .2
      material.depthWrite = code === selected
    } else if (item instanceof THREE.Line) {
      const material = item.material as THREE.LineBasicMaterial
      material.color.set('#ffffff')
      material.opacity = code === selected ? 1 : selected ? .3 : 1
    }
  })
}
