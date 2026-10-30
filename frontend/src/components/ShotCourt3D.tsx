import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import './ShotCourt3D.css'

export type CourtShot = {
  id: string
  player_name: string
  candidate_time_ms: number
  court_x: number | null
  court_y: number | null
  zone_label: string | null
  result: string
  shot_type: string
  status: string
}

const DEFAULT_CAMERA = new THREE.Vector3(62, 64, 76)
const COURT_TARGET = new THREE.Vector3(0, 0, 0)
const COURT_FIT_POINTS = [
  ...[-28, 28].flatMap((x) => [-26.5, 26.5].flatMap((z) => [-.7, .5].map((y) => new THREE.Vector3(x, y, z)))),
  ...[-3.5, 3.5].flatMap((x) => [9.5, 13.5].map((y) => new THREE.Vector3(x, y, -20.1))),
  new THREE.Vector3(0, 12, -26),
]

function minimumCourtDistance(camera: THREE.PerspectiveCamera, target: THREE.Vector3) {
  const direction = camera.position.clone().sub(target).normalize()
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion)
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion)
  const vertical = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * .9
  const horizontal = vertical * camera.aspect
  return Math.max(42, ...COURT_FIT_POINTS.map((point) => {
    const relative = point.clone().sub(target)
    return relative.dot(direction) + Math.max(Math.abs(relative.dot(right)) / horizontal, Math.abs(relative.dot(up)) / vertical)
  }))
}

// The API stores feet from the left sideline and from the baseline.
function courtPosition(x: number, y: number) {
  return new THREE.Vector3(x - 25, 0, y - 23.5)
}

function courtLine(scene: THREE.Scene, points: Array<[number, number]>, closed = false) {
  const geometry = new THREE.BufferGeometry().setFromPoints(
    points.map(([x, y]) => new THREE.Vector3(x - 25, 0.11, y - 23.5)),
  )
  const line = closed
    ? new THREE.LineLoop(geometry, new THREE.LineBasicMaterial({ color: '#f8f1db' }))
    : new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: '#f8f1db' }))
  scene.add(line)
}

function arc(cx: number, cy: number, radius: number, start: number, end: number, steps = 64): Array<[number, number]> {
  return Array.from({ length: steps + 1 }, (_, index) => {
    const angle = start + (end - start) * index / steps
    return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]
  })
}

function addCourt(scene: THREE.Scene) {
  const apron = new THREE.Mesh(new THREE.BoxGeometry(56, 0.65, 53), new THREE.MeshStandardMaterial({ color: '#222a28', roughness: 1 }))
  apron.position.y = -0.38
  scene.add(apron)
  const floor = new THREE.Mesh(new THREE.BoxGeometry(50, 0.22, 47), new THREE.MeshStandardMaterial({ color: '#b88d5a', roughness: .87 }))
  floor.position.y = -0.12
  scene.add(floor)
  for (let x = 0; x < 50; x += 2) {
    const plank = new THREE.Mesh(
      new THREE.PlaneGeometry(1.98, 46.96),
      new THREE.MeshStandardMaterial({ color: x % 4 === 0 ? '#c69c69' : '#b48754', roughness: .9, side: THREE.DoubleSide }),
    )
    plank.rotation.x = -Math.PI / 2
    plank.position.set(x - 24, 0.015, 0)
    scene.add(plank)
  }

  courtLine(scene, [[0, 0], [50, 0], [50, 47], [0, 47]], true)
  courtLine(scene, [[17, 0], [33, 0], [33, 19], [17, 19]], true)
  courtLine(scene, [[3, 0], [3, 14]])
  courtLine(scene, [[47, 0], [47, 14]])
  const theta = Math.atan2(14 - 5.25, Math.sqrt(23.75 ** 2 - (14 - 5.25) ** 2))
  courtLine(scene, arc(25, 5.25, 23.75, theta, Math.PI - theta))
  courtLine(scene, arc(25, 5.25, 4, 0, Math.PI))
  courtLine(scene, arc(25, 19, 6, 0, Math.PI))
  courtLine(scene, arc(25, 47, 6, Math.PI, 2 * Math.PI))

  const darkMetal = new THREE.MeshStandardMaterial({ color: '#303b3e', metalness: .5, roughness: .45 })
  const backboard = new THREE.Mesh(new THREE.BoxGeometry(7, 4, .24), new THREE.MeshStandardMaterial({ color: '#e5e7e3', roughness: .4 }))
  backboard.position.set(0, 11.5, -20.1)
  scene.add(backboard)
  const square = new THREE.Mesh(new THREE.BoxGeometry(2.2, 1.7, .04), new THREE.MeshBasicMaterial({ color: '#343e3d' }))
  square.position.set(0, 11.15, -19.95)
  scene.add(square)
  const ring = new THREE.Mesh(new THREE.TorusGeometry(.75, .085, 8, 32), new THREE.MeshStandardMaterial({ color: '#e65f31', metalness: .2, roughness: .4 }))
  ring.rotation.x = -Math.PI / 2
  ring.position.set(0, 10, -18.25)
  scene.add(ring)
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(.25, .38, 12, 10), darkMetal)
  pole.position.set(0, 6, -26)
  scene.add(pole)
  const arm = new THREE.Mesh(new THREE.BoxGeometry(.3, .3, 6), darkMetal)
  arm.position.set(0, 11.8, -23)
  scene.add(arm)
}

