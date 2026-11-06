"""Read-only Chrome rendering checks using real saved analysis responses.

Browser-tool transports are unavailable. A separate temporary headless Chrome
profile uses snapshots from the production serializers. Authentication alone
is replaced in this isolated browser; the running server's auth is unchanged.
No writes, OpenAI requests, or email requests can leave this browser.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / 'work/model-recovery/ui-vendor'))
from playwright.sync_api import sync_playwright

from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models import Analysis
from app.api.analyses import list_shots, get_players
from app.schemas.analyses import AnalysisRegistrationResponse
from app.services.practice_report import comparison_context, report_view
from app.services.email_notifications import notification_view

ROOT = Path(os.environ['LOCALAPPDATA']) / 'ShotLog/model-recovery/run-20261106/verification'


def main():
    state = json.loads((ROOT / 'full-analysis-state.json').read_text(encoding='utf-8'))
    settings = get_settings()
    fixtures = {}
    videos = {}
    with create_session_factory(create_database_engine(settings.database_url))() as session:
        session.info.update(auth_user_id=state['owner_id'], auth_required=True)
        for test in state['tests'].values():
            aid = test['analysis_id']
            analysis = session.get(Analysis, aid)
            prefix = '/api/analyses/' + aid
            fixtures[prefix] = AnalysisRegistrationResponse.model_validate(analysis).model_dump(mode='json')
            fixtures[prefix + '/players'] = get_players(aid, session).model_dump(mode='json')
            fixtures[prefix + '/shots'] = list_shots(aid, session).model_dump(mode='json')
            fixtures[prefix + '/practice-report'] = report_view(comparison_context(session, analysis), settings, aid)
            fixtures[prefix + '/email-notification'] = notification_view(session, analysis, settings)
            videos[prefix + '/video'] = settings.upload_path / aid / 'original.mp4'
    fixtures['/api/auth/config'] = {'required': False, 'configured': False, 'url': None,
        'publishableKey': None, 'providers': {'google': False, 'kakao': False}}
    (ROOT / 'ui-readonly-responses.json').write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding='utf-8')
    results = {'mode': 'isolated headless installed Chrome with real response snapshots',
               'authentication_tested': False, 'production_auth_changed': False,
               'smtp_sends': 0, 'mutating_requests_blocked': [], 'cases': []}
    def handler(route):
        request = route.request
        path = urlsplit(request.url).path
        if request.method != 'GET':
            results['mutating_requests_blocked'].append({'method': request.method, 'path': path})
            route.fulfill(status=409, json={'detail': {'message': 'Read-only verification'}})
        elif path in videos:
            content = videos[path].read_bytes()
            matched = re.match(r'bytes=(\d+)-(\d*)', request.headers.get('range', ''))
            if matched:
                start = int(matched[1]); end = min(int(matched[2]) if matched[2] else len(content)-1, len(content)-1)
                route.fulfill(status=206, body=content[start:end+1], content_type='video/mp4',
                    headers={'Content-Range': f'bytes {start}-{end}/{len(content)}', 'Accept-Ranges': 'bytes'})
            else:
                route.fulfill(status=200, body=content, content_type='video/mp4', headers={'Accept-Ranges': 'bytes'})
        elif path in fixtures:
            route.fulfill(status=200, json=fixtures[path])
        else:
            route.fulfill(status=404, json={'detail': 'Not in read-only verification snapshot'})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',
            headless=True, args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        context = browser.new_context(viewport={'width': 1600, 'height': 1000}, reduced_motion='reduce')
        context.route('**/api/**', handler)
        for name, test in state['tests'].items():
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:5173/results/' + test['analysis_id'])
            page.get_by_role('heading', name=test['title'], exact=True).wait_for()
            heatmap = page.get_by_role('img', name='14개 구역 슈팅 히트맵', exact=True)
            heatmap.wait_for()
            host = page.locator('.court-3d-canvas')
            host.locator('canvas').wait_for()
            page.wait_for_timeout(1000)
            case = {'test': name, 'analysis_id': test['analysis_id'], 'heatmap_zones': heatmap.get_by_role('button').count(),
                    'court_zone_count': host.get_attribute('data-zone-count'), 'court_label': host.get_attribute('aria-label'),
                    'summary': page.locator('.review-summary').inner_text(),
                    'report_visible': page.get_by_role('heading', name='연습 리포트', exact=True).is_visible(),
                    'report_delivery_enabled': page.get_by_role('button', name='리포트 보내기', exact=True).is_enabled()}
            assert case['heatmap_zones'] == 14
            assert case['court_zone_count'] == '14'
            assert case['report_visible'] and case['report_delivery_enabled']
            page.screenshot(path=str(ROOT / f'{name}-browser-initial.png'), full_page=True)
            page.get_by_role('button', name='실패 보기', exact=True).click()
            page.get_by_role('heading', name='실패 슛 기록', exact=True).wait_for()
            case['miss_filter_rows'] = page.locator('.review-shot-list > li').count()
            assert case['miss_filter_rows'] == 1
            case['miss_filter_heading'] = True
            page.get_by_role('button', name='전체 후보 보기', exact=True).click()
            page.get_by_role('heading', name='전체 슛 기록', exact=True).wait_for()
            # Use one real nonempty zone and exercise the public 2D keyboard action.
            shot = next(s for s in fixtures['/api/analyses/' + test['analysis_id'] + '/shots']['shots'] if s['zone_label'])
            zone_button = heatmap.get_by_role('button', name=re.compile('^' + re.escape(shot['zone_label']) + ','))
            zone_button.focus(); zone_button.press('Enter')
            page.wait_for_timeout(500)
            case['2d_to_3d_zone'] = host.get_attribute('data-selected-zone')
            assert case['2d_to_3d_zone'] == shot['zone_code']
            page.get_by_role('button', name='전체 후보 보기', exact=True).click()
            canvas = host.locator('canvas')
            canvas.scroll_into_view_if_needed()
            page.wait_for_timeout(700)
            before = canvas.screenshot()
            box = canvas.bounding_box()
            page.mouse.move(box['x']+box['width']*.5, box['y']+box['height']*.7)
            page.mouse.down(); page.mouse.move(box['x']+box['width']*.65, box['y']+box['height']*.55, steps=14); page.mouse.up()
            page.wait_for_timeout(700)
            after = canvas.screenshot()
            case['3d_drag_changed_render'] = before != after
            assert case['3d_drag_changed_render']
            page.get_by_role('button', name='초기 시점', exact=True).click()
            page.get_by_role('button', name='3D 코트 확대', exact=True).click()
            page.get_by_role('button', name='3D 코트 축소', exact=True).click()
            case['3d_controls'] = True
            page.wait_for_timeout(500)
            canvas.screenshot(path=str(ROOT / f'{name}-court-click-reference.png'))
            # Coordinates observed in the captured 1198x620 canvas, not derived
            # from hidden renderer objects. Click actual visible numbered disks.
            positions = [(1, 708, 290), (2, 695, 285)] if name == 'outdoor' else [(1, 649, 281), (3, 545, 284)]
            case['marker_clicks'] = []
            for number, x, y in positions:
                canvas.click(position={'x': x, 'y': y})
                selected = page.locator('.court-3d-selected strong')
                selected.wait_for()
                assert selected.inner_text().startswith(f'{number}번')
                page.wait_for_function("document.querySelector('video') && !document.querySelector('video').paused && document.querySelector('video').readyState >= 3")
                first = page.locator('video').evaluate('(video) => video.currentTime')
                page.wait_for_timeout(700)
                second = page.locator('video').evaluate('(video) => video.currentTime')
                case['marker_clicks'].append({'number': number, 'selection': selected.inner_text(),
                    'video_times': [first, second], 'playing': page.locator('video').evaluate('(video) => !video.paused')})
                assert second > first
            page.screenshot(path=str(ROOT / f'{name}-browser-selected.png'), full_page=True)
            # Opening delivery is a local expansion, not a send action.
            page.get_by_role('button', name='리포트 보내기', exact=True).click()
            page.get_by_role('heading', name='PDF 리포트 이메일 발송', exact=True).wait_for()
            case['delivery_details_visible'] = True
            case['page_errors'] = errors
            assert not errors
            results['cases'].append(case)
            (ROOT / 'browser-verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(case, ensure_ascii=False), flush=True)
            page.close()
        browser.close()


if __name__ == '__main__':
    main()
