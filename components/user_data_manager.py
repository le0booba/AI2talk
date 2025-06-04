# components/user_data_manager.py
import logging
import os
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum, auto
from datetime import datetime, timezone, timedelta
import sqlite3
import json # Добавлен импорт json для сериализации mistral_chat_history и flux_data

from .settings_config import settings

DB_FILE_PATH = os.path.join("data", "user_data.sqlite")
os.makedirs(os.path.dirname(DB_FILE_PATH), exist_ok=True)

class BotState(Enum):
    NONE = auto()
    GEMINI_MODE = auto()
    FLUX_PROMPT = auto()
    FLUX_DIMENSIONS = auto()
    MISTRAL_MODE = auto()
    WAITING_FOR_FORWARD = auto()
    DONATE_CUSTOM_AMOUNT_INPUT = auto()

@dataclass
class UserPersistentSettings:
    language: str = field(default=settings.DEFAULT_LANGUAGE)
    gemini_model: str = field(default_factory=lambda: settings.DEFAULT_GEMINI_MODEL)

@dataclass
class UserData:
    user_id: int
    persistent_settings: UserPersistentSettings = field(default_factory=UserPersistentSettings)
    state: BotState = field(default=BotState.NONE)
    gemini_chat: Optional[Any] = field(default=None, repr=False)
    requests_timestamps: List[datetime] = field(default_factory=list)
    last_request_timestamp: Optional[datetime] = None
    violations: int = 0
    blocked_until_timestamp: Optional[datetime] = None
    session_start_timestamp: Optional[datetime] = None
    mistral_chat_history: List[Dict[str, str]] = field(default_factory=list)
    last_rate_request_timestamp: Optional[datetime] = None
    flux_data: Dict[str, Any] = field(default_factory=dict)
    custom_donation_prompt_msg_id: Optional[int] = None
    processed_media_group_ids: Set[str] = field(default_factory=set)

    @property
    def language(self) -> str:
        return self.persistent_settings.language

    @language.setter
    def language(self, value: str):
        if self.persistent_settings.language != value:
            self.persistent_settings.language = value
            _save_user_persistent_settings_to_db(self.user_id, self.persistent_settings)

    @property
    def gemini_model(self) -> str:
        return self.persistent_settings.gemini_model

    @gemini_model.setter
    def gemini_model(self, value: str):
        if self.persistent_settings.gemini_model != value:
            self.persistent_settings.gemini_model = value
            _save_user_persistent_settings_to_db(self.user_id, self.persistent_settings)

    def reset_chat_histories(self):
        self.gemini_chat = None
        if self.mistral_chat_history: # Проверяем, есть ли что очищать
            self.mistral_chat_history.clear()
            # Не вызываем mark_user_data_dirty здесь, т.к. это не персистентные данные

    def clear_flux_data(self):
        if self.flux_data:
            self.flux_data.clear()
            # Не вызываем mark_user_data_dirty

    def clear_custom_donation_prompt(self):
        if self.custom_donation_prompt_msg_id is not None:
            self.custom_donation_prompt_msg_id = None
            # Не вызываем mark_user_data_dirty

    def reset_session_specific_data(self):
        changed = False
        if self.state != BotState.NONE:
            self.state = BotState.NONE
            changed = True
        self.reset_chat_histories()
        if self.session_start_timestamp is not None:
            self.session_start_timestamp = None
            changed = True
        self.clear_flux_data()
        self.clear_custom_donation_prompt()
        if self.processed_media_group_ids:
            self.processed_media_group_ids.clear()
            changed = True
        # if changed: # Если бы эти данные были персистентны и требовали флага
        # mark_user_data_dirty()

    def unblock(self): # Этот метод меняет персистентные данные в БД
        self.blocked_until_timestamp = None
        self.violations = 0
        unblock_user_db(self.user_id) # Запись в БД


user_data_store: Dict[int, UserData] = {}

