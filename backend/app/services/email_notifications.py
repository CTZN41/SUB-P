"""Opt-in delivery after every visible shot has a position and known outcome."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from html import escape
import math
import re
import smtplib
import ssl
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import Analysis, AnalysisJob, AnalysisNotification, AnalysisEmailRequest, AnalysisStatus, Shot, UserEmailPreference
from app.services.analysis_jobs import ACTIVE_JOB_STATUSES
from app.services.analysis_reuse import digest
from app.services.non_shot_policy import visible_shot
from app.services.practice_report import ReportError, comparison_context, generate_report, report_view
from app.services.account_profile import verified_account_email, email_reports_allowed
from app.services.practice_report_pdf import build_practice_pdf, PDFReportError

PENDING = ('waiting', 'preparing', 'sending')
READY = (AnalysisStatus.COMPLETED, AnalysisStatus.REVIEW_REQUIRED)


class NotificationError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def configured(settings):
    return bool(settings.email_notifications_enabled and valid_address(settings.gmail_sender)
                and settings.gmail_app_password and settings.gmail_app_password.get_secret_value().strip())


def valid_address(value):
    return isinstance(value, str) and len(value) <= 254 and bool(re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value))


def recipient_email(session, owner_id):
    email = verified_account_email(session, owner_id) if owner_id else None
    return email if valid_address(email) else None


def mask_email(email):
    local, domain = email.rsplit('@', 1)
    return local[:1] + '***@' + domain


def _completion_state(analysis, shots, active):
    shots = [s for s in shots if visible_shot(s)]
    position_missing = sum(s.court_x is None or s.court_y is None
                           or not math.isfinite(s.court_x) or not math.isfinite(s.court_y)
                           or not (0 <= s.court_x <= 50 and 0 <= s.court_y <= 47) for s in shots)
    outcome_missing = sum(s.result.value not in ('made', 'missed') for s in shots)
    fingerprint = digest(sorted((s.id, s.candidate_time_ms, s.court_x, s.court_y,
                                 s.result.value, s.shot_type.value,
                                 _utc(s.updated_at).isoformat()) for s in shots))
    return {'ready': bool(shots) and analysis.status in READY and not active and not position_missing and not outcome_missing,
            'position_pending': position_missing, 'outcome_pending': outcome_missing, 'fingerprint': fingerprint}


def completion_states(session, analyses):
    """Share delivery eligibility with history without per-record queries."""
    ids = [a.id for a in analyses]
    if not ids:
        return {}
    grouped = {aid: [] for aid in ids}
    for shot in session.scalars(select(Shot).where(Shot.analysis_id.in_(ids))):
        grouped[shot.analysis_id].append(shot)
    active = set(session.scalars(select(AnalysisJob.analysis_id).where(
        AnalysisJob.analysis_id.in_(ids), AnalysisJob.status.in_(ACTIVE_JOB_STATUSES))))
    return {a.id: _completion_state(a, grouped[a.id], a.id in active) for a in analyses}


def completion(session, analysis):
    return completion_states(session, [analysis])[analysis.id]


def notification_view(session, analysis, settings):
    row = session.get(AnalysisNotification, analysis.id)
    if row and (row.owner_id != analysis.owner_id or not row.recipient_email):
        row = None
    account_email = recipient_email(session, analysis.owner_id)
    state = completion(session, analysis)
    return {'configured': configured(settings), 'ready': state['ready'],
            'position_pending': state['position_pending'], 'outcome_pending': state['outcome_pending'],
            'status': row.status if row else None,
            'email_available': bool(account_email),
            'email_reports_consent': email_reports_allowed(session, analysis.owner_id),
            'account_email_masked': mask_email(account_email) if account_email else None,
            'recipient_masked': mask_email(row.recipient_email) if row else None,
            'error_code': row.error_code if row else None,
            'accepted_at': row.accepted_at.isoformat() if row and row.accepted_at else None}


def subscribe(session, analysis, settings, consent, *, explicit_resend=False):
    if not configured(settings):
        raise NotificationError(503, 'EMAIL_NOT_CONFIGURED', '이메일 알림을 준비하고 있습니다.')
    if not consent:
        raise NotificationError(422, 'CONSENT_REQUIRED', '이메일 알림 수신에 동의해 주세요.')
    if not email_reports_allowed(session, analysis.owner_id):
        raise NotificationError(422, 'CONSENT_REQUIRED', '내 프로필에서 이메일 수신에 동의해 주세요.')
    if not analysis.owner_id:
        raise NotificationError(409, 'OWNER_REQUIRED', '로그인한 본인 분석에서 신청해 주세요.')
    email = recipient_email(session, analysis.owner_id)
    if not email:
        raise NotificationError(409, 'EMAIL_VERIFICATION_REQUIRED', '내 프로필에서 이메일을 등록하고 인증을 완료해 주세요.')
    # Lock the analysis too, so first-time subscriptions serialize across workers.
    session.scalar(select(Analysis).where(Analysis.id == analysis.id).with_for_update())
    row = session.get(AnalysisNotification, analysis.id)
    blocked = ('preparing', 'sending', 'unknown') if explicit_resend else ('preparing', 'sending', 'accepted', 'unknown')
    if row and row.recipient_email and row.status in blocked:
        raise NotificationError(409, 'EMAIL_ALREADY_REQUESTED', '이미 발송을 처리한 신청입니다. 현재 상태를 확인해 주세요.')
    if row is None:
        row = AnalysisNotification(analysis_id=analysis.id, owner_id=analysis.owner_id)
        session.add(row)
    row.owner_id = analysis.owner_id
    row.recipient_email, row.status, row.error_code = email, 'waiting', None
    row.provider_message_id, row.accepted_at = None, None
    row.consent_at, row.lease_until, row.claim_token = datetime.now(timezone.utc), None, None
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise NotificationError(409, 'EMAIL_BUSY', '신청을 처리하고 있습니다. 잠시 후 다시 확인해 주세요.') from None
    return notification_view(session, analysis, settings)


def request_report_delivery(session, analysis, settings, consent, request_id):
    """One explicit click may resend; a retried HTTP request may not."""
    if not consent:
        raise NotificationError(422, 'CONSENT_REQUIRED', '이메일 수신에 동의해 주세요.')
    if not email_reports_allowed(session, analysis.owner_id):
        raise NotificationError(422, 'CONSENT_REQUIRED', '내 프로필에서 이메일 수신에 동의해 주세요.')
    session.scalar(select(Analysis).where(Analysis.id == analysis.id).with_for_update())
    previous = session.get(AnalysisEmailRequest, request_id)
    if previous:
        if previous.analysis_id != analysis.id or previous.owner_id != analysis.owner_id:
            raise NotificationError(409, 'EMAIL_REQUEST_CONFLICT', '새로 화면을 열어 다시 신청해 주세요.')
        return notification_view(session, analysis, settings)
    if not completion(session, analysis)['ready']:
        raise NotificationError(409, 'EMAIL_NOT_READY', '위치와 결과 확인을 모두 마친 후 보내 주세요.')
    row = session.get(AnalysisNotification, analysis.id)
    if row and row.status in PENDING:
        raise NotificationError(409, 'EMAIL_BUSY', '이미 발송을 준비하고 있습니다. 잠시 후 확인해 주세요.')
    if row and row.status == 'accepted' and row.accepted_at and datetime.now(timezone.utc) - _utc(row.accepted_at) < timedelta(seconds=60):
        raise NotificationError(429, 'EMAIL_COOLDOWN', '방금 발송했습니다. 1분 뒤 다시 보낼 수 있습니다.')
    session.add(AnalysisEmailRequest(request_id=request_id, analysis_id=analysis.id, owner_id=analysis.owner_id))
    return subscribe(session, analysis, settings, True, explicit_resend=True)


def cancel_subscription(session, analysis, settings):
    row = session.scalar(select(AnalysisNotification).where(
        AnalysisNotification.analysis_id == analysis.id).with_for_update())
    if row and row.recipient_email and row.status in ('preparing', 'sending', 'accepted', 'unknown'):
        raise NotificationError(409, 'EMAIL_ALREADY_REQUESTED', '발송 처리가 시작된 신청입니다. 현재 상태를 확인해 주세요.')
    if row:
        row.status, row.recipient_email, row.error_code = 'canceled', None, None
        session.commit()
    return notification_view(session, analysis, settings)


def public_result_link(settings, analysis_id):
    try:
        url = urlparse(settings.public_app_url or '')
        if (url.scheme == 'https' and url.hostname not in (None, 'localhost', '127.0.0.1', '::1')
                and not (url.username or url.password or url.query or url.fragment) and url.path in ('', '/')):
            return settings.public_app_url.rstrip('/') + '/results/' + analysis_id
    except ValueError:
        pass
    return None


def message_payload(analysis, context, report, settings, email):
    overall = next(row['current'] for row in context['rows'] if row['key'] == 'overall')
    title = ' '.join(analysis.title.split())
    rate = overall['success_rate']
    sections = [title, f"전체 {overall['attempts']}개 · 성공 {overall['made']}개 · 실패 {overall['missed']}개",
                f"성공률 {rate:g}%" if rate is not None else '성공률 확인 필요',
                '2D 히트맵, 구역별 성적과 중점 연습 과제를 첨부한 PDF에서 확인해 주세요.']
    previous = next(row.get('previous') for row in context['rows'] if row['key'] == 'overall')
    if previous and previous['success_rate'] is not None:
        sections.append(f"이전 연습 성공률 {previous['success_rate']:g}% (결과 확정 슛 기준)")
    link = None if context.get('period') == 'selection' else public_result_link(settings, analysis.id)
    if context.get('period') == 'selection':
        sections.insert(1, f"선택한 영상 {context['analysis_count']}개를 합산한 결과입니다.")
    sections.append('결과 및 원본 장면 보기: ' + link if link else '원본 장면은 ShotLog의 분석 기록에서 확인해 주세요.')
    message = EmailMessage()
    message['Subject'] = '[ShotLog] ' + title + ' 분석 결과'
    message['From'] = 'ShotLog <' + settings.gmail_sender + '>'
    message['To'] = email
    message['Message-ID'] = make_msgid(domain='shotlog.local')
    message['Date'] = format_datetime(datetime.now(timezone.utc))
    message.set_content('\n\n'.join(sections))
    html_parts = ''.join('<p>' + escape(part) + '</p>' for part in sections[:-1])
    html_parts += ('<p><a href="' + escape(link, quote=True) + '">결과 및 원본 장면 보기</a></p>' if link else
                   '<p>' + escape(sections[-1]) + '</p>')
    message.add_alternative('<html><body><div style="font-family:Arial,sans-serif;line-height:1.7;max-width:680px;margin:auto">'
                            + html_parts + '</div></body></html>', subtype='html')
    message.add_attachment(build_practice_pdf(analysis, context, report, settings),
                           maintype='application', subtype='pdf', filename='ShotLog-combined-report.pdf' if context.get('period') == 'selection' else 'ShotLog-practice-report.pdf')
    return message


def send_message(message, settings):
    dispatch_started = accepted = False
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20,context=ssl.create_default_context()) as smtp:
            smtp.login(settings.gmail_sender, ''.join(settings.gmail_app_password.get_secret_value().split()))
            dispatch_started = True
            refused = smtp.send_message(message,from_addr=settings.gmail_sender,to_addrs=[message['To']])
            if refused:
                return 'failed',None,'EMAIL_RECIPIENT_REJECTED'
            accepted = True
        return 'accepted',str(message['Message-ID']),None
    except smtplib.SMTPAuthenticationError:
        return 'failed',None,'EMAIL_AUTH_FAILED'
    except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):
        return 'failed',None,'EMAIL_REJECTED'
    except (smtplib.SMTPException,OSError):
        if accepted:
            return 'accepted',str(message['Message-ID']),None
        return ('unknown',None,'EMAIL_DELIVERY_UNKNOWN') if dispatch_started else ('failed',None,'EMAIL_CONNECTION_FAILED')


def process_notification(factory, settings, analysis_id):
    if not configured(settings):
        return
    now = datetime.now(timezone.utc)
    token = str(uuid4())
    with factory() as session:
        row = session.get(AnalysisNotification, analysis_id)
        analysis = session.get(Analysis, analysis_id)
        if row is None or analysis is None or row.owner_id != analysis.owner_id or not row.recipient_email:
            return
        if row.status in ('waiting', 'preparing') and not email_reports_allowed(session, analysis.owner_id):
            row.status, row.error_code, row.claim_token, row.lease_until = 'canceled', 'EMAIL_CONSENT_WITHDRAWN', None, None
            session.commit()
            return
        if row.status == 'sending' and row.lease_until and _utc(row.lease_until) < now:
            row.status, row.error_code = 'unknown', 'EMAIL_DELIVERY_UNKNOWN'
            session.commit()
            return
        eligible = row.status == 'waiting' or (row.status == 'preparing' and row.lease_until
                                               and _utc(row.lease_until) < now)
        if not eligible or not completion(session, analysis)['ready']:
            return
        if recipient_email(session, analysis.owner_id) != row.recipient_email:
            row.status, row.error_code = 'failed', 'EMAIL_RECIPIENT_CHANGED'
            session.commit()
            return
        previous_status, previous_claim = row.status, row.claim_token
        claim = session.execute(update(AnalysisNotification).where(
            AnalysisNotification.analysis_id == analysis_id, AnalysisNotification.status == previous_status,
            AnalysisNotification.claim_token == previous_claim).values(
                status='preparing', claim_token=token, lease_until=now + timedelta(minutes=10), error_code=None))
        if not claim.rowcount:
            session.rollback()
            return
        snapshot = completion(session, analysis)['fingerprint']
        session.commit()
        try:
            view = generate_report(session, analysis, settings)
            session.rollback()
            row = session.scalar(select(AnalysisNotification).where(
                AnalysisNotification.analysis_id == analysis_id).with_for_update())
            analysis = session.scalar(select(Analysis).where(Analysis.id == analysis_id).with_for_update())
            if row is None or analysis is None or row.claim_token != token or row.status != 'preparing':
                return
            current = completion(session, analysis)
            context = comparison_context(session, analysis)
            if (not current['ready'] or current['fingerprint'] != snapshot
                    or row.owner_id != analysis.owner_id or recipient_email(session, analysis.owner_id) != row.recipient_email
                    or report_view(context, settings, analysis_id)['stale']):
                row.status, row.error_code = 'failed', 'EMAIL_RECORD_CHANGED'
                session.commit()
                return
            payload = message_payload(analysis, context, view['report'], settings, row.recipient_email)
            row.status = 'sending'
            session.commit()  # A crash after dispatch must never cause an automatic duplicate.
            # Serialize the final dispatch with account-level consent withdrawal.
            preference = session.scalar(select(UserEmailPreference).where(
                UserEmailPreference.user_id == analysis.owner_id).with_for_update())
            allowed = bool(preference and preference.enabled)
            row = session.scalar(select(AnalysisNotification).where(
                AnalysisNotification.analysis_id == analysis_id).with_for_update())
            analysis = session.scalar(select(Analysis).where(Analysis.id == analysis_id).with_for_update())
            session.expire_all()
            if row and row.claim_token == token and not allowed:
                row.status, row.error_code, row.claim_token, row.lease_until = 'canceled', 'EMAIL_CONSENT_WITHDRAWN', None, None
                session.commit()
                return
            if (row is None or analysis is None or row.claim_token != token or row.status != 'sending'
                    or row.owner_id != analysis.owner_id
                    or recipient_email(session, analysis.owner_id) != row.recipient_email
                    or not completion(session, analysis)['ready']
                    or completion(session, analysis)['fingerprint'] != snapshot
                    or report_view(comparison_context(session, analysis), settings, analysis_id)['stale']):
                if row and row.claim_token == token:
                    row.status, row.error_code = 'failed', 'EMAIL_RECORD_CHANGED'
                    session.commit()
                return
            row.status, row.provider_message_id, row.error_code = send_message(payload, settings)
            row.accepted_at = datetime.now(timezone.utc) if row.status == 'accepted' else None
            row.lease_until = None
            session.commit()
        except PDFReportError:
            session.rollback()
            row = session.get(AnalysisNotification, analysis_id)
            if row and row.claim_token == token and row.status == 'preparing':
                row.status, row.error_code, row.lease_until = 'failed', 'EMAIL_PDF_FAILED', None
                session.commit()
        except (ReportError, NotificationError) as error:
            session.rollback()
            row = session.get(AnalysisNotification, analysis_id)
            if row and row.claim_token == token and row.status == 'preparing':
                if error.code == 'REPORT_BUSY':
                    row.status, row.error_code, row.claim_token = 'waiting', None, None
                else:
                    row.status = 'failed'
                    row.error_code = 'EMAIL_RECORD_CHANGED' if error.code == 'REPORT_RECORD_CHANGED' else error.code
                row.lease_until = None
                session.commit()
        except Exception:
            session.rollback()
            row = session.get(AnalysisNotification, analysis_id)
            if row and row.claim_token == token and row.status in ('preparing', 'sending'):
                row.error_code = 'EMAIL_DELIVERY_UNKNOWN' if row.status == 'sending' else 'EMAIL_PROCESSING_FAILED'
                row.status = 'unknown' if row.status == 'sending' else 'failed'
                row.lease_until = None
                session.commit()


async def notification_worker(factory, settings, stop):
    while not stop.is_set():
        try:
            def process_queue():
                with factory() as session:
                    ids = list(session.scalars(select(AnalysisNotification.analysis_id).where(
                        AnalysisNotification.status.in_(PENDING))))
                for analysis_id in ids:
                    if stop.is_set():
                        break
                    process_notification(factory, settings, analysis_id)
                from app.models import ReportEmailBatch
                from app.services.selected_report_email import process_batch
                with factory() as session:
                    batch_ids = list(session.scalars(select(ReportEmailBatch.id).where(ReportEmailBatch.status.in_(PENDING))))
                for batch_id in batch_ids:
                    if stop.is_set():
                        break
                    process_batch(factory, settings, batch_id)
            await asyncio.to_thread(process_queue)
        except Exception:
            # Preserve durable status, including an uncertain dispatch, for recovery.
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass
