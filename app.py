import os
import re
import time
import logging
from logging.handlers import RotatingFileHandler
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Set, Any, Callable, Union
import json # Может быть нужен для каких-то других целей, если нет - удалить
import functools

import pytz
import requests
import telebot
from telebot import types
from telebot.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message # Убрал неиспользуемые PreCheckoutQuery, SuccessfulPayment если они только в payment_handlers

from core import (
    bot,
    settings,
    hf_client,
    command_handler_map,
    ALLOWED_COMMANDS_WHEN_BLOCKED,
    BOT_START_TIME,
    AVAILABLE_MODELS,
    get_logger_with_trace_id,
    get_main_stop_keyboard_core as _get_main_stop_keyboard,
    check_session_expiry_core
)

from handlers.common_handlers import register_common_handlers
from handlers.owner_handlers import register_owner_handlers
from handlers.payment_handlers import register_payment_handlers
from handlers.mistral_handlers import register_mistral_handlers
from handlers.gemini_handlers import register_gemini_handlers
from handlers.flux_handlers import register_flux_handlers

# periodic_save и save_user_data (старые для JSON) больше не нужны для импорта из user_data_manager
from components.user_data_manager import get_user_data, BotState, get_all_blocked_users_info_db
from components.rate_limiter import is_user_blocked
from components.localization import get_translation
from components.telegram_utils import (
    escape_markdown_v2,
    escape_html_util,
    clean_markdown_text
)
from components.currency_service import (
    format_currency_number,
    RateFetchingError
)


def owner_only(func: Callable):
    @functools.wraps(func)
    def wrapper(message: Message, *args, **kwargs):
        user_id = message.from_user.id
        if settings.BOT_OWNER_USER_ID is None or user_id != settings.BOT_OWNER_USER_ID:
            logging.warning(f"User {user_id} (not owner) tried to access owner command {func.__name__}")
            try:
                user_s_data = get_user_data(user_id)
                bot.reply_to(message, get_translation(language=user_s_data.language, key='permission_denied_owner_only'))
            except telebot.apihelper.ApiTelegramException as e_reply:
                logging.error(f"Failed to send permission denied message to {user_id}: {e_reply}")
            return
        return func(message, *args, **kwargs)
    return wrapper

def block_checked(func: Callable):
    @functools.wraps(func)
    def wrapper(message_or_call: Union[Message, CallbackQuery], *args, **kwargs):
        user_id = message_or_call.from_user.id
        command_text = ""
        if isinstance(message_or_call, Message) and message_or_call.text and message_or_call.text.startswith('/'):
            command_text = message_or_call.text.split(maxsplit=1)[0].lower()
        is_command_allowed = command_text in ALLOWED_COMMANDS_WHEN_BLOCKED
        if not is_command_allowed and is_user_blocked(user_id):
            get_logger_with_trace_id().info(f"User {user_id} tried to use blocked command/action. Command: '{command_text}'. Function: {func.__name__}")
            return
        return func(message_or_call, *args, **kwargs)
    return wrapper

