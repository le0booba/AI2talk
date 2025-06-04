# components/telegram_utils.py
import re
import logging
from typing import List, Optional, Any, Tuple, Callable

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from .settings_config import settings


MARKDOWN_V2_ESCAPE_REGEX = re.compile(r"([*`\[\]()~>#+\-=|{}.!])")
HTML_ESCAPE_MAP = {'&': '&', '<': '<', '>': '>'}

CLEAN_MARKDOWN_CHARS_REGEX = re.compile(r'[*_`~]')
CLEAN_MARKDOWN_LINK_REGEX = re.compile(r'\[([^\]]+)\]\([^)]+\)')
CLEAN_MARKDOWN_HEADER_REGEX = re.compile(r'#+ ')

TRIPLE_BACKTICK = "```"
CODE_TRIPLE_BACKTICK_REGEX = re.compile(re.escape(TRIPLE_BACKTICK))
PREFERRED_SPLIT_CHARS = ('\n', '.', '!', '?', ';', ':', ',', ' ')

EXTRACT_CODE_BLOCK_REGEX = re.compile(r"```(?:[a-zA-Z0-9_+\-#\.]*?\n)?(.*?)```", re.DOTALL)

_bot_instance: Optional[telebot.TeleBot] = None


def init_telegram_utils_bot(bot_instance_param: telebot.TeleBot) -> None:
    global _bot_instance
    _bot_instance = bot_instance_param


def escape_markdown_v2(text: Any) -> str:
    return MARKDOWN_V2_ESCAPE_REGEX.sub(r"\\\1", str(text))


def escape_html_util(text: Any) -> str:
    text_str = str(text)
    return "".join(HTML_ESCAPE_MAP.get(c, c) for c in text_str)


def clean_markdown_text(text: Any) -> str:
    text_str = str(text)
    text_str = CLEAN_MARKDOWN_CHARS_REGEX.sub('', text_str)
    text_str = CLEAN_MARKDOWN_LINK_REGEX.sub(r'\1', text_str)
    text_str = CLEAN_MARKDOWN_HEADER_REGEX.sub('', text_str)
    return text_str


def split_long_text(text: Any, max_length: int = settings.TELEGRAM_MAX_LENGTH) -> List[str]:
    text_str = str(text)
    if not text_str:
        return []

    parts: List[str] = []
    current_pos = 0
    num_backticks_before_current_part = 0

    while current_pos < len(text_str):
        is_inside_code_block = (num_backticks_before_current_part % 2) == 1
        
        proposed_split_at = current_pos + max_length
        
        chunk_search_end = min(len(text_str), proposed_split_at + len(TRIPLE_BACKTICK))
        search_chunk = text_str[current_pos:chunk_search_end]
        
        code_block_indices_in_chunk = [
            m.start() for m in CODE_TRIPLE_BACKTICK_REGEX.finditer(search_chunk)
        ]

        best_split_point = -1

        if is_inside_code_block:
            for idx_in_chunk in code_block_indices_in_chunk:
                actual_idx = current_pos + idx_in_chunk
                if actual_idx + len(TRIPLE_BACKTICK) <= proposed_split_at:
                    best_split_point = actual_idx + len(TRIPLE_BACKTICK)
                    break
            if best_split_point == -1:
                best_split_point = proposed_split_at
        else:
            temp_char_split = -1
            char_search_limit = min(len(text_str), proposed_split_at)

            for char_pref in PREFERRED_SPLIT_CHARS:
                idx_of_char = text_str.rfind(char_pref, current_pos, char_search_limit)
                if idx_of_char != -1:
                    is_safe_split = True
                    for cb_idx_rel in code_block_indices_in_chunk:
                        actual_cb_idx = current_pos + cb_idx_rel
                        if idx_of_char < actual_cb_idx < idx_of_char + 4:
                            is_safe_split = False
                            break
                    if is_safe_split:
                        temp_char_split = idx_of_char + 1
                        break 
            
            earliest_cb_start_abs = -1
            if code_block_indices_in_chunk:
                first_cb_idx_rel = code_block_indices_in_chunk[0]
                actual_first_cb_idx = current_pos + first_cb_idx_rel
                if actual_first_cb_idx < proposed_split_at:
                    earliest_cb_start_abs = actual_first_cb_idx

            if earliest_cb_start_abs != -1:
                if temp_char_split != -1 and temp_char_split < earliest_cb_start_abs:
                    best_split_point = temp_char_split
                else:
                    best_split_point = earliest_cb_start_abs
            elif temp_char_split != -1:
                best_split_point = temp_char_split
            else:
                best_split_point = proposed_split_at
        
        best_split_point = min(best_split_point, len(text_str))

        if best_split_point <= current_pos:
            if proposed_split_at > current_pos:
                 best_split_point = proposed_split_at
            else:
                 best_split_point = current_pos + 1 
            best_split_point = min(best_split_point, len(text_str))

        part_content = text_str[current_pos:best_split_point]
        parts.append(part_content)
        num_backticks_before_current_part += part_content.count(TRIPLE_BACKTICK)
        
        current_pos = best_split_point
        while current_pos < len(text_str) and text_str[current_pos].isspace():
            current_pos += 1

    if parts and (num_backticks_before_current_part % 2 == 1):
        last_part_content = parts[-1]
        if len(last_part_content) + len(TRIPLE_BACKTICK) <= max_length:
            parts[-1] = last_part_content + TRIPLE_BACKTICK
        else:
            parts.append(TRIPLE_BACKTICK)
            
    return [p for p in parts if p.strip()]


