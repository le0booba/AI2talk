# components/localization.py
import logging
from typing import Dict, Optional, Any, Callable
from collections import defaultdict

from .settings_config import settings

WELCOME_BASE_STRINGS = {
    'en': "✨ Hello! ✨\nI'm AI Helper, ready to assist you.\n\nHere's what I can do:\n",
    'ru': "✨ Привет! ✨\nЯ - бот для взаимодействия с ИИ.\n\nВот что я умею:\n"
}

WELCOME_FEATURE_LINES = {
    'gemini': {
        'en': "💡 Gemini chat mode - /gemini",
        'ru': "💡 Чат с Gemini - /gemini"
    },
    'flux': {
        'en': "🎨 Generate images with FLUX - /flux",
        'ru': "🎨 Генерация изображений с FLUX - /flux"
    },
    'mistral': {
        'en': "📡 Mistral chat mode - /mistral",
        'ru': "📡 Чат с Mistral - /mistral"
    },
    'rate': {
        'en': "💹 Currency rates - /rate",
        'ru': "💹 Курсы валют - /rate"
    },
    'getid': {
        'en': "🆔 Get Telegram ID - /getid",
        'ru': "🆔 Узнать Telegram ID - /getid"
    }
}

WELCOME_COMMON_LINES = {
    'en': (
        "🌐 Change language - /lang \n"
        "ℹ Show limits and status - /user \n"
        "💸 Support the bot with Stars ⭐️ - /donate \n"
        "🤝 Payment support info - /paysupport"
    ),
    'ru': (
        "🌐 Сменить язык - /lang\n"
        "ℹ Показать лимиты и статус - /user\n"
        "💸 Поддержать звёздами ⭐️ - /donate\n"
        "🤝 Информация о поддержке платежей - /paysupport"
    )
}


