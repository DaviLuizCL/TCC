# SPDX-License-Identifier: AGPL-3.0-or-later
# alerting.py — disparo de sinal digital (GPIO) no Raspberry Pi

import time
import atexit
from dataclasses import dataclass

# Tenta usar RPi.GPIO; se não houver (desenvolvendo no PC), usa mock que só imprime
try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except Exception:
    _HAS_GPIO = False

    class _MockGPIO:
        BCM = 'BCM'
        BOARD = 'BOARD'
        OUT = 'OUT'
        HIGH = 1
        LOW = 0

        def setmode(self, mode): print(f"[GPIO-MOCK] setmode({mode})")
        def setup(self, pin, mode, initial=None): print(f"[GPIO-MOCK] setup(pin={pin}, mode={mode}, initial={initial})")
        def output(self, pin, value): print(f"[GPIO-MOCK] output(pin={pin}, value={value})")
        def cleanup(self): print("[GPIO-MOCK] cleanup()")

    GPIO = _MockGPIO()


@dataclass
class GpioConfig:
    pin: int = 17
    setup: str = "BCM"          # "BCM" ou "BOARD"
    active_high: bool = True
    mode: str = "pulse"         # "pulse" ou "latch"
    pulse_ms: int = 500         # usado quando mode="pulse"


class GpioAlerter:
    """
    Controla o pino GPIO que sinaliza o alerta.
    - mode='pulse': emite um pulso de duração definida (pulse_ms)
    - mode='latch': mantém o pino no nível ativo ao disparar; desarma manualmente via clear()
    """

    def __init__(self, cfg: GpioConfig):
        self.cfg = cfg
        # Modo de numeração
        if str(cfg.setup).upper() == "BCM":
            GPIO.setmode(GPIO.BCM)
        else:
            GPIO.setmode(GPIO.BOARD)

        initial_level = GPIO.LOW if cfg.active_high else GPIO.HIGH
        GPIO.setup(cfg.pin, GPIO.OUT, initial=initial_level)
        atexit.register(self.cleanup)

    def _level(self, active: bool):
        if self.cfg.active_high:
            return GPIO.HIGH if active else GPIO.LOW
        else:
            return GPIO.LOW if active else GPIO.HIGH

    def trigger(self):
        """Dispara o sinal conforme o modo configurado."""
        if self.cfg.mode.lower() == "pulse":
            GPIO.output(self.cfg.pin, self._level(True))
            time.sleep(max(self.cfg.pulse_ms, 1) / 1000.0)
            GPIO.output(self.cfg.pin, self._level(False))
        elif self.cfg.mode.lower() == "latch":
            GPIO.output(self.cfg.pin, self._level(True))
        else:
            # fallback: pulso curto
            GPIO.output(self.cfg.pin, self._level(True))
            time.sleep(0.2)
            GPIO.output(self.cfg.pin, self._level(False))

    def clear(self):
        """Força o pino ao nível inativo (útil para modo 'latch')."""
        GPIO.output(self.cfg.pin, self._level(False))

    def cleanup(self):
        """Libera recursos do GPIO na saída."""
        try:
            GPIO.cleanup()
        except Exception:
            pass
