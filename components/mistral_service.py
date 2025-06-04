# components/mistral_service.py
import logging
import json
from typing import List, Dict, Optional

import requests

class MistralError(Exception):
    pass

class MistralAPIError(MistralError):
    __slots__ = ('status_code', 'response_text')
    
    def __init__(self, message, status_code=None, response_text=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

class MistralResponseError(MistralError):
    pass


DEFAULT_MISTRAL_MODEL = "mistral-large-latest"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 2048

_session_pool = {}

def _get_session(requests_session: Optional[requests.Session] = None) -> tuple[requests.Session, bool]:
    if requests_session:
        return requests_session, False
    
    session_id = id(requests_session)
    if session_id not in _session_pool:
        _session_pool[session_id] = requests.Session()
    return _session_pool[session_id], True

def send_message_to_mistral(
    api_key: str,
    chat_history: List[Dict[str, str]],
    model_name: str = DEFAULT_MISTRAL_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    requests_session: Optional[requests.Session] = None
) -> str:
    if not api_key:
        logging.error("Mistral API key not provided.")
        raise MistralError("Mistral API key is missing.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    data = {
        "model": model_name,
        "messages": chat_history,
        "temperature": temperature,
        "top_p": 1,
        "max_tokens": max_tokens,
        "stream": False,
        "safe_prompt": False,
    }

    logging.debug(f"Sending request to Mistral API. Model: {model_name}. History length: {len(chat_history)}")
    
    session, should_close = _get_session(requests_session)
    response = None
    
    try:
        response = session.post(
            "https://api.mistral.ai/v1/chat/completions", 
            headers=headers, 
            json=data, 
            timeout=90
        )
        
        if response.status_code == 429:
            response_text = response.text[:200]
            logging.warning(f"Mistral API rate limit hit (429). Response: {response_text}")
            raise MistralAPIError(
                "The Mistral API service is currently busy (rate limit). Please try again in a moment.", 
                status_code=429, 
                response_text=response.text
            )
        
        response.raise_for_status()
        response_json = response.json()
        
    except requests.exceptions.HTTPError as http_err:
        response_text = getattr(http_err.response, 'text', 'N/A')
        status_code = getattr(http_err.response, 'status_code', None)
        logging.error(f"Mistral API HTTPError: {status_code} - {response_text[:500]}", exc_info=True)
        raise MistralAPIError(
            f"Mistral API request failed with status {status_code}.", 
            status_code=status_code, 
            response_text=response_text
        )
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Mistral API RequestException: {req_err}", exc_info=True)
        raise MistralAPIError(f"Mistral API request failed due to a network issue: {req_err}")
    except json.JSONDecodeError as json_err:
        response_text = response.text if response else "N/A"
        logging.error(f"Mistral API JSONDecodeError: {json_err}. Response text: {response_text[:500]}", exc_info=True)
        raise MistralResponseError(f"Failed to decode JSON response from Mistral API: {json_err}")
    except Exception as e:
        logging.error(f"Unexpected error during Mistral API call: {e}", exc_info=True)
        raise MistralError(f"An unexpected error occurred while communicating with Mistral API: {e}")
    finally:
        if should_close and session != requests:
            session.close()

    try:
        choices = response_json.get('choices', [])
        if not choices:
            logging.error(f"Mistral API response missing 'choices' or empty: {response_json}")
            raise MistralResponseError("Invalid Mistral API response structure: 'choices' list is missing or empty.")
        
        message_data = choices[0].get('message', {})
        if not message_data:
            logging.error(f"Mistral API response missing 'message' dict in first choice: {choices[0]}")
            raise MistralResponseError("Invalid Mistral API response structure: 'message' object is missing.")
        
        mistral_text_response = message_data.get('content')
        if mistral_text_response is None:
            logging.error(f"Mistral API response missing 'content' in message: {message_data}")
            raise MistralResponseError("Invalid Mistral API response structure: 'content' is missing from message.")
        
        return mistral_text_response
        
    except (KeyError, TypeError, IndexError) as e:
        logging.error(f"Mistral API response parsing error: {e}. Response JSON: {response_json}", exc_info=True)
        raise MistralResponseError(f"Error parsing Mistral API response: {e}")
    except Exception as e:
        logging.error(f"Unexpected error parsing Mistral API response: {e}. Response JSON: {response_json}", exc_info=True)
        raise MistralResponseError(f"An unexpected error occurred while parsing Mistral API response: {e}")