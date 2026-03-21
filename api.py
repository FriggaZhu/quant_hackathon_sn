import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests


def load_dotenv(dotenv_path: str = ".env") -> None:
    env_file = Path(dotenv_path)
    if not env_file.exists():
        return

    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')

        if key:
            os.environ.setdefault(key, value)


load_dotenv()


BASE_URL = os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com")
API_KEY = os.getenv("ROOSTOO_API_KEY", "")
SECRET_KEY = os.getenv("ROOSTOO_SECRET_KEY", "")


class RoostooAPIError(Exception):
    """Raised when the Roostoo API request fails."""


def current_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def build_signature(payload: str, secret_key: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def serialize_params(params: Dict[str, Any]) -> str:
    filtered = {key: value for key, value in params.items() if value is not None}
    return "&".join(f"{key}={filtered[key]}" for key in sorted(filtered.keys()))


class RoostooClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 15,
    ) -> None:
        self.api_key = api_key or API_KEY
        self.secret_key = secret_key or SECRET_KEY
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.timeout = timeout
        self._exchange_info_cache: Optional[Dict[str, Any]] = None

    def _headers(
        self,
        payload: str,
        content_type: Optional[str] = None,
    ) -> Dict[str, str]:
        if not self.api_key or not self.secret_key:
            raise RoostooAPIError(
                "Missing API credentials. Set ROOSTOO_API_KEY and ROOSTOO_SECRET_KEY."
            )

        headers = {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": build_signature(payload, self.secret_key),
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        timestamp = current_timestamp_ms()
        params = params or {}
        data = data or {}

        if method.upper() == "GET":
            payload_dict = {**params, "timestamp": timestamp}
            payload = serialize_params(payload_dict)
            headers = self._headers(payload)
            request_params = payload_dict
            response = requests.get(
                f"{self.base_url}{endpoint}",
                headers=headers,
                params=request_params,
                timeout=self.timeout,
            )
        else:
            body = {**data, "timestamp": timestamp}
            payload = serialize_params(body)
            headers = self._headers(payload, "application/x-www-form-urlencoded")
            response = requests.post(
                f"{self.base_url}{endpoint}",
                headers=headers,
                data=body,
                timeout=self.timeout,
            )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RoostooAPIError(
                f"{method.upper()} {endpoint} failed with status {response.status_code}: "
                f"{response.text}"
            ) from exc

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise RoostooAPIError(
                f"{method.upper()} {endpoint} returned invalid JSON: {response.text}"
            ) from exc

    def get_ticker(self, pair: str) -> Dict[str, Any]:
        return self._request("GET", "/v3/ticker", params={"pair": pair})

    def get_exchange_info(self, refresh: bool = False) -> Dict[str, Any]:
        if self._exchange_info_cache is not None and not refresh:
            return self._exchange_info_cache

        response = requests.get(
            f"{self.base_url}/v3/exchangeInfo",
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RoostooAPIError(
                f"GET /v3/exchangeInfo failed with status {response.status_code}: {response.text}"
            ) from exc

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise RoostooAPIError(
                f"GET /v3/exchangeInfo returned invalid JSON: {response.text}"
            ) from exc

        self._exchange_info_cache = payload
        return payload

    def get_pair_rules(self, pair: str) -> Dict[str, Any]:
        exchange_info = self.get_exchange_info()
        trade_pairs = exchange_info.get("TradePairs", {})
        pair_rules = trade_pairs.get(pair)
        if not isinstance(pair_rules, dict):
            raise RoostooAPIError(f"Pair {pair} not found in exchange info.")
        return pair_rules

    def get_balance(self) -> Dict[str, Any]:
        return self._request("GET", "/v3/balance")

    def place_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v3/place_order",
            data={
                "pair": pair,
                "side": side.upper(),
                "quantity": str(quantity),
                "type": order_type.upper(),
            },
        )
