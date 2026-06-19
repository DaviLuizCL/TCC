import yaml, time, numpy as np, cv2

def load_roi(path: str):
    with open(path, 'r') as f:
        data = yaml.safe_load(f)
    pts = np.array(data['polygon'], dtype=np.int32)
    return pts

def point_in_poly(pt, poly):
    # Retorna True se centro está dentro do polígono
    return cv2.pointPolygonTest(poly, pt, False) >= 0

def center_of(bbox):
    x1, y1, x2, y2 = bbox
    return (int((x1 + x2) // 2), int((y1 + y2) // 2))

class DwellTimer:
    """Mede há quanto tempo a condição está ativa, tolerando falhas breves.

    Na CPU o detector "pisca" (perde frames isolados). Sem tolerância, qualquer
    frame sem detecção zeraria a contagem e o alerta nunca dispararia no tempo.
    Por isso só reiniciamos se a condição ficar ausente por mais que `grace`.

    `update()` retorna os segundos decorridos (0.0 se inativo) — quem decide o
    limiar é quem chama, podendo usar limiares diferentes por situação.
    """

    def __init__(self, grace_seconds: float = 0.6):
        self.grace = grace_seconds
        self._t0 = None
        self._last_true = 0.0

    def update(self, condition: bool) -> float:
        now = time.time()
        if condition:
            # reinicia só se voltou depois de uma ausência longa (> grace)
            if self._t0 is None or (now - self._last_true) > self.grace:
                self._t0 = now
            self._last_true = now
        else:
            if self._t0 is not None and (now - self._last_true) > self.grace:
                self._t0 = None
        return (now - self._t0) if self._t0 is not None else 0.0

class Cooldown:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last = 0.0

    def ready(self):
        return (time.time() - self._last) >= self.seconds

    def mark(self):
        self._last = time.time()


class MotionGate:
    """Porteiro barato de movimento: decide se vale a pena chamar o YOLO.

    Rodar YOLO em todo frame na CPU do Raspberry Pi é caro. Como a piscina fica
    parada na maior parte do tempo, fazemos uma detecção de movimento leníssima
    (cinza minúsculo + média móvel do fundo) restrita à ROI e só "acordamos" o
    YOLO quando algo mexe ali. O YOLO continua sendo quem confirma "é pessoa?",
    classifica criança/adulto e decide o alerta — o gate só evita gastar CPU à toa.

    Segurança em primeiro lugar (anti-afogamento): o gate é enviesado para
    DEIXAR rodar, nunca para bloquear indevidamente. Três motivos acordam o YOLO:
      - movimento na ROI (onset imediato);
      - `person_hold`: alguém foi visto há pouco -> segue checando mesmo parado
        (criança boiando imóvel NÃO pode sumir do radar);
      - `heartbeat`: mesmo ocioso, força uma checagem a cada N s (rede de seguran-
        ça caso o gate perca uma entrada muito lenta).

    `should_run()` deve ser chamado em TODO frame: a detecção de movimento é
    barata e manter o modelo de fundo atualizado é o que pega o início do movimento.
    """

    def __init__(self, enabled=True, downscale_w=160, diff_thresh=18,
                 min_motion_frac=0.004, person_hold_s=4.0, motion_hold_s=1.5,
                 heartbeat_s=2.0):
        self.enabled = bool(enabled)
        self.downscale_w = int(downscale_w)
        self.diff_thresh = int(diff_thresh)
        self.min_motion_frac = float(min_motion_frac)
        self.person_hold_s = float(person_hold_s)
        self.motion_hold_s = float(motion_hold_s)
        self.heartbeat_s = float(heartbeat_s)
        self._avg = None          # média móvel do fundo (float32, cinza reduzido)
        self._t_motion = 0.0
        self._t_person = 0.0
        self._t_run = 0.0
        self.active = False       # exposto p/ UI/limpeza de detecções obsoletas

    def _roi_crop(self, frame, roi):
        if roi is None:
            return frame
        xs = [int(p[0]) for p in roi]
        ys = [int(p[1]) for p in roi]
        h, w = frame.shape[:2]
        x1, x2 = max(0, min(xs)), min(w, max(xs))
        y1, y2 = max(0, min(ys)), min(h, max(ys))
        crop = frame[y1:y2, x1:x2]
        return crop if crop.size else frame

    def _has_motion(self, frame, roi) -> bool:
        crop = self._roi_crop(frame, roi)
        h, w = crop.shape[:2]
        if w == 0 or h == 0:
            return True
        scale = self.downscale_w / float(w)
        small = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        if self._avg is None or self._avg.shape != gray.shape:
            self._avg = gray.astype("float32")
            return True  # primeiro frame: deixa rodar p/ inicializar
        cv2.accumulateWeighted(gray, self._avg, 0.15)
        diff = cv2.absdiff(gray, cv2.convertScaleAbs(self._avg))
        _, th = cv2.threshold(diff, self.diff_thresh, 255, cv2.THRESH_BINARY)
        frac = cv2.countNonZero(th) / float(th.size)
        return frac >= self.min_motion_frac

    def should_run(self, frame, roi, person_present: bool, now: float = None) -> bool:
        """True se o YOLO deve rodar neste frame. Atualiza o estado `active`."""
        if not self.enabled:
            self.active = True
            return True
        now = time.time() if now is None else now
        if person_present:
            self._t_person = now
        if self._has_motion(frame, roi):
            self._t_motion = now
        self.active = ((now - self._t_person) <= self.person_hold_s
                       or (now - self._t_motion) <= self.motion_hold_s)
        run = self.active or (now - self._t_run) >= self.heartbeat_s
        if run:
            self._t_run = now
        return run