def unhandled_exception_handler(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(message_or_call: Union[Message, CallbackQuery], *args, **kwargs):
        trace_id = str(uuid.uuid4())[:8]
        logger_adapter = get_logger_with_trace_id(trace_id)
        try:
            return func(message_or_call, *args, **kwargs)
        except Exception as e:
            logger_adapter.error(f"Unhandled exception in handler '{func.__name__}': {e}", exc_info=True)
            user_id = message_or_call.from_user.id
            user_s_data = get_user_data(user_id)
            error_message_key = 'unhandled_error_occurred'
            error_message = get_translation(
                language=user_s_data.language,
                key=error_message_key,
                trace_id=trace_id,
                default=f"An unexpected error occurred. Please report this issue with ID: {trace_id}"
            )
            try:
                if isinstance(message_or_call, Message):
                    bot.reply_to(message_or_call, error_message)
                elif isinstance(message_or_call, CallbackQuery):
                    bot.answer_callback_query(message_or_call.id, "An error occurred.", show_alert=True)
                    bot.send_message(message_or_call.from_user.id, error_message)
            except Exception as e_send:
                logger_adapter.error(f"Failed to send error message to user {user_id}: {e_send}")
    return wrapper

def register_all_handlers():
    global command_handler_map
    register_common_handlers(bot, block_checked, command_handler_map, ALLOWED_COMMANDS_WHEN_BLOCKED)
    register_owner_handlers(bot, owner_only, command_handler_map)
    register_payment_handlers(bot, block_checked, command_handler_map, ALLOWED_COMMANDS_WHEN_BLOCKED)
    register_mistral_handlers(bot, block_checked, command_handler_map, ALLOWED_COMMANDS_WHEN_BLOCKED, _get_main_stop_keyboard)
    register_gemini_handlers(bot, block_checked, command_handler_map, ALLOWED_COMMANDS_WHEN_BLOCKED, _get_main_stop_keyboard, check_session_expiry_core)
    register_flux_handlers(bot, block_checked, command_handler_map, ALLOWED_COMMANDS_WHEN_BLOCKED, _get_main_stop_keyboard, hf_client)

    @bot.message_handler(func=lambda message: True, content_types=['text'])
    @unhandled_exception_handler
    def handle_unknown_text_main(message: Message):
        if message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()): return
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        if user_s_data.state == BotState.DONATE_CUSTOM_AMOUNT_INPUT:
            return
        if is_user_blocked(user_id): return
        pass

    @bot.message_handler(func=lambda message: True, content_types=['audio', 'photo', 'voice', 'video', 'document', 'location', 'contact', 'sticker'])
    @unhandled_exception_handler
    def handle_unknown_content_main(message: Message):
        if message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()): return
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        if user_s_data.state == BotState.DONATE_CUSTOM_AMOUNT_INPUT:
            return
        if is_user_blocked(user_id): return
        get_logger_with_trace_id().info(f"Received unhandled content type '{message.content_type}' from user {user_id} in state '{user_s_data.state.name if user_s_data.state else 'None'}'.")

    logging.info("All handlers registered.")


def setup_commands_telegram() -> None:
    commands = []
    command_configs = [
        (settings.ENABLE_GEMINI_FEATURE and settings.GEMINI_API_KEY, [
            BotCommand("gemini", "Gemini chat mode / Чат с Gemini"),
            BotCommand("gemini_menu", "Gemini options / Настройки Gemini"),
            BotCommand("new_gemini_chat", "Start a new Gemini chat / Новый чат Gemini"),
        ]),
        (settings.ENABLE_FLUX_FEATURE and settings.HF_API_KEY and hf_client, [
            BotCommand("flux", "Generate images / Генерация изображений")
        ]),
        (settings.ENABLE_MISTRAL_FEATURE and settings.MISTRAL_API_KEY, [
            BotCommand("mistral", "Mistral chat mode / Чат с Mistral"),
            BotCommand("new_mistral_chat", "Start a new Mistral chat / Новый чат Mistral"),
        ]),
        (settings.ENABLE_RATE_FEATURE, [
            BotCommand("rate", "Сurrency rates / Курсы валют")
        ]),
        (settings.ENABLE_GETID_FEATURE, [
            BotCommand("getid", "Get Telegram ID / Узнать Telegram ID")
        ]),
        (settings.ENABLE_LANG_FEATURE, [
            BotCommand("lang", "Language / Язык")
        ]),
        (settings.ENABLE_USER_INFO_FEATURE, [
            BotCommand("user", "Limits & status / Лимиты и статус")
        ]),
        (settings.ENABLE_PAYMENTS_FEATURE, [
            BotCommand("donate", "Donate Stars ⭐️ / Пожертвовать звёзды ⭐️"),
            BotCommand("paysupport", "Payment support / Поддержка платежей"),
        ]),
    ]
    for condition, cmds in command_configs:
        if condition:
            commands.extend(cmds)
    if not commands:
        commands.append(BotCommand("start", "Start! / Старт!"))
    elif not any(cmd.command == "start" for cmd in commands):
        commands.insert(0, BotCommand("start", "Start! / Старт!"))
    try:
        seen_commands = set()
        unique_commands = []
        for cmd_obj in commands:
            if cmd_obj.command not in seen_commands:
                unique_commands.append(cmd_obj)
                seen_commands.add(cmd_obj.command)
        bot.set_my_commands(unique_commands)
        logging.info(f"Bot commands updated successfully. Count: {len(unique_commands)}")
    except telebot.apihelper.ApiTelegramException as e_api:
        get_logger_with_trace_id("SETUP_COMMANDS").error(f"Failed to set bot commands (API Error): {e_api}")
    except Exception as e_general:
        get_logger_with_trace_id("SETUP_COMMANDS").error(f"Failed to set bot commands (General Error): {e_general}", exc_info=True)


