from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sensocrypt:sensocrypt@localhost:5432/sensocrypt"

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, v: str) -> str:
        # Managed Postgres providers (e.g. Render) hand out a plain postgresql:// or
        # postgres:// connection string -- SQLAlchemy's async engine needs the asyncpg
        # driver spelled out explicitly.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    # §4.2 step 6 / §0.3: the two checks that reject a repackaged app running on a
    # rooted-but-otherwise-valid device. Both must be set correctly before enrollment
    # can be trusted -- see plan.md §0.3 for why.
    android_package: str = "com.sensocrypt"
    pinned_signing_cert_digests_hex: str = ""  # comma-separated hex SHA-256 digests

    # TTLs from plan.md §4.2 / §4.3
    attestation_challenge_ttl_s: int = 120
    auth_nonce_ttl_s: int = 60
    session_token_ttl_s: int = 300

    # Separate from session_token_ttl_s above (that one's for the short-lived device/
    # telemetry session token) -- this is the human "you're logged in" token from phone
    # OTP auth, which should survive far longer than 5 minutes between app opens.
    user_session_token_ttl_s: int = 30 * 24 * 3600  # 30 days

    # PASETO v4.local symmetric key, 32 raw bytes, hex-encoded. Generate with:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    paseto_local_key_hex: str = ""

    @field_validator("paseto_local_key_hex")
    @classmethod
    def _require_valid_paseto_key(cls, v: str) -> str:
        # A managed host's "generate a random value for this env var" feature (e.g.
        # Render's generateValue: true) isn't guaranteed to produce valid hex -- that
        # failure mode previously surfaced as an unhandled 500 on the first real
        # /auth/verify call in production (bytes.fromhex() raising deep inside token
        # issuance), instead of a clear error at startup where it's actually diagnosable.
        try:
            raw = bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError(
                "PASETO_LOCAL_KEY_HEX is not valid hex -- generate one with "
                "`python -c \"import secrets; print(secrets.token_hex(32))\"` and set it explicitly "
                "(don't rely on a host's auto-generated value for this)."
            ) from exc
        if len(raw) != 32:
            raise ValueError(f"PASETO_LOCAL_KEY_HEX must decode to exactly 32 bytes, got {len(raw)}")
        return v

    @property
    def pinned_signing_cert_digests(self) -> set[bytes]:
        if not self.pinned_signing_cert_digests_hex:
            return set()
        return {bytes.fromhex(d.strip()) for d in self.pinned_signing_cert_digests_hex.split(",") if d.strip()}

    # Full service-account JSON (the file Firebase Console -> Project Settings ->
    # Service Accounts -> Generate new private key downloads), pasted whole into one env
    # var rather than a file path -- Render's environment tab is the natural place for a
    # secret like this, and it avoids needing to bake the file into the Docker image.
    firebase_service_account_json: str = ""

    # Voice-detection feature (voice-detection branch only). Inference itself runs on a
    # separate Google Cloud Run service, not here -- RawNet2 + torch measure ~671MB RSS
    # at runtime, more than this backend's entire 512MB Render free-tier budget. This
    # backend just proxies the uploaded audio to that service and relays its verdict.
    voice_service_url: str = ""
    voice_service_api_key: str = ""


settings = Settings()
