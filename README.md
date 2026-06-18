# PoolGuard — Sistema de Prevenção de Afogamentos (TCC)

MVP em Python que detecta **pessoas** na área da **piscina (ROI)** usando **YOLOv8 + OpenCV** e dispara um **Sinal Digital** via pino GPIO

> ⚠️ **Aviso**: Este software é um **apoio** tecnológico e **não substitui** a supervisão humana. Teste exaustivamente antes de uso real.

---

## 🎯 Funcionalidades
- Detecção de **pessoas** em tempo real (webcam ou vídeo).
- Definição de **ROI poligonal** (área da piscina) com o mouse.
- **Armar/Desarmar** o sistema (tecla **A**).
- Filtros anti-ruído: **dwell** (tempo mínimo na ROI) e **cooldown** (intervalo entre alertas).
- **Envio de Sinal Digital** por **Pino GPIO**.
- Overlay com ROI, caixas e status (**ARMADO/ALERT**).

---

## 🧱 Arquitetura (alto nível)
```
[Camera/Vídeo] -> OpenCV -> YOLOv8 (pessoa) -> filtro (conf/área) ->
-> verificador ROI -> dwell + cooldown -> evento -> SINAL DIGITAL UP
```

---

## 📁 Estrutura do projeto
```
seu_projeto/
├─ config.yaml
├─ main.py                # loop principal
├─ roi_setup.py           # desenhar/salvar ROI
├─ detector.py            # wrapper YOLO (pessoa)
├─ alerting.py            # schema + ligação do pino
├─ engine.py              # motor de detecção (thread) usado pelo web e pelo desktop
├─ server.py              # app web (FastAPI): painel + vídeo ao vivo + APIs
├─ static/index.html      # painel mobile (config + ROI + ao vivo)
├─ notifier.py            # fan-out p/ ntfy, Telegram, MQTT, Pushover, Gotify
├─ utils.py               # ROI, temporizadores, helpers
├─ requirements.txt
├─ Makefile               # opcional (atalhos)
└─ samples/               # vídeos de teste (opcional)
```

---

## ⚙️ Requisitos
- Python **3.10+** (recomendado 3.10/3.11; 3.12 também funciona)
- Dependências (pip): `ultralytics`, `opencv-python`, `numpy`, `pydantic`, `pyyaml`
- Para o servidor de alerta: `fastapi`, `uvicorn`, `requests`, `paho-mqtt` (este só p/ MQTT)

Instalação sugerida:
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

> 💡 Sem GPU? Use `device: cpu` no `config.yaml`. Mesmo com PyTorch + CUDA instalados, se `torch.cuda.is_available()` for `False`, use CPU.

---

## 🔧 Configuração (`config.yaml`)
```yaml
video:
  source: 0            # 0 = webcam; ou caminho de arquivo, ex: samples/pool1.mp4
  display: true        # mostra janela com overlay
  resize_width: 960    # 0 para manter tamanho original

model:
  weights: yolov8n.pt  # use yolov8s.pt se tiver GPU/CPU boa
  conf: 0.5
  iou: 0.45
  device: cpu          # cpu | cuda:0 | 0,1,2 ... (auto-resolvido no código, opcional)

roi:
  file: roi_pool.yaml  # salvo pelo roi_setup.py

alarm:
  enabled_start: true  # começa armado?
  dwell_seconds: 1.5   # tempo mínimo na ROI
  min_box_area: 7000   # filtra detecções pequenas
  cooldown_seconds: 8  # evita spam

outputs:
  gpio:
    enabled: true
    pin: 17           # GPIO de ALERTA (BCM)
    setup: BCM        # BCM ou BOARD
    active_high: true
    mode: latch       # 'pulse' (pulso) ou 'latch' (fica ligado até limpar)
    pulse_ms: 500     # usado apenas se mode='pulse'

    #entrada digital para limpar o latch
    clear_pin: 27         # GPIO de LIMPEZA (BCM). 
    clear_active_high: true  # nível ativo do clear_pin (true = 1 limpa, false = 0 limpa)
    clear_pull: PUD_DOWN     # PUD_UP | PUD_DOWN | NONE
    clear_debounce_ms: 120   # antirruído por software
```

---

## 🚀 Como usar (app web — recomendado)

Tudo pelo navegador do celular/PC: ver ao vivo, armar, desenhar a ROI e
configurar. Não precisa mexer no Raspberry.

