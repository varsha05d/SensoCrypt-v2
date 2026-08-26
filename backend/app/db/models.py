"""SQLAlchemy models.

Device/Session are unchanged from the original SensoCrypt project (hardware-attestation
identity layer). User and Call are new for v2: a User (phone/name/email, OTP-verified via
Firebase) owns one or more attested Devices; a Call is between two Users, gated by the
pre-connect liveness verification flow before its signaling/media is ever unlocked.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # E.164 format (e.g. "+919876543210"), as Firebase phone auth returns it -- the unique
    # identity a call is placed to.
    phone_number: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    fcm_token: Mapped[str | None] = mapped_column(String, nullable=True)  # for incoming-call push
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    public_key_der: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    security_level: Mapped[str] = mapped_column(String, nullable=False)  # 'trusted_environment' | 'strong_box'
    package_name: Mapped[str] = mapped_column(String, nullable=False)
    signing_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    verified_boot: Mapped[bool] = mapped_column(Boolean, nullable=False)
    os_version: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.device_id"), nullable=False)
    call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.call_id"), nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="INIT")  # INIT|AUTHED|KEYED|ACTIVE|CLOSED
    channel_binding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    dtls_fp: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    last_seq: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String, nullable=True)


class Call(Base):
    __tablename__ = "calls"

    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    callee_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    # RINGING -> VERIFYING -> VERIFIED -> CONNECTED -> ENDED, or -> FAILED_VERIFICATION /
    # DECLINED / MISSED at various points. See app/api/calls.py for the transition logic.
    state: Mapped[str] = mapped_column(String, nullable=False, default="RINGING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
