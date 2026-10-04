"""Core data models"""
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import SecretStr


class TokenStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RATE_LIMITED = "rate_limited"
    EXHAUSTED = "exhausted"
    INVALID = "invalid"
    QUARANTINED = "quarantined"
    PENDING_VALIDATION = "pending_validation"
    REFRESHING = "refreshing"


class TokenOrigin(Enum):
    STATIC = "static"
    ENV = "env"
    FILE = "file"
    VAULT = "vault"
    GENERATED = "generated"
    SCRAPED = "scraped"
    MARKETPLACE = "marketplace"
    OAUTH = "oauth"
    IMPORTED = "imported"


class EmailProvider(Enum):
    TEMP_MAIL = "temp_mail"
    MAIL_TM = "mail_tm"
    GUERRILLA_MAIL = "guerrilla_mail"
    MAIL_GW = "mail_gw"
    ONESEC_MAIL = "onesec_mail"
    CUSTOM_API = "custom_api"
    SELF_HOSTED = "self_hosted"
    OUTLOOK = "outlook"
    GMAIL = "gmail"
    YAHOO = "yahoo"
    PROTON = "proton"


class CaptchaProvider(Enum):
    TWO_CAPTCHA = "2captcha"
    ANTI_CAPTCHA = "anti_captcha"
    CAPSOLVER = "capsolver"
    CAPMONSTER = "capmonster"
    LOCAL_AI = "local_ai"
    MANUAL = "manual"
    NONE = "none"


class PhoneProvider(Enum):
    SMS_ACTIVATE = "sms_activate"
    FIVE_SIM = "5sim"
    ONLINESIM = "onlinesim"
    SMAN = "sman"
    CUSTOM_API = "custom_api"
    NONE = "none"


class RegistrationStage(Enum):
    INIT = "init"
    EMAIL_CREATED = "email_created"
    EMAIL_VERIFIED = "email_verified"
    REGISTRATION_STARTED = "registration_started"
    CAPTCHA_SOLVED = "captcha_solved"
    PHONE_VERIFIED = "phone_verified"
    ACCOUNT_CREATED = "account_created"
    TOKEN_EXTRACTED = "token_extracted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Token:
    value: SecretStr
    provider: str
    origin: TokenOrigin = TokenOrigin.STATIC
    status: TokenStatus = TokenStatus.PENDING_VALIDATION
    rpm_used: int = 0
    tpm_used: int = 0
    rpd_used: int = 0
    rpm_reset: float = field(default_factory=lambda: time.time() + 60)
    tpm_reset: float = field(default_factory=lambda: time.time() + 60)
    rpd_reset: float = field(default_factory=lambda: time.time() + 86400)
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_used: float = 0
    last_health_check: float = 0
    last_validation: float = 0
    validation_attempts: int = 0
    total_requests: int = 0
    total_tokens: int = 0
    total_errors: int = 0
    total_latency: float = 0.0
    quarantine_until: float = 0
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    @property
    def masked(self) -> str:
        v = self.value.get_secret_value()
        return f"{v[:8]}...{v[-4:]}" if len(v) > 12 else "***"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.value.get_secret_value().encode()).hexdigest()[:16]

    @property
    def is_available(self) -> bool:
        now = time.time()
        if self.status == TokenStatus.QUARANTINED and now < self.quarantine_until:
            return False
        if self.status in (TokenStatus.INVALID, TokenStatus.EXHAUSTED):
            return False
        if self.expires_at and now > self.expires_at:
            return False
        if now >= self.rpm_reset:
            self.rpm_used = 0
            self.rpm_reset = now + 60
        if now >= self.tpm_reset:
            self.tpm_used = 0
            self.tpm_reset = now + 60
        if now >= self.rpd_reset:
            self.rpd_used = 0
            self.rpd_reset = now + 86400
        return self.status in (TokenStatus.HEALTHY, TokenStatus.DEGRADED, TokenStatus.RATE_LIMITED)

    @property
    def avg_latency(self) -> float:
        return self.total_latency / max(self.total_requests, 1)

    def to_dict(self) -> Dict:
        d = self.__dict__.copy()
        d["value"] = self.value.get_secret_value()
        d["status"] = self.status.value
        d["origin"] = self.origin.value
        return d

    @classmethod
    def from_dict(cls, data: Dict) -> "Token":
        data = data.copy()
        data.pop("fingerprint", None)
        data["value"] = SecretStr(data["value"])
        data["status"] = TokenStatus(data["status"])
        data["origin"] = TokenOrigin(data["origin"])
        return cls(**data)


@dataclass
class EmailAccount:
    address: str
    password: str
    provider: EmailProvider
    access_token: str = ""
    expires_at: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at


@dataclass
class PhoneNumber:
    number: str
    country: str
    provider: PhoneProvider
    id: str = ""
    price: float = 0
    expires_at: float = 0


@dataclass
class CaptchaTask:
    id: str
    type: str
    sitekey: str
    page_url: str
    enterprise: bool = False
    invisible: bool = False
    score: float = 0.3
    action: str = ""


@dataclass
class RegistrationResult:
    success: bool
    email: Optional[EmailAccount] = None
    phone: Optional[PhoneNumber] = None
    token: Optional[str] = None
    cookies: Dict[str, str] = field(default_factory=dict)
    local_storage: Dict[str, str] = field(default_factory=dict)
    session_data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    stage: RegistrationStage = RegistrationStage.INIT
    duration: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderRegistrationConfig:
    name: str
    register_url: str
    login_url: str
    email_selector: str = "input[type='email'], input[name='email'], #email"
    password_selector: str = "input[type='password'], input[name='password'], #password"
    submit_selector: str = "button[type='submit'], button:has-text('Sign up'), button:has-text('Register')"
    captcha_selectors: Dict[str, str] = field(default_factory=dict)
    phone_selectors: Dict[str, str] = field(default_factory=dict)
    verification_selectors: Dict[str, str] = field(default_factory=dict)
    token_extractors: List[Dict[str, Any]] = field(default_factory=list)
    pre_registration_actions: List[Dict[str, Any]] = field(default_factory=list)
    post_registration_actions: List[Dict[str, Any]] = field(default_factory=list)
    wait_for_navigation: bool = True
    navigation_timeout: int = 30000
    custom_steps: List = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    required_fields: Dict[str, str] = field(default_factory=dict)
    anti_bot_bypass: bool = True
    stealth_config: Dict[str, Any] = field(default_factory=dict)