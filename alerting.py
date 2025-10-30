from pydantic import BaseModel, Field
from typing import Tuple, List
import requests, time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

class AlertEvent(BaseModel):
    source: str
    timestamp: float = Field(default_factory=lambda: time.time())
    roi_name: str
    persons: int
    boxes: List[Tuple[int, int, int, int]]
    frame_w: int
    frame_h: int
    reason: str  # ex.: "person_in_pool_roi"

def send_http(event: AlertEvent, url: str, timeout: float = 2.0):
    r = requests.post(url, json=event.dict(), timeout=timeout)
    r.raise_for_status()

def send_mqtt(event: AlertEvent, broker: str, port: int, topic: str, qos: int = 1):
    if mqtt is None:
        raise RuntimeError('paho-mqtt não instalado')
    client = mqtt.Client()
    client.connect(broker, port, 60)
    client.loop_start()
    import json
    payload = json.dumps(event.dict())
    client.publish(topic, payload, qos=qos)
    client.loop_stop()
    client.disconnect()
