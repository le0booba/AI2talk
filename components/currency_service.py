import logging
import json
import re
from typing import Dict, Optional, Tuple, Any, Union, NamedTuple, List
from functools import lru_cache
from contextlib import contextmanager

import requests
from pydantic import HttpUrl

from .settings_config import settings

NUMBER_FORMAT_REGEX = re.compile(r"(\d)(?=(\d{3})+(?!\d))")

class CurrencyServiceError(Exception):
    pass

class RateFetchingError(CurrencyServiceError):
    pass

class FiatRates(NamedTuple):
    usd: Optional[float]
    eur: Optional[float]

class CryptoRates(NamedTuple):
    btc_rub: Optional[float]
    eth_rub: Optional[float]
    btc_usd: Optional[float]
    eth_usd: Optional[float]

class CryptoGainer(NamedTuple):
    symbol: str
    name: str
    change_30d: Optional[float]

@contextmanager
def _get_session(provided_session: Optional[requests.Session] = None):
    if provided_session:
        yield provided_session
    else:
        with requests.Session() as session:
            yield session

def _get_rates_from_url(url: Union[str, HttpUrl], params: Optional[Dict[str, Any]] = None,
                       session: Optional[requests.Session] = None) -> Dict[str, Any]:
    with _get_session(session) as active_session:
        try:
            response = active_session.get(str(url), params=params, timeout=10)
            response.raise_for_status()
            if isinstance(response.json(), list):
                return {"data": response.json()} 
            return response.json()
        except requests.exceptions.HTTPError as e:
            logging.error(f"CurrencyService: HTTP error fetching data from {url}: {e.response.status_code} - {e.response.text}")
            raise RateFetchingError(f"HTTP error {e.response.status_code} while fetching rates from {url}.")
        except requests.exceptions.RequestException as e:
            logging.error(f"CurrencyService: Error fetching data from {url}: {e}")
            raise RateFetchingError(f"Network error while fetching rates from {url}: {e}")
        except json.JSONDecodeError as e:
            response_text_snippet = response.text[:200] if 'response' in locals() and hasattr(response, 'text') else 'N/A'
            logging.error(f"CurrencyService: Error decoding JSON from {url}: {e}. Response: {response_text_snippet}")
            raise RateFetchingError(f"Error decoding rates data from {url}.")

@lru_cache(maxsize=32)
def get_fiat_rates(session: Optional[requests.Session] = None) -> FiatRates:
    try:
        data = _get_rates_from_url(settings.CBR_URL, session=session)
        valute = data.get('Valute', {})
        
        usd_data = valute.get('USD', {})
        eur_data = valute.get('EUR', {})
        
        usd_rate = usd_data.get('Value') if isinstance(usd_data.get('Value'), (int, float)) else None
        eur_rate = eur_data.get('Value') if isinstance(eur_data.get('Value'), (int, float)) else None
        
        if not (usd_rate and eur_rate):
            logging.error("CurrencyService: Incorrect CBR data format or missing values for USD/EUR.")
        
        return FiatRates(usd_rate, eur_rate)
    except RateFetchingError as e:
        logging.error(f"CurrencyService: Error processing fiat rates: {e}")
        return FiatRates(None, None)
    except Exception as e:
        logging.error(f"CurrencyService: Unexpected error processing fiat rates: {e}", exc_info=True)
        return FiatRates(None, None)

@lru_cache(maxsize=32)
def get_crypto_rates(session: Optional[requests.Session] = None) -> CryptoRates:
    try:
        params = {"ids": "bitcoin,ethereum", "vs_currencies": "rub,usd"}
        data = _get_rates_from_url(settings.COINGECKO_URL, params=params, session=session)
        
        btc_data = data.get('bitcoin', {})
        eth_data = data.get('ethereum', {})

        btc_rub_rate = btc_data.get('rub')
        eth_rub_rate = eth_data.get('rub')
        btc_usd_rate = btc_data.get('usd')
        eth_usd_rate = eth_data.get('usd')
        
        btc_rub_rate = btc_rub_rate if isinstance(btc_rub_rate, (int, float)) else None
        eth_rub_rate = eth_rub_rate if isinstance(eth_rub_rate, (int, float)) else None
        btc_usd_rate = btc_usd_rate if isinstance(btc_usd_rate, (int, float)) else None
        eth_usd_rate = eth_usd_rate if isinstance(eth_usd_rate, (int, float)) else None
        
        if not ('bitcoin' in data and 'ethereum' in data and \
              all(cur in btc_data for cur in ['rub', 'usd']) and \
              all(cur in eth_data for cur in ['rub', 'usd'])):
            if not (btc_rub_rate and eth_rub_rate and btc_usd_rate and eth_usd_rate): 
                 logging.warning("CurrencyService: CoinGecko data format might be missing expected currency fields or values (rub/usd for btc/eth).")

        return CryptoRates(btc_rub_rate, eth_rub_rate, btc_usd_rate, eth_usd_rate)
    except RateFetchingError as e:
        logging.error(f"CurrencyService: Error processing crypto rates: {e}")
        return CryptoRates(None, None, None, None)
    except Exception as e:
        logging.error(f"CurrencyService: Unexpected error processing crypto rates: {e}", exc_info=True)
        return CryptoRates(None, None, None, None)

@lru_cache(maxsize=4)
def get_top_gainers_crypto(session: Optional[requests.Session] = None, limit: int = 5) -> List[CryptoGainer]:
    gainers = []
    try:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc", 
            "per_page": 100, 
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "30d" 
        }
        if not hasattr(settings, 'COINGECKO_MARKETS_URL'):
            logging.error("CurrencyService: COINGECKO_MARKETS_URL is not defined in settings.")
            return []
            
        raw_data_response = _get_rates_from_url(settings.COINGECKO_MARKETS_URL, params=params, session=session)
        market_data_list = raw_data_response.get("data", [])


        if not isinstance(market_data_list, list):
            logging.error(f"CurrencyService: Expected a list from COINGECKO_MARKETS_URL, got {type(market_data_list)}")
            return []

        for coin_data in market_data_list:
            change_30d = coin_data.get('price_change_percentage_30d_in_currency')
            symbol = coin_data.get('symbol')
            name = coin_data.get('name')

            if change_30d is not None and isinstance(change_30d, (int, float)) and symbol and name:
                gainers.append(CryptoGainer(symbol=symbol.upper(), name=name, change_30d=float(change_30d)))
        
        gainers.sort(key=lambda x: x.change_30d, reverse=True)
        return gainers[:limit]
        
    except RateFetchingError as e:
        logging.error(f"CurrencyService: Error processing top crypto gainers: {e}")
        return []
    except Exception as e:
        logging.error(f"CurrencyService: Unexpected error processing top crypto gainers: {e}", exc_info=True)
        return []

def format_currency_number(number: Optional[Union[float, int]], decimal_places: int = 2) -> str:
    if number is None:
        return "N/A"
    
    try:
        num_val = float(number)
    except (ValueError, TypeError):
        return str(number)

    formatted_num = f"{num_val:.{decimal_places}f}"
    
    if '.' in formatted_num:
        integer_part, decimal_part = formatted_num.rsplit('.', 1)
    else:
        integer_part, decimal_part = formatted_num, ""
    
    formatted_integer = NUMBER_FORMAT_REGEX.sub(r"\1 ", integer_part)
    
    if decimal_places == 0 or not decimal_part or decimal_part == '0' * len(decimal_part):
        return formatted_integer
    
    return f"{formatted_integer}.{decimal_part}"