"""One opt-in email and one combined PDF for an explicitly selected set.

Aggregation and practice priorities use saved statistics only; no new AI call.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import Analysis, Shot, ReportEmailBatch, UserEmailPreference
from app.services import email_notifications as email, practice_report as reports
from app.services.analysis_reuse import digest
from app.services.non_shot_policy import visible_shot


def selected_context(session, owner_id, ids, *, lock=False):
    query = select(Analysis).where(Analysis.id.in_(ids), Analysis.owner_id == owner_id).order_by(Analysis.id)
    if lock:
        query = query.with_for_update()
    analyses = session.scalars(query).all()
    if len(analyses) != len(ids):
        raise email.NotificationError(404, 'NOT_FOUND', '선택한 분석 기록을 찾을 수 없습니다.')
    states = email.completion_states(session, analyses)
    if not all(state['ready'] for state in states.values()):
        raise email.NotificationError(409, 'EMAIL_NOT_READY', '선택한 기록의 위치와 결과 확인을 모두 마친 후 보내 주세요.')
    shots = [s for s in session.scalars(select(Shot).where(Shot.analysis_id.in_(ids))) if visible_shot(s)]
    rows = [{'key': key, 'kind': group['kind'], 'label': group['label'], 'current': reports._line(group['shots']),
             'previous': None, 'attempt_change': None, 'rate_change_pp': None}
            for key, group in sorted(reports._groups(shots).items())]
    dates = sorted(reports._metadata(a)['date'] for a in analyses)
    title = analyses[0].title if len(analyses) == 1 else f'선택한 영상 {len(analyses)}개 통합 리포트'
    context = {'period': 'selection', 'analysis_count': len(analyses),
               'current': {'id': digest(sorted(ids)), 'title': title, 'date': dates[0] if dates[0] == dates[-1] else dates[0] + ' ~ ' + dates[-1]},
               'previous': None, 'same_source_metadata': False, 'rows': rows, 'position_unresolved': 0, 'ready': True}
    snapshot = digest({'states': states, 'context': context})
    return context, snapshot


def batch_view(row):
    return {'requested': row.analysis_ids, 'request_id': row.id, 'status': row.status, 'combined': True}


def request_batch(session, settings, owner_id, ids, consent, request_id):
    if not owner_id:
        raise email.NotificationError(409, 'OWNER_REQUIRED', '로그인한 본인 분석에서 신청해 주세요.')
    if not consent or not email.email_reports_allowed(session, owner_id):
        raise email.NotificationError(422, 'CONSENT_REQUIRED', '내 프로필에서 이메일 수신에 동의해 주세요.')
    if not email.configured(settings):
        raise email.NotificationError(503, 'EMAIL_NOT_CONFIGURED', '이메일 발송을 준비하고 있습니다.')
    address = email.recipient_email(session, owner_id)
    if not address:
        raise email.NotificationError(409, 'EMAIL_VERIFICATION_REQUIRED', '내 프로필에서 이메일 인증을 완료해 주세요.')
    ids = sorted(ids)
    selected_context(session, owner_id, ids, lock=True)
    previous = session.get(ReportEmailBatch, request_id)
    if previous:
        if previous.owner_id != owner_id or previous.analysis_ids != ids:
            raise email.NotificationError(409, 'EMAIL_REQUEST_CONFLICT', '새로 화면을 열어 다시 신청해 주세요.')
        return batch_view(previous)
    key = digest({'owner': owner_id, 'ids': ids})
    latest = session.scalar(select(ReportEmailBatch).where(ReportEmailBatch.owner_id == owner_id,
        ReportEmailBatch.selection_key == key).order_by(ReportEmailBatch.created_at.desc(), ReportEmailBatch.id.desc()).limit(1))
    if latest and latest.status in (*email.PENDING, 'unknown'):
        raise email.NotificationError(409, 'EMAIL_BUSY', '같은 기록의 발송을 이미 처리하고 있습니다. 잠시 후 확인해 주세요.')
    if latest and latest.status == 'accepted' and latest.accepted_at and datetime.now(timezone.utc) - email._utc(latest.accepted_at) < timedelta(seconds=60):
        raise email.NotificationError(429, 'EMAIL_COOLDOWN', '방금 발송했습니다. 1분 뒤 다시 보낼 수 있습니다.')
    row = ReportEmailBatch(id=request_id, owner_id=owner_id, analysis_ids=ids, selection_key=key,
                           recipient_email=address, status='waiting')
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise email.NotificationError(409, 'EMAIL_BUSY', '신청을 처리 중입니다. 잠시 후 다시 확인해 주세요.') from None
    return batch_view(row)


def _allowed(session, row):
    if not email.email_reports_allowed(session, row.owner_id):
        raise email.NotificationError(422, 'EMAIL_CONSENT_WITHDRAWN', '이메일 수신 동의가 해제됐습니다.')
    if email.recipient_email(session, row.owner_id) != row.recipient_email:
        raise email.NotificationError(409, 'EMAIL_RECIPIENT_CHANGED', '인증 이메일이 변경됐습니다.')


def process_batch(factory, settings, batch_id):
    if not email.configured(settings):
        return
    now, token = datetime.now(timezone.utc), str(uuid4())
    with factory() as session:
        row = session.get(ReportEmailBatch, batch_id)
        if row is None:
            return
        if row.status == 'sending' and row.lease_until and email._utc(row.lease_until) < now:
            row.status, row.error_code = 'unknown', 'EMAIL_DELIVERY_UNKNOWN'
            session.commit()
            return
        if row.status != 'waiting' and not (row.status == 'preparing' and row.lease_until and email._utc(row.lease_until) < now):
            return
        claim = session.execute(update(ReportEmailBatch).where(ReportEmailBatch.id == batch_id,
            ReportEmailBatch.status == row.status, ReportEmailBatch.claim_token == row.claim_token).values(
                status='preparing', claim_token=token, lease_until=now + timedelta(minutes=10)))
        if not claim.rowcount:
            session.rollback()
            return
        session.commit()
        try:
            row = session.get(ReportEmailBatch, batch_id)
            _allowed(session, row)
            context, snapshot = selected_context(session, row.owner_id, row.analysis_ids)
            # One heatmap + practice-priority PDF; never average per-video percentages.
            payload = email.message_payload(SimpleNamespace(id=batch_id, title=context['current']['title']),
                                            context, None, settings, row.recipient_email)
            owner_id = row.owner_id
            session.rollback()
            # Serialize final dispatch with profile-level opt-out.
            session.scalar(select(UserEmailPreference).where(UserEmailPreference.user_id == owner_id).with_for_update())
            row = session.scalar(select(ReportEmailBatch).where(ReportEmailBatch.id == batch_id).with_for_update())
            if row is None or row.claim_token != token or row.status != 'preparing':
                return
            session.expire_all()
            _allowed(session, row)
            _, current = selected_context(session, row.owner_id, row.analysis_ids, lock=True)
            if current != snapshot:
                raise email.NotificationError(409, 'EMAIL_RECORD_CHANGED', '리포트 준비 중 기록이 변경됐습니다.')
            row.status = 'sending'
            session.commit()  # Durable before SMTP: a crash must not automatically resend.
            session.scalar(select(UserEmailPreference).where(UserEmailPreference.user_id == owner_id).with_for_update())
            row = session.scalar(select(ReportEmailBatch).where(ReportEmailBatch.id == batch_id).with_for_update())
            if row is None or row.claim_token != token or row.status != 'sending':
                return
            session.expire_all()
            _allowed(session, row)
            _, current = selected_context(session, row.owner_id, row.analysis_ids, lock=True)
            if current != snapshot:
                raise email.NotificationError(409, 'EMAIL_RECORD_CHANGED', '리포트 준비 중 기록이 변경됐습니다.')
            # Keep the consent lock through dispatch. A dispatch exception is never retried automatically.
            row.status, row.provider_message_id, row.error_code = email.send_message(payload, settings)
            row.accepted_at = datetime.now(timezone.utc) if row.status == 'accepted' else None
            row.lease_until = None
            session.commit()
        except Exception as error:
            session.rollback()
            row = session.get(ReportEmailBatch, batch_id)
            if row and row.claim_token == token and row.status in ('preparing', 'sending'):
                code = error.code if isinstance(error, email.NotificationError) else 'EMAIL_PDF_FAILED'
                if row.status == 'sending' and not isinstance(error, email.NotificationError):
                    row.status, code = 'unknown', 'EMAIL_DELIVERY_UNKNOWN'
                else:
                    row.status = 'canceled' if code == 'EMAIL_CONSENT_WITHDRAWN' else 'failed'
                row.error_code, row.lease_until = code, None
                session.commit()
