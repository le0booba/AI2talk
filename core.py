# ./core.py
import logging
import os
import uuid
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
import pytz
from typing import Optional, Dict, Any
from functools import lru_cache

import google.generativeai as genai
import requests
import telebot
from telebot import types
from gradio_client import Client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from components.settings_config import settings, AVAILABLE_MODELS
from components.user_data_manager import get_user_data
from components.rate_limiter import init_rate_limiter_bot
from components.telegram_utils import init_telegram_utils_bot, get_universal_stop_keyboard
from components.localization import get_translation

LOG_DIR_CORE = "."
log_file_path = os.path.join(LOG_DIR_CORE, 'logs/bot_app.log')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("httpx").setLevel(logging.WARNING)   # !!! <<<---

class MoscowTimeFormatter(logging.Formatter):
    tz_moscow = pytz.timezone('Europe/Moscow')

    def formatTime(self, record, datefmt=None):
        dt_utc = datetime.fromtimestamp(record.created, tz=timezone.utc)
        dt_moscow = dt_utc.astimezone(self.tz_moscow)
        return dt_moscow.strftime(datefmt or "%d-%m-%Y %H:%M:%S")


log_message_format = '%(asctime)s MSK - %(levelname)-8s - %(trace_id)s - %(message)s'
file_formatter = MoscowTimeFormatter(
    fmt=log_message_format,
    datefmt='%d-%m-%Y %H:%M:%S'
)


class DefaultTraceIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'trace_id'):
            record.trace_id = 'SYSTEM_EVENT'
        record.trace_id = f"{record.trace_id:<8}"
        return True


default_trace_id_filter = DefaultTraceIdFilter()

if logger.hasHandlers():
    logger.handlers.clear()

os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
file_handler_core = RotatingFileHandler(
    log_file_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
)
file_handler_core.setFormatter(file_formatter)
file_handler_core.addFilter(default_trace_id_filter)
logger.addHandler(file_handler_core)

try:
    import colorlog
    
    class ColoredMoscowTimeFormatter(MoscowTimeFormatter, colorlog.ColoredFormatter):
        def __init__(self, *args, **kwargs):
            colorlog.ColoredFormatter.__init__(self, *args, **kwargs)
            MoscowTimeFormatter.__init__(self, fmt=self._fmt, datefmt=self.datefmt)

    console_formatter = ColoredMoscowTimeFormatter(
        fmt="%(log_color)s" + log_message_format,
        datefmt='%d-%m-%Y %H:%M:%S',
        reset=True,
        log_colors={
            'DEBUG':    'cyan',
            'INFO':     'green',
            'WARNING':  'yellow',
            'ERROR':    'red',
            'CRITICAL': 'red,bg_white',
        },
        secondary_log_colors={},
        style='%'
    )
except ImportError:
    console_formatter = MoscowTimeFormatter(
        fmt=log_message_format,
        datefmt='%d-%m-%Y %H:%M:%S'
    )

console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)
console_handler.addFilter(default_trace_id_filter)
logger.addHandler(console_handler)


class TraceIdAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        final_trace_id = kwargs.get('extra', {}).get('trace_id') or self.extra.get('trace_id', 'ADAPTER_MISSING')
        
        kwargs.setdefault('extra', {})['trace_id'] = final_trace_id
        return msg, kwargs


@lru_cache(maxsize=128)
def get_logger_with_trace_id(trace_id: Optional[str] = None) -> logging.LoggerAdapter:
    actual_trace_id = trace_id or str(uuid.uuid4())[:8]
    return TraceIdAdapter(logger, {'trace_id': actual_trace_id})


ALLOWED_COMMANDS_WHEN_BLOCKED = frozenset([
    '/start', '/donate', '/paysupport', '/user', '/lang'
])

command_handler_map: Dict[str, Any] = {}

bot = telebot.TeleBot(
    settings.TELEGRAM_TOKEN,
    num_threads=getattr(settings, 'BOT_WORKER_THREADS', 4)
)

BOT_START_TIME = datetime.now(timezone.utc)
bot.BOT_START_TIME_REFERENCE = BOT_START_TIME


@lru_cache(maxsize=1)
def create_retry_session_core() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=10,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"])
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


telebot.apihelper.SESSION_TIME_OUT = (60, 120)
telebot.apihelper._get_req_session = create_retry_session_core

hf_client = None
init_logger = get_logger_with_trace_id("INIT")

if settings.HF_API_KEY:
    try:
        hf_client = Client("black-forest-labs/FLUX.1-dev", hf_token=settings.HF_API_KEY)   # !!! <<<---
        init_logger.info("Gradio client for FLUX initialized successfully.")
    except Exception as e:
        init_logger.error(f"Failed to initialize Gradio client for FLUX: {e}", exc_info=True)
else:
    init_logger.warning("HF_API_KEY not set. FLUX functionality will be disabled.")

if settings.GEMINI_API_KEY:
    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        init_logger.info("Gemini API configured successfully.")
    except Exception as e:
        init_logger.error(f"Failed to configure Gemini API: {e}", exc_info=True)
else:
    init_logger.warning("GEMINI_API_KEY not set. Gemini functionality will be disabled.")

init_rate_limiter_bot(bot)
init_telegram_utils_bot(bot)


def get_main_stop_keyboard_core(user_id: int) -> types.InlineKeyboardMarkup:
    user_s_data = get_user_data(user_id)
    return get_universal_stop_keyboard(language=user_s_data.language, get_translation_func=get_translation)


def check_session_expiry_core(user_id: int) -> bool:
    from components.user_data_manager import BotState

    if user_id in settings.TRUSTED_USERS_SET:
        return True

    user_s_data = get_user_data(user_id)
    if not user_s_data.session_start_timestamp:
        return True

    now_utc = datetime.now(timezone.utc)
    session_age_seconds = (now_utc - user_s_data.session_start_timestamp).total_seconds()
    
    if session_age_seconds <= settings.SESSION_LIFETIME_MINUTES * 60:
        return True

    expiry_logger = get_logger_with_trace_id("SESSION_MGR")
    expiry_logger.info(f"Session expired for user {user_id}. Clearing chat history and state.")
    session_expired_message = get_translation(language=user_s_data.language, key='session_expired')
    
    try:
        bot.send_message(user_id, session_expired_message)
    except telebot.apihelper.ApiTelegramException as e_send:
        expiry_logger.error(f"Error sending session expired message to {user_id}: {e_send}")

    user_s_data.reset_chat_histories()
    user_s_data.state = BotState.NONE
    user_s_data.session_start_timestamp = None
    return False


bot.check_session_expiry_reference = check_session_expiry_core