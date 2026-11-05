from datetime import date,datetime,timezone

import pytest
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.analyses import router
from app.models import Shot,ShotResult
from app.services import monthly_practice_report as monthly,practice_report as reports
from app.services.authentication import require_analysis_user
from test_practice_report import store,add_practice,fake_text


def test_monthly_combines_videos_uses_calendar_previous_month_and_isolates_owner(store):
    factory,settings=store
    with factory() as session:
        prior=add_practice(session,day=1,results=(ShotResult.MISSED,));prior.recorded_date=date(2026,10,15)
        older=add_practice(session,day=1);older.recorded_date=date(2026,9,10)
        add_practice(session,day=2,results=(ShotResult.MADE,ShotResult.MISSED))
        add_practice(session,day=3)
        add_practice(session,owner='other',day=4,results=(ShotResult.MISSED,)*8)
        session.commit()
        context=monthly.monthly_context(session,'a','2026-11')
        overall=next(r for r in context['rows'] if r['key']=='overall')
        assert overall['current']['attempts']==3 and overall['current']['made']==2
        assert overall['previous']['attempts']==1 and overall['previous']['made']==0
        assert context['previous']['id']=='2026-10'
        assert monthly.monthly_context(session,'a','2026-12')['previous']['id']=='2026-11'
        assert monthly.cache_path(settings,'a','2026-11')!=monthly.cache_path(settings,'other','2026-11')


def test_recording_date_fallback_uses_korea_and_january_rolls_to_december(store):
    factory,_=store
    with factory() as session:
        previous=add_practice(session);previous.recorded_date=date(2025,12,30)
        current=add_practice(session);current.recorded_date=None;current.created_at=datetime(2025,12,31,16,tzinfo=timezone.utc)
        session.commit()
        context=monthly.monthly_context(session,'a','2026-01')
        assert context['previous']['date']=='2025-12'
        assert next(r for r in context['rows'] if r['key']=='overall')['current']['attempts']==1


def test_monthly_read_is_free_cache_reused_and_changes_mark_stale(store,monkeypatch):
    factory,settings=store;calls=[]
    monkeypatch.setattr(reports,'_generate_text',lambda c,s:calls.append(c['period']) or fake_text(c,s))
    with factory() as session:
        analysis=add_practice(session)
        assert monthly.monthly_report_view(session,settings,'a','2026-11')['report'] is None
        assert calls==[]
        assert monthly.generate_monthly_report(session,settings,'a','2026-11')['report']
        monthly.generate_monthly_report(session,settings,'a','2026-11')
        assert calls==['monthly']
        session.scalar(select(Shot).where(Shot.analysis_id==analysis.id)).result=ShotResult.MISSED
        session.commit()
        assert monthly.monthly_report_view(session,settings,'a','2026-11')['stale']
        assert monthly.monthly_report_view(session,settings,'other','2026-11')['report'] is None


def test_monthly_record_change_and_generation_lock_do_not_publish_stale_result(store,monkeypatch):
    factory,settings=store
    with factory() as session:
        analysis=add_practice(session);aid=analysis.id
        path=monthly.cache_path(settings,'a','2026-11')
        with reports._generation_lock(path.with_suffix('.lock')):
            with pytest.raises(reports.ReportError) as busy:monthly.generate_monthly_report(session,settings,'a','2026-11')
            assert busy.value.code=='REPORT_BUSY'
        def change(context,settings):
            with factory() as other:
                other.scalar(select(Shot).where(Shot.analysis_id==aid)).result=ShotResult.MISSED;other.commit()
            return fake_text(context,settings)
        monkeypatch.setattr(reports,'_generate_text',change)
        with pytest.raises(reports.ReportError) as changed:monthly.generate_monthly_report(session,settings,'a','2026-11')
        assert changed.value.code=='REPORT_RECORD_CHANGED'
        assert not path.exists()


def test_monthly_routes_bind_logged_in_owner_and_do_not_call_ai_on_get(store,monkeypatch):
    factory,settings=store
    app=FastAPI();app.state.settings=settings.model_copy(update={'auth_required':True});app.state.session_factory=factory;app.include_router(router)
    async def signed_in(request:Request):request.state.auth_user_id='a'
    app.dependency_overrides[require_analysis_user]=signed_in
    with factory() as session:
        add_practice(session);add_practice(session,owner='other',results=(ShotResult.MISSED,)*3)
    monkeypatch.setattr(reports,'_generate_text',lambda *a:pytest.fail('GET must not call AI'))
    with TestClient(app) as client:
        response=client.get('/api/analyses/monthly/practice-report?month=2026-11')
        assert response.status_code==200
        assert next(r for r in response.json()['rows'] if r['key']=='overall')['current']['attempts']==1
        assert client.get('/api/analyses/monthly/practice-report?month=../../x').status_code==422
        assert client.post('/api/analyses/monthly/practice-report?month=2026-12').status_code==409


def test_unknown_positions_and_outcomes_keep_separate_denominators(store):
    factory,_=store
    with factory() as session:
        a=add_practice(session,results=(ShotResult.MADE,ShotResult.MISSED,ShotResult.NEEDS_REVIEW))
        a.shots[0].court_x=None;a.shots[0].court_y=None;session.commit()
        context=monthly.monthly_context(session,'a','2026-11');rows={r['key']:r for r in context['rows']}
        assert rows['overall']['current']['attempts']==3
        assert rows['overall']['current']['success_rate']==50
        assert rows['zone/mid_center']['current']['attempts']==2
        assert context['position_unresolved']==1
