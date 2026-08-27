from pydantic import BaseModel


class EnrollInitRequest(BaseModel):
    device_model: str
    os_version: str


class EnrollInitResponse(BaseModel):
    enroll_id: str
    att_challenge_b64: str


class EnrollFinishRequest(BaseModel):
    enroll_id: str
    cert_chain_b64: list[str]


class EnrollFinishResponse(BaseModel):
    device_id: str


class ChallengeRequest(BaseModel):
    device_id: str


class ChallengeResponse(BaseModel):
    session_id: str
    nonce_b64: str
    server_ts: int


class VerifyRequest(BaseModel):
    session_id: str
    sig_der_b64: str
    channel_binding_b64: str | None = None


class VerifyResponse(BaseModel):
    token: str
    expires_in: int


class KexRequest(BaseModel):
    session_id: str
    epk_c_b64: str


class KexResponse(BaseModel):
    epk_s_b64: str


# --- Phone auth (v2) -----------------------------------------------------------------


class PhoneSignupRequest(BaseModel):
    firebase_id_token: str
    name: str
    email: str


class PhoneLoginRequest(BaseModel):
    firebase_id_token: str


class PhoneAuthResponse(BaseModel):
    user_id: str
    token: str
    expires_in: int


class FcmTokenRequest(BaseModel):
    fcm_token: str


# --- Calling (v2) ----------------------------------------------------------------------


class PlaceCallRequest(BaseModel):
    callee_phone_number: str


class PlaceCallResponse(BaseModel):
    call_id: str


class CallLogEntry(BaseModel):
    call_id: str
    other_party_name: str
    other_party_phone: str
    direction: str  # "outgoing" | "incoming"
    state: str
    created_at: str


class SessionKeyRequest(BaseModel):
    session_id: str
    side: str  # "caller" | "callee"


class SessionKeyResponse(BaseModel):
    wrapped_key_b64: str | None  # None if not verified yet -- caller should keep polling


class CallStatusResponse(BaseModel):
    state: str


class UserProfileResponse(BaseModel):
    user_id: str
    name: str
    email: str
    phone_number: str
