# handlers/gemini_handlers.py
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Callable, Optional

import telebot
from telebot import types
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup

from components.settings_config import settings, AVAILABLE_MODELS
from components.localization import get_translation
from components.user_data_manager import get_user_data, BotState
from components.rate_limiter import is_user_blocked, check_rate_limits
from components.telegram_utils import (
    send_message_splitted,
    clean_markdown_text,
    escape_markdown_v2,
    extract_code_blocks,
    send_code_snippets
)
from components.gemini_service import (
    initialize_model as init_gemini_model,
    start_new_chat as start_gemini_chat,
    send_message_to_gemini,
    GeminiBlockedPromptError,
    GeminiChatError
)

def register_gemini_handlers(
    bot: telebot.TeleBot,
    block_checked_decorator: Callable,
    command_handler_map_ref: Dict,
    allowed_commands_when_blocked_list: List[str],
    get_main_stop_keyboard_func: Callable,
    check_session_expiry_func: Callable
):
    if not settings.ENABLE_GEMINI_FEATURE:
        logging.info("Gemini feature is disabled by settings. Skipping Gemini handlers registration.")
        return
    if not settings.GEMINI_API_KEY:
        logging.warning("GEMINI_API_KEY is not set. Gemini handlers will not be registered even if feature is enabled.")
        return

    def get_gemini_main_menu_keyboard_local(user_id: int) -> InlineKeyboardMarkup:
        user_s_data = get_user_data(user_id)
        keyboard = InlineKeyboardMarkup(row_width=1)
        change_model_text = get_translation(language=user_s_data.language, key='change_model_button')
        new_chat_text = get_translation(language=user_s_data.language, key='new_chat_button')
        keyboard.add(types.InlineKeyboardButton(change_model_text, callback_data="gemini:model"))
        keyboard.add(types.InlineKeyboardButton(new_chat_text, callback_data="gemini:newchat"))
        return keyboard

    def get_gemini_model_selection_keyboard_local(user_id: int) -> InlineKeyboardMarkup:
        user_s_data = get_user_data(user_id)
        keyboard = InlineKeyboardMarkup(row_width=1)
        for model_name_iter in AVAILABLE_MODELS:
            keyboard.add(types.InlineKeyboardButton(text=model_name_iter, callback_data=f"set_model:{model_name_iter}"))
        back_button_text = get_translation(language=user_s_data.language, key='back_button')
        keyboard.add(types.InlineKeyboardButton(text=back_button_text, callback_data="gemini_menu_back"))
        return keyboard

    @bot.message_handler(commands=['gemini_menu'])
    @block_checked_decorator
    def show_gemini_menu_handler(message: Message) -> None:
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()):
            return
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        keyboard = get_gemini_main_menu_keyboard_local(user_id)
        gemini_menu_title = get_translation(language=user_s_data.language, key='gemini_menu_title')
        try:
            bot.send_message(user_id, gemini_menu_title, reply_markup=keyboard)
        except telebot.apihelper.ApiTelegramException as e_send:
            logging.error(f"Error sending gemini menu message to {user_id}: {e_send}")
        user_s_data.state = BotState.NONE
    command_handler_map_ref['/gemini_menu'] = show_gemini_menu_handler

    @bot.message_handler(commands=['model'])
    @block_checked_decorator
    def set_gemini_model_command_handler(message: Message) -> None:
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()):
            return
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        keyboard = get_gemini_model_selection_keyboard_local(user_id)
        select_model_message = get_translation(language=user_s_data.language, key='select_model')
        try:
            bot.send_message(user_id, select_model_message, reply_markup=keyboard)
        except telebot.apihelper.ApiTelegramException as e_send:
            logging.error(f"Error sending gemini model selection message to {user_id}: {e_send}")
        user_s_data.state = BotState.NONE
    command_handler_map_ref['/model'] = set_gemini_model_command_handler

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("gemini:"))
    @block_checked_decorator
    def gemini_menu_callback_handler(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_s_data = get_user_data(user_id)
        message_obj = call.message
        action = call.data.split(":", 1)[1]
        try:
            bot.answer_callback_query(call.id)
        except telebot.apihelper.ApiTelegramException:
            pass
        if action == "model":
            keyboard = get_gemini_model_selection_keyboard_local(user_id)
            select_model_message = get_translation(language=user_s_data.language, key='select_model')
            try:
                 bot.edit_message_text(select_model_message, chat_id=message_obj.chat.id, message_id=message_obj.message_id, reply_markup=keyboard)
            except telebot.apihelper.ApiTelegramException as e_edit:
                 logging.warning(f"Error editing message for gemini:model callback: {e_edit}. Sending new message.")
                 try:
                    bot.send_message(message_obj.chat.id, select_model_message, reply_markup=keyboard)
                    try:
                        bot.delete_message(chat_id=message_obj.chat.id, message_id=message_obj.message_id)
                    except telebot.apihelper.ApiTelegramException:
                        pass
                 except telebot.apihelper.ApiTelegramException as e_send:
                     logging.error(f"Failed to send gemini model selection as new message: {e_send}")
        elif action == "newchat":
            if not settings.GEMINI_API_KEY:
                error_msg = get_translation(language=user_s_data.language, key='error_init_gemini', error="API key not configured")
                try:
                    bot.send_message(user_id, error_msg)
                except telebot.apihelper.ApiTelegramException:
                    pass
                return
            user_s_data.gemini_chat = None
            model_instance = init_gemini_model(user_s_data.gemini_model)
            if model_instance:
                chat_session = start_gemini_chat(model_instance)
                if chat_session:
                    user_s_data.gemini_chat = chat_session
                    user_s_data.session_start_timestamp = datetime.now(timezone.utc)
                    new_chat_message = get_translation(language=user_s_data.language, key='new_chat')
                    bot.send_message(message_obj.chat.id, new_chat_message)
                    user_s_data.state = BotState.GEMINI_MODE
                    gemini_mode_message = get_translation(language=user_s_data.language, key='gemini_mode', model=user_s_data.gemini_model)
                    bot.send_message(message_obj.chat.id, gemini_mode_message, reply_markup=get_main_stop_keyboard_func(user_id))
                else:
                    error_msg = get_translation(language=user_s_data.language, key='error_start_new_gemini', error="Could not start chat session.")
                    try:
                        bot.send_message(user_id, error_msg)
                    except telebot.apihelper.ApiTelegramException:
                        pass
                    user_s_data.state = BotState.NONE
            else:
                error_msg = get_translation(language=user_s_data.language, key='error_init_gemini', error="Could not initialize model.")
                try:
                    bot.send_message(user_id, error_msg)
                except telebot.apihelper.ApiTelegramException:
                    pass
                user_s_data.state = BotState.NONE
            try:
                bot.delete_message(chat_id=message_obj.chat.id, message_id=message_obj.message_id)
            except telebot.apihelper.ApiTelegramException:
                pass

    @bot.callback_query_handler(func=lambda call: call.data == "gemini_menu_back")
    @block_checked_decorator
    def handle_gemini_menu_back_callback_handler(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_s_data = get_user_data(user_id)
        message_obj = call.message
        try:
            bot.answer_callback_query(call.id)
        except telebot.apihelper.ApiTelegramException:
            pass
        keyboard = get_gemini_main_menu_keyboard_local(user_id)
        gemini_menu_title = get_translation(language=user_s_data.language, key='gemini_menu_title')
        try:
            bot.edit_message_text(gemini_menu_title, chat_id=message_obj.chat.id, message_id=message_obj.message_id, reply_markup=keyboard)
        except telebot.apihelper.ApiTelegramException as e_edit:
            logging.warning(f"Error editing message for gemini_menu_back callback: {e_edit}. Sending new message.")
            try:
                 bot.send_message(message_obj.chat.id, gemini_menu_title, reply_markup=keyboard)
            except telebot.apihelper.ApiTelegramException as e_send:
                 logging.error(f"Failed to send main gemini menu as new message: {e_send}")

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("set_model:"))
    @block_checked_decorator
    def set_model_callback_handler(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_s_data = get_user_data(user_id)
        message_obj = call.message
        try:
            model_name = call.data.split(":", 1)[1]
            if model_name in AVAILABLE_MODELS:
                user_s_data.gemini_model = model_name
                logging.info(f"User {user_id} set their Gemini model to {model_name}")
                model_set_message = get_translation(language=user_s_data.language, key='model_set', model=model_name)
                bot.answer_callback_query(call.id, model_set_message)
                keyboard = get_gemini_main_menu_keyboard_local(user_id)
                gemini_menu_title = get_translation(language=user_s_data.language, key='gemini_menu_title')
                try:
                     bot.edit_message_text(gemini_menu_title, chat_id=message_obj.chat.id, message_id=message_obj.message_id, reply_markup=keyboard, parse_mode="Markdown")
                except telebot.apihelper.ApiTelegramException as e_edit:
                    logging.warning(f"Error editing message to show main menu after set_model callback: {e_edit}. Selection still applied.")
                user_s_data.gemini_chat = None
                logging.debug(f"Cleared Gemini chat history for user {user_id} after model change.")
                if user_s_data.state == BotState.GEMINI_MODE:
                     gemini_mode_message = get_translation(language=user_s_data.language, key='gemini_mode', model=user_s_data.gemini_model)
                     try:
                         bot.send_message(user_id, gemini_mode_message, parse_mode="Markdown", reply_markup=get_main_stop_keyboard_func(user_id))
                     except telebot.apihelper.ApiTelegramException:
                         pass
            else:
                invalid_model_message = get_translation(language=user_s_data.language, key='invalid_model')
                bot.answer_callback_query(call.id, invalid_model_message, show_alert=True)
                try:
                    keyboard = get_gemini_model_selection_keyboard_local(user_id)
                    select_model_message = get_translation(language=user_s_data.language, key='select_model')
                    bot.edit_message_text(select_model_message, chat_id=message_obj.chat.id, message_id=message_obj.message_id, reply_markup=keyboard)
                except telebot.apihelper.ApiTelegramException:
                    pass
        except Exception as e:
            logging.error(f"Error in set_model_callback for user {user_id}: {e}", exc_info=True)
            error_alert = get_translation(language=user_s_data.language, key='error_setting_model_alert')
            try:
                bot.answer_callback_query(call.id, error_alert, show_alert=True)
            except telebot.apihelper.ApiTelegramException:
                pass

    @bot.message_handler(commands=['gemini'])
    @block_checked_decorator
    def start_gemini_mode_command_handler(message: Message) -> None:
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()):
            return
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        if not settings.GEMINI_API_KEY:
            error_msg = get_translation(language=user_s_data.language, key='error_init_gemini', error="API key not configured")
            try:
                bot.send_message(user_id, error_msg)
            except telebot.apihelper.ApiTelegramException:
                pass
            return
        gemini_mode_message = get_translation(language=user_s_data.language, key='gemini_mode', model=user_s_data.gemini_model)
        try:
            bot.send_message(user_id, gemini_mode_message, reply_markup=get_main_stop_keyboard_func(user_id))
        except telebot.apihelper.ApiTelegramException as e_send:
            logging.error(f"Failed to send gemini mode entry message to {user_id}: {e_send}")
            return
        user_s_data.state = BotState.GEMINI_MODE
        if not user_s_data.gemini_chat or not check_session_expiry_func(user_id):
            model_instance = init_gemini_model(user_s_data.gemini_model)
            if model_instance:
                chat_session = start_gemini_chat(model_instance)
                if chat_session:
                    user_s_data.gemini_chat = chat_session
                    user_s_data.session_start_timestamp = datetime.now(timezone.utc)
                    logging.info(f"Started new Gemini session for {user_id} on entering mode with model {user_s_data.gemini_model}.")
                else:
                    error_msg = get_translation(language=user_s_data.language, key='error_start_new_gemini', error="Could not start chat session on mode entry.")
                    try:
                        bot.send_message(user_id, error_msg)
                    except telebot.apihelper.ApiTelegramException:
                        pass
                    user_s_data.state = BotState.NONE
            else:
                error_msg = get_translation(language=user_s_data.language, key='error_init_gemini', error="Could not initialize model on mode entry.")
                try:
                    bot.send_message(user_id, error_msg)
                except telebot.apihelper.ApiTelegramException:
                    pass
                user_s_data.state = BotState.NONE
    command_handler_map_ref['/gemini'] = start_gemini_mode_command_handler

    @bot.message_handler(commands=['new_gemini_chat'])
    @block_checked_decorator
    def new_gemini_chat_command_handler(message: Message) -> None:
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()):
            return
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        if not settings.GEMINI_API_KEY:
            error_msg = get_translation(language=user_s_data.language, key='error_start_new_gemini', error="API key not configured")
            try:
                bot.send_message(user_id, error_msg)
            except telebot.apihelper.ApiTelegramException:
                pass
            return
        user_s_data.gemini_chat = None
        logging.info(f"User {user_id} started a new Gemini chat via command with model {user_s_data.gemini_model}.")
        model_instance = init_gemini_model(user_s_data.gemini_model)
        if model_instance:
            chat_session = start_gemini_chat(model_instance)
            if chat_session:
                user_s_data.gemini_chat = chat_session
                user_s_data.session_start_timestamp = datetime.now(timezone.utc)
                new_chat_message = get_translation(language=user_s_data.language, key='new_chat')
                bot.send_message(user_id, new_chat_message)
                user_s_data.state = BotState.GEMINI_MODE
                gemini_mode_message = get_translation(language=user_s_data.language, key='gemini_mode', model=user_s_data.gemini_model)
                bot.send_message(user_id, gemini_mode_message, reply_markup=get_main_stop_keyboard_func(user_id))
            else:
                error_msg = get_translation(language=user_s_data.language, key='error_start_new_gemini', error="Could not start chat session via command.")
                try:
                    bot.send_message(user_id, error_msg)
                except telebot.apihelper.ApiTelegramException:
                    pass
                user_s_data.state = BotState.NONE
        else:
            error_msg = get_translation(language=user_s_data.language, key='error_init_gemini', error="Could not initialize model for new chat command.")
            try:
                bot.send_message(user_id, error_msg)
            except telebot.apihelper.ApiTelegramException:
                pass
            user_s_data.state = BotState.NONE
    command_handler_map_ref['/new_gemini_chat'] = new_gemini_chat_command_handler

    @bot.message_handler(func=lambda message: get_user_data(message.chat.id).state == BotState.GEMINI_MODE)
    @block_checked_decorator
    def handle_gemini_mode_handler(message: Message) -> None:
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()):
            return
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        if not settings.GEMINI_API_KEY:
            error_msg = get_translation(language=user_s_data.language, key='error_gemini_processing', error="API key not configured")
            try:
                bot.send_message(user_id, error_msg)
            except telebot.apihelper.ApiTelegramException:
                pass
            user_s_data.state = BotState.NONE
            return

        if message.text and message.text.startswith('/'):
            current_state_name = user_s_data.state.name
            user_s_data.state = BotState.NONE
            logging.info(f"User {user_id} sent command '{message.text}' while in {current_state_name}. Exiting mode.")
            command = message.text.split(maxsplit=1)[0].lower()
            handler_func = command_handler_map_ref.get(command)
            if handler_func:
                try:
                    if command not in allowed_commands_when_blocked_list and is_user_blocked(user_id):
                        return
                    handler_func(message)
                except Exception as e_exec:
                    logging.error(f"Error executing command '{command}' from Gemini mode: {e_exec}", exc_info=True)
                    error_msg = get_translation(language=user_s_data.language, key='error_executing_command', command=command)
                    try:
                        bot.send_message(user_id, error_msg)
                    except telebot.apihelper.ApiTelegramException:
                        pass
            else:
                logging.warning(f"User {user_id} sent unknown command '{command}' in Gemini mode.")
                try:
                    bot.send_message(user_id, get_translation(language=user_s_data.language, key='mode_exited'))
                except telebot.apihelper.ApiTelegramException:
                    pass
            return
        elif message.text:
            if not check_rate_limits(user_id, "gemini"):
                return
            if not user_s_data.gemini_chat or not check_session_expiry_func(user_id):
                model_instance = init_gemini_model(user_s_data.gemini_model)
                if model_instance:
                    chat_session = start_gemini_chat(model_instance)
                    if chat_session:
                        user_s_data.gemini_chat = chat_session
                        user_s_data.session_start_timestamp = datetime.now(timezone.utc)
                        logging.info(f"Re-initialized Gemini session for user {user_id} with model {user_s_data.gemini_model}.")
                    else:
                        error_msg = get_translation(language=user_s_data.language, key='error_session_reinit_gemini', error="Could not start chat session.")
                        try:
                            bot.send_message(user_id, error_msg)
                        except telebot.apihelper.ApiTelegramException:
                            pass
                        user_s_data.state = BotState.NONE
                        return
                else:
                    error_msg = get_translation(language=user_s_data.language, key='error_session_reinit_gemini', error="Could not initialize model.")
                    try:
                        bot.send_message(user_id, error_msg)
                    except telebot.apihelper.ApiTelegramException:
                        pass
                    user_s_data.state = BotState.NONE
                    return
            processing_msg = None
            processing_msg_text = get_translation(language=user_s_data.language, key='processing_message')
            try:
                processing_msg = bot.send_message(user_id, processing_msg_text, parse_mode="Markdown")
            except telebot.apihelper.ApiTelegramException:
                pass
            try:
                full_ai_response = send_message_to_gemini(user_s_data.gemini_chat, message.text)
                if processing_msg:
                    try:
                        bot.delete_message(user_id, processing_msg.message_id)
                    except telebot.apihelper.ApiTelegramException:
                        pass
                
                main_text, code_blocks = extract_code_blocks(full_ai_response)
                final_reply_markup = get_main_stop_keyboard_func(user_id)
                
                if main_text.strip():
                    send_message_splitted(user_id, clean_markdown_text(main_text), 
                                          reply_markup=final_reply_markup if not code_blocks else None)
                
                if code_blocks:
                    send_code_snippets(user_id, user_s_data.language, code_blocks, get_translation,
                                       reply_markup_for_last=final_reply_markup)
                elif not main_text.strip() and not code_blocks:
                   bot.send_message(user_id, "...", reply_markup=final_reply_markup, disable_notification=True)

            except GeminiBlockedPromptError:
                error_msg = get_translation(language=user_s_data.language, key='error_gemini_processing', error="Content policy violation or unsafe prompt.")
                if processing_msg:
                    try:
                        bot.edit_message_text(error_msg, user_id, processing_msg.message_id, parse_mode="Markdown")
                    except telebot.apihelper.ApiTelegramException:
                        bot.send_message(user_id, error_msg, parse_mode="Markdown")
                else:
                    bot.send_message(user_id, error_msg, parse_mode="Markdown")
                user_s_data.state = BotState.NONE
            except GeminiChatError as e_chat:
                error_msg = get_translation(language=user_s_data.language, key='error_gemini_processing', error=escape_markdown_v2(str(e_chat)))
                if processing_msg:
                    try:
                        bot.edit_message_text(error_msg, user_id, processing_msg.message_id, parse_mode="Markdown")
                    except telebot.apihelper.ApiTelegramException:
                        bot.send_message(user_id, error_msg, parse_mode="Markdown")
                else:
                    bot.send_message(user_id, error_msg, parse_mode="Markdown")
                user_s_data.state = BotState.NONE
            except Exception as e_general:
                logging.error(f"Unhandled error in handle_gemini_mode for user {user_id}: {e_general}", exc_info=True)
                error_msg = get_translation(language=user_s_data.language, key='error_gemini_processing', error="An unexpected error occurred with AI service.")
                if processing_msg:
                    try:
                        bot.edit_message_text(error_msg, user_id, processing_msg.message_id, parse_mode="Markdown")
                    except telebot.apihelper.ApiTelegramException:
                        bot.send_message(user_id, error_msg, parse_mode="Markdown")
                else:
                    bot.send_message(user_id, error_msg, parse_mode="Markdown")
                user_s_data.state = BotState.NONE
        else:
            logging.debug(f"Received non-text message from user {user_id} in gemini_mode.")

    logging.info("Gemini handlers registered.")