# components/rate_limiter.py
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Union, List, Optional, Any

from .settings_config import settings
from .user_data_manager import get_user_data, UserData, BotState, block_user_db, unblock_user_db
from .localization import get_translation

if TYPE_CHECKING:
    import telebot
    from telebot.types import Message, CallbackQuery

total_daily_requests_rl: List[datetime] = []
_bot_instance: Optional['telebot.TeleBot'] = None
_markdown_regex = re.compile(r'[*_`~]|\[([^\]]+)\]\([^)]+\)|#+ ')

def init_rate_limiter_bot(bot_instance: 'telebot.TeleBot'):
    global _bot_instance
    _bot_instance = bot_instance

def _send_message_rl(user_id: int, text: str, **kwargs):
    if _bot_instance:
        try:
            _bot_instance.send_message(user_id, text, **kwargs)
        except Exception as e:
            logging.error(f"RateLimiter: Failed to send message to {user_id}: {e}")
    else:
        logging.error("RateLimiter: Bot instance not initialized. Cannot send message.")

def _clean_markdown_rl(text: Any) -> str:
    text_str = str(text)
    text_str = _markdown_regex.sub(lambda m: m.group(1) if m.group(1) else '', text_str)
    return text_str

def check_rate_limits(user_id: int, command_type: str = "general", increment_request_count: bool = True) -> bool:
    user_s_data: UserData = get_user_data(user_id)
    if user_id in settings.TRUSTED_USERS_SET:
        return True

    now = datetime.now(timezone.utc)

    if user_s_data.blocked_until_timestamp and now < user_s_data.blocked_until_timestamp:
        return False
    elif user_s_data.blocked_until_timestamp and now >= user_s_data.blocked_until_timestamp:
        logging.info(f"RateLimiter: User {user_id} block expired. Unblocking.")
        user_s_data.unblock()

    if command_type == "rate":
        if user_s_data.last_rate_request_timestamp:
            time_since_last = (now - user_s_data.last_rate_request_timestamp).total_seconds()
            if time_since_last < 60:
                rate_limit_message = get_translation(language=user_s_data.language, key='rate_limit_minute')
                _send_message_rl(user_id, rate_limit_message)
                return False
        user_s_data.last_rate_request_timestamp = now
        logging.debug(f"RateLimiter: /rate command check passed for user {user_id}.")
        return True

    day_ago = now - timedelta(hours=24)
    user_s_data.requests_timestamps = [req for req in user_s_data.requests_timestamps if req > day_ago]

    global total_daily_requests_rl
    total_daily_requests_rl = [req for req in total_daily_requests_rl if req > day_ago]

    def handle_limit_violation(reason_key, limit=None):
        user_s_data.violations += 1
        logging.warning(f"RateLimiter: User {user_id} violation count increased to {user_s_data.violations}/{settings.LIMIT_VIOLATIONS_BEFORE_BLOCK} due to {reason_key}")
        limit_msg_args = {'limit': limit} if limit is not None else {}
        limit_message = get_translation(language=user_s_data.language, key=reason_key, _user_violations=user_s_data.violations, **limit_msg_args)
        _send_message_rl(user_id, limit_message)
        
        if user_s_data.violations >= settings.LIMIT_VIOLATIONS_BEFORE_BLOCK:
            block_until = now + timedelta(hours=settings.USER_BLOCK_DURATION_HOURS)
            user_s_data.blocked_until_timestamp = block_until
            block_user_db(user_id, block_until, user_s_data.violations)
            user_blocked_message = get_translation(language=user_s_data.language, key='user_blocked', hours=settings.USER_BLOCK_DURATION_HOURS)
            
            try:
                _send_message_rl(user_id, user_blocked_message, parse_mode="Markdown")
            except Exception:
                _send_message_rl(user_id, _clean_markdown_rl(user_blocked_message))
            
            user_s_data.state = BotState.NONE
            user_s_data.reset_chat_histories()
            logging.warning(f"RateLimiter: User {user_id} blocked until {block_until.isoformat()}")
            return True
        return False

    if len(total_daily_requests_rl) >= settings.MAX_REQUESTS_PER_DAY:
        if handle_limit_violation('rate_limit_total_daily', limit=settings.MAX_REQUESTS_PER_DAY):
            return False
        return False

    if len(user_s_data.requests_timestamps) >= settings.MAX_REQUESTS_PER_USER_PER_DAY:
        if handle_limit_violation('rate_limit_daily', limit=settings.MAX_REQUESTS_PER_USER_PER_DAY):
            return False
        return False

    minute_ago = now - timedelta(minutes=1)
    requests_last_minute = sum(1 for req in user_s_data.requests_timestamps if req > minute_ago)
    if requests_last_minute >= settings.MAX_REQUESTS_PER_MINUTE:
        if handle_limit_violation('rate_limit_minute'):
            return False
        return False

    if user_s_data.last_request_timestamp:
        time_since_last = (now - user_s_data.last_request_timestamp).total_seconds()
        if time_since_last < settings.REQUEST_COOLDOWN_SECONDS:
            rate_limit_cooldown_message = get_translation(language=user_s_data.language, key='rate_limit_cooldown', seconds=settings.REQUEST_COOLDOWN_SECONDS)
            _send_message_rl(user_id, rate_limit_cooldown_message)
            return False

    if increment_request_count:
        logging.debug(f"RateLimiter: Rate limit check passed AND INCREMENTING for User ID: {user_id}. Request type: {command_type}.")
        user_s_data.requests_timestamps.append(now)
        total_daily_requests_rl.append(now)
        user_s_data.last_request_timestamp = now
    else:
        logging.debug(f"RateLimiter: Rate limit check passed (NO INCREMENT) for User ID: {user_id}. Request type: {command_type}.")
    return True

def is_user_blocked(user_id: int) -> bool:
    user_s_data: UserData = get_user_data(user_id)
    now = datetime.now(timezone.utc)
    
    if user_s_data.blocked_until_timestamp and now < user_s_data.blocked_until_timestamp:
        hours_remaining = round((user_s_data.blocked_until_timestamp - now).total_seconds() / 3600)
        user_blocked_message = get_translation(language=user_s_data.language, key='user_blocked', hours=hours_remaining)
        try:
            _send_message_rl(user_id, user_blocked_message, parse_mode="Markdown")
        except Exception:
            _send_message_rl(user_id, _clean_markdown_rl(user_blocked_message))
        return True
    elif user_s_data.blocked_until_timestamp and now >= user_s_data.blocked_until_timestamp:
        logging.info(f"RateLimiter: User {user_id} block expired during 'is_user_blocked' check. Unblocking.")
        user_s_data.unblock()
    return False