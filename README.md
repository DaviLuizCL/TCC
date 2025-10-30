# PoolGuard — Sistema de Prevenção de Afogamentos (TCC)

MVP em Python que detecta **pessoas** na área da **piscina (ROI)** usando **YOLOv8 + OpenCV** e dispara **eventos JSON** via **HTTP** e/ou **MQTT**. Ideal para demonstração acadêmica e prototipagem.

> ⚠️ **Aviso**: Este software é um **apoio** tecnológico e **não substitui** a supervisão humana. Teste exaustivamente antes de uso real.

---

## 🎯 Funcionalidades
- Detecção de **pessoas** em tempo real (webcam ou vídeo).
- Definição de **ROI poligonal** (área da piscina) com o mouse.
- **Armar/Desarmar** o sistema (tecla **A**).
- Filtros anti-ruído: **dwell** (tempo mínimo na ROI) e **cooldown** (intervalo entre alertas).
- **Envio de JSON** por **HTTP webhook** e/ou **MQTT** (rede local ou externa).
- Overlay com ROI, caixas e status (**ARMADO/ALERT**).

---

## 🧱 Arquitetura (alto nível)
```
[Camera/Vídeo] -> OpenCV -> YOLOv8 (pessoa) -> filtro (conf/área) ->
-> verificador ROI -> dwell + cooldown -> evento -> [HTTP POST] / [MQTT publish]
```

---

## 📁 Estrutura do projeto
```
seu_projeto/
├─ config.yaml
├─ main.py                # loop principal
├─ roi_setup.py           # desenhar/salvar ROI
├─ detector.py            # wrapper YOLO (pessoa)
├─ alerting.py            # schema + envio HTTP/MQTT
├─ utils.py               # ROI, temporizadores, helpers
├─ server_demo.py         # receptor HTTP (FastAPI)
├─ requirements.txt
├─ Makefile               # opcional (atalhos)
└─ samples/               # vídeos de teste (opcional)
```

---

## ⚙️ Requisitos
- Python **3.10+** (recomendado 3.10/3.11; 3.12 também funciona)
- Dependências (pip): `ultralytics`, `opencv-python`, `numpy`, `pydantic`, `requests`, `paho-mqtt`, `pyyaml`, `fastapi`, `uvicorn`

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
  http:
    enabled: true
    url: http://127.0.0.1:8000/event
    timeout: 2
  mqtt:
    enabled: false
    broker: 127.0.0.1
    port: 1883
    topic: pool/alert
    qos: 1
```

---

## 🚀 Como usar
### 1) Criar a ROI (área da piscina)
```bash
python roi_setup.py --source 0 --out roi_pool.yaml
```
- Clique os vértices do polígono (sentido horário ajuda). 
- Pressione **ENTER** para salvar; **ESC** limpa os pontos.

### 2) (Opcional) Subir o receptor HTTP
```bash
python server_demo.py
```
Você verá os **JSONs** chegando quando um alerta for disparado.

### 3) Executar o sistema
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

## 📨 Esquema do evento (JSON)
Exemplo do payload enviado por HTTP/MQTT:
```json
{
  "source": "0",
  "timestamp": 1712345678.12,
  "roi_name": "pool",
  "persons": 1,
  "boxes": [[120, 80, 220, 340]],
  "frame_w": 960,
  "frame_h": 540,
  "reason": "person_in_pool_roi"
}
```

### Testar envio manual (HTTP)
Com o servidor rodando (`server_demo.py`):
```bash
curl -X POST http://127.0.0.1:8000/event   -H 'Content-Type: application/json'   -d '{"source":"manual","timestamp":0,"roi_name":"pool","persons":1,"boxes":[[0,0,10,10]],"frame_w":100,"frame_h":100,"reason":"test"}'
```

### Testar MQTT
- Suba um broker local (por exemplo, Mosquitto) e ative `outputs.mqtt.enabled: true`.
- Assine o tópico:
```bash
mosquitto_sub -h 127.0.0.1 -t pool/alert -v
```
- Rode o `main.py` e veja as mensagens.

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
server:  sobe o receptor HTTP
run:     roda o sistema principal
```
Uso:
```bash
make setup
make roi
make server
make run
```

---

## 🧾 Solução de Problemas (FAQ)
**Erro: `FileNotFoundError: roi_pool.yaml`**  
→ Rode `python roi_setup.py` ou ajuste `roi.file` no `config.yaml` para o caminho correto.

**Erro: `Invalid CUDA 'device=auto'` / `torch.cuda.is_available(): False`**  
→ Use `device: cpu` no `config.yaml` (ou aplique o auto-resolve no `detector.py`).

**Aviso: `Conexão recusada 127.0.0.1:8000`**  
→ O receptor HTTP não está rodando. Suba `python server_demo.py` ou desative `outputs.http.enabled`.

**Preview não abre**  
→ Verifique `video.display: true`. No WSL/servidores remotos, GUI do OpenCV pode não funcionar.

**ROI desalinhada**  
→ Crie a ROI com a **mesma resolução** usada em execução (respeite `resize_width`).

---

## 🔮 Melhorias futuras
- **Classificar criança vs adulto** (heurística pela altura aparente ou *pose estimation* com `yolov8n-pose.pt`).
- **Rastreamento** (ByteTrack/StrongSORT) para dwell mais robusto.
- **Detecção automática da piscina** (segmentação/cor).
- **Pré-alerta** (zona externa).
- **Clips de evento** (gravar 5s antes/depois) com `cv2.VideoWriter`.
- **Segurança** (TLS/token no webhook e MQTT).

---

## 📚 Créditos & Licenças
- **YOLOv8 (Ultralytics)** — respeite a licença e cite no TCC.
- Código deste repositório: uso acadêmico/didático.

---

## 👤 Autor / Contato
Projeto desenvolvido para TCC por **Davi** (davilcl). 

Se precisar, inclua prints de tela, amostras de eventos e métricas (FPS, TP/FP) nesta seção do repositório.