LANGUAGES: Dict[str, Dict[str, Any]] = {
    'en': {
        'gemini_mode': (
            "💬 You are now in Gemini chat mode.\n"
            "Ask your question.\n\n"
            "⚙️ Model: {model}\n\n"
            "🔧 Gemini options - /gemini_menu \n"
            "🔄 Start a new chat - /new_gemini_chat \n\n"
            "🚪 To quit chat mode - just send any other command or use the button below."
        ),
        'error_fetch_rates': "Failed to get currency exchange rates. Please try again later. Error: {error}",
        'error_gemini_processing': "Error processing Gemini: {error}",
        'gemini_menu_title': "Gemini options:",
        'select_model': "Select Gemini model:",
        'model_set': "Gemini model set to: {model}",
        'invalid_model': "Error: Invalid model.",
        'select_language': "Select language:",
        'new_chat': "Started a new conversation with Gemini.",
        'language_set': "Language set to: {selected_lang}",
        'rate_limit_daily': "Sorry, you've reached your daily limit of {limit} requests. Try again tomorrow.",
        'rate_limit_total_daily': "Sorry, the bot has reached its daily limit of {limit} requests. Try again tomorrow.",
        'rate_limit_minute': "Too many requests. Please wait before sending more requests.",
        'rate_limit_cooldown': "Please wait {seconds} seconds between requests.",
        'user_blocked': "You have been blocked due to rate limit violations. Try again in {hours} hours.\n\n*Alternatively, any donation via /donate grants immediate unlimited access.*",
        'session_expired': "Your session has expired. Starting a new conversation.",
        'change_model_button': "Change model",
        'new_chat_button': "New chat",
        'flux_mode': "🖼️ You are now in FLUX image generation mode. Describe the image you want to generate.\n🖌️ *English only*\n\n🚪 Use the button below to exit this mode.",
        'flux_generating': "_⏳ Generating your image, please wait..._",
        'flux_error': "Error generating image: {error}",
        'flux_dimensions_prompt': "Please select image dimensions:\n\n🚪 Use the button below to exit this mode.",
        'flux_reenter_prompt_button': "Re-enter Prompt",
        'flux_generate_more': "Generate more - /flux",
        'mistral_mode': "📡 You are in chat mode with Mistral. Ask your question.\n\n🔄 Start a new chat - /new_mistral_chat \n\n🚪 To quit chat mode - just send any other command or use the button below.",
        'error_mistral_processing': "Error processing Mistral: {error}",
        'new_mistral_chat': "Started a new conversation with Mistral.",
        'error_currency_rates': "An error occurred while getting currency rates. Please try again later.",
        'trusted_user': "You are: *Trusted User*\n\nUse without limitations.",
        'not_in_trusted': (
            "Limitations:\n"
            "- Maximum {max_requests_per_day} requests per day.\n"
            "- Maximum {max_requests_per_minute} requests per minute.\n"
            "- {cooldown_seconds} seconds cooldown between requests.\n"
            "- You have {violations} out of {max_violations} allowed limit violations before a {block_duration_hours}-hour block."
        ),
        'donate_info': "🎉 You can support the bot by donating Telegram Stars! 🫰\n\n*Any donation grants you unlimited access to the bot's features.*\n\nSelect an amount below or choose 'Custom Amount'.\nDonations are voluntary. Thank you for your support!",
        'donate_invoice_title': "Bot Donation",
        'donate_invoice_description': "Voluntary donation to support the AI Helper Bot.",
        'payment_success': "🥳 Payment successful! Thank you for donating {amount}⭐️! You now have unlimited access. 🤗",
        'payment_support_info': (
            "ℹ️ Payment Information & Support:\n\n"
            "Payments for donations are made using Telegram Stars ⭐️\n"
            "Refund Policy: Donations are generally non-refundable. "
            "If you encounter issues during the donation process, please contact support.\n\n"
            "For any payment issues, please contact the {contact_info}\n\nWhen this command is used, a notification to the administrator is sent automatically (you will be contacted)"
        ),
        'unknown_payload': "Payment received, but the original request context could not be found (perhaps the bot restarted). Please contact support using /paysupport.",
        'blocked_generic_alert': "You are currently blocked.",
        'error_setting_model_alert': "An error occurred setting the model.",
        'invalid_language_alert': "Error: Invalid language.",
        'processing_message': "_⏳ Processing..._",
        'error_init_gemini': "Error initializing Gemini: {error}",
        'error_start_new_gemini': "Error starting new Gemini chat: {error}",
        'error_executing_command': "Error executing command {command}.",
        'error_session_reinit_gemini': "Error starting new session after expiry: {error}",
        'code_snippets_title': "*Code Snippets:*",
        'flux_prompt_needed': "Please provide a description for the image, or use the exit button.",
        'rate_limit_alert': "Rate limit exceeded. Please wait.",
        'flux_service_unavailable_alert': "Image generation service unavailable.",
        'flux_session_expired_alert': "Session expired or data missing. Please start again with /flux.",
        'flux_generation_failed': "Image generation failed.",
        'mistral_busy': "The AI service is currently busy, please try again in a moment.",
        'mistral_invalid_response': "Invalid API response structure",
        'mistral_api_request_failed': "API request failed ({error_code})",
        'user_status_usage_today': "*Usage Today:* {used}/{limit}",
        'user_status_blocked_until': "*Status:* Blocked until {datetime}",
        'user_status_rate_cooldown': "/rate command cooldown: {seconds_remaining}s remaining.",
        'unhandled_error_occurred': "An unexpected error occurred. Please report this issue with ID: {trace_id}",
        'donate_error_creating': "Error creating donation request. Please check configuration or try later.",
        'donate_error_unexpected': "An unexpected error occurred trying to initiate donation.",
        'precheckout_failed_invalid_id': "Could not verify payment details (Invalid ID). Please try again or contact support.",
        'precheckout_failed_wrong_amount': "Could not verify payment details (Incorrect amount/currency or amount < 1). Please try again or contact support.",
        'precheckout_blocked_user_error': "Payment cannot be completed while user is blocked.",
        'stop_mode_button': "Exit Current Mode",
        'mode_exited': "✔ You have exited the current mode.",
        'not_in_active_mode_error': "You are not in an active mode that can be exited now.",
        'donate_invalid_amount': "Invalid amount. Please provide a positive number of stars (at least 1).",
        'select_donation_amount': "Select donation amount:",
        'custom_amount_button': "Custom Amount...",
        'enter_custom_amount': "Please enter the number of stars you wish to donate (minimum 1):",
        'cancel_button': "Cancel",
        'donation_cancelled': "Donation cancelled.",
        'getid_choice': "How do you want to get the ID?",
        'getid_your_id_is': "Your Telegram ID: {user_telegram_id}",
        'button_get_my_id': "👤 Get My ID",
        'button_forward_message': "⤵ Get ID from Forwarded Message",
        'forward_prompt_message': "Okay, now forward a message here from the user/channel whose ID you want to find.",
        'getid_sender_id': "Telegram ID of the sender: {sender_id}",
        'not_forwarded': "⚠️ This doesn't seem to be a forwarded message. Please forward a message.",
        'getid_reset': "Ready for the next request. Use /getid again if needed.",
        'flux_selected_dim_confirm': "Selected ⌗ {width}x{height}",
        'back_button': "↩️ Back",
        'owner_cmd_addtrusted_success': "User {target_user_id} added to trusted list.",
        'owner_cmd_addtrusted_already': "User {target_user_id} is already trusted.",
        'owner_cmd_addtrusted_parse_error': "Could not parse user ID. Usage: /addtrusted <user_id>",
        'owner_cmd_removetrusted_success': "User {target_user_id} removed from trusted list.",
        'owner_cmd_removetrusted_not_found': "User {target_user_id} was not in the trusted list.",
        'owner_cmd_removetrusted_is_owner': "Bot owner {target_user_id} cannot be removed from trusted list this way.",
        'owner_cmd_removetrusted_parse_error': "Could not parse user ID. Usage: /removetrusted <user_id>",
        'owner_cmd_ban_parse_error': "Could not parse user ID. Usage: /ban <user_id>",
        'owner_cmd_unban_parse_error': "Could not parse user ID. Usage: /unban <user_id>",
        'owner_cmd_user_already_banned': "User {target_user_id} is already manually banned.",
        'owner_cmd_ban_success': "User {target_user_id} has been banned.",
        'owner_cmd_ban_error': "An error occurred while trying to ban the user.",
        'owner_cmd_user_not_banned': "User {target_user_id} is not currently banned.",
        'owner_cmd_unban_success': "User {target_user_id} has been unbanned.",
        'owner_cmd_unban_error': "An error occurred while trying to unban the user.",
        'cannot_ban_owner': "Cannot ban the bot owner.",
        'cannot_ban_self': "You cannot ban yourself.",
        'permission_denied_owner_only': "This command is only available to the bot owner.",
        'top_gainers_header': "🚀 *Top 5 Gainers (30d vs USD):*",
    },
    'ru': {
        'gemini_mode': (
            "💬 Вы в режиме общения с Gemini.\n"
            "Задайте ваш вопрос.\n\n"
            "⚙️ Модель:  {model}\n\n"
            "🔧 Настройки Gemini - /gemini_menu \n"
            "🔄 Начать новый чат - /new_gemini_chat \n\n"
            "🚪 Для выхода из режима чата - просто отправьте любую другую команду или нажмите кнопку ниже."
        ),
        'error_fetch_rates': "Не удалось получить курсы валют. Пожалуйста, попробуйте позже. Ошибка: {error}",
        'error_gemini_processing': "Ошибка при обработке Gemini: {error}",
        'gemini_menu_title': "Настройки Gemini:",
        'select_model': "Выберите модель Gemini:",
        'model_set': "Модель Gemini установлена на: {model}",
        'invalid_model': "Ошибка: Неверная модель.",
        'new_chat': "Начат новый диалог с Gemini.",
        'select_language': "Выберите язык:",
        'language_set': "Язык установлен на: {selected_lang}",
        'rate_limit_daily': "Извините, вы достигли дневного лимита в {limit} запросов. Попробуйте завтра.",
        'rate_limit_total_daily': "Извините, бот достиг дневного лимита в {limit} запросов. Попробуйте завтра.",
        'rate_limit_minute': "Слишком много запросов. Пожалуйста, подождите перед отправкой новых запросов.",
        'rate_limit_cooldown': "Пожалуйста, подождите {seconds} секунд между запросами.",
        'user_blocked': "Вы были заблокированы из-за нарушений ограничений. Попробуйте снова через {hours} часов.\n\n*В качестве альтернативы, любое пожертвование через команду /donate предоставит немедленный безлимитный доступ.*",
        'session_expired': "Ваша сессия истекла. Начинаю новый диалог.",
        'change_model_button': "Сменить модель",
        'new_chat_button': "Начать новый чат",
        'flux_mode': "🖼️ Вы в режиме генерации изображений FLUX. Опишите изображение, которое хотите создать.\n🖌️ *Только на английском языке*\n\n🚪 Используйте кнопку ниже, чтобы выйти из этого режима.",
        'flux_generating': "_⏳ Генерация изображения, пожалуйста, подождите..._",
        'flux_error': "Ошибка при генерации изображения: {error}",
        'flux_dimensions_prompt': "Пожалуйста, выберите размеры изображения:\n\n🚪 Используйте кнопку ниже, чтобы выйти из этого режима.",
        'flux_reenter_prompt_button': "Ввести запрос заново",
        'flux_generate_more': "Сгенерировать еще - /flux",
        'mistral_mode': "📡 Вы в режиме общения с Mistral. Задайте ваш вопрос.\n\n🔄 Начать новый чат - /new_mistral_chat \n\n🚪 Для выхода из режима чата - просто отправьте любую другую команду или нажмите кнопку ниже.",
        'error_mistral_processing': "Ошибка при обработке Mistral: {error}",
        'new_mistral_chat': "Начат новый диалог с Mistral.",
        'error_currency_rates': "Произошла ошибка при получении курсов валют. Пожалуйста, попробуйте позже.",
        'trusted_user': "Вы: *Доверенный Пользователь*\n\nИспользование без ограничений.",
        'not_in_trusted': (
            "Ограничения:\n"
            "- Максимум {max_requests_per_day} запросов в день.\n"
            "- Максимум {max_requests_per_minute} запросов в минуту.\n"
            "- {cooldown_seconds} секунд ожидания между запросами.\n"
            "- У вас {violations} из {max_violations} допустимых нарушений ограничений до {block_duration_hours}-часовой блокировки."
        ),
        'donate_info': "🎉 Вы можете поддержать бота, пожертвовав Telegram Stars! 🫰\n\n*Любое пожертвование предоставит вам безлимитный доступ к функциям бота.*\n\nВыберите сумму ниже или нажмите 'Другая сумма'.\nПожертвования добровольны. Спасибо за вашу поддержку!",
        'donate_invoice_title': "Пожертвование боту",
        'donate_invoice_description': "Добровольное пожертвование на поддержку AI Helper Bot.",
        'payment_success': "🥳 Платеж успешно выполнен! Спасибо за пожертвование {amount}⭐️! Теперь у вас безлимитный доступ. 🤗",
        'payment_support_info': (
            "ℹ️ Информация и Поддержка Платежей:\n\n"
            "Оплата пожертвований производится с помощью Telegram Stars ⭐️\n"
            "Политика Возврата: Пожертвования обычно не возвращаются. "
            "Если у вас возникли проблемы в процессе пожертвования, пожалуйста, свяжитесь с поддержкой.\n\n"
            "При возникновении проблем с оплатой, свяжитесь с {contact_info}\n\nПри вызове этой команды оповещение администратору отправляется автоматически (с вами свяжутся)"
        ),
        'unknown_payload': "Платеж получен, но исходный контекст запроса не найден (возможно, бот перезапускался). Пожалуйста, свяжитесь с поддержкой через /paysupport.",
        'blocked_generic_alert': "Вы сейчас заблокированы.",
        'error_setting_model_alert': "Произошла ошибка при установке модели.",
        'invalid_language_alert': "Ошибка: Неверный язык.",
        'processing_message': "_⏳ Обработка..._",
        'error_init_gemini': "Ошибка инициализации Gemini: {error}",
        'error_start_new_gemini': "Ошибка при запуске нового чата Gemini: {error}",
        'error_executing_command': "Ошибка выполнения команды {command}.",
        'error_session_reinit_gemini': "Ошибка запуска новой сессии после истечения срока: {error}",
        'code_snippets_title': "*Фрагменты кода:*",
        'flux_prompt_needed': "Пожалуйста, предоставьте описание для изображения или используйте кнопку выхода.",
        'rate_limit_alert': "Превышен лимит запросов. Пожалуйста, подождите.",
        'flux_service_unavailable_alert': "Сервис генерации изображений недоступен.",
        'flux_session_expired_alert': "Сессия истекла или данные отсутствуют. Пожалуйста, начните снова с /flux.",
        'flux_generation_failed': "Ошибка генерации изображения.",
        'mistral_busy': "Сервис ИИ сейчас занят, попробуйте еще раз через мгновение.",
        'mistral_invalid_response': "Неверная структура ответа API",
        'mistral_api_request_failed': "Ошибка запроса к API ({error_code})",
        'user_status_usage_today': "*Использовано сегодня:* {used}/{limit}",
        'user_status_blocked_until': "*Статус:* Заблокирован до {datetime}",
        'user_status_rate_cooldown': "Кулдаун команды /rate: осталось {seconds_remaining} сек.",
        'unhandled_error_occurred': "Произошла непредвиденная ошибка. Пожалуйста, сообщите об этой проблеме с ID: {trace_id}",
        'donate_error_creating': "Ошибка создания запроса на пожертвование. Проверьте конфигурацию или попробуйте позже.",
        'donate_error_unexpected': "Произошла непредвиденная ошибка при попытке инициировать пожертвование.",
        'precheckout_failed_invalid_id': "Не удалось проверить детали платежа (Неверный ID). Попробуйте еще раз или свяжитесь с поддержкой.",
        'precheckout_failed_wrong_amount': "Не удалось проверить детали платежа (Неверная сумма/валюта или сумма < 1). Попробуйте еще раз или свяжитесь с поддержкой.",
        'precheckout_blocked_user_error': "Платеж не может быть выполнен, пока пользователь заблокирован.",
        'stop_mode_button': "Выйти из режима",
        'mode_exited': "✔ Вы вышли из текущего режима.",
        'not_in_active_mode_error': "Вы не находитесь в активном режиме, из которого можно сейчас выйти.",
        'donate_invalid_amount': "Неверная сумма. Пожалуйста, введите положительное число звёзд (минимум 1).",
        'select_donation_amount': "Выберите сумму пожертвования:",
        'custom_amount_button': "Другая сумма...",
        'enter_custom_amount': "Пожалуйста, введите количество звёзд для пожертвования (минимум 1):",
        'cancel_button': "Отмена",
        'donation_cancelled': "Пожертвование отменено.",
        'getid_choice': "Как вы хотите получить ID?",
        'getid_your_id_is': "Ваш Telegram ID: {user_telegram_id}",
        'button_get_my_id': "👤 Узнать мой ID",
        'button_forward_message': "⤵ ID из пересланного сообщения",
        'forward_prompt_message': "Хорошо, теперь перешлите сюда сообщение от пользователя/канала, ID которого вы хотите узнать.",
        'getid_sender_id': "Telegram ID отправителя: {sender_id}",
        'not_forwarded': "⚠️ Это сообщение не похоже на пересланное. Пожалуйста, перешлите сообщение.",
        'getid_reset': "Готов к следующему запросу. Используйте /getid снова, если нужно.",
        'flux_selected_dim_confirm': "Выбрано ⌗ {width}x{height}",
        'back_button': "↩️ Назад",
        'owner_cmd_addtrusted_success': "Пользователь {target_user_id} добавлен в доверенные.",
        'owner_cmd_addtrusted_already': "Пользователь {target_user_id} уже в списке доверенных.",
        'owner_cmd_addtrusted_parse_error': "Не удалось распознать ID пользователя. Используйте: /addtrusted <user_id>",
        'owner_cmd_removetrusted_success': "Пользователь {target_user_id} удален из доверенных.",
        'owner_cmd_removetrusted_not_found': "Пользователь {target_user_id} не найден в списке доверенных.",
        'owner_cmd_removetrusted_is_owner': "Владелец бота {target_user_id} не может быть удален из доверенных этим способом.",
        'owner_cmd_removetrusted_parse_error': "Не удалось распознать ID пользователя. Используйте: /removetrusted <user_id>",
        'owner_cmd_ban_parse_error': "Не удалось распознать ID пользователя. Используйте: /ban <user_id>",
        'owner_cmd_unban_parse_error': "Не удалось распознать ID пользователя. Используйте: /unban <user_id>",
        'owner_cmd_user_already_banned': "Пользователь {target_user_id} уже заблокирован вручную.",
        'owner_cmd_ban_success': "Пользователь {target_user_id} был заблокирован.",
        'owner_cmd_ban_error': "Произошла ошибка при попытке заблокировать пользователя.",
        'owner_cmd_user_not_banned': "Пользователь {target_user_id} в данный момент не заблокирован.",
        'owner_cmd_unban_success': "Пользователь {target_user_id} был разблокирован.",
        'owner_cmd_unban_error': "Произошла ошибка при попытке разблокировать пользователя.",
        'cannot_ban_owner': "Нельзя заблокировать владельца бота.",
        'cannot_ban_self': "Вы не можете заблокировать себя.",
        'permission_denied_owner_only': "Эта команда доступна только владельцу бота.",
        'top_gainers_header': "🚀 *Топ-5 по росту (30д к USD):*",
    }
}

