# handlers/flux_handlers.py
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Callable, Optional, Any, Tuple

import telebot
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from components.settings_config import settings
from components.localization import get_translation
from components.user_data_manager import get_user_data, BotState
from components.rate_limiter import check_rate_limits, is_user_blocked
from components.telegram_utils import escape_markdown_v2, clean_markdown_text
from components.flux_service import (
    generate_image_with_flux,
    FluxClientError,
    FluxGenerationError,
    FluxError
)

FLUX_DIMENSIONS = (
    ("768x768", 768, 768),
    ("1024x1024", 1024, 1024),
    ("1024x768", 1024, 768),
    ("768x1024", 768, 1024),
    ("1600x1200", 1600, 1200),
    ("1200x1600", 1200, 1600),
    ("1500x1500", 1500, 1500),
    ("2048x2048", 2048, 2048)
)

FLUX_EXCEPTIONS = (FluxClientError, FluxGenerationError, FluxError)
CAPTION_MAX_LENGTH = 1024
MESSAGE_DELETE_THRESHOLD = 48 * 3600 - 60


def _is_old_message(bot: telebot.TeleBot, message: Message) -> bool:
    return (hasattr(bot, 'BOT_START_TIME_REFERENCE') and 
            message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()))


def _send_error_and_cleanup(bot: telebot.TeleBot, user_id: int, user_data, error_key: str, **kwargs):
    error_msg = get_translation(language=user_data.language, key=error_key, **kwargs)
    try:
        bot.send_message(user_id, error_msg, parse_mode="Markdown")
    except Exception:
        pass
    user_data.state = BotState.NONE
    user_data.clear_flux_data()


def _create_dimensions_keyboard(language: str) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(text=dim[0], callback_data=f"flux_dim:{dim[1]}:{dim[2]}") 
               for dim in FLUX_DIMENSIONS]
    keyboard.add(*buttons)
    
    reenter_text = get_translation(language=language, key='flux_reenter_prompt_button')
    stop_text = get_translation(language=language, key='stop_mode_button')
    keyboard.add(
        InlineKeyboardButton(reenter_text, callback_data="flux_reenter"),
        InlineKeyboardButton(stop_text, callback_data="stop_mode")
    )
    return keyboard


def _create_image_caption(prompt: str, language: str) -> str:
    escaped_prompt = escape_markdown_v2(prompt)
    generate_more_text = get_translation(language=language, key='flux_generate_more')
    caption = f"`{escaped_prompt}`\n\n{generate_more_text}"
    
    if len(caption) > CAPTION_MAX_LENGTH:
        max_prompt_len = CAPTION_MAX_LENGTH - len(generate_more_text) - 10
        caption = f"`{escaped_prompt[:max_prompt_len]}...`\n\n{generate_more_text}"
    
    return caption