def print_startup_message_to_console():
    try:
        timezone_moscow = pytz.timezone("Europe/Moscow")
        now_moscow = datetime.now(timezone_moscow)
        current_time_str = now_moscow.strftime("%d.%m.%Y %H:%M:%S")
        offset_seconds_obj = now_moscow.utcoffset()
        offset_seconds = offset_seconds_obj.total_seconds() if offset_seconds_obj else 0
        offset_hours = int(offset_seconds // 3600)
        sign = "+" if offset_hours >= 0 else "-"
        formatted_offset = f"GMT{sign}{abs(offset_hours):02d}"
        startup_msg = f"[{current_time_str} MSK ({formatted_offset})] Bot started successfully with Telebot!"
    except Exception as tz_err_general:
        startup_msg = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Bot started successfully with Telebot! (Error getting Moscow time: {tz_err_general})"
    logger_adapter = get_logger_with_trace_id("STARTUP")
    print(startup_msg)
    logger_adapter.info(startup_msg)
    log_items = [
        ("Bot Owner ID set to", settings.BOT_OWNER_USER_ID if settings.BOT_OWNER_USER_ID else 'Not set (owner features disabled)'),
        ("Contact Info set to", settings.BOT_CONTACT_INFO),
        ("Trusted Users Set (Initial from ENV & DB)", settings.TRUSTED_USERS_SET), # Обновлено
        ("Default Gemini Model (for new users)", settings.DEFAULT_GEMINI_MODEL),
        ("Available Gemini Models", AVAILABLE_MODELS),
        ("Default donation suggestion", f"{settings.DEFAULT_DONATION_AMOUNT_STARS}⭐️ (Presets: {settings.DONATION_PRESET_AMOUNTS})"),
        ("Rate Limits (Non-Trusted)", f"UserDaily={settings.MAX_REQUESTS_PER_USER_PER_DAY}, UserMinute={settings.MAX_REQUESTS_PER_MINUTE}, Cooldown={settings.REQUEST_COOLDOWN_SECONDS}s, TotalDaily={settings.MAX_REQUESTS_PER_DAY}"),
        ("Block Policy", f"Violations={settings.LIMIT_VIOLATIONS_BEFORE_BLOCK}, Duration={settings.USER_BLOCK_DURATION_HOURS}h"),
        ("Session Lifetime", f"{settings.SESSION_LIFETIME_MINUTES} minutes"),
    ]
    for label, value in log_items:
        logger_adapter.info(f"{label}: {value}")
    if settings.BOT_OWNER_ID_STR and not settings.BOT_OWNER_USER_ID:
        logger_adapter.warning(f"Invalid BOT_OWNER_ID environment variable: '{settings.BOT_OWNER_ID_STR}'.")
    if hf_client:
        logger_adapter.info("Flux Model (Gradio client) initialized.")
    else:
        logger_adapter.warning("Flux Model (Gradio client) NOT initialized.")
    for api_key, service in [(settings.GEMINI_API_KEY, "Gemini"), (settings.MISTRAL_API_KEY, "Mistral")]:
        if not api_key:
            logger_adapter.warning(f"{service} API key NOT configured. {service} features will fail.")
    blocked_users_info = get_all_blocked_users_info_db()
    if blocked_users_info:
        blocked_users_str = ", ".join([f"{user_id} (until: {info['unblock_time'].strftime('%Y-%m-%d %H:%M:%S %Z') if info.get('unblock_time') else 'Permanent'})" for user_id, info in blocked_users_info.items()])
        console_message = f"Currently blocked users from DB: {blocked_users_str}"
        print(console_message)
        logger_adapter.info(console_message)
    else:
        console_message = "No users are currently blocked in DB."
        print(console_message)
        logger_adapter.info(console_message)

def run_bot():
    logger_adapter = get_logger_with_trace_id("RUN_BOT")
    setup_commands_telegram()
    print_startup_message_to_console()
    logger_adapter.info("Attempting to start bot polling...")
    try:
        while True:
            try:
                # periodic_save() # УДАЛЕНО, т.к. SQLite сохраняет "на лету" или по транзакциям
                logger_adapter.debug("Calling bot.polling()...")
                bot.polling(none_stop=True, interval=1, timeout=75, long_polling_timeout=50)
                logger_adapter.info("bot.polling() exited cleanly (should not happen with none_stop=True).")
                break
            except requests.exceptions.ReadTimeout as e_timeout:
                 logger_adapter.warning(f"Polling ReadTimeout: {e_timeout}. Reconnecting...")
                 time.sleep(1)
            except requests.exceptions.ConnectionError as e_conn:
                 logger_adapter.error(f"Polling ConnectionError: {e_conn}. Reconnecting in {settings.RECONNECT_DELAY} seconds...")
                 time.sleep(settings.RECONNECT_DELAY)
            except telebot.apihelper.ApiTelegramException as e_api_poll:
                 logger_adapter.error(f"Telegram API Error during polling: {e_api_poll}", exc_info=False)
                 if "Conflict: terminated by other getUpdates" in str(e_api_poll):
                     logger_adapter.critical("Bot instance conflict detected. Stopping this instance.")
                     break
                 elif e_api_poll.error_code in (401, 404):
                      logger_adapter.critical(f"Authorization Error ({e_api_poll.error_code}). Check TELEGRAM_TOKEN. Stopping.")
                      break
                 elif e_api_poll.error_code == 429:
                      logger_adapter.warning(f"Polling resulted in 429 Too Many Requests. Waiting {settings.RECONNECT_DELAY*2} seconds...")
                      time.sleep(settings.RECONNECT_DELAY*2)
                 else:
                      logger_adapter.info(f"Retrying polling after API error ({e_api_poll.error_code}) in {settings.RECONNECT_DELAY} seconds...")
                      time.sleep(settings.RECONNECT_DELAY)
            except Exception as e_poll_general:
                logger_adapter.exception(f"Unhandled Polling error: {e_poll_general}")
                logger_adapter.info(f"Restarting bot polling in {settings.RECONNECT_DELAY + 5} seconds...")
                time.sleep(settings.RECONNECT_DELAY + 5)
            else:
                logger_adapter.info("Bot polling loop 'else' block reached. Exiting run_bot.")
                break
    finally:
        get_logger_with_trace_id("SHUTDOWN").info("Bot run_bot function is ending.")
        # save_user_data(force_save=True) # УДАЛЕНО, т.к. SQLite сохраняет "на лету"

def _run_self_tests():
    from components.telegram_utils import escape_markdown_v2, clean_markdown_text, escape_html_util as local_escape_html_util
    from components.currency_service import format_currency_number
    print("Running self-tests for utility functions...")
    test_cases = [
        (format_currency_number(12345.6789), "12 345.68"),
        (escape_markdown_v2("*test*"), "\\*test\\*"),
        (clean_markdown_text("_test_"), "test"),
        (local_escape_html_util("<tag>&"), "<tag>&"), # Используем переименованный импорт
    ]
    for actual, expected in test_cases:
        assert actual == expected
    print("Self-tests passed (basic).")


if __name__ == '__main__':
    startup_logger_main = get_logger_with_trace_id("MAIN_INIT")
    try:
        if not settings.TELEGRAM_TOKEN:
            error_msg = "TELEGRAM_TOKEN is not set. Bot cannot start."
            startup_logger_main.critical(error_msg)
            print(f"CRITICAL: {error_msg}")
            exit(1)
    except Exception as e_settings_main:
        startup_logger_main.critical(f"Failed during initial setup in main: {e_settings_main}.", exc_info=True)
        print(f"CRITICAL: Failed during initial setup in main: {e_settings_main}. Bot cannot start.")
        exit(1)

    _run_self_tests()
    register_all_handlers()
    run_bot()