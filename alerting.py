# SPDX-License-Identifier: AGPL-3.0-or-later
# alerting.py — disparo de sinal digital (GPIO) no Raspberry Pi + entrada de limpeza

import time
import atexit
from dataclasses import dataclass

# Tenta usar RPi.GPIO; se não houver (desenvolvimento no PC), usa mock que só imprime
try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except Exception:
    _HAS_GPIO = False

    class _MockGPIO:
        BCM = 'BCM'
        BOARD = 'BOARD'
        OUT = 'OUT'
        IN = 'IN'
        HIGH = 1
        LOW = 0
        PUD_UP = 'PUD_UP'
        PUD_DOWN = 'PUD_DOWN'
        PUD_OFF = 'PUD_OFF'

        def setmode(self, mode): print(f"[GPIO-MOCK] setmode({mode})")
        def setup(self, pin, mode, initial=None, pull_up_down=None):
            print(f"[GPIO-MOCK] setup(pin={pin}, mode={mode}, initial={initial}, pud={pull_up_down})")
        def output(self, pin, value): print(f"[GPIO-MOCK] output(pin={pin}, value={value})")
        def input(self, pin): return 0
        def cleanup(self): print("[GPIO-MOCK] cleanup()")

    GPIO = _MockGPIO()


@dataclass
class GpioConfig:
    pin: int = 17
    setup: str = "BCM"          # "BCM" ou "BOARD"
    active_high: bool = True
    mode: str = "pulse"         # "pulse" ou "latch"
    pulse_ms: int = 500         # usado quando mode="pulse"
    # entrada de limpeza (opcional)
    clear_pin: int | None = None
    clear_active_high: bool = True
    clear_pull: str = "PUD_DOWN"   # "PUD_UP" | "PUD_DOWN" | "NONE"
    clear_debounce_ms: int = 120


class GpioAlerter:
    """
    Controla o pino GPIO que sinaliza o alerta.
    - mode='pulse': emite um pulso de duração definida (pulse_ms)
    - mode='latch': mantém o pino no nível ativo ao disparar; desarma via clear() ou pino de limpeza
    """

    def __init__(self, cfg: GpioConfig):
        self.cfg = cfg
        # Modo de numeração
        if str(cfg.setup).upper() == "BCM":
            GPIO.setmode(GPIO.BCM)
        else:
            GPIO.setmode(GPIO.BOARD)

        # Saída de alerta
        initial_level = GPIO.LOW if cfg.active_high else GPIO.HIGH
        GPIO.setup(cfg.pin, GPIO.OUT, initial=initial_level)

        # Entrada de limpeza (opcional)
        self._last_clear_ts = 0.0
        if cfg.clear_pin is not None:
            pud = None
            if cfg.clear_pull.upper() == "PUD_UP":
                pud = GPIO.PUD_UP
            elif cfg.clear_pull.upper() in ("PUD_DOWN", "PUD_OFF", "NONE"):
                pud = GPIO.PUD_DOWN if cfg.clear_pull.upper() == "PUD_DOWN" else None
            GPIO.setup(cfg.clear_pin, GPIO.IN, pull_up_down=pud)

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

    def check_clear_input(self) -> bool:
        """
        Verifica (com debouncing simples) se o pino de limpeza foi acionado.
        Retorna True quando detectar uma ativação válida.
        """
        if self.cfg.clear_pin is None:
            return False
        try:
            val = GPIO.input(self.cfg.clear_pin)
        except Exception:
            return False

        active = (val == GPIO.HIGH) if self.cfg.clear_active_high else (val == GPIO.LOW)

        if active:
            now = time.time()
            if (now - self._last_clear_ts) * 1000.0 >= max(0, self.cfg.clear_debounce_ms):
                self._last_clear_ts = now
                return True
        return False

    def cleanup(self):
        """Libera recursos do GPIO na saída."""
        try:
            GPIO.cleanup()
        except Exception:
            pass
