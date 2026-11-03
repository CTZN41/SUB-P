import * as THREE from 'three'
import { SVGLoader } from 'three/addons/loaders/SVGLoader.js'
import { COURT_ZONES } from '../utils/courtZones'

// Same outlines as the 2D chart. Cut the restricted circle out of the paint
// so lifting a paint section never covers the separate restricted section.
function floorPath(code: string, path: string) {
  const top = 53 - Math.sqrt(40 ** 2 - 27 ** 2)
  const bottom = 106 - top
  if (code === 'paint_left') return `M170 0H223V${top}A40 40 0 0 0 223 ${bottom}V190H170Z`
  if (code === 'paint_right') return `M277 0H330V190H277V${bottom}A40 40 0 0 0 277 ${top}Z`
  if (code === 'paint_center') return `M223 0H277V${top}A40 40 0 0 0 223 ${top}Z M223 ${bottom}A40 40 0 0 0 277 ${bottom}V190H223Z`
  return path
}

export function addCourtZones(scene: THREE.Scene): Map<string, THREE.Group> {
  const zones = new Map<string, THREE.Group>()
  const loader = new SVGLoader()
  COURT_ZONES.forEach((zone, index) => {
    const parsed = loader.parse(`<svg xmlns="http://www.w3.org/2000/svg"><path d="${floorPath(zone.code, zone.path)}"/></svg>`)
    const group = new THREE.Group()
    group.userData.zoneCode = zone.code
    group.userData.baseColor = index % 2 ? '#bd9b68' : '#d3b585'
    for (const path of parsed.paths) for (const shape of SVGLoader.createShapes(path)) {
      const geometry = new THREE.ExtrudeGeometry(shape, { depth: 8, bevelEnabled: false, curveSegments: 48 })
      geometry.scale(.1, .1, .1)
      geometry.rotateX(Math.PI / 2)
      geometry.translate(-25, .065, -23.5)
      const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: group.userData.baseColor, transparent: true, opacity: .2, roughness: .85, depthWrite: false, side: THREE.DoubleSide }))
      mesh.userData.zoneCode = zone.code
      mesh.renderOrder = 1
      group.add(mesh)
      const edge = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(shape.getPoints(80).map(point => new THREE.Vector3(point.x / 10 - 25, .09, point.y / 10 - 23.5))), new THREE.LineBasicMaterial({ color: '#e9d9b6', transparent: true, opacity: .65 }))
      group.add(edge)
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
      material.color.set(code === selected ? '#f8ffd9' : '#e9d9b6')
      material.opacity = code === selected ? 1 : selected ? .3 : .65
    }
  })
}