_DEFAULT_LANG_DICT = LANGUAGES[settings.DEFAULT_LANGUAGE]

_KEY_DEFAULT_ARGS_GENERATORS: Dict[str, Callable[[Optional[int], Optional[str]], Dict[str, Any]]] = {
    'not_in_trusted': lambda user_violations, _user_gemini_model_arg: {
        'max_requests_per_day': settings.MAX_REQUESTS_PER_USER_PER_DAY,
        'max_requests_per_minute': settings.MAX_REQUESTS_PER_MINUTE,
        'cooldown_seconds': settings.REQUEST_COOLDOWN_SECONDS,
        'violations': user_violations if user_violations is not None else 0,
        'max_violations': settings.LIMIT_VIOLATIONS_BEFORE_BLOCK,
        'block_duration_hours': settings.USER_BLOCK_DURATION_HOURS,
    },
    'welcome': lambda _user_violations_arg, _user_gemini_model_arg: {
        'default_stars': settings.DEFAULT_DONATION_AMOUNT_STARS
    },
    'payment_support_info': lambda _user_violations_arg, _user_gemini_model_arg: {
        'contact_info': settings.BOT_CONTACT_INFO
    },
    'getid_sender_id': lambda _user_violations_arg, _user_gemini_model_arg: {
        'sender_id': 'N/A'
    },
    'getid_your_id_is': lambda _user_violations_arg, _user_gemini_model_arg: {
        'user_telegram_id': 'N/A'
    },
    'gemini_mode': lambda _user_violations_arg, user_gemini_model: {
        'model': user_gemini_model if user_gemini_model is not None else settings.DEFAULT_GEMINI_MODEL
    },
    'user_status_rate_cooldown': lambda _user_violations_arg, _user_gemini_model_arg: {
        'seconds_remaining': 'N/A'
    },
    'unhandled_error_occurred': lambda _user_violations_arg, _user_gemini_model_arg: {
        'trace_id': 'N/A'
    },
}

