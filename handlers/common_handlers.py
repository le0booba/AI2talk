import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import pytz
import telebot
from telebot import types, util
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup

from components.settings_config import settings
from components.localization import get_translation, LANGUAGES
from components.user_data_manager import get_user_data, BotState
from components.rate_limiter import check_rate_limits, is_user_blocked
from components.telegram_utils import clean_markdown_text, escape_html_util
from components.currency_service import (
    get_fiat_rates as get_fiat_rates_cs,
    get_crypto_rates as get_crypto_rates_cs,
    get_top_gainers_crypto as get_top_gainers_crypto_cs,
    format_currency_number,
    RateFetchingError
)


def register_common_handlers(
    bot: telebot.TeleBot,
    block_checked_decorator: callable,
    command_handler_map_ref: dict,
    allowed_commands_when_blocked_list: List[str]
):
    def is_message_old(message: Message) -> bool:
        return (hasattr(bot, 'BOT_START_TIME_REFERENCE') and 
                message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()))

    def safe_send_message(user_id: int, text: str, markdown: bool = True, **kwargs):
        try:
            parse_mode = "Markdown" if markdown else None
            return bot.send_message(user_id, text, parse_mode=parse_mode, **kwargs)
        except telebot.apihelper.ApiTelegramException:
            if markdown:
                try:
                    return bot.send_message(user_id, clean_markdown_text(text), **kwargs)
                except telebot.apihelper.ApiTelegramException as e:
                    logging.error(f"Failed to send message to {user_id}: {e}")
            else:
                logging.error(f"Failed to send plain message to {user_id}")
        except Exception as e:
            logging.error(f"Unexpected error sending message to {user_id}: {e}")

    def safe_edit_message(chat_id: int, message_id: int, text: str, **kwargs):
        try:
            return bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, **kwargs)
        except telebot.apihelper.ApiTelegramException as e:
            logging.warning(f"Error editing message {message_id}: {e}")

    def safe_answer_callback(call_id: str, text: str = None, **kwargs):
        try:
            return bot.answer_callback_query(call_id, text, **kwargs)
        except Exception as e:
            logging.warning(f"Error answering callback query: {e}")

    def safe_edit_markup(chat_id: int, message_id: int, markup=None):
        try:
            return bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=markup)
        except Exception:
            pass

    @bot.message_handler(commands=['start'])
    def send_welcome_handler(message: Message) -> None:
        if is_message_old(message):
            logging.debug(f"Ignoring old message from {message.chat.id}")
            return
        
        user_id = message.chat.id
        user_s_data = get_user_data(user_id)
        welcome_message = get_translation(language=user_s_data.language, key='welcome')
        safe_send_message(user_id, welcome_message)
    
    command_handler_map_ref['/start'] = send_welcome_handler

    if settings.ENABLE_LANG_FEATURE:
        @bot.message_handler(commands=['lang'])
        @block_checked_decorator
        def set_language_command_handler(message: Message) -> None:
            if is_message_old(message):
                return
            
            user_id = message.chat.id
            user_s_data = get_user_data(user_id)
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            buttons = [
                types.InlineKeyboardButton(text=lang_code.upper(), callback_data=f"set_language:{lang_code}")
                for lang_code in LANGUAGES
            ]
            keyboard.add(*buttons)
            
            select_language_message = get_translation(language=user_s_data.language, key='select_language')
            safe_send_message(user_id, select_language_message, reply_markup=keyboard)
            user_s_data.state = BotState.NONE
        
        command_handler_map_ref['/lang'] = set_language_command_handler

        @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("set_language:"))
        def set_language_callback_handler(call: CallbackQuery) -> None:
            user_id = call.from_user.id
            user_s_data = get_user_data(user_id)
            message_obj = call.message
            language_code_from_callback = call.data.split(":", 1)[1]
            
            if language_code_from_callback in LANGUAGES:
                user_s_data.language = language_code_from_callback
                logging.info(f"User {user_id} set language to {language_code_from_callback}")
                
                language_set_message = get_translation(
                    language=user_s_data.language, 
                    key='language_set', 
                    selected_lang=language_code_from_callback.upper()
                )
                
                safe_answer_callback(call.id, language_set_message)
                safe_edit_message(message_obj.chat.id, message_obj.message_id, language_set_message, reply_markup=None)
                
                welcome_message = get_translation(language=user_s_data.language, key='welcome')
                safe_send_message(user_id, welcome_message)
            else:
                invalid_lang_alert = get_translation(language=user_s_data.language, key='invalid_language_alert')
                safe_answer_callback(call.id, invalid_lang_alert, show_alert=True)
                safe_edit_markup(message_obj.chat.id, message_obj.message_id, None)
    else:
        logging.info("Language feature (/lang) is disabled by settings.")

    if settings.ENABLE_USER_INFO_FEATURE:
        @bot.message_handler(commands=['user'])
        @block_checked_decorator
        def check_user_status_command_handler(message: Message) -> None:
            if is_message_old(message):
                return
            
            user_id = message.chat.id
            user_s_data = get_user_data(user_id)
            status_lines = []
            user_is_trusted = user_id in settings.TRUSTED_USERS_SET
            
            if user_is_trusted:
                status_lines.append(get_translation(language=user_s_data.language, key='trusted_user'))
            else:
                now = datetime.now(timezone.utc)
                day_ago = now - timedelta(hours=24)
                user_reqs_today = sum(1 for req in user_s_data.requests_timestamps if req > day_ago)
                
                limitations_text = get_translation(
                    language=user_s_data.language, 
                    key='not_in_trusted', 
                    _user_violations=user_s_data.violations
                )
                status_lines.append(limitations_text)
                
                usage_text = get_translation(
                    language=user_s_data.language, 
                    key='user_status_usage_today', 
                    used=user_reqs_today, 
                    limit=settings.MAX_REQUESTS_PER_USER_PER_DAY
                )
                status_lines.append(f"\n{usage_text}")
                
                if user_s_data.last_rate_request_timestamp:
                    time_since_last_rate = (now - user_s_data.last_rate_request_timestamp).total_seconds()
                    rate_cooldown_total = 60
                    if time_since_last_rate < rate_cooldown_total:
                        seconds_remaining_rate = int(rate_cooldown_total - time_since_last_rate)
                        if 'user_status_rate_cooldown' in LANGUAGES[user_s_data.language]:
                            rate_cooldown_info = get_translation(
                                language=user_s_data.language,
                                key='user_status_rate_cooldown',
                                seconds_remaining=seconds_remaining_rate
                            )
                            status_lines.append(f"\n{rate_cooldown_info}")
                
                if user_s_data.blocked_until_timestamp and now < user_s_data.blocked_until_timestamp:
                    try:
                        target_tz = pytz.timezone('Europe/Moscow')
                        blocked_until_local_dt = user_s_data.blocked_until_timestamp.astimezone(target_tz)
                        blocked_until_local_str = blocked_until_local_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
                    except:
                        blocked_until_local_str = user_s_data.blocked_until_timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')
                    
                    blocked_text = get_translation(
                        language=user_s_data.language, 
                        key='user_status_blocked_until', 
                        datetime=blocked_until_local_str
                    )
                    status_lines.append(f"\n{blocked_text}")
            
            safe_send_message(user_id, "\n".join(status_lines))
            user_s_data.state = BotState.NONE
        
        command_handler_map_ref['/user'] = check_user_status_command_handler
    else:
        logging.info("User info feature (/user) is disabled by settings.")

    if settings.ENABLE_GETID_FEATURE:
        @bot.message_handler(commands=['getid'])
        @block_checked_decorator
        def getid_command_handler(message: Message):
            if is_message_old(message):
                return
            
            user_id = message.from_user.id
            user_s_data = get_user_data(user_id)
            
            if not check_rate_limits(user_id, "getid_entry", increment_request_count=False):
                return
            
            user_s_data.state = BotState.NONE
            markup = types.InlineKeyboardMarkup(row_width=1)
            
            myid_button = types.InlineKeyboardButton(
                get_translation(language=user_s_data.language, key="button_get_my_id"), 
                callback_data="getid_myid"
            )
            forward_button = types.InlineKeyboardButton(
                get_translation(language=user_s_data.language, key="button_forward_message"), 
                callback_data="getid_forward"
            )
            markup.add(myid_button, forward_button)
            
            choice_text = get_translation(language=user_s_data.language, key="getid_choice")
            safe_send_message(user_id, choice_text, reply_markup=markup)
        
        command_handler_map_ref['/getid'] = getid_command_handler

        @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("getid_"))
        def handle_getid_callbacks_handler(call: CallbackQuery):
            user_id = call.from_user.id
            user_s_data = get_user_data(user_id)
            message_obj = call.message
            
            safe_answer_callback(call.id)
            safe_edit_markup(message_obj.chat.id, message_obj.message_id, None)
            
            if call.data == "getid_myid":
                if not check_rate_limits(user_id, "getid_myid_action"):
                    return
                
                user_id_markdown = f"`{user_id}`"
                your_id_message_text = get_translation(
                    language=user_s_data.language, 
                    key="getid_your_id_is", 
                    user_telegram_id=user_id_markdown
                )
                safe_send_message(user_id, your_id_message_text)
                user_s_data.state = BotState.NONE
                
            elif call.data == "getid_forward":
                user_s_data.state = BotState.WAITING_FOR_FORWARD
                forward_prompt_text = get_translation(language=user_s_data.language, key="forward_prompt_message")
                if not safe_send_message(user_id, forward_prompt_text):
                    user_s_data.state = BotState.NONE

        @bot.message_handler(
            func=lambda message: get_user_data(message.chat.id).state == BotState.WAITING_FOR_FORWARD,
            content_types=util.content_type_media + ['text']
        )
        def handle_forwarded_message_handler(message: Message):
            if is_message_old(message):
                return
            
            user_id = message.chat.id
            user_s_data = get_user_data(user_id)
            
            if message.text and message.text.startswith('/'):
                user_s_data.state = BotState.NONE
                user_s_data.processed_media_group_ids.clear()
                command = message.text.split(maxsplit=1)[0].lower()
                handler_func = command_handler_map_ref.get(command)
                
                if handler_func:
                    try:
                        if command not in allowed_commands_when_blocked_list and is_user_blocked(user_id):
                            return
                        handler_func(message)
                    except Exception as e_exec:
                        logging.error(f"Error executing command '{command}': {e_exec}", exc_info=True)
                        error_msg = get_translation(
                            language=user_s_data.language, 
                            key='error_executing_command', 
                            command=command
                        )
                        safe_send_message(user_id, error_msg, markdown=False)
                else:
                    logging.warning(f"User {user_id} sent unknown command '{command}' in WAITING_FOR_FORWARD state.")
                    safe_send_message(user_id, get_translation(language=user_s_data.language, key='mode_exited'), markdown=False)
                return
            
            if message.media_group_id:
                if message.media_group_id in user_s_data.processed_media_group_ids:
                    return
                user_s_data.processed_media_group_ids.add(message.media_group_id)
            
            sender_info_val = None
            is_forwarded = False
            sender_id_for_markdown = None
            
            if message.forward_from:
                sender_info_val = message.forward_from.id
                is_forwarded = True
                sender_id_for_markdown = f"`{sender_info_val}`"
            elif message.forward_from_chat:
                sender_info_val = message.forward_from_chat.id
                is_forwarded = True
                sender_id_for_markdown = f"`{sender_info_val}`"
            elif message.forward_sender_name:
                sender_info_val = f"Hidden Account ({escape_html_util(message.forward_sender_name)})"
                is_forwarded = True
                sender_id_for_markdown = sender_info_val
            
            if is_forwarded and sender_info_val is not None and sender_id_for_markdown is not None:
                if not check_rate_limits(user_id, "getid_forward_action"):
                    user_s_data.state = BotState.NONE
                    user_s_data.processed_media_group_ids.clear()
                    return
                
                sender_text = get_translation(
                    language=user_s_data.language, 
                    key="getid_sender_id", 
                    sender_id=sender_id_for_markdown
                )
                safe_send_message(user_id, sender_text, reply_to_message_id=message.message_id)
            else:
                not_forwarded_text = get_translation(language=user_s_data.language, key='not_forwarded')
                safe_send_message(user_id, not_forwarded_text, reply_to_message_id=message.message_id, markdown=False)
            
            user_s_data.state = BotState.NONE
            user_s_data.processed_media_group_ids.clear()
            
            reset_text = get_translation(language=user_s_data.language, key='getid_reset')
            safe_send_message(user_id, reset_text, markdown=False)
    else:
        logging.info("GetID feature (/getid) is disabled by settings.")

    if settings.ENABLE_RATE_FEATURE:
        @bot.message_handler(commands=['rate'])
        @block_checked_decorator
        def send_rates_command_handler(message: Message) -> None:
            if is_message_old(message):
                return
            
            user_id = message.chat.id
            user_s_data = get_user_data(user_id)
            
            if not check_rate_limits(user_id, command_type="rate"):
                return
            
            user_s_data.state = BotState.NONE
            processing_msg_text = get_translation(language=user_s_data.language, key='processing_message')
            processing_msg = safe_send_message(user_id, processing_msg_text)
            
            try:
                requests_session = telebot.apihelper._get_req_session()
                
                fiat_rates_tuple = get_fiat_rates_cs(session=requests_session)
                usd_rur_rate, eur_rur_rate = fiat_rates_tuple.usd, fiat_rates_tuple.eur
                
                crypto_rates_tuple = get_crypto_rates_cs(session=requests_session)
                btc_rur_rate, eth_rur_rate = crypto_rates_tuple.btc_rub, crypto_rates_tuple.eth_rub
                btc_usd_rate, eth_usd_rate = crypto_rates_tuple.btc_usd, crypto_rates_tuple.eth_usd
                
                if None in (usd_rur_rate, eur_rur_rate, btc_rur_rate, eth_rur_rate):
                    error_detail = "Could not retrieve all core currency rates."
                    error_message = get_translation(
                        language=user_s_data.language, 
                        key='error_fetch_rates', 
                        error=error_detail
                    )
                    if processing_msg:
                        safe_edit_message(user_id, processing_msg.message_id, error_message)
                    else:
                        safe_send_message(user_id, error_message, markdown=False)
                    return
                
                rur_usd_val = None
                if usd_rur_rate is not None and usd_rur_rate != 0:
                    rur_usd_val = 1 / usd_rur_rate
                
                eur_usd_val = None
                if eur_rur_rate is not None and usd_rur_rate is not None and usd_rur_rate != 0:
                    eur_usd_val = eur_rur_rate / usd_rur_rate

                formatted_rur_usd = format_currency_number(rur_usd_val, decimal_places=4)
                formatted_eur_usd = format_currency_number(eur_usd_val, decimal_places=4)
                formatted_btc_usd = format_currency_number(btc_usd_rate, decimal_places=0)
                formatted_eth_usd = format_currency_number(eth_usd_rate, decimal_places=0)

                formatted_usd_rur = format_currency_number(usd_rur_rate)
                formatted_eur_rur = format_currency_number(eur_rur_rate)
                formatted_btc_rur = format_currency_number(btc_rur_rate, decimal_places=0)
                formatted_eth_rur = format_currency_number(eth_rur_rate, decimal_places=0)
                
                response_parts = [
                    f"( _USD_ )\n"
                    f"RUR: *{formatted_rur_usd}*\n"
                    f"EUR: *{formatted_eur_usd}*\n"
                    f"BTC: *{formatted_btc_usd}*\n"
                    f"ETH: *{formatted_eth_usd}*",
                    
                    f"( _RUR_ )\n"
                    f"USD: *{formatted_usd_rur}*\n"
                    f"EUR: *{formatted_eur_rur}*\n"
                    f"BTC: *{formatted_btc_rur}*\n"
                    f"ETH: *{formatted_eth_rur}*"
                ]
                
                top_gainers = get_top_gainers_crypto_cs(session=requests_session, limit=5)
                if top_gainers:
                    raw_header_text = get_translation(language=user_s_data.language, key='top_gainers_header', default="Top 5 Gainers (30d vs USD):")
                    cleaned_header = raw_header_text.replace("*","").replace("_","")
                    gainers_text_lines = [f"_{cleaned_header}_"] 

                    for i, gainer in enumerate(top_gainers):
                        if gainer.change_30d is not None:
                            sign = "+" if gainer.change_30d > 0 else ""
                            gainer_symbol_md = clean_markdown_text(gainer.symbol)
                            gainer_name_md = clean_markdown_text(gainer.name)
                            change_str = f"{sign}{gainer.change_30d:.2f}%"
                            gainers_text_lines.append(
                                f"{i+1}. *{gainer_symbol_md}* ({gainer_name_md}): *{change_str}*"
                            )
                        else:
                             gainers_text_lines.append(f"{i+1}. *{clean_markdown_text(gainer.symbol)}* ({clean_markdown_text(gainer.name)}): *N/A*")
                    response_parts.append("\n" + "\n".join(gainers_text_lines))
                
                response_message = "\n\n".join(response_parts)
                
                if processing_msg:
                    safe_edit_message(user_id, processing_msg.message_id, response_message, parse_mode="Markdown")
                else:
                    safe_send_message(user_id, response_message)
                    
            except RateFetchingError as e_fetch:
                logging.error(f"RateFetchingError for user {user_id}: {e_fetch}", exc_info=True)
                error_message = get_translation(language=user_s_data.language, key='error_currency_rates')
                if processing_msg:
                    safe_edit_message(user_id, processing_msg.message_id, error_message)
                else:
                    safe_send_message(user_id, error_message, markdown=False)
            except Exception as e_general:
                logging.error(f"General error in send_rates_command for user {user_id}: {e_general}", exc_info=True)
                error_message = get_translation(language=user_s_data.language, key='error_currency_rates')
                if processing_msg:
                    safe_edit_message(user_id, processing_msg.message_id, error_message)
                else:
                    safe_send_message(user_id, error_message, markdown=False)
        
        command_handler_map_ref['/rate'] = send_rates_command_handler
    else:
        logging.info("Rate feature (/rate) is disabled by settings.")

    @bot.callback_query_handler(func=lambda call: call.data == "stop_mode")
    def stop_mode_callback_handler(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_s_data = get_user_data(user_id)
        message_obj = call.message
        current_state = user_s_data.state
        
        exit_states = {
            BotState.GEMINI_MODE, BotState.MISTRAL_MODE,
            BotState.FLUX_PROMPT, BotState.FLUX_DIMENSIONS,
            BotState.DONATE_CUSTOM_AMOUNT_INPUT, BotState.WAITING_FOR_FORWARD
        }
        
        if current_state in exit_states:
            logging.info(f"User {user_id} exited mode '{current_state.name}' via stop button.")
            
            if current_state in {BotState.FLUX_PROMPT, BotState.FLUX_DIMENSIONS}:
                user_s_data.clear_flux_data()
            elif current_state == BotState.DONATE_CUSTOM_AMOUNT_INPUT:
                user_s_data.clear_custom_donation_prompt()
            elif current_state == BotState.WAITING_FOR_FORWARD:
                user_s_data.processed_media_group_ids.clear()
            
            user_s_data.state = BotState.NONE
            safe_edit_markup(message_obj.chat.id, message_obj.message_id, None)
            
            exited_msg_text = get_translation(language=user_s_data.language, key='mode_exited')
            safe_answer_callback(call.id, exited_msg_text)
            
            if current_state != BotState.DONATE_CUSTOM_AMOUNT_INPUT:
                safe_send_message(user_id, exited_msg_text, markdown=False)
        else:
            logging.warning(f"User {user_id} clicked stop_mode but was not in active state ({current_state.name if current_state else 'None'})")
            not_in_mode_text = get_translation(
                language=user_s_data.language, 
                key='not_in_active_mode_error', 
                default="You are not in an active mode that can be exited with this button."
            )
            safe_answer_callback(call.id, not_in_mode_text, show_alert=True)
            safe_edit_markup(message_obj.chat.id, message_obj.message_id, None)

    logging.info("Common handlers registered or skipped based on settings.")