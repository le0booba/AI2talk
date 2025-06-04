# handlers/mistral_handlers.py
import logging
from datetime import datetime, timezone
from typing import List, Dict, Callable, Optional

import telebot
from telebot.types import Message

from components.settings_config import settings
from components.localization import get_translation
from components.user_data_manager import get_user_data, BotState
from components.rate_limiter import check_rate_limits, is_user_blocked
from components.telegram_utils import (
    send_message_splitted,
    clean_markdown_text,
    escape_markdown_v2,
    extract_code_blocks,
    send_code_snippets
)
from components.mistral_service import (
    send_message_to_mistral,
    MistralAPIError,
    MistralResponseError,
    MistralError
)


def register_mistral_handlers(
    bot: telebot.TeleBot,
    block_checked_decorator: Callable,
    command_handler_map_ref: Dict,
    allowed_commands_when_blocked_list: List[str],
    get_main_stop_keyboard_func: Callable
):
    if not (settings.ENABLE_MISTRAL_FEATURE and settings.MISTRAL_API_KEY):
        if not settings.ENABLE_MISTRAL_FEATURE:
            logging.info("Mistral feature is disabled by settings. Skipping Mistral handlers registration.")
        else:
            logging.warning("MISTRAL_API_KEY is not set. Mistral handlers will not be registered even if feature is enabled.")
        return

    def _is_old_message(message: Message) -> bool:
        return (hasattr(bot, 'BOT_START_TIME_REFERENCE') and 
                message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()))

    def _send_safe_message(user_id: int, text: str, **kwargs) -> Optional[Message]:
        try:
            return bot.send_message(user_id, text, **kwargs)
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send message to {user_id}: {e}")
            return None

    def _init_mistral_session(user_data) -> None:
        user_data.mistral_chat_history = []
        user_data.session_start_timestamp = datetime.now(timezone.utc)

    @bot.message_handler(commands=['mistral'])
    @block_checked_decorator
    def start_mistral_mode_command_handler(message: Message) -> None:
        if _is_old_message(message):
            return
        
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        
        mistral_mode_message = get_translation(user_data.language, 'mistral_mode')
        if not _send_safe_message(user_id, mistral_mode_message, 
                                 reply_markup=get_main_stop_keyboard_func(user_id)):
            return
            
        user_data.state = BotState.MISTRAL_MODE
        if not user_data.mistral_chat_history or not bot.check_session_expiry_reference(user_id):
            _init_mistral_session(user_data)
            logging.info(f"Initialized Mistral chat history for user {user_id}.")

    @bot.message_handler(commands=['new_mistral_chat'])
    @block_checked_decorator
    def new_mistral_chat_command_handler(message: Message) -> None:
        if _is_old_message(message):
            return
            
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        
        _init_mistral_session(user_data)
        logging.info(f"User {user_id} started a new Mistral chat via command.")
        
        new_chat_message = get_translation(user_data.language, 'new_mistral_chat')
        _send_safe_message(user_id, new_chat_message)
        
        user_data.state = BotState.MISTRAL_MODE
        mistral_mode_message = get_translation(user_data.language, 'mistral_mode')
        
        if not _send_safe_message(user_id, mistral_mode_message, 
                                 reply_markup=get_main_stop_keyboard_func(user_id)):
            logging.error(f"Failed to send mistral mode message after /new_mistral_chat")
            user_data.state = BotState.NONE

    def _handle_command_in_mistral_mode(message: Message, user_data, user_id: int) -> None:
        current_state_name = user_data.state.name
        user_data.state = BotState.NONE
        logging.info(f"User {user_id} sent command '{message.text}' while in {current_state_name}. Exiting mode.")
        
        command = message.text.split(maxsplit=1)[0].lower()
        handler_func = command_handler_map_ref.get(command)
        
        if handler_func:
            try:
                if command not in allowed_commands_when_blocked_list and is_user_blocked(user_id):
                    return
                handler_func(message)
            except Exception as e:
                logging.error(f"Error executing command '{command}' from Mistral mode: {e}", exc_info=True)
                error_msg = get_translation(user_data.language, 'error_executing_command', command=command)
                _send_safe_message(user_id, error_msg)
        else:
            logging.warning(f"User {user_id} sent unknown command '{command}' in Mistral mode.")
            _send_safe_message(user_id, get_translation(user_data.language, 'mode_exited'))

    def _handle_mistral_error(error, user_data, user_id: int, processing_msg_obj: Optional[Message]) -> None:
        if isinstance(error, MistralAPIError):
            if error.status_code == 429:
                user_error_message = get_translation(user_data.language, 'mistral_busy')
            else:
                specific_error_text = get_translation(
                    user_data.language, 'mistral_api_request_failed', 
                    error_code=str(error.status_code or "N/A")
                )
                user_error_message = get_translation(
                    user_data.language, 'error_mistral_processing', error=specific_error_text
                )
            
            if user_data.mistral_chat_history and user_data.mistral_chat_history[-1]["role"] == "user":
                user_data.mistral_chat_history.pop()
        else:
            if isinstance(error, MistralResponseError):
                error_detail_text = get_translation(user_data.language, 'mistral_invalid_response')
            else:
                error_detail_text = get_translation(
                    user_data.language, "error_mistral_processing", 
                    error=escape_markdown_v2(str(error))
                )
            
            user_error_message = get_translation(
                user_data.language, 'error_mistral_processing', error=error_detail_text
            )
            user_data.mistral_chat_history = []
        
        if processing_msg_obj:
            try:
                bot.edit_message_text(user_error_message, user_id, processing_msg_obj.message_id, parse_mode="Markdown")
            except:
                _send_safe_message(user_id, user_error_message, parse_mode="Markdown")
        else:
            _send_safe_message(user_id, user_error_message, parse_mode="Markdown")
        
        user_data.state = BotState.NONE

    @bot.message_handler(func=lambda message: get_user_data(message.chat.id).state == BotState.MISTRAL_MODE)
    @block_checked_decorator
    def handle_mistral_mode_message_handler(message: Message) -> None:
        if _is_old_message(message):
            return
            
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        
        if not settings.MISTRAL_API_KEY:
            error_msg = get_translation(user_data.language, 'error_mistral_processing', 
                                      error="API key not configured")
            _send_safe_message(user_id, error_msg)
            user_data.state = BotState.NONE
            return

        if message.text and message.text.startswith('/'):
            _handle_command_in_mistral_mode(message, user_data, user_id)
            return
        
        if not message.text:
            logging.debug(f"Received non-text message from user {user_id} in mistral_mode.")
            return
            
        if not check_rate_limits(user_id, "mistral"):
            return
            
        if not bot.check_session_expiry_reference(user_id):
            _init_mistral_session(user_data)
            logging.info(f"Re-initialized expired Mistral session for user {user_id} during message handling.")

        processing_msg_text = get_translation(user_data.language, 'processing_message')
        processing_msg_obj = _send_safe_message(user_id, processing_msg_text, parse_mode="Markdown")
        
        try:
            chat_history = user_data.mistral_chat_history
            chat_history.append({"role": "user", "content": message.text})
            
            requests_session = telebot.apihelper._get_req_session()
            full_ai_response = send_message_to_mistral(
                api_key=settings.MISTRAL_API_KEY, 
                chat_history=chat_history, 
                requests_session=requests_session
            )
            
            chat_history.append({"role": "assistant", "content": full_ai_response})

            if processing_msg_obj:
                try:
                    bot.delete_message(user_id, processing_msg_obj.message_id)
                except:
                    pass
            
            main_text, code_blocks = extract_code_blocks(full_ai_response)
            final_reply_markup = get_main_stop_keyboard_func(user_id)
            
            if main_text.strip():
                send_message_splitted(
                    user_id, clean_markdown_text(main_text), 
                    reply_markup=final_reply_markup if not code_blocks else None
                )
            
            if code_blocks:
                send_code_snippets(
                    user_id, user_data.language, code_blocks, get_translation,
                    reply_markup_for_last=final_reply_markup
                )
            elif not main_text.strip():
                _send_safe_message(user_id, "...", reply_markup=final_reply_markup, disable_notification=True)

        except (MistralAPIError, MistralResponseError, MistralError) as e:
            _handle_mistral_error(e, user_data, user_id, processing_msg_obj)
        except Exception as e:
            logging.error(f"General unhandled error in handle_mistral_mode_message for user {user_id}: {e}", exc_info=True)
            user_error_message = get_translation(user_data.language, "error_mistral_processing", 
                                               error="An unexpected error occurred.")
            user_data.mistral_chat_history = []
            
            if processing_msg_obj:
                try:
                    bot.edit_message_text(user_error_message, user_id, processing_msg_obj.message_id, parse_mode="Markdown")
                except:
                    _send_safe_message(user_id, user_error_message, parse_mode="Markdown")
            else:
                _send_safe_message(user_id, user_error_message, parse_mode="Markdown")
            
            user_data.state = BotState.NONE

    command_handler_map_ref['/mistral'] = start_mistral_mode_command_handler
    command_handler_map_ref['/new_mistral_chat'] = new_mistral_chat_command_handler
    
    logging.info("Mistral handlers registered.")