def _generate_welcome_message(language: str) -> str:
    parts = [WELCOME_BASE_STRINGS.get(language, WELCOME_BASE_STRINGS['en'])]
    if settings.ENABLE_GEMINI_FEATURE and settings.GEMINI_API_KEY:
        parts.append(WELCOME_FEATURE_LINES['gemini'].get(language, WELCOME_FEATURE_LINES['gemini']['en']))
    if settings.ENABLE_FLUX_FEATURE and settings.HF_API_KEY: # Проверяем и ключ API
        parts.append(WELCOME_FEATURE_LINES['flux'].get(language, WELCOME_FEATURE_LINES['flux']['en']))
    if settings.ENABLE_MISTRAL_FEATURE and settings.MISTRAL_API_KEY:
        parts.append(WELCOME_FEATURE_LINES['mistral'].get(language, WELCOME_FEATURE_LINES['mistral']['en']))
    if settings.ENABLE_RATE_FEATURE:
        parts.append(WELCOME_FEATURE_LINES['rate'].get(language, WELCOME_FEATURE_LINES['rate']['en']))
    if settings.ENABLE_GETID_FEATURE:
        parts.append(WELCOME_FEATURE_LINES['getid'].get(language, WELCOME_FEATURE_LINES['getid']['en']))

    parts.append("\n" + WELCOME_COMMON_LINES.get(language, WELCOME_COMMON_LINES['en']))
    return "\n".join(filter(None, parts))