def _init_db():
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.executescript('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT NOT NULL,
                    gemini_model TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trusted_users (
                    user_id INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    blocked_until_iso TEXT,
                    violations INTEGER DEFAULT 0
                );
            ''')
            conn.commit()
        logging.info(f"Database initialized successfully at {DB_FILE_PATH}")
    except sqlite3.Error as e:
        logging.error(f"Error initializing database: {e}", exc_info=True)
        raise

def _load_user_persistent_settings_from_db(user_id: int) -> Optional[UserPersistentSettings]:
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT language, gemini_model FROM user_settings WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return UserPersistentSettings(language=row[0], gemini_model=row[1]) if row else None
    except sqlite3.Error as e:
        logging.error(f"Error loading persistent settings for user {user_id} from DB: {e}", exc_info=True)
        return None

def _save_user_persistent_settings_to_db(user_id: int, p_settings: UserPersistentSettings):
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO user_settings (user_id, language, gemini_model)
                VALUES (?, ?, ?)
            ''', (user_id, p_settings.language, p_settings.gemini_model))
            conn.commit()
        logging.debug(f"Saved persistent settings for user {user_id} to DB.")
    except sqlite3.Error as e:
        logging.error(f"Error saving persistent settings for user {user_id} to DB: {e}", exc_info=True)

def add_trusted_user_db(user_id: int):
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO trusted_users (user_id) VALUES (?)", (user_id,))
            conn.commit()
        logging.info(f"User {user_id} added to trusted_users table in DB.")
    except sqlite3.Error as e:
        logging.error(f"Error adding trusted user {user_id} to DB: {e}", exc_info=True)

def remove_trusted_user_db(user_id: int):
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trusted_users WHERE user_id = ?", (user_id,))
            conn.commit()
        logging.info(f"User {user_id} removed from trusted_users table in DB.")
    except sqlite3.Error as e:
        logging.error(f"Error removing trusted user {user_id} from DB: {e}", exc_info=True)

def get_all_trusted_users_db() -> Set[int]:
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM trusted_users")
            return {row[0] for row in cursor.fetchall()}
    except sqlite3.Error as e:
        logging.error(f"Error fetching all trusted users from DB: {e}", exc_info=True)
        return set()

