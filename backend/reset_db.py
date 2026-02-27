from sqlalchemy import text
from core.database import engine, Base
import core.models as models

print("🗑️  Initializing Database...")

with engine.connect() as conn:
    # 1. 기존 스키마 삭제 (데이터 포함)
    print("   - Dropping existing schemas...")
    conn.execute(text("DROP SCHEMA IF EXISTS core CASCADE;"))
    conn.execute(text("DROP SCHEMA IF EXISTS chat CASCADE;"))
    conn.execute(text("DROP SCHEMA IF EXISTS job CASCADE;"))
    conn.execute(text("DROP SCHEMA IF EXISTS domain CASCADE;"))
    
    # 2. 스키마 생성
    print("   - Creating new schemas...")
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS core;"))
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS chat;"))
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS job;"))
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS domain;"))
    
    conn.commit()

print("🔨  Creating tables...")
try:
    # 3. 테이블 생성 (위에서 만든 스키마 안에 쏙쏙 들어감)
    Base.metadata.create_all(bind=engine)
    print("✅ Database & Schemas created successfully!")
except Exception as e:
    print(f"❌ Error creating tables: {e}")