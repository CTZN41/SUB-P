"""Create a Korean practice PDF from the same saved statistics as the result UI.

Rendering is local. No video processing or additional AI request is made here.
"""
from __future__ import annotations

from io import BytesIO
from functools import lru_cache
import math
import os
from pathlib import Path
from threading import RLock
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from app.services.court_zones import ZoneCode, ZONE_LABELS, classify_zone

INK = '#172a27'
MUTED = '#536660'
HOT = '#b4dec6'
COLD = '#f0b9b0'
EMPTY = '#e6eae7'
FONT_LOCK = RLock()

# Same label anchors as the interactive 2D chart, in court feet.
ANCHORS = {
    'restricted': (25, 5.5, 6),
    'paint_left': (19.6667, 15.7, 4.8),
    'paint_center': (25, 15.7, 4.8),
    'paint_right': (30.3333, 15.7, 4.8),
    'mid_baseline_left': (13, 5.5, 7),
    'mid_wing_left': (11.2, 17.5, 8.2),
    'mid_center': (25, 25, 8.2),
    'mid_wing_right': (38.8, 17.5, 8.2),
    'mid_baseline_right': (37, 5.5, 7),
    'three_corner_left': (3.8, 5.5, 7),
    'three_wing_left': (8, 37, 8.2),
    'three_center': (25, 40.6, 8.2),
    'three_wing_right': (42, 37, 8.2),
    'three_corner_right': (46.2, 5.5, 7),
}


class PDFReportError(Exception):
    pass


def zone_target(code):
    if code == 'restricted' or code.startswith('paint_'):
        return 90
    return 50 if code.startswith('mid_') else 40


def zone_rows(context):
    """Never infer a zone from a cached AI label; context uses saved XY."""
    source = {row['key']: row for row in context['rows']}
    result = []
    for number, zone in enumerate(ZoneCode, 1):
        stat = source.get('zone/' + zone.value, {}).get('current') or {
            'attempts': 0, 'made': 0, 'missed': 0, 'unresolved': 0, 'success_rate': None}
        rate = stat['success_rate']
        target = zone_target(zone.value)
        result.append({'number': number, 'code': zone.value, 'label': ZONE_LABELS[zone],
                       **stat, 'target': target,
                       'gap_pp': None if rate is None else round(rate - target, 2),
                       'tone': EMPTY if stat['attempts'] < 5 or rate is None else HOT if rate >= target else COLD})
    return result


def practice_priorities(rows):
    attempted = [r for r in rows if r['success_rate'] is not None]
    below = [r for r in attempted if r['gap_pp'] < 0]
    # Rank by missing makes relative to the personal target, then by attempts.
    # A single attempt still gets a color; it does not establish a stable weakness.
    if not below:
        return sorted(attempted, key=lambda r: (r['success_rate'], -r['attempts'], r['number']))[:2]
    return sorted(below, key=lambda r: (
        -(r['target'] * (r['made'] + r['missed']) / 100 - r['made']),
        -r['attempts'], r['number']))[:2]


def _font(settings):
    configured = getattr(settings, 'report_font_path', None)
    choices = [Path(configured)] if configured else [
        Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/malgun.ttf',
        Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf'),
        Path('/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf'),
    ]
    path = next((p for p in choices if p.is_file()), None)
    if path is None:
        raise PDFReportError('Korean PDF font is not configured')
    import hashlib
    name = 'ShotLog-' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    with FONT_LOCK:
        if name not in pdfmetrics.getRegisteredFontNames():
            try:
                font = TTFont(name, str(path))
                if any(ord(c) not in font.face.charWidths for c in '골밑슛연습결과'):
                    raise ValueError('Missing Korean glyphs')
                pdfmetrics.registerFont(font)
            except Exception as error:
                raise PDFReportError('Korean PDF font could not be loaded') from error
    return name


def _paragraph(c, text, x, top, width, font, size=10, color=INK, leading=None):
    style = ParagraphStyle('report', fontName=font, fontSize=size, leading=leading or size * 1.6,
                           textColor=colors.HexColor(color), wordWrap='CJK', splitLongWords=True)
    para = Paragraph(escape(str(text)).replace('\n', '<br/>'), style)
    _, height = para.wrap(width, 10000)
    para.drawOn(c, x, top - height)
    return top - height


@lru_cache(maxsize=8)
def _heatmap_image(tones):
    # Classify each pixel with the production rule; no approximate sector shapes.
    w, h = 1000, 940
    palette = {code: bytes.fromhex(tone.lstrip('#')) for code,tone in tones}
    pixels = bytearray(w * h * 3)
    last = None
    for py in range(h):
        current = []
        for px in range(w):
            code = classify_zone((px + .5) * 50 / w, (py + .5) * 47 / h).value
            current.append(code)
            color = palette[code]
            if (px and code != current[px - 1]) or (last and code != last[px]):
                color = b'\xff\xff\xff'
            offset = (py * w + px) * 3
            pixels[offset:offset + 3] = color
        last = current
    return Image.frombytes('RGB', (w, h), bytes(pixels))


