# handlers/payment_handlers.py
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Callable, Dict, Optional

import telebot
from telebot import types
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, SuccessfulPayment

from components.settings_config import settings
from components.localization import get_translation
from components.user_data_manager import get_user_data, BotState, add_trusted_user_db, check_and_unblock_if_trusted
from components.telegram_utils import clean_markdown_text, escape_markdown_v2


def register_payment_handlers(
    bot: telebot.TeleBot,
    block_checked_decorator: Callable,
    command_handler_map_ref: Dict,
    allowed_commands_when_blocked_list: List[str]
) -> None:
    if not settings.ENABLE_PAYMENTS_FEATURE:
        logging.info("Payments feature is disabled by settings. Skipping Payment handlers registration.")
        return

    def _safe_bot_action(action_func, *args, **kwargs) -> bool:
        try:
            action_func(*args, **kwargs)
            return True
        except Exception:
            return False

    def _send_error_message(user_id: int, language: str, error_key: str) -> None:
        error_msg = get_translation(language=language, key=error_key)
        _safe_bot_action(bot.send_message, user_id, error_msg)

    def _create_donation_keyboard(language: str) -> InlineKeyboardMarkup:
        keyboard = InlineKeyboardMarkup(row_width=3)
        buttons = [
            InlineKeyboardButton(f"{amount} ⭐️", callback_data=f"donate_amount:{amount}")
            for amount in settings.DONATION_PRESET_AMOUNTS
        ]
        keyboard.add(*buttons)
        
        custom_amount_text = get_translation(language=language, key='custom_amount_button')
        keyboard.add(InlineKeyboardButton(custom_amount_text, callback_data="donate_custom"))
        return keyboard

    def _send_donation_invoice(user_id: int, amount: int) -> None:
        user_data = get_user_data(user_id)
        
        try:
            title = get_translation(language=user_data.language, key='donate_invoice_title')
            description = get_translation(language=user_data.language, key='donate_invoice_description')
            payload_id = f"donate_{uuid.uuid4()}"
            price = LabeledPrice(label="Support AI Helper", amount=amount)
            
            bot.send_invoice(
                chat_id=user_id,
                title=title,
                description=description,
                invoice_payload=payload_id,
                provider_token="",
                currency="XTR",
                prices=[price]
            )
            
            logging.info(f"Sent donation invoice to user {user_id} for {amount} stars with payload {payload_id}")
            
        except Exception as e:
            logging.error(f"Error sending donation invoice for user {user_id}: {e}", exc_info=True)
            error_key = ('donate_error_creating' if isinstance(e, telebot.apihelper.ApiTelegramException) 
                        else 'donate_error_unexpected')
            _send_error_message(user_id, user_data.language, error_key)

    def _handle_command_during_custom_input(message: Message, user_data, prompt_msg_id: Optional[int]) -> None:
        user_data.state = BotState.NONE
        user_data.clear_custom_donation_prompt()
        
        if prompt_msg_id:
            cancelled_text = get_translation(language=user_data.language, key='donation_cancelled')
            if not _safe_bot_action(bot.edit_message_text, cancelled_text, 
                                  chat_id=message.chat.id, message_id=prompt_msg_id, reply_markup=None):
                _safe_bot_action(bot.delete_message, chat_id=message.chat.id, message_id=prompt_msg_id)
        
        command = message.text.split(maxsplit=1)[0].lower()
        handler_func = command_handler_map_ref.get(command)
        
        if handler_func and (command in allowed_commands_when_blocked_list or 
                           not __import__('components.rate_limiter', fromlist=['is_user_blocked']).is_user_blocked(message.chat.id)):
            try:
                handler_func(message)
            except Exception:
                _send_error_message(message.chat.id, user_data.language, 'error_executing_command')

    def _create_owner_notification(user_id: int, username: Optional[str], amount: int, 
                                 payload: str, charge_id: str, was_blocked: bool, 
                                 was_trusted: bool, is_donation: bool = True) -> str:
        username_md = f"@{escape_markdown_v2(username)}" if username else "N/A"
        
        if is_donation:
            notification = (f"💎 Received donation of {amount}⭐️ from user:\n"
                          f"ID: `{user_id}`\nUsername: {username_md}\n"
                          f"Payload: `{escape_markdown_v2(payload)}`\n"
                          f"Charge ID: `{escape_markdown_v2(charge_id)}`")
        else:
            notification = (f"⚠️ Received unexpected successful payment:\n"
                          f"User ID: `{user_id}` ({username_md})\n"
                          f"Amount: {amount} XTR\n"
                          f"Payload: `{escape_markdown_v2(payload)}`\n"
                          f"Charge ID: `{escape_markdown_v2(charge_id)}`")
        
        status_notes = []
        current_user_data = get_user_data(user_id)
        is_currently_blocked = current_user_data.blocked_until_timestamp is not None
        is_currently_trusted = user_id in settings.TRUSTED_USERS_SET
        
        if was_blocked and not is_currently_blocked:
            status_notes.append("User was blocked and is now unblocked.")
        if not was_trusted and is_currently_trusted:
            status_notes.append("User was not trusted and is now added to trusted list.")
        elif was_trusted and not was_blocked:
            status_notes.append("User was already trusted.")
        
        if status_notes:
            notification += "\n\n*Status Change:* " + " ".join(status_notes)
        
        return notification

    @bot.message_handler(commands=['donate'])
    def donate_command_handler(message: Message) -> None:
        if (hasattr(bot, 'BOT_START_TIME_REFERENCE') and 
            message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp())):
            return
        
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        user_data.state = BotState.NONE
        
        info_text = get_translation(language=user_data.language, key='donate_info')
        if not _safe_bot_action(bot.send_message, user_id, info_text, parse_mode="Markdown"):
            _safe_bot_action(bot.send_message, user_id, clean_markdown_text(info_text))
        
        keyboard = _create_donation_keyboard(user_data.language)
        select_amount_text = get_translation(language=user_data.language, key='select_donation_amount')
        _safe_bot_action(bot.send_message, user_id, select_amount_text, reply_markup=keyboard)

    @bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("donate_amount:"))
    def handle_donate_preset_amount_callback(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_data = get_user_data(user_id)
        
        try:
            amount = int(call.data.split(":", 1)[1])
            if amount < 1:
                raise ValueError("Invalid amount")
            
            _safe_bot_action(bot.answer_callback_query, call.id)
            _safe_bot_action(bot.edit_message_reply_markup, 
                           chat_id=call.message.chat.id, 
                           message_id=call.message.message_id, 
                           reply_markup=None)
            
            _send_donation_invoice(user_id, amount)
            
        except (ValueError, IndexError):
            error_msg = get_translation(language=user_data.language, key='donate_error_unexpected')
            _safe_bot_action(bot.answer_callback_query, call.id, error_msg, show_alert=True)
            _safe_bot_action(bot.edit_message_reply_markup, 
                           chat_id=call.message.chat.id, 
                           message_id=call.message.message_id, 
                           reply_markup=None)

    @bot.callback_query_handler(func=lambda call: call.data == "donate_custom")
    def handle_donate_custom_amount_callback(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_data = get_user_data(user_id)
        
        _safe_bot_action(bot.answer_callback_query, call.id)
        _safe_bot_action(bot.edit_message_reply_markup, 
                       chat_id=call.message.chat.id, 
                       message_id=call.message.message_id, 
                       reply_markup=None)
        
        user_data.state = BotState.DONATE_CUSTOM_AMOUNT_INPUT
        
        prompt_text = get_translation(language=user_data.language, key='enter_custom_amount')
        cancel_button_text = get_translation(language=user_data.language, key='cancel_button')
        keyboard = InlineKeyboardMarkup().add(
            InlineKeyboardButton(cancel_button_text, callback_data="cancel_donation")
        )
        
        try:
            sent_message = bot.send_message(user_id, prompt_text, reply_markup=keyboard)
            user_data.custom_donation_prompt_msg_id = sent_message.message_id
        except Exception:
            user_data.state = BotState.NONE

    @bot.message_handler(func=lambda message: get_user_data(message.chat.id).state == BotState.DONATE_CUSTOM_AMOUNT_INPUT)
    def handle_donate_custom_amount_input(message: Message) -> None:
        if (hasattr(bot, 'BOT_START_TIME_REFERENCE') and 
            message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp())):
            return
        
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        prompt_msg_id = user_data.custom_donation_prompt_msg_id
        
        if message.text and message.text.startswith('/'):
            _handle_command_during_custom_input(message, user_data, prompt_msg_id)
            return
        
        if not message.text:
            error_msg = get_translation(language=user_data.language, key='donate_invalid_amount')
            _safe_bot_action(bot.reply_to, message, error_msg)
            return
        
        try:
            amount = int(message.text.strip())
            if amount >= 1:
                user_data.state = BotState.NONE
                user_data.clear_custom_donation_prompt()
                
                if prompt_msg_id:
                    _safe_bot_action(bot.delete_message, chat_id=user_id, message_id=prompt_msg_id)
                
                _send_donation_invoice(user_id, amount)
            else:
                error_msg = get_translation(language=user_data.language, key='donate_invalid_amount')
                _safe_bot_action(bot.reply_to, message, error_msg)
                
        except ValueError:
            error_msg = get_translation(language=user_data.language, key='donate_invalid_amount')
            _safe_bot_action(bot.reply_to, message, error_msg)

    @bot.callback_query_handler(func=lambda call: call.data == "cancel_donation")
    def handle_cancel_donation_callback(call: CallbackQuery) -> None:
        user_id = call.from_user.id
        user_data = get_user_data(user_id)
        
        _safe_bot_action(bot.answer_callback_query, call.id)
        
        user_data.state = BotState.NONE
        user_data.clear_custom_donation_prompt()
        
        cancelled_text = get_translation(language=user_data.language, key='donation_cancelled')
        if not _safe_bot_action(bot.edit_message_text, cancelled_text, 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.message_id, 
                              reply_markup=None):
            _safe_bot_action(bot.delete_message, 
                           chat_id=call.message.chat.id, 
                           message_id=call.message.message_id)

    @bot.pre_checkout_query_handler(func=lambda query: True)
    def pre_checkout_handler(query: PreCheckoutQuery) -> None:
        user_data = get_user_data(query.from_user.id)
        
        if not query.invoice_payload or not query.invoice_payload.startswith("donate_"):
            error_msg = get_translation(language=user_data.language, key='precheckout_failed_invalid_id')
            bot.answer_pre_checkout_query(query.id, ok=False, error_message=error_msg)
            return
        
        if query.currency != "XTR" or query.total_amount < 1:
            error_msg = get_translation(language=user_data.language, key='precheckout_failed_wrong_amount')
            bot.answer_pre_checkout_query(query.id, ok=False, error_message=error_msg)
            return
        
        bot.answer_pre_checkout_query(query.id, ok=True)

    @bot.message_handler(content_types=['successful_payment'])
    def success_payment_handler(message: Message) -> None:
        if (hasattr(bot, 'BOT_START_TIME_REFERENCE') and 
            message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp())):
            return
        
        user_id = message.from_user.id
        user_data = get_user_data(user_id)
        payment_info = message.successful_payment
        
        if not payment_info:
            return
        
        payload = payment_info.invoice_payload
        amount = payment_info.total_amount
        currency = payment_info.currency
        charge_id = payment_info.telegram_payment_charge_id
        
        logging.info(f"Successful payment: User={user_id}, Amount={amount}{currency}, "
                    f"Payload={payload}, ChargeID={charge_id}")
        
        was_blocked = user_data.blocked_until_timestamp is not None
        was_trusted = user_id in settings.TRUSTED_USERS_SET
        
        if user_data.blocked_until_timestamp:
            user_data.unblock()
        
        if user_id not in settings.TRUSTED_USERS_SET:
            add_trusted_user_db(user_id)
            settings._trusted_users_set_cache = None
            logging.info(f"User {user_id} automatically added to trusted list after successful payment.")
        
        check_and_unblock_if_trusted(user_id, settings.TRUSTED_USERS_SET)
        
        is_donation = payload and payload.startswith("donate_")
        
        if is_donation:
            thank_you_msg = get_translation(language=user_data.language, key='payment_success', amount=amount)
            _safe_bot_action(bot.send_message, message.chat.id, thank_you_msg)
        else:
            generic_thank_you = get_translation(language=user_data.language, key='unknown_payload')
            if not was_trusted and user_id in settings.TRUSTED_USERS_SET:
                generic_thank_you += "\n\nYou have been granted trusted status."
            _safe_bot_action(bot.send_message, message.chat.id, generic_thank_you)
        
        if settings.BOT_OWNER_USER_ID:
            notification = _create_owner_notification(
                user_id, message.from_user.username, amount, payload, 
                charge_id, was_blocked, was_trusted, is_donation
            )
            _safe_bot_action(bot.send_message, settings.BOT_OWNER_USER_ID, notification, parse_mode="Markdown")

    @bot.message_handler(commands=['paysupport'])
    def pay_support_handler(message: Message) -> None:
        if (hasattr(bot, 'BOT_START_TIME_REFERENCE') and 
            message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp())):
            return
        
        user_id = message.chat.id
        user_data = get_user_data(user_id)
        user_data.state = BotState.NONE
        
        username_log_str = f"@{message.from_user.username}" if message.from_user.username else "N/A"
        logging.info(f"User {user_id} ({username_log_str}) requested payment support using /paysupport.")
        
        support_text = get_translation(language=user_data.language, key='payment_support_info')
        _safe_bot_action(bot.send_message, user_id, support_text)
        
        if settings.BOT_OWNER_USER_ID:
            username_md = (f"@{escape_markdown_v2(message.from_user.username)}" 
                          if message.from_user.username else "N/A")
            
            owner_notification = (f"ℹ️ User requested payment support (/paysupport):\n"
                                f"ID: `{user_id}`\nUsername: {username_md}\n"
                                f"Chat ID: `{message.chat.id}`")
            
            if (user_data.blocked_until_timestamp and 
                datetime.now(timezone.utc) < user_data.blocked_until_timestamp):
                owner_notification += "\n\n*Note: This user is currently blocked.*"
            
            _safe_bot_action(bot.send_message, settings.BOT_OWNER_USER_ID, 
                           owner_notification, parse_mode="Markdown")

    command_handler_map_ref['/donate'] = donate_command_handler
    command_handler_map_ref['/paysupport'] = pay_support_handler
    
    logging.info("Payment handlers registered.")