```bash
python server.py --config config.yaml        # ou: make server
```
Abra **http://IP-DO-RASPBERRY:8000** e entre com o usuário/senha do
`config.yaml > web` (padrão `admin` / `poolguard` — **troque!**).

No painel você tem:
- **Ao vivo** — abas por câmera, vídeo com overlay + *Armar/Desarmar* e *Limpar alerta*.
- **Área da piscina (ROI)** — clique no vídeo para marcar o polígono e *Salvar* (uma ROI por câmera).
- **Regras de alerta (criança/adulto)** — ver seção abaixo.
- **Câmeras** — adicionar/remover fontes (webcam ou RTSP), respeitando o tier.
- **Configurações gerais** — sensibilidade, performance e canais de alerta.
  Salvar **aplica em tempo real** (exceto adicionar/remover câmera, que pede restart).

> Dica: ao mudar `resize_width`, **redesenhe a ROI** (as coordenadas mudam).

---

## 🎥 Múltiplas câmeras e RTSP

O mesmo código roda do produto de **1 piscina** ao de **clube com 3 câmeras** —
muda só o `config.yaml`. Cada câmera tem sua fonte e sua ROI; sobe **um motor de
detecção por câmera**, todos compartilhando o mesmo GPIO e os mesmos alertas.

```yaml
product:
  max_cameras: 3            # trava de tier (entry=1, clube=3)
cameras:
  - id: cam1
    name: Piscina Principal
    source: rtsp://admin:senha@192.168.0.50:554/Streaming/Channels/102   # substream!
    roi_file: roi_cam1.yaml
  - id: cam2
    name: Piscina Infantil
    source: /dev/video0       # ou 0, ou outra URL rtsp://
    roi_file: roi_cam2.yaml
```

- **`source`** aceita webcam (`0`, `/dev/videoX`), arquivo, ou **`rtsp://...`** (câmera IP/DVR).
- Use o **substream** da câmera (baixa resolução) na detecção — economiza MUITA CPU.
- RTSP já vai por **TCP** (mais estável) e com **reconexão automática**.

---

## 🎯 Regras de alerta (criança / adulto)

Avaliadas **dentro da ROI**, todas configuráveis no painel (`config.yaml > classify`):

| Situação na piscina | Resultado |
|---|---|
| **Criança sozinha** | 🚨 alerta |
| Criança + adulto, **horário permitido** | ok |
| Criança + adulto, **fora do horário** | 🚨 alerta |
| Adulto sozinho / vazio | ok |

```yaml
classify:
  enabled: true
  child_height_frac: 0.45     # altura < 45% do frame = "criança" (ajuste por câmera)
  alert_child_alone: true
  alert_child_with_adult_outside_hours: true
  allowed_hours:              # vazio = sempre permitido; suporta cruzar meia-noite
    - { start: "08:00", end: "18:00" }
```

> ⚠️ A distinção criança/adulto é **heurística por altura aparente** (YOLOv8 não
> estima idade). Ajuste `child_height_frac` por câmera conforme o ângulo. Evolução
> futura: *pose estimation* ou modelo de idade para robustez.

### Alternativa: viewer desktop (debug local)
```bash
python main.py --config config.yaml
```
- Janela OpenCV local. Teclas: **A** arma/desarma, **C** limpa alerta, **Q** sai.
- (A ferramenta antiga `roi_setup.py` ainda existe, mas o editor de ROI do app web a substitui.)

---

## 🐳 Rodar com Docker (simula o Raspberry no notebook)

Sobe o sistema num container — sem `RPi.GPIO`, o `alerting.py` usa o **mock**
automaticamente (disparos de GPIO viram logs), igual seria no Pi, mas com o app
web e os alertas funcionando de verdade.

```bash
docker compose up --build
# abra http://localhost:8000  (admin / poolguard — troque no config.yaml)
```

- A webcam do notebook é passada como `/dev/video0 → /dev/video2` (casa com o
  `config.yaml`). Se sua câmera não for a `video0`, ajuste em `docker-compose.yml`.
- O projeto é montado como volume: `config.yaml`, ROI e `events/` editados pelo
  painel **persistem no host**.

---

## 📱 Alertas no celular

