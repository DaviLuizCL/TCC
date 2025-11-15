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
├─ utils.py               # ROI, temporizadores, helpers
├─ requirements.txt
├─ Makefile               # opcional (atalhos)
└─ samples/               # vídeos de teste (opcional)
```

---

## ⚙️ Requisitos
- Python **3.10+** (recomendado 3.10/3.11; 3.12 também funciona)
- Dependências (pip): `ultralytics`, `opencv-python`, `numpy`, `pydantic`, `requests`, `pyyaml`

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

## 🚀 Como usar
### 1) Criar a ROI (área da piscina)
```bash
python roi_setup.py --source 0 --out roi_pool.yaml
```
- Clique os vértices do polígono (sentido horário ajuda). 
- Pressione **ENTER** para salvar; **ESC** limpa os pontos.

### 2) Executar o sistema
```bash
python main.py --config config.yaml
```
- Teclas: **A** arma/desarma, **Q** sai.
- Caixas **verdes** quando a pessoa está **dentro** da ROI.

> Dica: Se usar `resize_width`, crie a ROI na **mesma resolução** do preview para evitar desencontro de coordenadas.

---

## 🧪 Testes sem piscina
- **ROI simulada**: marque um retângulo no chão com fita/tecido azul, aponte a webcam e desenhe a ROI ali.
- **Monitor/TV**: exiba uma foto/vídeo de piscina e desenhe a ROI por cima.
- **Vídeos públicos**: salve clipes em `samples/` e troque `video.source` para o arquivo.
- **Piscina inflável/brinquedo**: ROI sobre a borda.

---


## ⌨️ Atalhos do teclado
- **A** — armar/desarmar o sistema
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