def get_translation(
    language: str,
    key: str,
    *,
    _user_violations: Optional[int] = None,
    _user_gemini_model: Optional[str] = None,
    default: Optional[str] = None,
    **kwargs: Any
) -> str:
    if key == 'welcome':
        return _generate_welcome_message(language).format_map(kwargs)

    lang_dict = LANGUAGES.get(language, _DEFAULT_LANG_DICT)
    translation_template = lang_dict.get(key)

    if translation_template is None:
        translation_template = _DEFAULT_LANG_DICT.get(key)
        if translation_template is None:
            if default is not None:
                translation_template = default
            else:
                logging.warning(f"Missing translation for key '{key}' in language '{language}' and default.")
                return f"Missing translation: {key}"
        else:
            logging.debug(f"Using default language for key '{key}' as it's missing in '{language}'.")

    default_kwargs_from_generator: Dict[str, Any] = {}
    generator = _KEY_DEFAULT_ARGS_GENERATORS.get(key)
    if generator:
        default_kwargs_from_generator = generator(_user_violations, _user_gemini_model)

    final_kwargs_dd = defaultdict(lambda: "", default_kwargs_from_generator)
    final_kwargs_dd.update(kwargs)

    try:
        return str(translation_template).format_map(final_kwargs_dd)
    except KeyError as e_key:
        logging.error(f"KeyError during formatting key '{key}' in language '{language}'. Missing key: {e_key}. Kwargs: {dict(final_kwargs_dd)}", exc_info=True)
        if default is not None and isinstance(default, str):
            try:
                return default.format_map(final_kwargs_dd)
            except Exception as e_default_format:
                 logging.error(f"Error formatting default string for key '{key}': {e_default_format}")
                 return default
        return f"[Formatting Error - Missing Key: {key}]"
    except Exception as e:
        logging.error(f"Unexpected formatting error for key '{key}' in language '{language}'. Kwargs: {dict(final_kwargs_dd)}. Error: {e}", exc_info=True)
        return f"[Formatting Error: {key}]"