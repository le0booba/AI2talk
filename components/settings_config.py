# components/settings_config.py
import logging
from typing import Dict, List, Optional, Any, Set, Final, Tuple

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import (
    PositiveInt, HttpUrl, Field, NonNegativeInt, field_validator,
    PrivateAttr, ValidationInfo
)

AVAILABLE_MODELS: Final[Tuple[str, ...]] = (
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.0-flash-lite",
)

_DEFAULT_BOT_CONTACT_VALUE: Final[str] = "administrator (contact info not set)"

_INT_FIELDS_TO_CONVERT: Final[frozenset[str]] = frozenset([
    'MAX_REQUESTS_PER_DAY', 'MAX_REQUESTS_PER_USER_PER_DAY', 'MAX_REQUESTS_PER_MINUTE',
    'USER_BLOCK_DURATION_HOURS', 'LIMIT_VIOLATIONS_BEFORE_BLOCK', 'SESSION_LIFETIME_MINUTES',
    'DEFAULT_DONATION_AMOUNT_STARS', 'BOT_WORKER_THREADS'
])

_BOOL_FEATURE_FLAGS: Final[frozenset[str]] = frozenset([
    'ENABLE_GEMINI_FEATURE', 'ENABLE_MISTRAL_FEATURE', 'ENABLE_FLUX_FEATURE',
    'ENABLE_RATE_FEATURE', 'ENABLE_GETID_FEATURE', 'ENABLE_USER_INFO_FEATURE',
    'ENABLE_PAYMENTS_FEATURE', 'ENABLE_LANG_FEATURE', 'ENABLE_OWNER_FEATURES'
])


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    TELEGRAM_TOKEN: str
    GEMINI_API_KEY: Optional[str] = None
    HF_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None

    BOT_OWNER_ID_STR: Optional[str] = Field(default=None, alias="BOT_OWNER_ID")
    BOT_CONTACT_INFO: str = _DEFAULT_BOT_CONTACT_VALUE

    TRUSTED_USERS_RAW: str = Field(default="", alias="TRUSTED_USERS")

    LOG_DIR: str = './logs'
    CBR_URL: HttpUrl = "https://www.cbr-xml-daily.ru/daily_json.js"
    COINGECKO_URL: HttpUrl = "https://api.coingecko.com/api/v3/simple/price"
    COINGECKO_MARKETS_URL: HttpUrl = "https://api.coingecko.com/api/v3/coins/markets"

    DEFAULT_LANGUAGE: str = 'en'
    RECONNECT_DELAY: NonNegativeInt = 10
    DEFAULT_GEMINI_MODEL: str = "gemini-2.5-flash-preview-05-20"

    MAX_CODE_LENGTH: PositiveInt = 2000
    TELEGRAM_MAX_LENGTH: PositiveInt = 4096

    MAX_REQUESTS_PER_DAY: PositiveInt = Field(default=200, validate_default=True)
    MAX_REQUESTS_PER_USER_PER_DAY: PositiveInt = Field(default=20, validate_default=True)
    MAX_REQUESTS_PER_MINUTE: PositiveInt = Field(default=6, validate_default=True)
    REQUEST_COOLDOWN_SECONDS: NonNegativeInt = Field(default=2, validate_default=True)
    USER_BLOCK_DURATION_HOURS: PositiveInt = Field(default=1, validate_default=True)
    LIMIT_VIOLATIONS_BEFORE_BLOCK: PositiveInt = Field(default=5, validate_default=True)
    SESSION_LIFETIME_MINUTES: PositiveInt = Field(default=120, validate_default=True)

    DEFAULT_DONATION_AMOUNT_STARS: PositiveInt = 100
    DONATION_PRESET_AMOUNTS: List[PositiveInt] = [10, 50, 100, 250, 500]
    BOT_WORKER_THREADS: PositiveInt = 4

    ENABLE_GEMINI_FEATURE: bool = True
    ENABLE_MISTRAL_FEATURE: bool = True
    ENABLE_FLUX_FEATURE: bool = True
    ENABLE_RATE_FEATURE: bool = True
    ENABLE_GETID_FEATURE: bool = True
    ENABLE_USER_INFO_FEATURE: bool = True
    ENABLE_PAYMENTS_FEATURE: bool = True
    ENABLE_LANG_FEATURE: bool = True
    ENABLE_OWNER_FEATURES: bool = True

    _bot_owner_user_id_cache: Optional[int] = PrivateAttr(default=None)
    _trusted_users_set_cache: Optional[Set[int]] = PrivateAttr(default=None)

    @field_validator('BOT_CONTACT_INFO', mode='before')
    @classmethod
    def validate_bot_contact_info(cls, v: Optional[str]) -> str:
        if v:
            stripped_v = v.strip()
            if stripped_v:
                return stripped_v
        return _DEFAULT_BOT_CONTACT_VALUE

    @field_validator(*_INT_FIELDS_TO_CONVERT, *_BOOL_FEATURE_FLAGS, mode='before')
    @classmethod
    def ensure_type_from_env(cls, v: Any, info: ValidationInfo) -> Any:
        field_name = info.field_name
        target_type = None
        # Pydantic v2 field info access
        if field_name and field_name in cls.model_fields:
            target_type = cls.model_fields[field_name].annotation
        
        if isinstance(v, str):
            if target_type == bool:
                if v.lower() in ('true', '1', 'yes', 'y', 'on'): return True
                if v.lower() in ('false', '0', 'no', 'n', 'off'): return False
                raise ValueError(f"Invalid boolean value for {field_name}: {v}")
            if target_type in (int, PositiveInt, NonNegativeInt):
                try:
                    return int(v)
                except ValueError:
                    raise ValueError(f"Invalid integer value for {field_name}: {v}")
        return v

    @property
    def BOT_OWNER_USER_ID(self) -> Optional[int]:
        if self._bot_owner_user_id_cache is None and self.BOT_OWNER_ID_STR is not None:
            try:
                self._bot_owner_user_id_cache = int(self.BOT_OWNER_ID_STR)
            except ValueError:
                self._bot_owner_user_id_cache = -1

        if self._bot_owner_user_id_cache == -1:
            return None
        return self._bot_owner_user_id_cache

    @property
    def TRUSTED_USERS_SET(self) -> Set[int]:
        if self._trusted_users_set_cache is None:
            from components.user_data_manager import get_all_trusted_users_db # Предполагается, что эта функция существует
            
            db_trusted_users: Set[int] = set() # Инициализация пустым множеством
            try:
                db_trusted_users = get_all_trusted_users_db()
            except Exception as e: # Обработка возможной ошибки, если БД недоступна при старте
                logging.error(f"Could not load trusted users from DB: {e}. Proceeding without DB trusted users.")


            env_trusted_users: Set[int] = set()
            if self.TRUSTED_USERS_RAW:
                env_trusted_users = {
                    int(stripped_id)
                    for user_id_str in self.TRUSTED_USERS_RAW.split(',')
                    if (stripped_id := user_id_str.strip()).isdigit()
                }
            
            combined_set = db_trusted_users.union(env_trusted_users)
            owner_id = self.BOT_OWNER_USER_ID
            if owner_id is not None:
                combined_set.add(owner_id)
            
            self._trusted_users_set_cache = combined_set
            logging.info(f"TRUSTED_USERS_SET initialized/loaded: {self._trusted_users_set_cache}")
        return self._trusted_users_set_cache

settings = BotSettings()

if settings.ENABLE_GEMINI_FEATURE and settings.DEFAULT_GEMINI_MODEL not in AVAILABLE_MODELS:
    model_to_use = AVAILABLE_MODELS[0] if AVAILABLE_MODELS else "N/A (No Gemini Models Available)"
    logging.warning(
        f"DEFAULT_GEMINI_MODEL '{settings.DEFAULT_GEMINI_MODEL}' not in AVAILABLE_MODELS. "
        f"Using: {model_to_use}"
    )
    if AVAILABLE_MODELS:
        settings.DEFAULT_GEMINI_MODEL = AVAILABLE_MODELS[0]
    else:
        logging.error("No models available in AVAILABLE_MODELS list, but Gemini feature is enabled!")
        # settings.ENABLE_GEMINI_FEATURE = False # Можно принудительно отключить фичу
        # logging.warning("Gemini feature has been disabled due to no available models.")