def send_message_splitted(chat_id: int, text: str, parse_mode: Optional[str] = None, reply_markup=None, **kwargs) -> None:
    if not _bot_instance:
        logging.error("TelegramUtils: Bot instance not initialized. Cannot send message.")
        return
    if not text:
        logging.warning(f"TelegramUtils: Attempted to send empty message to chat {chat_id}")
        return

    message_parts = split_long_text(text, settings.TELEGRAM_MAX_LENGTH)
    if not message_parts:
        logging.warning(f"TelegramUtils: split_long_text returned empty list for non-empty text to chat {chat_id}. Original text head: {text[:100]}")
        return

    last_index = len(message_parts) - 1
    for i, part in enumerate(message_parts):
        current_reply_markup = reply_markup if i == last_index else None
        try:
            _bot_instance.send_message(chat_id, part, parse_mode=parse_mode, reply_markup=current_reply_markup, **kwargs)
        except telebot.apihelper.ApiTelegramException as e_api:
            logging.error(f"TelegramUtils: Telegram API Error sending part to {chat_id}: {e_api}. Part head: {part[:100]}...")
            if parse_mode:
                logging.info(f"TelegramUtils: Retrying message part to {chat_id} without parse_mode.")
                try:
                    _bot_instance.send_message(chat_id, part, reply_markup=current_reply_markup, **kwargs)
                except telebot.apihelper.ApiTelegramException as fallback_e_api:
                    logging.error(f"TelegramUtils: Fallback send (API) failed for {chat_id}: {fallback_e_api}. Part head: {part[:100]}...")
                except Exception as fallback_e:
                    logging.error(f"TelegramUtils: Fallback send (General) failed for {chat_id}: {fallback_e}. Part head: {part[:100]}...")
        except Exception as e_general:
            logging.error(f"TelegramUtils: Unexpected error sending message part to {chat_id}: {e_general}. Part head: {part[:100]}...")


def get_universal_stop_keyboard(language: str, get_translation_func: Callable) -> InlineKeyboardMarkup:
    stop_button_text = get_translation_func(language=language, key='stop_mode_button')
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton(stop_button_text, callback_data="stop_mode"))
    return keyboard


def extract_code_blocks(text: str) -> Tuple[str, List[str]]:
    extracted_full_blocks: List[str] = []
    last_end = 0
    remaining_text_parts: List[str] = []
    for match in EXTRACT_CODE_BLOCK_REGEX.finditer(text):
        remaining_text_parts.append(text[last_end:match.start()])
        full_block = match.group(0)
        extracted_full_blocks.append(full_block)
        last_end = match.end()
    remaining_text_parts.append(text[last_end:])
    text_without_code = "".join(remaining_text_parts).strip()
    return text_without_code, extracted_full_blocks


def send_code_snippets(
    chat_id: int,
    user_language: str,
    code_blocks: List[str],
    get_translation_func: Callable,
    reply_markup_for_last: Optional[InlineKeyboardMarkup] = None
) -> None:
    if not _bot_instance:
        logging.error("TelegramUtils: Bot instance not initialized for send_code_snippets.")
        return
    if not code_blocks:
        return

    code_title = get_translation_func(language=user_language, key='code_snippets_title')
    try:
        _bot_instance.send_message(chat_id, code_title, parse_mode="Markdown")
    except Exception as e_send_title:
        logging.warning(f"TelegramUtils: Failed to send code snippets title to {chat_id}: {e_send_title}")

    for i, code_block_content in enumerate(code_blocks):
        current_markup = reply_markup_for_last if i == len(code_blocks) - 1 else None
        if len(code_block_content) > settings.MAX_CODE_LENGTH + (len(TRIPLE_BACKTICK) * 2):
             logging.warning(f"Code block for chat {chat_id} is very long ({len(code_block_content)} chars), might be truncated by Telegram.")
        send_message_splitted(chat_id, code_block_content, parse_mode="Markdown", reply_markup=current_markup)