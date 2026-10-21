"""DB 연결 도우미.

사용법:
    with get_conn() as conn:
        rows = conn.execute("SELECT ...", (값,)).fetchall()   # 결과는 dict 목록
`with` 블록이 끝나면 자동 커밋, 중간에 오류가 나면 자동 롤백된다.
"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from .config import settings


@contextmanager
def get_conn():
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        register_vector(conn)
        yield conn
