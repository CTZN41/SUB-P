"""Owner-scoped calendar-month practice reports; GET never calls AI."""
from datetime import date, timedelta

from openai import OpenAIError
from pydantic import ValidationError
from sqlalchemy import select

from app.models import Analysis, Shot
from app.services import practice_report as reports
from app.services.analysis_reuse import atomic_json, digest
from app.services.monthly_statistics import _month
from app.services.non_shot_policy import visible_shot


def validate_month(month):
    try:
        if len(month) != 7 or month[4] != '-':
            raise ValueError
        return date.fromisoformat(month + '-01')
    except (TypeError, ValueError):
        raise reports.ReportError(422, 'MONTH_INVALID', '월은 YYYY-MM 형식으로 입력해 주세요.') from None


def monthly_context(session, owner_id, month):
    first = validate_month(month)
    previous_month = (first-timedelta(days=1)).strftime('%Y-%m') if first > date.min else None
    pairs = session.execute(select(Analysis, Shot).join(Shot, Shot.analysis_id == Analysis.id).where(Analysis.owner_id == owner_id)).all()
    current = [s for a,s in pairs if _month(a) == month and visible_shot(s)]
    previous = [s for a,s in pairs if _month(a) == previous_month and visible_shot(s)]
    groups, prior = reports._groups(current), reports._groups(previous)
    rows=[]
    for key in sorted(groups.keys() | prior.keys()):
        group=groups.get(key) or prior[key]
        now=reports._line(groups.get(key,{}).get('shots',[]))
        before=reports._line(prior.get(key,{}).get('shots',[])) if previous else None
        rate=now['success_rate'];old_rate=before['success_rate'] if before else None
        rows.append({'key':key,'kind':group['kind'],'label':group['label'],'current':now,'previous':before,
                     'attempt_change':now['attempts']-before['attempts'] if before else None,
                     'rate_change_pp':round(rate-old_rate,2) if rate is not None and old_rate is not None else None})
    return {'period':'monthly','current':{'id':month,'title':month+' 월간 연습','date':month},
            'previous':{'id':previous_month,'title':previous_month+' 월간 연습','date':previous_month} if previous else None,
            'same_source_metadata':False,'rows':rows,
            'position_unresolved':sum(s.court_x is None or s.court_y is None for s in current),'ready':bool(current)}


def cache_path(settings, owner_id, month):
    validate_month(month)
    return settings.work_path/'monthly-reports'/digest({'owner':owner_id})/(month+'.json')


def monthly_report_view(session, settings, owner_id, month):
    return reports.report_view(monthly_context(session,owner_id,month),settings,month,cache_path=cache_path(settings,owner_id,month))


def generate_monthly_report(session, settings, owner_id, month):
    path=cache_path(settings,owner_id,month)
    with reports._generation_lock(path.with_suffix('.lock')):
        view=monthly_report_view(session,settings,owner_id,month)
        if not view['ready']:
            raise reports.ReportError(409,'REPORT_NOT_READY','이 달에 저장된 슛 기록이 있을 때 생성할 수 있습니다.')
        if view['report'] and not view['stale']:
            return view
        if not settings.openai_api_key:
            raise reports.ReportError(503,'REPORT_AI_UNAVAILABLE','OpenAI 연결 설정이 필요합니다.')
        context=monthly_context(session,owner_id,month)
        fingerprint=reports._fingerprint(context,settings.openai_vision_model)
        session.rollback()
        try:
            report=reports._generate_text(context,settings)
        except (OpenAIError,ValidationError):
            raise reports.ReportError(502,'REPORT_AI_FAILED','월간 AI 요약을 생성하지 못했습니다. 다시 시도해 주세요.') from None
        session.expire_all()
        updated=monthly_context(session,owner_id,month)
        if reports._fingerprint(updated,settings.openai_vision_model)!=fingerprint:
            raise reports.ReportError(409,'REPORT_RECORD_CHANGED','생성 중 월별 기록이 변경됐습니다. 다시 생성해 주세요.')
        report['fingerprint']=fingerprint
        atomic_json(path,report)
        return monthly_report_view(session,settings,owner_id,month)
