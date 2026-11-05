from io import BytesIO

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.models import Analysis, AnalysisNotification, Shot, ShotResult
from app.services import email_notifications as notifications
from app.services import practice_report as reports
from app.services import practice_report_pdf as pdf
from test_practice_report import store, add_practice, fake_text
from test_email_notifications import setup, request_one


def test_pdf_has_only_one_page_with_korean_heatmap_and_focus(store):
    factory,settings=store
    with factory() as session:
        analysis=add_practice(session,results=(ShotResult.MADE,ShotResult.MISSED))
        # Old AI zone name must not redirect the heatmap away from actual XY.
        session.scalar(select(Shot).where(Shot.analysis_id==analysis.id)).zone_code='three_center'
        session.commit()
        context=reports.comparison_context(session,analysis)
        result=pdf.build_practice_pdf(analysis,context,fake_text(context,settings),settings)
    reader=PdfReader(BytesIO(result));text='\n'.join(p.extract_text() for p in reader.pages)
    assert len(reader.pages)==1
    for expected in ('14개 구역 슈팅 히트맵','전체 슛','성공률','50%','정면 미드레인지','1/2','다음 연습에서 먼저 확인할 구역'):
        assert expected in text
    for removed in ('모두 개인 목표 이상이며','적은 시도만으로 약점을 단정하지 않습니다','목표 대비 부족한 성공 수와 시도 수로 정렬했습니다','저장된 슛 기록 기준','자세 원인은 통계만으로 판단하지 않습니다','다음 페이지','구역별 성적표','AI 연습 요약','중점 연습 과제'):
        assert removed not in text
    assert len(reader.pages[0].images)==1
    assert {r['code']:r for r in pdf.zone_rows(context)}['mid_center']['attempts']==2


def test_all_14_targets_and_fewer_than_five_attempts_use_empty_color():
    context={'rows':[{'key':'zone/mid_center','current':{'attempts':1,'made':0,'missed':1,'unresolved':0,'success_rate':0}},
                     {'key':'zone/three_center','current':{'attempts':1,'made':1,'missed':0,'unresolved':0,'success_rate':100}}]}
    rows=pdf.zone_rows(context);by_code={r['code']:r for r in rows}
    assert len(rows)==14
    assert by_code['mid_center']['tone']==pdf.EMPTY
    assert by_code['three_center']['tone']==pdf.EMPTY
    assert by_code['restricted']['tone']==pdf.EMPTY
    assert by_code['restricted']['target']==90
    assert by_code['paint_left']['target']==90
    assert by_code['mid_center']['target']==50
    assert by_code['three_center']['target']==40
    assert [r['code'] for r in pdf.practice_priorities(rows)]==['mid_center']


def test_priorities_account_for_attempt_count_without_minimum_sample_cutoff():
    def row(number,made,missed,target):
        rate=made/(made+missed)*100
        return {'number':number,'made':made,'missed':missed,'attempts':made+missed,'target':target,'success_rate':rate,'gap_pp':rate-target}
    a=row(1,0,1,45);b=row(2,2,8,45);c=row(3,9,1,45)
    assert pdf.practice_priorities([a,b,c])==[b,a]


def test_font_failure_prevents_send_and_leaves_analysis_unchanged(setup,monkeypatch):
    factory,settings,calls=setup;aid=request_one(factory,settings)
    def fail(*args):raise pdf.PDFReportError('test missing font')
    monkeypatch.setattr(notifications,'build_practice_pdf',fail)
    notifications.process_notification(factory,settings,aid)
    with factory() as session:
        assert session.get(AnalysisNotification,aid).error_code=='EMAIL_PDF_FAILED'
        assert session.scalar(select(Shot).where(Shot.analysis_id==aid)).result==ShotResult.MADE
    assert calls==[]


def test_long_title_and_ai_text_do_not_create_an_extra_page(store):
    factory,settings=store
    with factory() as session:
        analysis=add_practice(session);analysis.title=('긴 제목 연습 결과 '*20)[:100]
        context=reports.comparison_context(session,analysis);report=fake_text(context,settings)
        report['content']['summary']='연습 결과를 확인했습니다. '*25
        report['content']['next_practice']=[{'text':'정면 미드레인지에서 연습합니다. '*18}]*2
        data=pdf.build_practice_pdf(analysis,context,report,settings)
    reader=PdfReader(BytesIO(data));text=''.join(p.extract_text() for p in reader.pages)
    assert len(reader.pages)==1
    assert '중점 연습 과제' not in text
    assert '14개 구역 슈팅 히트맵' in text


def test_heatmap_pixels_use_production_classifier():
    from app.services.court_zones import classify_zone
    rows=pdf.zone_rows({'rows':[]})
    tones=tuple((row['code'], '#11aa33' if row['code']=='mid_center' else '#cc4444') for row in rows)
    image=pdf._heatmap_image(tones)
    for x,y in ((25,24),(12,18),(40,7),(2,5),(25,38)):
        expected=(17,170,51) if classify_zone(x,y).value=='mid_center' else (204,68,68)
        assert image.getpixel((int(x/50*1000),int(y/47*940)))==expected


def test_all_targets_met_still_lists_relative_practice_focus():
    context={'rows':[{'key':'zone/mid_center','current':{'attempts':4,'made':2,'missed':2,'unresolved':0,'success_rate':50}},
                     {'key':'zone/mid_baseline_right','current':{'attempts':6,'made':4,'missed':2,'unresolved':0,'success_rate':66.67}}]}
    assert [r['code'] for r in pdf.practice_priorities(pdf.zone_rows(context))]==['mid_center','mid_baseline_right']


@pytest.mark.parametrize('code,target',[('restricted',90),('paint_left',90),('paint_center',90),('paint_right',90),('mid_center',50),('mid_wing_left',50),('three_center',40),('three_corner_right',40)])
def test_heatmap_fifth_attempt_and_exact_target_boundaries(code,target):
    def tone(attempts,rate):
        ctx={'rows':[{'key':'zone/'+code,'current':{'attempts':attempts,'made':0,'missed':attempts,'unresolved':0,'success_rate':rate}}]}
        return next(r['tone'] for r in pdf.zone_rows(ctx) if r['code']==code)
    assert tone(0,None)==pdf.EMPTY
    assert tone(4,100)==pdf.EMPTY
    assert tone(4,0)==pdf.EMPTY
    assert tone(5,target)==pdf.HOT
    assert tone(5,target-.01)==pdf.COLD
    assert tone(6,target)==pdf.HOT
    assert tone(6,target-.01)==pdf.COLD
    assert tone(6,None)==pdf.EMPTY
