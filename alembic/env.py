"""
Alembic env.py — DB 마이그레이션 환경 설정
==========================================
.env의 DATABASE_URL을 사용하여 DB 연결.
core/models.py의 Base.metadata를 target으로 autogenerate 지원.
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가 (core.models import 가능)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# .env 로드
load_dotenv()

# Alembic Config
config = context.config

# 로깅 설정
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DATABASE_URL을 .env에서 동적으로 로드
database_url = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password1234@localhost:5432/quantum_db",
)
config.set_main_option("sqlalchemy.url", database_url)

# autogenerate를 위한 모델 메타데이터
from core.database import Base  # noqa: E402
from core.models import *  # noqa: E402, F401, F403 — 모든 모델 import (autogenerate용)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """offline 모드: SQL 스크립트만 생성 (DB 연결 없이)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """online 모드: DB에 직접 연결하여 마이그레이션 실행."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
