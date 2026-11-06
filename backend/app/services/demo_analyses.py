"""Keep explicitly named presentation/test records outside real practice data."""
from sqlalchemy import or_

from app.models import Analysis


DEMO_TITLE_PREFIXES = ('[발표용 더미]', '[테스트용 더미]')
DEMO_TITLES = ('초록색 더미', '빨간색 더미', '위치 선정 더미', '결과 선정 더미')


def is_demo_analysis(analysis) -> bool:
    title = analysis.title or ''
    return title in DEMO_TITLES or title.startswith(DEMO_TITLE_PREFIXES)


def demo_analysis_clause():
    return or_(Analysis.title.in_(DEMO_TITLES),
               *(Analysis.title.startswith(prefix, autoescape=True) for prefix in DEMO_TITLE_PREFIXES))


def real_analysis_clause():
    return ~demo_analysis_clause()