def _court(c, rows, x, top, width, font):
    image = _heatmap_image(tuple((r['code'],r['tone']) for r in rows))
    height = width * 47 / 50
    c.drawImage(ImageReader(image), x, top - height, width=width, height=height)
    scale = width / 50
    def point(cx, cy):
        return x + cx * scale, top - cy * scale
    def line(points):
        path = c.beginPath()
        path.moveTo(*point(*points[0]))
        for p in points[1:]:
            path.lineTo(*point(*p))
        c.drawPath(path)
    c.setStrokeColor(colors.white); c.setLineWidth(1)
    c.rect(x, top - height, width, height, fill=0)
    line([(17, 0), (17, 19), (33, 19), (33, 0)])
    line([(22.8, 3.4), (27.2, 3.4)])
    c.circle(*point(25, 5.25), .7 * scale)
    join = math.acos(22 / 23.75)
    arc = [(25 + 23.75 * math.cos(a), 5.25 + 23.75 * math.sin(a))
           for a in [math.pi - join - (math.pi - 2 * join) * i / 180 for i in range(181)]]
    line([(3, 0), *arc, (47, 0)])
    for row in rows:
        ax, ay, aw = ANCHORS[row['code']]
        cx, cy = point(ax, ay); card_w = aw * scale
        c.setFillColor(colors.HexColor('#ffffff')); c.setStrokeColor(colors.HexColor('#d7e0db'))
        c.roundRect(cx-card_w/2, cy-21, card_w, 42, 4, fill=1, stroke=0)
        c.setFillColor(colors.HexColor(MUTED));c.setFont(font, 6.5)
        c.drawCentredString(cx, cy+11, f"{row['number']:02d}")
        c.setFillColor(colors.HexColor(INK));c.setFont(font, 9)
        c.drawCentredString(cx, cy-1, f"{row['made']}/{row['attempts']}")
        c.setFont(font, 7)
        rate = '-' if row['success_rate'] is None else f"{row['success_rate']:g}%"
        c.drawCentredString(cx, cy-13, rate)
    return top - height


def build_practice_pdf(analysis, context, report, settings):
    """Return PDF bytes. Only local statistics and the saved report are used."""
    font = _font(settings)
    rows = zone_rows(context)
    overall = next(r['current'] for r in context['rows'] if r['key'] == 'overall')
    output = BytesIO()
    c = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    c.setTitle('ShotLog 연습 리포트 - ' + analysis.title)
    c.setAuthor('ShotLog')
    width, height = A4
    margin, usable = 38, width - 76
    def header(subtitle):
        c.setFillColor(colors.HexColor(INK));c.rect(0,height-56,width,56,fill=1,stroke=0)
        c.setFillColor(colors.white);c.setFont(font,19);c.drawString(margin,height-35,'ShotLog')
        c.setFont(font,9);c.drawRightString(width-margin,height-33,subtitle)
        title = analysis.title if len(analysis.title) <= 150 else analysis.title[:147] + '...'
        y = _paragraph(c, title, margin,height-76,usable,font,14)
        return _paragraph(c, '촬영일 ' + context['current']['date'],margin,y-6,usable,font,8,MUTED)-16
    y = header('연습 리포트 / SHOOTING REPORT')
    stat_w = usable/4
    for i,(label,value) in enumerate((('전체 슛',f"{overall['attempts']}개"),('성공',f"{overall['made']}개"),
                                    ('실패',f"{overall['missed']}개"),('성공률',f"{overall['success_rate']:g}%" if overall['success_rate'] is not None else '-'))):
        sx=margin+i*stat_w
        c.setFillColor(colors.HexColor('#eff4f0'));c.roundRect(sx,y-52,stat_w-6,52,6,fill=1,stroke=0)
        _paragraph(c,label,sx+10,y-7,stat_w-20,font,8,MUTED)
        _paragraph(c,value,sx+10,y-22,stat_w-20,font,15)
    y-=75
    y=_paragraph(c,'14개 구역 슈팅 히트맵',margin,y,usable,font,13)-12
    court_width=min(usable-50, (y-198)*50/47)
    bottom=_court(c,rows,(width-court_width)/2,y,court_width,font)
    legend_y=bottom-18
    for i,(label,color) in enumerate((('목표 이상',HOT),('목표 미만',COLD),('시도 5개 미만',EMPTY))):
        lx=margin+35+i*145
        c.setFillColor(colors.HexColor(color));c.rect(lx,legend_y-3,9,9,fill=1,stroke=0)
        _paragraph(c,label,lx+15,legend_y+6,110,font,8,MUTED)
    y=legend_y-24
    y=_paragraph(c,'카드: 구역 번호 · 성공/시도 · 성공률.',margin,y,usable,font,8,MUTED)-12
    priorities=practice_priorities(rows)
    y=_paragraph(c,'다음 연습에서 먼저 확인할 구역',margin,y,usable,font,12)-6
    if priorities:
        for i,row in enumerate(priorities,1):
            y=_paragraph(c,f"{i}. {row['label']}  {row['made']}/{row['attempts']} · {row['success_rate']:g}% · 목표 대비 {row['gap_pp']:+g}%p",margin,y,usable,font,9)-4
    else:
        _paragraph(c,'시도한 구역은 모두 개인 목표 이상입니다. 다음 연습에서도 같은 구역을 반복해 성적을 비교해 보세요.',margin,y,usable,font,9)
    c.save()
    return output.getvalue()
