# SPDX-License-Identifier: AGPL-3.0-or-later
"""notifier.py — fan-out de alertas para múltiplos canais (push no celular).

Cada canal é independente e isolado: se um falhar (rede, token errado), os
demais continuam funcionando. O envio acontece em threads para não travar o
servidor enquanto espera a rede.

Canais suportados (todos opcionais, ligados/desligados via config.yaml):
  - ntfy      (push grátis, app próprio, auto-hospedável)  [suporta snapshot]
  - telegram  (bot)                                        [suporta snapshot]
  - mqtt      (IoT / Home Assistant)                       [texto]
  - pushover  (app de push)                                [suporta snapshot]
  - gotify    (push self-hosted)                           [texto]
"""

from __future__ import annotations

import threading
import requests

# paho-mqtt é opcional: só é necessário se o canal MQTT estiver habilitado.
try:
    import paho.mqtt.publish as mqtt_publish
    _HAS_MQTT = True
except Exception:
    _HAS_MQTT = False

_HTTP_TIMEOUT = 8  # segundos — nunca deixar um canal pendurar o sistema


class Channel:
    """Canal base. Subclasses implementam send()."""

    name = "base"

    def __init__(self, conf: dict):
        self.conf = conf or {}

    def send(self, title: str, message: str, snapshot: bytes | None = None) -> None:
        raise NotImplementedError


class NtfyChannel(Channel):
    name = "ntfy"

    def send(self, title, message, snapshot=None):
        server = str(self.conf.get("server", "https://ntfy.sh")).rstrip("/")
        topic = self.conf.get("topic")
        if not topic:
            raise ValueError("ntfy: 'topic' não configurado")
        url = f"{server}/{topic}"

        headers = {
            "Title": title,
            "Priority": str(self.conf.get("priority", "urgent")),
            "Tags": str(self.conf.get("tags", "warning,rotating_light")),
        }
        token = self.conf.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if snapshot:
            # Anexa a foto: corpo = imagem; mensagem vai no header.
            headers["Message"] = message
            headers["Filename"] = "snapshot.jpg"
            r = requests.put(url, data=snapshot, headers=headers, timeout=_HTTP_TIMEOUT)
        else:
            r = requests.post(url, data=message.encode("utf-8"),
                              headers=headers, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()


class TelegramChannel(Channel):
    name = "telegram"

    def send(self, title, message, snapshot=None):
        token = self.conf.get("bot_token")
        chat_id = self.conf.get("chat_id")
        if not token or not chat_id:
            raise ValueError("telegram: 'bot_token' e 'chat_id' são obrigatórios")
        base = f"https://api.telegram.org/bot{token}"
        text = f"{title}\n{message}"

        if snapshot:
            r = requests.post(
                f"{base}/sendPhoto",
                data={"chat_id": chat_id, "caption": text},
                files={"photo": ("snapshot.jpg", snapshot, "image/jpeg")},
                timeout=_HTTP_TIMEOUT,
            )
        else:
            r = requests.post(
                f"{base}/sendMessage",
                data={"chat_id": chat_id, "text": text},
                timeout=_HTTP_TIMEOUT,
            )
        r.raise_for_status()


class MqttChannel(Channel):
    name = "mqtt"

    def send(self, title, message, snapshot=None):
        if not _HAS_MQTT:
            raise RuntimeError("paho-mqtt não instalado (pip install paho-mqtt)")
        topic = self.conf.get("topic", "poolguard/alert")
        host = self.conf.get("host", "localhost")
        port = int(self.conf.get("port", 1883))
        qos = int(self.conf.get("qos", 1))

        auth = None
        if self.conf.get("username"):
            auth = {"username": self.conf["username"],
                    "password": self.conf.get("password", "")}

        payload = f"{title} | {message}"
        mqtt_publish.single(
            topic, payload=payload, qos=qos, hostname=host, port=port, auth=auth,
        )


class PushoverChannel(Channel):
    name = "pushover"

    def send(self, title, message, snapshot=None):
        token = self.conf.get("token")
        user = self.conf.get("user")
        if not token or not user:
            raise ValueError("pushover: 'token' e 'user' são obrigatórios")
        data = {
            "token": token,
            "user": user,
            "title": title,
            "message": message,
            "priority": int(self.conf.get("priority", 1)),
        }
        files = {"attachment": ("snapshot.jpg", snapshot, "image/jpeg")} if snapshot else None
        r = requests.post("https://api.pushover.net/1/messages.json",
                          data=data, files=files, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()


class GotifyChannel(Channel):
    name = "gotify"

    def send(self, title, message, snapshot=None):
        server = str(self.conf.get("server", "")).rstrip("/")
        token = self.conf.get("token")
        if not server or not token:
            raise ValueError("gotify: 'server' e 'token' são obrigatórios")
        r = requests.post(
            f"{server}/message",
            params={"token": token},
            json={"title": title, "message": message,
                  "priority": int(self.conf.get("priority", 8))},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()


_REGISTRY = {
    "ntfy": NtfyChannel,
    "telegram": TelegramChannel,
    "mqtt": MqttChannel,
    "pushover": PushoverChannel,
    "gotify": GotifyChannel,
}


class Notifier:
    """Constrói os canais habilitados e dispara para todos em paralelo."""

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.title = self.cfg.get("title", "PoolGuard — ALERTA")
        self.default_message = self.cfg.get(
            "message", "Pessoa detectada na area da piscina!")

        self.channels: list[Channel] = []
        channels_cfg = self.cfg.get("channels", {}) or {}
        for name, conf in channels_cfg.items():
            if not isinstance(conf, dict) or not conf.get("enabled"):
                continue
            cls = _REGISTRY.get(name)
            if cls is None:
                print(f"[NOTIFY] Canal desconhecido ignorado: {name}")
                continue
            self.channels.append(cls(conf))

    def channel_names(self) -> list[str]:
        return [c.name for c in self.channels]

    def dispatch(self, title: str | None = None, message: str | None = None,
                 snapshot: bytes | None = None) -> dict:
        """Dispara para todos os canais em threads. Retorna status por canal."""
        title = title or self.title
        message = message or self.default_message
        results: dict[str, str] = {}
        threads = []

        def _run(ch: Channel):
            try:
                ch.send(title, message, snapshot=snapshot)
                results[ch.name] = "ok"
            except Exception as e:
                results[ch.name] = f"erro: {e}"
                print(f"[NOTIFY] Falha no canal '{ch.name}': {e}")

        for ch in self.channels:
            t = threading.Thread(target=_run, args=(ch,), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=_HTTP_TIMEOUT + 2)

        return results
