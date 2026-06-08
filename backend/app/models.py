import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Enum
from .database import Base

class SkinUpload(Base):
    __tablename__ = "skin_uploads"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    discord_user_id = Column(String, index=True)
    discord_username = Column(String)
    original_filename = Column(String)
    internal_filename = Column(String, unique=True, index=True)
    pack_name = Column(String, index=True, default="default")
    status = Column(String, default="uploaded") # 'uploaded', 'packed', 'error'
    created_at = Column(DateTime, default=datetime.utcnow)