Quando o sistema dispara, o motor de detecção aciona o **GPIO** e o **Notifier**,
que envia para **todos os canais habilitados ao mesmo tempo**, cada um em paralelo
e isolado: se um falhar (rede/token), os outros continuam. O `notify.snapshot: true`
anexa a **foto do momento** nos canais que suportam.

### Canais suportados (em `config.yaml > notify`, ou pelo painel web)
| Canal | Como funciona | Precisa de |
|------|----------------|-----------|
| **ntfy** | Push grátis com app próprio (ntfy.sh ou auto-hospedado). **Suporta foto.** | Só escolher um `topic` único/secreto |
| **Telegram** | Mensagem (e foto) via bot. | `bot_token` (@BotFather) + `chat_id` |
| **MQTT** | Publica em um tópico (Home Assistant / IoT). | Broker MQTT |
| **Pushover** | App de push. **Suporta foto.** | `token` (app) + `user` key |
| **Gotify** | Push self-hosted. | `server` + `token` |

> O jeito mais rápido de testar: instale o app **ntfy** no celular, inscreva-se num
> `topic` secreto, coloque o mesmo nome no painel (canal ntfy), marque *ativo* e salve.

### Fluxo
```
[server.py] ── engine (YOLO+ROI) ──> alerta ──┬─> GPIO
   │  (painel web + vídeo MJPEG)               └─> Notifier ─> ntfy / Telegram / MQTT / Pushover / Gotify
   └─ navegador do celular (config + ao vivo)
```

> 🔒 **Segurança:** o painel usa Basic Auth, mas mantenha-o **só na LAN** — não
> exponha a porta direto na internet.

---

## 🧪 Testes sem piscina
- **ROI simulada**: marque um retângulo no chão com fita/tecido azul, aponte a webcam e desenhe a ROI ali.
- **Monitor/TV**: exiba uma foto/vídeo de piscina e desenhe a ROI por cima.
- **Vídeos públicos**: salve clipes em `samples/` e troque `video.source` para o arquivo.
- **Piscina inflável/brinquedo**: ROI sobre a borda.

---


## ⌨️ Atalhos do teclado (viewer desktop `main.py`)
- **A** — armar/desarmar o sistema
- **C** — limpar alerta (latch)
- **Q** — sair

---

## 🛠️ Dicas de calibração & performance
- **Falsos positivos?** aumente `dwell_seconds` e/ou `min_box_area`.
- **Baixo FPS?** use `yolov8n.pt`, reduza `resize_width` (ex.: 640) e mantenha `device: cpu`.
- **Mais precisão?** `yolov8s.pt` (se o hardware permitir).

---

## 🧩 Makefile (atalhos úteis)
```makefile
setup:   cria e instala o venv
roi:     abre ferramenta de ROI
run:     roda o sistema principal
```
Uso:
```bash
make setup
make roi
make run
```

---

## 🔮 Melhorias futuras
- **Classificar criança vs adulto** (heurística pela altura aparente ou *pose estimation* com `yolov8n-pose.pt`).
- **Rastreamento** (ByteTrack/StrongSORT) para dwell mais robusto.
- **Detecção automática da piscina** (segmentação/cor).
- **Pré-alerta** (zona externa).
- **Clips de evento** (gravar 5s antes/depois) com `cv2.VideoWriter`.

---

## 📚 Créditos & Licenças

**Licença deste repositório**
- **PoolGuard (este projeto)** — Licenciado sob **AGPL-3.0**. Uso acadêmico/didático; sem garantias. Veja o arquivo `LICENSE`.

**Dependências e respectivas licenças**
- **Ultralytics YOLOv8 (`ultralytics`)** — **AGPL-3.0** (© Ultralytics). Cite no TCC:  
  JOCHER, G. et al. *Ultralytics YOLOv8*. Ultralytics, 2023–2025. Disponível em: https://github.com/ultralytics/ultralytics. Acesso em: 30 out. 2025.
- **OpenCV** — **BSD-3-Clause**.
- **Pydantic** — **MIT**.
- **NumPy** — **BSD-3-Clause**.
- **PyYAML** — **MIT**.
- (**Opcional/indireta**) **PyTorch** — **BSD-3-Clause**.

> Observação: ao importar `ultralytics` (AGPL-3.0) diretamente, este projeto adota **AGPL-3.0** para manter a compatibilidade de licença.

## 👤 Autor / Contato
Projeto desenvolvido para TCC por **Davi** (davilcl). 


