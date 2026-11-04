"""Stable normalization shared by profile claims and the one-time migration."""
import json
import unicodedata


def normalize_nickname(value) -> str:
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKC', value).strip()
    value = ' '.join(part for part in value.split(' ') if part)
    if not 2 <= len(value) <= 20 or not all(
        char in (' ', '_') or unicodedata.category(char)[0] in ('L', 'M', 'N')
        for char in value
    ):
        return ''
    return value


def nickname_key(value: str) -> str:
    return normalize_nickname(value).casefold()


def nickname_from_metadata(value) -> str:
    if not isinstance(value, dict):
        try:
            value = json.loads(value or '{}')
        except (ValueError, TypeError):
            return ''
    return normalize_nickname(value.get('shotlog_nickname')) if isinstance(value, dict) else ''
