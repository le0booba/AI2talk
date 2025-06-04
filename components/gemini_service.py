# components/gemini_service.py
import logging
from typing import Optional, List, Dict
from functools import lru_cache

import google.generativeai as genai
from google.generativeai.generative_models import GenerativeModel, ChatSession
from google.generativeai.types import generation_types

class GeminiError(Exception):
    pass

class GeminiChatError(GeminiError):
    pass

class GeminiBlockedPromptError(GeminiChatError):
    pass


@lru_cache(maxsize=8)
def initialize_model(model_name: str) -> Optional[GenerativeModel]:
    try:
        model = genai.GenerativeModel(model_name)
        logging.info(f"Gemini model '{model_name}' initialized successfully.")
        return model
    except Exception as e:
        logging.error(f"Failed to initialize Gemini model '{model_name}': {e}", exc_info=True)
        return None

def start_new_chat(model: GenerativeModel, history: Optional[List[Dict[str, str]]] = None) -> Optional[ChatSession]:
    if not model:
        logging.error("Cannot start new chat: Gemini model not provided or not initialized.")
        return None
    try:
        chat_session = model.start_chat(history=history or [])
        logging.info("New Gemini chat session started.")
        return chat_session
    except Exception as e:
        logging.error(f"Failed to start new Gemini chat session: {e}", exc_info=True)
        return None

def send_message_to_gemini(chat_session: ChatSession, prompt_text: str) -> str:
    if not chat_session:
        logging.error("Cannot send message: Gemini chat session not provided or not initialized.")
        raise GeminiChatError("Chat session is not active.")
    
    try:
        logging.debug("Sending prompt to Gemini.")
        response = chat_session.send_message(prompt_text)
        return response.text
    except generation_types.BlockedPromptException as bpe:
        logging.warning(f"Gemini blocked prompt: {bpe}")
        raise GeminiBlockedPromptError(str(bpe))
    except Exception as e:
        logging.error(f"Error sending message to Gemini: {e}", exc_info=True)
        raise GeminiChatError(f"Error processing Gemini message: {e}")