def _send_flux_image(bot: telebot.TeleBot, user_id: int, image_path: str, 
                     prompt: str, width: int, height: int, language: str):
    caption = _create_image_caption(prompt, language)
    
    try:
        with open(image_path, 'rb') as f:
            if width > 1024 or height > 1024:
                bot.send_document(user_id, f, caption=caption, parse_mode="Markdown",
                                visible_file_name=f"flux_image_{user_id}.png")
            else:
                bot.send_photo(user_id, f, caption=caption, parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Telegram API Error sending FLUX image (Markdown) to {user_id}: {e}. Retrying with plain text.")
        with open(image_path, 'rb') as f:
            generate_more_text = get_translation(language=language, key='flux_generate_more')
            plain_caption = f"{prompt}\n\n{generate_more_text}"
            if len(plain_caption) > CAPTION_MAX_LENGTH:
                plain_caption = plain_caption[:CAPTION_MAX_LENGTH-3] + "..."
            
            if width > 1024 or height > 1024:
                bot.send_document(user_id, f, caption=plain_caption,
                                visible_file_name=f"flux_image_{user_id}.png")
            else:
                bot.send_photo(user_id, f, caption=plain_caption)


def _handle_command_in_flux_mode(bot: telebot.TeleBot, message: Message, user_data,
                                command_handler_map: Dict, allowed_commands: List[str]):
    current_state = user_data.state.name
    user_data.state = BotState.NONE
    user_data.clear_flux_data()
    
    command = message.text.split(maxsplit=1)[0].lower()
    logging.info(f"User {message.chat.id} sent command '{command}' while in {current_state}. Exiting mode.")
    
    handler_func = command_handler_map.get(command)
    if handler_func:
        try:
            if command not in allowed_commands and is_user_blocked(message.chat.id):
                return
            handler_func(message)
        except Exception as e:
            logging.error(f"Error executing command '{command}' from FLUX_PROMPT: {e}", exc_info=True)
            error_msg = get_translation(language=user_data.language, key='error_executing_command', command=command)
            try:
                bot.send_message(message.chat.id, error_msg)
            except Exception:
                pass
    else:
        logging.warning(f"User {message.chat.id} sent unknown command '{command}' in FLUX_PROMPT state.")
        try:
            exit_msg = get_translation(language=user_data.language, key='mode_exited')
            bot.send_message(message.chat.id, exit_msg)
        except Exception:
            pass


def _cleanup_temp_file(file_path: str):
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError as e:
            logging.error(f"Error removing temporary FLUX image file {file_path}: {e}")


def _safe_delete_message(bot: telebot.TeleBot, user_id: int, message_obj):
    if not message_obj:
        return
    
    try:
        time_diff = (datetime.now(timezone.utc) - 
                    datetime.fromtimestamp(message_obj.date, tz=timezone.utc)).total_seconds()
        if time_diff < MESSAGE_DELETE_THRESHOLD:
            bot.delete_message(user_id, message_obj.message_id)
    except Exception:
        pass


def register_flux_handlers(
    bot: telebot.TeleBot,
    block_checked_decorator: Callable,
    command_handler_map_ref: Dict,
    allowed_commands_when_blocked_list: List[str],
    get_main_stop_keyboard_func: Callable,
    hf_client_ref: Optional[Any]
):
    if not settings.ENABLE_FLUX_FEATURE:
        logging.info("Flux feature is disabled by settings. Skipping Flux handlers registration.")
        return
    
    if not settings.HF_API_KEY or not hf_client_ref:
        logging.warning(f"HF_API_KEY not set or hf_client not initialized. "
                       f"Flux handlers will not be registered. "
                       f"(HF_API_KEY: {bool(settings.HF_API_KEY)}, hf_client: {bool(hf_client_ref)})")
        return

    @bot.message_handler(commands=['flux'])
    @block_checked_decorator
    def start_flux_mode_command_handler(message: Message) -> None:
        if _is_old_message(bot, message):
            return
        
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        
        if not hf_client_ref:
            _send_error_and_cleanup(bot, user_id, user_data, 'flux_service_unavailable_alert')
            return
        
        flux_mode_message = get_translation(language=user_data.language, key='flux_mode')
        try:
            bot.send_message(user_id, flux_mode_message, 
                           reply_markup=get_main_stop_keyboard_func(user_id), 
                           parse_mode="Markdown")
            user_data.state = BotState.FLUX_PROMPT
            user_data.clear_flux_data()
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send flux mode entry message to {user_id}: {e}")

    command_handler_map_ref['/flux'] = start_flux_mode_command_handler

    @bot.message_handler(func=lambda message: get_user_data(message.chat.id).state == BotState.FLUX_PROMPT)
    @block_checked_decorator
    def handle_flux_prompt_message_handler(message: Message) -> None:
        if _is_old_message(bot, message):
            return
        
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        
        if not hf_client_ref:
            _send_error_and_cleanup(bot, user_id, user_data, 'flux_service_unavailable_alert')
            return
        
        if message.text and message.text.startswith('/'):
            _handle_command_in_flux_mode(bot, message, user_data, command_handler_map_ref, 
                                       allowed_commands_when_blocked_list)
            return
        
        if not message.text:
            prompt_needed_msg = get_translation(language=user_data.language, key='flux_prompt_needed')
            try:
                bot.send_message(user_id, prompt_needed_msg, 
                               reply_markup=get_main_stop_keyboard_func(user_id))
            except Exception:
                pass
            return
        
        prompt = message.text.strip()
        if not prompt:
            prompt_needed_msg = get_translation(language=user_data.language, key='flux_prompt_needed')
            try:
                bot.send_message(user_id, prompt_needed_msg, 
                               reply_markup=get_main_stop_keyboard_func(user_id))
            except Exception:
                pass
            return
        
        user_data.flux_data = {"prompt": prompt}
        user_data.state = BotState.FLUX_DIMENSIONS
        
        keyboard = _create_dimensions_keyboard(user_data.language)
        dimensions_prompt_text = get_translation(language=user_data.language, key='flux_dimensions_prompt')
        
        try:
            bot.send_message(user_id, dimensions_prompt_text, reply_markup=keyboard)
        except Exception:
            user_data.state = BotState.NONE
            user_data.clear_flux_data()

    @bot.callback_query_handler(func=lambda call: call.data == "flux_reenter")
    @block_checked_decorator
    def handle_flux_reenter_callback_handler(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_data = get_user_data(user_id)
        
        try:
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, 
                                        message_id=call.message.message_id, 
                                        reply_markup=None)
        except Exception:
            pass
        
        user_data.state = BotState.FLUX_PROMPT
        user_data.clear_flux_data()
        
        flux_mode_message = get_translation(language=user_data.language, key='flux_mode')
        try:
            bot.send_message(user_id, flux_mode_message, 
                           reply_markup=get_main_stop_keyboard_func(user_id), 
                           parse_mode="Markdown")
        except Exception:
            user_data.state = BotState.NONE

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("flux_dim:"))
    @block_checked_decorator
    def handle_flux_dimensions_callback_handler(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_data = get_user_data(user_id)
        message_obj = call.message
        
        if not check_rate_limits(user_id, "flux"):
            rate_limit_msg = get_translation(language=user_data.language, key='rate_limit_alert')
            try:
                bot.answer_callback_query(call.id, rate_limit_msg, show_alert=True)
            except Exception:
                pass
            return
        
        status_message_obj = None
        generated_image_path = None
        original_chat_id = message_obj.chat.id
        original_message_id = message_obj.message_id
        
        try:
            try:
                bot.edit_message_reply_markup(chat_id=original_chat_id, 
                                            message_id=original_message_id, 
                                            reply_markup=None)
            except Exception:
                pass
            
            if not hf_client_ref:
                unavailable_msg = get_translation(language=user_data.language, key='flux_service_unavailable_alert')
                try:
                    bot.answer_callback_query(call.id, unavailable_msg, show_alert=True)
                except Exception:
                    pass
                user_data.state = BotState.NONE
                user_data.clear_flux_data()
                try:
                    failed_msg = get_translation(language=user_data.language, key='flux_generation_failed')
                    bot.edit_message_text(failed_msg, chat_id=original_chat_id, 
                                        message_id=original_message_id, reply_markup=None)
                except Exception:
                    pass
                return
            
            current_flux_data = user_data.flux_data
            if not current_flux_data or "prompt" not in current_flux_data:
                expired_msg = get_translation(language=user_data.language, key='flux_session_expired_alert')
                try:
                    bot.answer_callback_query(call.id, expired_msg, show_alert=True)
                except Exception:
                    pass
                user_data.state = BotState.NONE
                user_data.clear_flux_data()
                try:
                    failed_msg = get_translation(language=user_data.language, key='flux_generation_failed') + " (Session Data Lost)"
                    bot.edit_message_text(failed_msg, chat_id=original_chat_id, 
                                        message_id=original_message_id, reply_markup=None)
                except Exception:
                    pass
                return
            
            prompt = current_flux_data["prompt"]
            
            try:
                _, width_str, height_str = call.data.split(":")
                width, height = int(width_str), int(height_str)
                if width <= 0 or height <= 0:
                    raise ValueError("Dimensions must be positive")
            except Exception:
                try:
                    bot.answer_callback_query(call.id, "Invalid dimension selection.", show_alert=True)
                except Exception:
                    pass
                user_data.state = BotState.NONE
                user_data.clear_flux_data()
                try:
                    failed_msg = get_translation(language=user_data.language, key='flux_generation_failed') + " (Invalid Dimension Data)"
                    bot.edit_message_text(failed_msg, chat_id=original_chat_id, 
                                        message_id=original_message_id, reply_markup=None)
                except Exception:
                    pass
                return
            
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            
            selected_dimensions_text = get_translation(language=user_data.language, 
                                                     key='flux_selected_dim_confirm', 
                                                     width=width, height=height)
            try:
                bot.edit_message_text(selected_dimensions_text, chat_id=original_chat_id, 
                                    message_id=original_message_id, reply_markup=None)
            except Exception:
                pass
            
            generating_message_text = get_translation(language=user_data.language, key='flux_generating')
            try:
                status_message_obj = bot.send_message(user_id, generating_message_text, parse_mode="Markdown")
            except Exception:
                status_message_obj = None
            
            generated_image_path = generate_image_with_flux(hf_client=hf_client_ref, 
                                                          prompt=prompt, width=width, height=height)
            
            _send_flux_image(bot, user_id, generated_image_path, prompt, width, height, user_data.language)
            logging.info(f"Sent FLUX image to user {user_id}. Path: {generated_image_path}")
            
        except FLUX_EXCEPTIONS as e:
            logging.error(f"FLUX Service Error for user {user_id}: {e}")
            
            if isinstance(e, FluxClientError):
                error_msg = get_translation(language=user_data.language, key='flux_service_unavailable_alert')
            elif isinstance(e, FluxGenerationError):
                error_msg = get_translation(language=user_data.language, key='flux_error', 
                                          error=escape_markdown_v2(str(e)))
            else:
                error_msg = get_translation(language=user_data.language, key='flux_error', 
                                          error="An error occurred with image generation.")
            
            if status_message_obj:
                try:
                    bot.edit_message_text(error_msg, chat_id=user_id, 
                                        message_id=status_message_obj.message_id, parse_mode="Markdown")
                except Exception:
                    bot.send_message(user_id, error_msg, parse_mode="Markdown")
            else:
                bot.send_message(user_id, error_msg, parse_mode="Markdown")
                
        except Exception as e:
            logging.error(f"General unhandled error in handle_flux_dimensions_callback for user {user_id}: {e}", exc_info=True)
            error_msg = get_translation(language=user_data.language, key='flux_error', 
                                      error="An unexpected error occurred.")
            
            if status_message_obj:
                try:
                    bot.edit_message_text(error_msg, chat_id=user_id, 
                                        message_id=status_message_obj.message_id, parse_mode="Markdown")
                except Exception:
                    bot.send_message(user_id, error_msg, parse_mode="Markdown")
            else:
                bot.send_message(user_id, error_msg, parse_mode="Markdown")
                
        finally:
            user_data.state = BotState.NONE
            user_data.clear_flux_data()
            _safe_delete_message(bot, user_id, status_message_obj)
            _cleanup_temp_file(generated_image_path)

    logging.info("Flux handlers registered.")