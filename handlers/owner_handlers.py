# handlers/owner_handlers.py
import logging
import telebot
from telebot.types import Message
from typing import Dict, Callable
from datetime import datetime, timezone, timedelta

from components.settings_config import settings
from components.localization import get_translation
from components.user_data_manager import (
    get_user_data,
    block_user_db,
    unblock_user_db,
    add_trusted_user_db,
    remove_trusted_user_db,
    user_data_store,
    get_blocked_user_info_db,
    check_and_unblock_if_trusted,
    BotState
)

def register_owner_handlers(
    bot: telebot.TeleBot,
    owner_only_decorator: Callable,
    command_handler_map_ref: Dict
):
    if not settings.ENABLE_OWNER_FEATURES:
        logging.info("Owner features are disabled by settings.")
        return

    @bot.message_handler(commands=['addtrusted'])
    @owner_only_decorator
    def add_trusted_command_handler(message: Message):
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()):
            return
        user_id_of_caller = message.from_user.id
        caller_s_data = get_user_data(user_id_of_caller)

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_addtrusted_parse_error'))
            return
        try:
            target_user_id = int(parts[1])
        except ValueError:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_addtrusted_parse_error'))
            return

        try:
            if target_user_id in settings.TRUSTED_USERS_SET:
                bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_addtrusted_already', target_user_id=target_user_id))
            else:
                add_trusted_user_db(target_user_id)
                settings._trusted_users_set_cache = None
                if target_user_id in settings.TRUSTED_USERS_SET:
                    check_and_unblock_if_trusted(target_user_id, settings.TRUSTED_USERS_SET)
                    logging.info(f"Owner {user_id_of_caller} added user {target_user_id} to trusted list (DB and runtime).")
                    bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_addtrusted_success', target_user_id=target_user_id))
                else:
                    logging.error(f"Failed to verify user {target_user_id} in TRUSTED_USERS_SET after DB add.")
                    bot.reply_to(message, "Error verifying trusted status after update.")
        except telebot.apihelper.ApiTelegramException as e_reply:
            logging.error(f"API error sending reply in add_trusted_command: {e_reply}")
        except Exception as e:
            logging.error(f"Unexpected error in add_trusted_command: {e}", exc_info=True)
    command_handler_map_ref['/addtrusted'] = add_trusted_command_handler

    @bot.message_handler(commands=['removetrusted'])
    @owner_only_decorator
    def remove_trusted_command_handler(message: Message):
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()):
            return
        user_id_of_caller = message.from_user.id
        caller_s_data = get_user_data(user_id_of_caller)

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_removetrusted_parse_error'))
            return
        try:
            target_user_id = int(parts[1])
        except ValueError:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_removetrusted_parse_error'))
            return

        try:
            if target_user_id == settings.BOT_OWNER_USER_ID:
                bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_removetrusted_is_owner', target_user_id=target_user_id))
                return
            if target_user_id in settings.TRUSTED_USERS_SET:
                remove_trusted_user_db(target_user_id)
                settings._trusted_users_set_cache = None
                if target_user_id not in settings.TRUSTED_USERS_SET:
                    logging.info(f"Owner {user_id_of_caller} removed user {target_user_id} from trusted list (DB and runtime).")
                    bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_removetrusted_success', target_user_id=target_user_id))
                else:
                    logging.error(f"Failed to verify user {target_user_id} removal from TRUSTED_USERS_SET after DB remove.")
                    bot.reply_to(message, "Error verifying trusted status after update.")
            else:
                bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_removetrusted_not_found', target_user_id=target_user_id))
        except telebot.apihelper.ApiTelegramException as e_reply:
            logging.error(f"API error sending reply in remove_trusted_command: {e_reply}")
        except Exception as e:
            logging.error(f"Unexpected error in remove_trusted_command: {e}", exc_info=True)
    command_handler_map_ref['/removetrusted'] = remove_trusted_command_handler

    @bot.message_handler(commands=['ban'])
    @owner_only_decorator
    def ban_user_command_handler(message: Message):
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()): return
        caller_id = message.from_user.id
        caller_s_data = get_user_data(caller_id)

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_ban_parse_error', default="Usage: /ban <user_id>"))
            return
        try:
            target_user_id = int(parts[1])
        except ValueError:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_ban_parse_error', default="Usage: /ban <user_id>"))
            return

        try:
            if target_user_id == settings.BOT_OWNER_USER_ID:
                bot.reply_to(message, get_translation(language=caller_s_data.language, key='cannot_ban_owner', default="Cannot ban the bot owner."))
                return
            if target_user_id == caller_id:
                bot.reply_to(message, get_translation(language=caller_s_data.language, key='cannot_ban_self', default="You cannot ban yourself."))
                return

            existing_block_info = get_blocked_user_info_db(target_user_id)
            if existing_block_info and existing_block_info[1] == -1 and existing_block_info[0] > datetime.now(timezone.utc):
                already_banned_msg = get_translation(language=caller_s_data.language, key='owner_cmd_user_already_banned', default="User {target_user_id} is already manually banned.", target_user_id=target_user_id)
                bot.reply_to(message, already_banned_msg)
                return
            
            blocked_until = datetime.now(timezone.utc) + timedelta(days=365 * 100)
            block_user_db(target_user_id, blocked_until, violations=-1)

            target_user_s_data = get_user_data(target_user_id)
            target_user_s_data.blocked_until_timestamp = blocked_until
            target_user_s_data.violations = -1
            target_user_s_data.state = BotState.NONE
            target_user_s_data.reset_chat_histories()
            
            logging.info(f"Owner {caller_id} manually banned user {target_user_id} permanently.")
            banned_success_msg = get_translation(language=caller_s_data.language, key='owner_cmd_ban_success', default="User {target_user_id} has been banned.", target_user_id=target_user_id)
            bot.reply_to(message, banned_success_msg)
        except Exception as e:
            logging.error(f"Error in ban_user_command: {e}", exc_info=True)
            error_msg = get_translation(language=caller_s_data.language, key='owner_cmd_ban_error', default="An error occurred while trying to ban the user.")
            bot.reply_to(message, error_msg)
    command_handler_map_ref['/ban'] = ban_user_command_handler

    @bot.message_handler(commands=['unban'])
    @owner_only_decorator
    def unban_user_command_handler(message: Message):
        if hasattr(bot, 'BOT_START_TIME_REFERENCE') and message.date < int(bot.BOT_START_TIME_REFERENCE.timestamp()): return
        caller_id = message.from_user.id
        caller_s_data = get_user_data(caller_id)

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_unban_parse_error', default="Usage: /unban <user_id>"))
            return
        try:
            target_user_id = int(parts[1])
        except ValueError:
            bot.reply_to(message, get_translation(language=caller_s_data.language, key='owner_cmd_unban_parse_error', default="Usage: /unban <user_id>"))
            return

        try:
            existing_block_info = get_blocked_user_info_db(target_user_id)
            if not existing_block_info or existing_block_info[0] < datetime.now(timezone.utc):
                not_banned_msg = get_translation(language=caller_s_data.language, key='owner_cmd_user_not_banned', default="User {target_user_id} is not currently effectively banned.", target_user_id=target_user_id)
                bot.reply_to(message, not_banned_msg)
                unblock_user_db(target_user_id)
                if target_user_id in user_data_store:
                    user_data_store[target_user_id].unblock()
                return

            target_user_s_data = get_user_data(target_user_id)
            target_user_s_data.unblock()
            
            logging.info(f"Owner {caller_id} unbanned user {target_user_id}.")
            unbanned_success_msg = get_translation(language=caller_s_data.language, key='owner_cmd_unban_success', default="User {target_user_id} has been unbanned.", target_user_id=target_user_id)
            bot.reply_to(message, unbanned_success_msg)
        except Exception as e:
            logging.error(f"Error in unban_user_command: {e}", exc_info=True)
            error_msg = get_translation(language=caller_s_data.language, key='owner_cmd_unban_error', default="An error occurred while trying to unban the user.")
            bot.reply_to(message, error_msg)
    command_handler_map_ref['/unban'] = unban_user_command_handler

    if settings.ENABLE_OWNER_FEATURES:
        logging.info("Owner handlers registered.")
    else:
        logging.info("Owner handlers are disabled by settings.")