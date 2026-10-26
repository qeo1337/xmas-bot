"""Клиент Bot API Яндекс Мессенджера (проверенные вызовы из документации bp.mssngr).

Идентификация пользователя: staff login из update (`from.login`) — он и есть username.
Числовой id берём из `from.id`.
"""

import os
import requests

BASE = "https://bp.mssngr.yandex.net/public/bot/v1/"


def _ca_bundle():
    """Внутренние хосты используют Yandex Internal CA — берём известные пути."""
    env_ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    candidates = [env_ca] if env_ca else []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(os.path.join(local_appdata, "Stefania", "certs", "YandexInternalCA.pem"))
    candidates.append("/etc/ssl/certs/YandexInternalCA.pem")
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return True


class YimClient:
    def __init__(self, token: str):
        self.token = token
        self.auth = {"Authorization": f"OAuthTeam {token}"}

    def _request(self, method: str, path: str, params: dict | None = None, json_data: dict | None = None) -> dict:
        resp = requests.request(
            method, BASE + path,
            headers={**self.auth, "Content-Type": "application/json"},
            params=params, json=json_data, timeout=30,
            verify=_ca_bundle())
        resp.raise_for_status()
        return resp.json()

    # --- методы Bot API ---

    def get_updates(self, limit: int = 50, offset: int | None = None):
        params = {"limit": limit}
        if offset:
            params["offset"] = offset
        data = self._request("GET", "messages/getUpdates", params=params)
        return data.get("updates", [])

    def send_text_login(self, login: str, text: str) -> bool:
        """Личное сообщение по staff login. Получатель обязан первым написать боту."""
        try:
            res = self._request("POST", "messages/sendText",
                                json_data={"login": login, "text": text})
            return bool(res.get("ok"))
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            raise RuntimeError(f"sendText -> login={login} failed: HTTP {code}") from e

    def send_text_chat(self, chat_id: str, text: str) -> bool:
        try:
            res = self._request("POST", "messages/sendText",
                                json_data={"chat_id": chat_id, "text": text})
            return bool(res.get("ok"))
        except requests.HTTPError as e:
            raise RuntimeError(f"sendText -> chat={chat_id} failed") from e

    def self_get(self) -> dict:
        return self._request("GET", "self/get")
