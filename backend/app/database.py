import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

import urllib.parse

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    user = os.getenv("POSTGRES_USER", "postgres")
    pwd = os.getenv("POSTGRES_PASSWORD", "postgres")
    db_name = os.getenv("POSTGRES_DB", "skinpack")
    encoded_pwd = urllib.parse.quote_plus(pwd)
    DATABASE_URL = f"postgresql://{user}:{encoded_pwd}@db:5432/{db_name}"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