def block_user_db(user_id: int, blocked_until: Optional[datetime], violations: int):
    blocked_until_iso = blocked_until.isoformat() if blocked_until else None
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO blocked_users (user_id, blocked_until_iso, violations)
                VALUES (?, ?, ?)
            ''', (user_id, blocked_until_iso, violations))
            conn.commit()
        logging.info(f"User {user_id} DB record updated: blocked until {blocked_until_iso} with {violations} violations.")
    except sqlite3.Error as e:
        logging.error(f"Error blocking/updating user {user_id} in DB: {e}", exc_info=True)

def unblock_user_db(user_id: int):
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
            conn.commit()
            logging.info(f"User {user_id} unblocked in DB (record deleted).")
    except sqlite3.Error as e:
        logging.error(f"Error unblocking user {user_id} in DB: {e}", exc_info=True)

def get_blocked_user_info_db(user_id: int) -> Optional[Tuple[Optional[datetime], int]]:
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT blocked_until_iso, violations FROM blocked_users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                blocked_until_dt = None
                if row[0]:
                    try:
                        blocked_until_dt = datetime.fromisoformat(row[0])
                    except ValueError:
                        logging.error(f"Error parsing date for blocked user {user_id} from DB: {row[0]}")
                return blocked_until_dt, row[1]
        return None
    except sqlite3.Error as e:
        logging.error(f"Error fetching blocked user info for {user_id} from DB: {e}", exc_info=True)
        return None

def get_all_blocked_users_info_db() -> Dict[int, Dict[str, Any]]:
    blocked_users_info: Dict[int, Dict[str, Any]] = {}
    try:
        with sqlite3.connect(DB_FILE_PATH, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, blocked_until_iso, violations FROM blocked_users")
            rows = cursor.fetchall()
            now_utc = datetime.now(timezone.utc)
            for row_data in rows:
                user_id, blocked_until_iso, violations_val = row_data
                unblock_time_utc = None
                if blocked_until_iso:
                    try:
                        unblock_time_utc = datetime.fromisoformat(blocked_until_iso)
                        # Убедимся, что время в UTC, если нет информации о таймзоне
                        if unblock_time_utc.tzinfo is None:
                             unblock_time_utc = unblock_time_utc.replace(tzinfo=timezone.utc)

                    except ValueError:
                        logging.error(f"Error parsing date for blocked user {user_id} from DB: {blocked_until_iso}")
                        continue # Пропускаем эту запись, если дата некорректна

                if unblock_time_utc is None or unblock_time_utc > now_utc : # Либо вечный бан, либо еще не истек
                    blocked_users_info[user_id] = {
                        "unblock_time": unblock_time_utc, # Может быть None для вечного бана
                        "violations": violations_val
                    }
                # Можно добавить логику удаления истекших записей из БД здесь, если нужно
        return blocked_users_info
    except sqlite3.Error as e:
        logging.error(f"Error fetching all blocked users info from DB: {e}", exc_info=True)
        return {}

def check_and_unblock_if_trusted(user_id: int, current_trusted_set: Set[int]):
    if user_id not in current_trusted_set:
        return

    ud = user_data_store.get(user_id)
    db_block_info = get_blocked_user_info_db(user_id)

    if ud and ud.blocked_until_timestamp:
        logging.info(f"User {user_id} is trusted. Removing active block from cache.")
        ud.blocked_until_timestamp = None
        ud.violations = 0
        # Также удаляем из БД, если он там есть
        if db_block_info:
            unblock_user_db(user_id)
    elif db_block_info: # Блокировка есть в БД, но не в кэше (или кэш еще не создан)
        now_utc = datetime.now(timezone.utc)
        unblock_time = db_block_info[0]
        if unblock_time is None or unblock_time > now_utc: # Вечный или активный бан в БД
            logging.info(f"User {user_id} is trusted. Removing block from DB.")
            unblock_user_db(user_id)


def get_user_data(user_id: int) -> UserData:
    if user_id in user_data_store:
        # Периодически проверяем, не стал ли пользователь доверенным, пока был в кэше
        check_and_unblock_if_trusted(user_id, settings.TRUSTED_USERS_SET)
        return user_data_store[user_id]

    p_settings = _load_user_persistent_settings_from_db(user_id)
    if not p_settings:
        p_settings = UserPersistentSettings()
        _save_user_persistent_settings_to_db(user_id, p_settings)
        logging.info(f"Created and saved default persistent settings for new user_id: {user_id}.")

    ud_kwargs = {
        'user_id': user_id,
        'persistent_settings': p_settings
    }

    if user_id not in settings.TRUSTED_USERS_SET:
        blocked_info = get_blocked_user_info_db(user_id)
        if blocked_info:
            blocked_until_dt, violations_count = blocked_info
            now_utc = datetime.now(timezone.utc)
            if blocked_until_dt is None or blocked_until_dt > now_utc: # Вечный бан или активный
                ud_kwargs.update({
                    'blocked_until_timestamp': blocked_until_dt,
                    'violations': violations_count
                })
                logging.info(f"Loaded active block for user {user_id} until {blocked_until_dt} with {violations_count} violations.")
            else: # Блокировка истекла
                unblock_user_db(user_id)
                logging.info(f"Found expired block for user {user_id} in DB, removing.")
    else: # Пользователь доверенный, убедимся, что он не заблокирован в БД
        unblock_user_db(user_id)

    new_user_data = UserData(**ud_kwargs)
    user_data_store[user_id] = new_user_data
    logging.info(f"Initialized UserData for user_id: {user_id} from DB or defaults.")
    return new_user_data

try:
    _init_db()
except sqlite3.Error:
    logging.critical("Failed to initialize the database. Bot functionality might be impaired.")

# Функции periodic_save и save_user_data для JSON здесь больше не нужны, если данные хранятся только в SQLite
# и оперативно обновляются при изменении.
# atexit.register(save_user_data) также не нужен в этом контексте.
# Если вы хотите периодически делать бэкап всей SQLite БД, это отдельная задача.