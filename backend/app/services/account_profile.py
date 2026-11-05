"""Read only the signed-in user's account; never return password material."""
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import UserProfile, UserEmailPreference, AnalysisNotification
from app.services.nickname_policy import nickname_from_metadata, normalize_nickname, nickname_key


def profile_error(code: str, message: str, status: int = 409):
    return HTTPException(status, detail={'code': code, 'message': message})


def nickname_available(session: Session, value: str) -> bool:
    nickname = normalize_nickname(value)
    if not nickname:
        raise profile_error('NICKNAME_INVALID', '닉네임을 2~20자로 입력해 주세요.', 422)
    return session.scalar(select(UserProfile.user_id).where(UserProfile.nickname_key == nickname_key(nickname))) is None


def check_email_available(session: Session, value: str, excluding_id: str | None = None) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise profile_error('EMAIL_INVALID', '이메일 주소를 확인해 주세요.', 422)
    # Advisory check also covers another account's pending email. Supabase Auth
    # performs the final uniqueness/verification check at update and confirmation.
    exists = session.execute(text('''SELECT 1 FROM auth.users
        WHERE (lower(email) = :email OR lower(email_change) = :email)
          AND (cast(:excluding_id AS text) IS NULL OR cast(id AS text) <> cast(:excluding_id AS text)) LIMIT 1'''),
        {'email': email, 'excluding_id': excluding_id}).first()
    if exists:
        raise profile_error('EMAIL_TAKEN', '이미 사용 중인 이메일입니다. 다른 이메일을 입력해 주세요.')
    return email


def claim_nickname(session: Session, user_id: str, value: str) -> dict:
    nickname = normalize_nickname(value)
    if not nickname:
        raise profile_error('NICKNAME_INVALID', '닉네임을 2~20자로 입력해 주세요.', 422)
    profile = read_account_profile(session, user_id)
    existing = session.get(UserProfile, user_id)
    if existing:
        if existing.nickname_key == nickname_key(nickname):
            return profile  # Safe retry after a lost response.
        raise profile_error('NICKNAME_EXISTS', '닉네임이 이미 등록됐습니다.')
    session.add(UserProfile(user_id=user_id, nickname=nickname, nickname_key=nickname_key(nickname)))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.get(UserProfile, user_id)
        if existing and existing.nickname_key == nickname_key(nickname):
            return read_account_profile(session, user_id)
        raise profile_error('NICKNAME_TAKEN', '이미 사용 중인 닉네임입니다. 다른 닉네임을 입력해 주세요.') from None
    return read_account_profile(session, user_id)


def read_account_profile(session: Session, user_id: str) -> dict:
    # Supabase owns the auth schema. This is an owner-scoped read, not a schema
    # change or an administrative password update. Select only a boolean for it.
    row = session.execute(text('''
        SELECT id, email, email_change, created_at, raw_user_meta_data,
               email_confirmed_at IS NOT NULL AS email_confirmed,
               coalesce(encrypted_password, '') <> '' AS password_registered
        FROM auth.users WHERE id = :user_id
    '''), {'user_id': user_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, detail={'code': 'PROFILE_NOT_FOUND', 'message': '내 계정 정보를 찾을 수 없습니다.'})
    providers = session.execute(text('SELECT provider FROM auth.identities WHERE user_id = :user_id'),
                                {'user_id': user_id}).scalars().all()
    providers = sorted(set(providers))
    registered = bool(row['password_registered'])
    saved = session.get(UserProfile, user_id)
    email_preference = session.get(UserEmailPreference, user_id)
    suggestion = nickname_from_metadata(row['raw_user_meta_data']) if not saved else ''
    conflict = bool(suggestion and not nickname_available(session, suggestion))
    return {
        'id': str(row['id']),
        'nickname': saved.nickname if saved else '',
        'nickname_suggestion': suggestion,
        'nickname_conflict': conflict,
        'email': row['email'],
        'pending_email': row['email_change'] or None,
        'can_register_email': not bool(row['email']),
        'email_confirmed': bool(row['email_confirmed']),
        'email_reports_consent': bool(email_preference and email_preference.enabled),
        'providers': providers,
        'created_at': row['created_at'],
        'password_registered': registered,
        'can_register_password': bool(row['email'] and row['email_confirmed'] and not registered
            and set(providers).intersection({'google', 'kakao', 'custom:naver'})),
    }


def verified_account_email(session: Session, user_id: str) -> str | None:
    """Read the current confirmed address from Auth, never a browser-supplied recipient."""
    email = session.execute(text('''SELECT email FROM auth.users
        WHERE id = :user_id AND email_confirmed_at IS NOT NULL'''),
        {'user_id': user_id}).scalar_one_or_none()
    return email.strip().lower() if isinstance(email, str) else None


def email_reports_allowed(session: Session, user_id: str | None) -> bool:
    preference = session.get(UserEmailPreference, user_id) if user_id else None
    return bool(preference and preference.enabled)


def save_email_preference(session: Session, user_id: str, enabled: bool) -> dict:
    profile = read_account_profile(session, user_id)
    if enabled and not (profile['email'] and profile['email_confirmed']):
        raise profile_error('EMAIL_VERIFICATION_REQUIRED', '이메일을 등록하고 인증한 후 동의해 주세요.')
    preference = session.scalar(select(UserEmailPreference).where(
        UserEmailPreference.user_id == user_id).with_for_update())
    if preference is None:
        preference = UserEmailPreference(user_id=user_id)
        session.add(preference)
    preference.enabled, preference.updated_at = enabled, datetime.now(timezone.utc)
    if not enabled:
        session.execute(update(AnalysisNotification).where(
            AnalysisNotification.owner_id == user_id,
            AnalysisNotification.status.in_(('waiting', 'preparing'))).values(
                status='canceled', error_code='EMAIL_CONSENT_WITHDRAWN', claim_token=None, lease_until=None))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise profile_error('EMAIL_PREFERENCE_BUSY', '설정을 저장 중입니다. 다시 확인해 주세요.') from None
    return read_account_profile(session, user_id)