function markerTexture(result: string, selected: boolean) {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 128
  const context = canvas.getContext('2d')!
  const color = result === 'made' ? '#1fb878' : result === 'missed' ? '#e3534d' : '#be9342'
  context.beginPath()
  context.arc(64, 64, 53, 0, Math.PI * 2)
  context.fillStyle = color
  context.fill()
  context.lineWidth = selected ? 9 : 5
  context.strokeStyle = selected ? '#f5ff8e' : '#fff'
  context.stroke()
  context.fillStyle = '#fff'
  context.textAlign = 'center'
  context.textBaseline = 'middle'
  context.font = '900 70px system-ui'
  context.fillText(result === 'made' ? 'O' : result === 'missed' ? 'X' : '?', 64, 67)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

function spreadOverlaps(shots: CourtShot[]) {
  const counts = new Map<string, number>()
  return shots.map((shot) => {
    const key = `${Math.round(shot.court_x! * 4)},${Math.round(shot.court_y! * 4)}`
    const index = counts.get(key) ?? 0
    counts.set(key, index + 1)
    const angle = index * 2.39996
    const radius = index === 0 ? 0 : 2.8 * Math.sqrt(index)
    return { shot, offsetX: radius * Math.cos(angle), offsetY: radius * Math.sin(angle) }
  })
}

export function ShotCourt3D({ shots, selectedShot, jerseyNumber, onSelect, onPlay, typeLabels }: {
  shots: CourtShot[]
  selectedShot: CourtShot | null
  jerseyNumber: string | null
  onSelect: (shotId: string) => void
  onPlay: () => void
  typeLabels: Record<string, string>
}) {
  const mountRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const markersRef = useRef<THREE.Group | null>(null)
  const texturesRef = useRef<Map<string, THREE.Texture>>(new Map())
  const onSelectRef = useRef(onSelect)
  const shotsRef = useRef(shots)
  const [renderError, setRenderError] = useState(false)

  useEffect(() => {
    onSelectRef.current = onSelect
    shotsRef.current = shots
  }, [onSelect, shots])

  function render() {
    if (sceneRef.current && cameraRef.current) rendererRef.current?.render(sceneRef.current, cameraRef.current)
  }

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    } catch {
      window.requestAnimationFrame(() => setRenderError(true))
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor('#0c1512')
    renderer.outputColorSpace = THREE.SRGBColorSpace
    mount.appendChild(renderer.domElement)
    const scene = new THREE.Scene()
    scene.background = new THREE.Color('#0c1512')
    scene.add(new THREE.HemisphereLight('#ffffff', '#54675e', 2.2))
    const light = new THREE.DirectionalLight('#fff4d8', 2.3)
    light.position.set(-15, 55, 30)
    scene.add(light)
    addCourt(scene)
    const markers = new THREE.Group()
    scene.add(markers)
    const camera = new THREE.PerspectiveCamera(48, 1, .1, 400)
    camera.position.copy(DEFAULT_CAMERA)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.target.copy(COURT_TARGET)
    controls.enablePan = false
    controls.minDistance = 42
    controls.maxDistance = 160
    controls.minPolarAngle = .25
    controls.maxPolarAngle = Math.PI / 2.15
    controls.update()
    const draw = () => {
      controls.minDistance = minimumCourtDistance(camera, controls.target)
      const offset = camera.position.clone().sub(controls.target)
      if (offset.length() < controls.minDistance) camera.position.copy(controls.target).add(offset.setLength(controls.minDistance))
      renderer.render(scene, camera)
    }
    controls.addEventListener('change', draw)
    const resize = () => {
      const width = mount.clientWidth
      const height = mount.clientHeight
      if (!width || !height) return
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
      draw()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(mount)
    resize()
    let pointerDown: { id: number; x: number; y: number } | null = null
    const down = (event: PointerEvent) => {
      if (event.button === 0) pointerDown = { id: event.pointerId, x: event.clientX, y: event.clientY }
    }
    const up = (event: PointerEvent) => {
      const start = pointerDown
      pointerDown = null
      if (!start || start.id !== event.pointerId || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) return
      const bounds = renderer.domElement.getBoundingClientRect()
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) return
      let nearest: { id: string; distance: number } | null = null
      for (const marker of markers.children) {
        const id = marker.userData.shotId as string | undefined
        if (!id || !(marker instanceof THREE.Mesh)) continue
        const center = marker.position.clone().project(camera)
        const edge = marker.position.clone().add(new THREE.Vector3(marker.geometry.parameters.width / 2, 0, 0)).project(camera)
        const x = bounds.left + (center.x + 1) * bounds.width / 2
        const y = bounds.top + (1 - center.y) * bounds.height / 2
        const radius = Math.max(18, Math.abs(edge.x - center.x) * bounds.width / 2 + 8)
        const distance = Math.hypot(event.clientX - x, event.clientY - y)
        if (distance <= radius && (!nearest || distance < nearest.distance)) nearest = { id, distance }
      }
      const shot = shotsRef.current.find((item) => item.id === nearest?.id)
      if (shot) onSelectRef.current(shot.id)
    }
    const cancel = () => { pointerDown = null }
    renderer.domElement.addEventListener('pointerdown', down)
    renderer.domElement.ownerDocument.addEventListener('pointerup', up, true)
    renderer.domElement.addEventListener('pointercancel', cancel)
    sceneRef.current = scene
    cameraRef.current = camera
    controlsRef.current = controls
    rendererRef.current = renderer
    markersRef.current = markers
    const textures = texturesRef.current
    return () => {
      observer.disconnect()
      renderer.domElement.removeEventListener('pointerdown', down)
      renderer.domElement.ownerDocument.removeEventListener('pointerup', up, true)
      renderer.domElement.removeEventListener('pointercancel', cancel)
      controls.removeEventListener('change', draw)
      controls.dispose()
      scene.traverse((item) => {
        if (item instanceof THREE.Mesh || item instanceof THREE.Line || item instanceof THREE.LineLoop) {
          item.geometry.dispose()
          const materials = Array.isArray(item.material) ? item.material : [item.material]
          materials.forEach((material) => material.dispose())
        }
      })
      textures.forEach((texture) => texture.dispose())
      textures.clear()
      renderer.dispose()
      mount.removeChild(renderer.domElement)
      sceneRef.current = null
      cameraRef.current = null
      controlsRef.current = null
      rendererRef.current = null
      markersRef.current = null
    }
  }, [])

  useEffect(() => {
    const markers = markersRef.current
    if (!markers) return
    for (const child of [...markers.children]) {
      markers.remove(child)
      if (child instanceof THREE.Mesh) { child.geometry.dispose(); child.material.dispose() }
    }
    const located = shots.filter((shot) => shot.court_x != null && shot.court_y != null &&
      shot.court_x >= 0 && shot.court_x <= 50 && shot.court_y >= 0 && shot.court_y <= 47)
    for (const { shot, offsetX, offsetY } of spreadOverlaps(located)) {
      const point = courtPosition(shot.court_x!, shot.court_y!)
      const selected = shot.id === selectedShot?.id
      const key = `${shot.result}-${selected}`
      let texture = texturesRef.current.get(key)
      if (!texture) { texture = markerTexture(shot.result, selected); texturesRef.current.set(key, texture) }
      const size = selected ? 3.8 : 3.3
      const marker = new THREE.Mesh(
        new THREE.PlaneGeometry(size, size),
        new THREE.MeshBasicMaterial({ map: texture, transparent: true, side: THREE.DoubleSide, depthWrite: false }),
      )
      marker.rotation.x = -Math.PI / 2
      marker.position.set(
        THREE.MathUtils.clamp(point.x + offsetX, -25 + size / 2, 25 - size / 2),
        .17,
        THREE.MathUtils.clamp(point.z + offsetY, -23.5 + size / 2, 23.5 - size / 2),
      )
      marker.userData.shotId = shot.id
      marker.renderOrder = 2
      markers.add(marker)
    }
    render()
  }, [shots, selectedShot?.id])

  function zoom(factor: number) {
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!camera || !controls) return
    const vector = camera.position.clone().sub(controls.target).multiplyScalar(factor)
    vector.setLength(THREE.MathUtils.clamp(vector.length(), controls.minDistance, controls.maxDistance))
    camera.position.copy(controls.target).add(vector)
    controls.update()
    render()
  }

  function reset() {
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!camera || !controls) return
    camera.position.copy(DEFAULT_CAMERA)
    controls.target.copy(COURT_TARGET)
    controls.update()
    render()
  }

  const locatedCount = shots.filter((shot) => shot.court_x != null && shot.court_y != null).length
  return <div className="shot-court-3d">
    <div className="court-3d-heading"><div><strong>3D 슈팅 맵</strong><span>코트를 끌어 회전하고, O·X를 눌러 슛을 선택하세요.</span></div><div className="court-3d-controls"><button type="button" onClick={() => zoom(.8)} aria-label="3D 코트 확대">+</button><button type="button" onClick={() => zoom(1.25)} aria-label="3D 코트 축소">−</button><button type="button" onClick={reset}>초기 시점</button></div></div>
    {renderError ? <p className="court-3d-unavailable">이 브라우저에서는 3D 코트를 표시할 수 없습니다. 아래 슛 목록에서 기록을 확인할 수 있습니다.</p> : <div ref={mountRef} className="court-3d-canvas" role="img" aria-label={`슛 ${locatedCount}개가 표시된 3D 반코트`} />}
    <div className="court-3d-footer"><span><i className="made" /> 성공 O</span><span><i className="missed" /> 실패 X</span><span><i className="uncertain" /> 판단 불가 ?</span><span>위치 없는 기록은 아래 목록에서 확인</span></div>
    {selectedShot && <div className="court-3d-selected"><div><strong>{selectedShot.player_name}{jerseyNumber ? ` #${jerseyNumber}` : ''} · {(selectedShot.candidate_time_ms / 1000).toFixed(2)}초</strong><span>{selectedShot.zone_label ?? '위치 확인 필요'} · {selectedShot.result === 'made' ? '성공' : selectedShot.result === 'missed' ? '실패' : '판단 불가'} · {typeLabels[selectedShot.shot_type] ?? selectedShot.shot_type} · {selectedShot.status === 'user_confirmed' ? '사용자 확인' : selectedShot.status === 'needs_review' ? '확인 필요' : '자동 판정'}</span></div><button type="button" className="button secondary" onClick={onPlay}>슛 장면 재생</button></div>}
  </div>
}
