"""Render one-screen presentation videos from fresh YOLO boxes and saved AI evidence.

No OpenAI requests, database writes, or fabricated detections are used.
Run from backend with its virtual environment. Keep the input comparison clip intact.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch  # Import before OpenCV on Windows.
import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

from app.analysis.phase7_pipeline import _detections, center, choose, release_candidate, remove_static_ball_detections


W, H, FPS = 1920, 1080, 24
GREEN, YELLOW, BLUE, RED = '#49d3a4', '#ffd36c', '#7ebdff', '#ff7077'
WHITE, DARK = '#f6faf8', '#131b20'
FONTS = {size: ImageFont.truetype('C:/Windows/Fonts/malgunbd.ttf', size)
         for size in (24, 28, 32, 36, 40, 44, 48)}


def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def pill(image, text, x, y, *, size=32, color=WHITE, anchor='left', background=DARK):
    draw = ImageDraw.Draw(image)
    font = FONTS[size]
    width = draw.textbbox((0, 0), text, font=font)[2] + 32
    height = size + 24
    if anchor == 'right':
        x -= width
    elif anchor == 'center':
        x -= width / 2
    x = max(14, min(W - width - 14, x))
    y = max(14, min(H - height - 14, y))
    draw.rounded_rectangle((x, y, x + width, y + height), 9, fill=background)
    draw.text((x + 16, y + 7), text, font=font, fill=color)
    return width


def caption(image, first, second=None):
    if second:
        pill(image, first, W / 2, H - 150, size=36, anchor='center')
        pill(image, second, W / 2, H - 87, size=32, anchor='center', color=YELLOW)
    else:
        pill(image, first, W / 2, H - 91, size=36, anchor='center')


def mark(image, point, label, *, color=YELLOW, delta=(-80, 24), number=None):
    d = ImageDraw.Draw(image)
    x, y = point
    d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=color, outline=DARK, width=2)
    if number:
        d.text((x, y), str(number), font=FONTS[24], fill=DARK, anchor='mm')
    pill(image, label, x + delta[0], y + delta[1], size=28, color=color)


def arrow(draw, start, end, color=YELLOW):
    a, b = np.array(start, float), np.array(end, float)
    draw.line((tuple(a), tuple(b)), fill=color, width=5)
    v = b - a
    norm = np.linalg.norm(v)
    if norm:
        v /= norm
        normal = np.array([-v[1], v[0]])
        draw.polygon([tuple(b), tuple(b - v * 21 + normal * 10),
                      tuple(b - v * 21 - normal * 10)], fill=color)


class Encoder:
    def __init__(self, path):
        self.path = path
        self.process = subprocess.Popen([
            imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS),
            '-i', '-', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
            '-threads', '2', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(path)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.frames = 0

    def add(self, frame):
        self.process.stdin.write(np.asarray(frame, dtype=np.uint8).tobytes())
        self.frames += 1

    def finish(self):
        self.process.stdin.close()
        error = self.process.stderr.read().decode('utf-8', errors='replace')
        assert self.process.wait() == 0, error[-500:]
        return {'frames': self.frames, 'seconds': self.frames / FPS,
                'bytes': self.path.stat().st_size, 'sha256': sha(self.path)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    model_path = Path('models/basketball-yolo26n-pilot-v4_openvino_model')
    model_hash = sha(Path('models/basketball-yolo26n-pilot-v4.pt'))
    analysis = json.loads(args.analysis.read_text(encoding='utf-8'))
    candidates = analysis['candidates']
    assert [(c['time_ms'], c['ai_review']['outcome']) for c in candidates] == [(2500, 'made'), (8600, 'missed')]
    source_hash = sha(args.source)
    cache = args.work / 'frames'
    cache.mkdir(exist_ok=True)
    detection_file = args.work / 'fresh-detections.json'
    saved = json.loads(detection_file.read_text(encoding='utf-8')) if detection_file.exists() else None
    if saved and saved.get('render_detector_policy') == 2:
        assert saved['model_sha256'] == model_hash and saved['source_sha256'] == source_hash
        observations = saved['observations']
    else:
        model = YOLO(str(model_path), task='detect')
        cap = cv2.VideoCapture(str(args.source))
        source_fps = cap.get(cv2.CAP_PROP_FPS)
        n = round(cap.get(cv2.CAP_PROP_FRAME_COUNT) / source_fps * FPS)
        observations = []
        previous = None
        for index in range(n):
            cap.set(cv2.CAP_PROP_POS_MSEC, index / FPS * 1000)
            ok, frame = cap.read()
            assert ok, index
            result = model.predict(frame, imgsz=640, conf=.12, verbose=False, device='cpu')[0]
            boxes = _detections(result, result.names)
            person = choose(boxes['person'], previous, W, H, .18)
            size = 640
            if person is None or (person['xyxy'][3] - person['xyxy'][1]) / H < .25:
                detailed = model.predict(frame, imgsz=960, conf=.12, verbose=False, device='cpu')[0]
                detailed_boxes = _detections(detailed, detailed.names)
                detailed_person = choose(detailed_boxes['person'], previous, W, H, .18)
                if detailed_person is not None:
                    boxes, person = detailed_boxes, detailed_person
                size = 960
            previous = person
            observations.append({'time_ms': round(index / FPS * 1000), 'frame_index': index,
                                  'person': person, 'balls': boxes['basketball'], 'rims': boxes['rim'],
                                  'inference_size': size})
            cv2.imwrite(str(cache / f'{index:04}.jpg'), frame, [cv2.IMWRITE_JPEG_QUALITY, 96])
            if index % 48 == 0:
                print(f'YOLO frames {index}/{n}', flush=True)
        cap.release()
        saved = {'model_sha256': model_hash, 'source_sha256': source_hash, 'render_detector_policy': 2,
                 'visualization_fps': FPS, 'observations': observations}
        detection_file.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')

    static_removed = remove_static_ball_detections(observations, W, H)

    @lru_cache(maxsize=24)
    def frame_at(index):
        return Image.open(cache / f'{index:04}.jpg').convert('RGB')

    def source(t):
        return frame_at(max(0, min(len(observations) - 1, round(t * FPS)))).copy()

    def boxes_on(image, t):
        rec = observations[max(0, min(len(observations) - 1, round(t * FPS)))]
        draw = ImageDraw.Draw(image)
        rim = min(rec['rims'], key=lambda b: np.linalg.norm(np.array(center(b)) - analysis['hoop_point_px'])) if rec['rims'] else None
        ball = None
        if rec['balls']:
            # Show actual detections only. A highlighted observation is not a final shot decision.
            ball = max(rec['balls'], key=lambda b: b['confidence'])
        for label, item, color in [('선수', rec['person'], GREEN), ('공', ball, YELLOW), ('림', rim, BLUE)]:
            if not item:
                continue
            x1, y1, x2, y2 = item['xyxy']
            draw.rectangle((x1, y1, x2, y2), outline=color, width=4)
            pill(image, f"{label} {item['confidence']:.0%}", x1, max(100, y1 - 53), size=28, color=color)
        return rec

    # Locate an actual pair accepted by the current trajectory rule. Do not invent ball positions.
    pair = None
    for rec in observations:
        if not 1500 <= rec['time_ms'] <= 3000:
            continue
        for old in reversed(observations[:rec['frame_index']]):
            if rec['time_ms'] - old['time_ms'] > 750:
                break
            for a in old['balls']:
                for b in rec['balls']:
                    earlier = {**old, 'ball': a, 'hoop_valid': True}
                    later = {**rec, 'ball': b, 'hoop_valid': True}
                    candidate = release_candidate(earlier, later, analysis['hoop_point_px'], H)
                    if candidate and (pair is None or old['time_ms'] > pair[0]['time_ms']):
                        pair = (earlier, later, candidate)
    assert pair is not None, 'No actual rising-ball pair; revise storyboard instead of fabricating evidence.'
    pair_info = {'from_ms': pair[0]['time_ms'], 'to_ms': pair[1]['time_ms'],
                 'from_ball_px': center(pair[0]['ball']), 'to_ball_px': center(pair[1]['ball']),
                 'rule': pair[2]}
    print('Actual release-rule pair', pair_info['from_ms'], pair_info['to_ms'], flush=True)

    yolo_out = args.work / 'YOLO.mp4'
    writer = Encoder(yolo_out)
    for i in range(round(24.3 * FPS)):
        t = i / FPS
        if t < 10.3:
            source_time = t
            stage = '실제 객체 탐지'
        elif t < 17.3:
            source_time = 1.5 + (t - 10.3) * .3
            stage = '첫 슛 · 릴리스 확인 · 0.3×'
        else:
            source_time = 7.7 + (t - 17.3) * .3
            stage = '다음 슛 · 릴리스 확인 · 0.3×'
        image = source(source_time)
        boxes_on(image, source_time)
        pill(image, 'ShotLog · YOLO', 36, 30, size=36)
        pill(image, stage, W - 36, 30, size=32, anchor='right')
        if t < 10.3:
            caption(image, '선수·공·림의 위치를 찾고, 자세와 공의 이동에서 슛 후보를 만듭니다')
        elif t < 14:
            caption(image, '선수 주변 공이 위쪽·골대 방향으로 움직이는지 비교합니다')
        elif t < 17.3:
            a, b = pair_info['from_ball_px'], pair_info['to_ball_px']
            arrow(ImageDraw.Draw(image), a, b)
            dt = pair_info['to_ms'] - pair_info['from_ms']
            caption(image, f'실제 탐지 두 장면: {dt}ms 동안 공이 위로 이동',
                    '저장 후보 2.00초 → AI가 손에서 떨어진 시각 2.50초로 보정')
        else:
            caption(image, '손과 공이 분리된 앞뒤 장면을 다시 확인합니다',
                    '저장 후보 8.80초 → AI가 릴리스 8.60초로 보정')
        pill(image, f'원본 {source_time:04.2f}초', 36, 106, size=28)
        writer.add(image)
        if i % 240 == 0:
            print(f'YOLO render {i}/{round(24.3 * FPS)}', flush=True)
    yolo_info = writer.finish()

    first = candidates[0]
    review = first['ai_review']
    plant_ms = review['plant_sample_time_ms']
    plant = min(first['foot_track'], key=lambda f: abs(f['time_ms'] - plant_ms))
    pixel = plant['foot_point_px']
    transform = np.array(first['validated_court_transform'], float)
    projected = transform @ np.array([*pixel, 1.0])
    projected = projected[:2] / projected[2]
    assert np.linalg.norm(projected - review['calibrated_plant_projection']) < .02
    image_points = [[452.2943722943723, 696.038961038961], [923.2900432900434, 617.0779220779222],
                    [1440, 637.8571428571428], [963.4632034632034, 758.3766233766233]]
    court_points = [(17, 0), (33, 0), (33, 19), (17, 19)]
    ai_out = args.work / 'AI.mp4'
    writer = Encoder(ai_out)
    for i in range(44 * FPS):
        t = i / FPS
        if t < 5.5:
            image = source(t)
            stage = '슛 여부·종류 확인'
            caption(image, '드리블 → 발을 모으고 점프 → 릴리스', '저장 판정: 실제 슛 · 드리블 풀업')
        elif t < 10.5:
            image = source(min(10.25, 5.5 + t - 5.5))
            stage = '다음 슛 확인'
            caption(image, '다음 후보도 별도의 앞뒤 장면으로 확인합니다', '각 슛의 판정 요청은 분리하고 코트 기준은 함께 사용합니다')
        elif t < 18:
            image = source(plant_ms / 1000)
            stage = '코트 좌표 · 네 기준점'
            overlay = Image.new('RGBA', (W, H))
            d = ImageDraw.Draw(overlay)
            d.polygon([tuple(p) for p in image_points], fill=(218, 255, 75, 30), outline=YELLOW, width=4)
            image = Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')
            for j, (point, world) in enumerate(zip(image_points, court_points)):
                mark(image, point, f'{j + 1}  ({world[0]}, {world[1]}) ft', number=j + 1,
                     delta=(-130, -65) if j in (1, 2) else (-80, 24))
            mark(image, [734.8918, 308.1602], '림 중심 · 추적 기준', color=BLUE, delta=(25, -25))
            if t < 14:
                caption(image, '사용자가 페인트존 네 모서리를 실제 바닥 선에 맞춥니다')
            else:
                caption(image, '가상 반코트 50×47ft · 페인트존 폭 16ft · 깊이 19ft',
                        '좌우 x=17·33 / 베이스라인 y=0 / 자유투선 y=19')
        elif t < 26:
            image = source(plant_ms / 1000)
            stage = '이미지 픽셀 → 코트 좌표'
            d = ImageDraw.Draw(image)
            x, y = pixel
            d.ellipse((x-24, y-24, x+24, y+24), outline=YELLOW, width=5)
            d.line((x-36, y, x+36, y), fill=YELLOW, width=3)
            d.line((x, y-36, x, y+36), fill=YELLOW, width=3)
            mark(image, pixel, f'상자 하단 참고점 ({x:.0f}, {y:.0f}) px', delta=(-520, 35))
            if t < 23:
                caption(image, '네 바닥점으로 원근 변환을 계산합니다',
                        f'화면 ({x:.0f}, {y:.0f}) px → 참고 좌표 ({projected[0]:.2f}, {projected[1]:.2f}) ft')
            else:
                pill(image, '(35.3, 16.0) ft · 오른쪽 45도 미드레인지', 36, 112, size=36, color=GREEN)
                caption(image, 'AI가 신발·코트 선·전후 장면을 검토해 최종 위치를 저장합니다',
                        '참고 환산점은 정밀 신발 접지점을 측정한 값과 다릅니다')
        elif t < 34:
            source_time = 2.7 + (t - 26) * .29
            image = source(source_time).crop((300, 0, 1260, 540)).resize((W, H), Image.Resampling.LANCZOS)
            stage = '성공 판정 · 림 장면'
            pill(image, '성공', 36, 112, size=44, color=GREEN)
            caption(image, '공이 림·네트를 통과하고 아래로 내려오는 연속 장면을 확인합니다')
        elif t < 42:
            source_time = 8.0 + (t - 34) * .28
            image = source(source_time).crop((300, 0, 1260, 540)).resize((W, H), Image.Resampling.LANCZOS)
            stage = '실패 판정 · 림 장면'
            pill(image, '실패', 36, 112, size=44, color=RED)
            caption(image, '공이 림에 튕긴 뒤 바깥으로 떨어지는 연속 장면을 확인합니다')
        else:
            image = source(9.6)
            stage = '실제 저장 결과'
            pill(image, '성공 1 · 실패 1 · 위치 확인 완료', 36, 112, size=36, color=GREEN)
            caption(image, '2.50초: 드리블 풀업 · 오른쪽 45도 미드레인지',
                    '8.60초: 드리블 풀업 · 오른쪽 베이스라인 미드레인지')
        pill(image, f'AI · {stage}', 36, 30, size=36)
        pill(image, '실제 저장 판정 설명', W - 36, 30, size=28, anchor='right')
        writer.add(image)
        if i % 240 == 0:
            print(f'AI render {i}/{44 * FPS}', flush=True)
    ai_info = writer.finish()

    # Verify all encoded frames and layout samples before replacing the user's files.
    for name, info in [('YOLO', yolo_info), ('AI', ai_info)]:
        cap = cv2.VideoCapture(str(args.work / f'{name}.mp4'))
        count = 0
        while True:
            ok, decoded = cap.read()
            if not ok:
                break
            assert decoded.shape[:2] == (H, W)
            assert decoded.mean() > 4, (name, count, 'empty frame')
            count += 1
        cap.release()
        assert count == info['frames'], (name, count, info)
    assert sha(args.source) == source_hash
    archive = args.output / '이전 버전' / ('2026-11-06_교체전_' + datetime.now().strftime('%H%M%S'))
    archive.mkdir(parents=True, exist_ok=True)
    for name in ['YOLO.mp4', 'AI.mp4', '최신_발표영상_안내.md']:
        old = args.output / name
        if old.exists():
            shutil.copy2(old, archive / name)
    for name in ['YOLO.mp4', 'AI.mp4']:
        shutil.copy2(args.work / name, args.output / name)
    report = {'source_unchanged_sha256': source_hash, 'model_sha256': model_hash,
              'paid_api_calls': 0, 'static_ball_detections_removed': static_removed,
              'saved_analysis_id': 'b0f40c55-f0e0-4820-afb2-52f5a88b8c2b',
              'YOLO': yolo_info, 'AI': ai_info, 'actual_rule_pair': pair_info,
              'plant_sample_ms': plant_ms, 'plant_proxy_px': pixel, 'projection_ft': projected.tolist(),
              'final_ai_position_ft': [review['court_x'], review['court_y']],
              'format': '1920x1080 / 24fps / H.264 / no audio',
              'archive': str(archive)}
    (args.work / 'render-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
