# SPDX-License-Identifier: AGPL-3.0-or-later
"""discovery.py — descoberta de câmeras para o PoolGuard.

Duas frentes, ambas sem dependências externas (só socket/ioctl da stdlib):

  • Locais (USB/CSI): varre /dev/video* no Linux e, via ioctl V4L2 VIDIOC_QUERYCAP,
    mantém só os nós que realmente CAPTURAM vídeo (cada câmera física costuma
    expor 2+ nós; metadados/saída não servem). Em outros SOs, cai num probe
    simples com OpenCV (índices 0..9).

  • Rede (IP/RTSP): WS-Discovery ONVIF (multicast UDP 239.255.255.250:3702) para
    achar câmeras que se anunciam, mais um scan TCP opcional da porta 554 (RTSP)
    na sub-rede /24 local. Devolve IPs + uma sugestão de URL rtsp:// pra preencher.
"""

from __future__ import annotations

import os
import re
import socket
import struct
import fcntl
import glob
import uuid
import ipaddress
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------- locais (V4L2)

# VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability)  -> 0x80685600
_VIDIOC_QUERYCAP = 0x80685600
_CAP_STRUCT = "16s32s32sII I" + "12x"   # driver, card, bus_info, version, caps, device_caps, reserved
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_V4L2_CAP_DEVICE_CAPS = 0x80000000


def _querycap(path):
    """Retorna (card, bus_info, capabilities) ou None se não for capturador de vídeo."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        buf = bytearray(104)
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, buf, True)
        driver, card, bus, version, caps, device_caps = struct.unpack(_CAP_STRUCT, bytes(buf))
        effective = device_caps if (caps & _V4L2_CAP_DEVICE_CAPS) else caps
        if not (effective & _V4L2_CAP_VIDEO_CAPTURE):
            return None
        decode = lambda b: b.split(b"\x00", 1)[0].decode(errors="replace")
        return decode(card), decode(bus), effective
    except OSError:
        return None
    finally:
        os.close(fd)


def list_local_cameras():
    """Lista câmeras locais utilizáveis. [{'source','name','bus','available'}]."""
    nodes = sorted(glob.glob("/dev/video*"),
                   key=lambda p: int(re.search(r"(\d+)$", p).group(1)))
    if not nodes:
        return _probe_opencv()

    cams, seen_bus = [], set()
    for path in nodes:
        cap = _querycap(path)
        if cap is None:
            continue                       # nó que não captura (metadados/saída)
        card, bus, _ = cap
        key = bus or card                  # dedup: 2+ nós da mesma câmera física
        if key in seen_bus:
            continue
        seen_bus.add(key)
        cams.append({"source": path, "name": card or os.path.basename(path),
                     "bus": bus, "available": _can_open(path)})
    return cams or _probe_opencv()


def _can_open(source):
    """True só se a câmera abrir E entregar um frame de verdade.

    Vários nós V4L2 anunciam captura mas não funcionam (câmera integrada tampada,
    nó secundário/metadados, sem driver). Por isso lemos 1 frame, não só isOpened().
    Para /dev/videoN, abre pelo índice N (CAP_V4L2 abrir por nome é instável)."""
    try:
        import cv2
        m = re.search(r"/dev/video(\d+)$", str(source))
        target = int(m.group(1)) if m else source
        cap = cv2.VideoCapture(target, cv2.CAP_V4L2)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        return bool(ok)
    except Exception:
        return False


def _probe_opencv(n=10):
    """Fallback (Windows/macOS): testa índices 0..n-1 abrindo com OpenCV."""
    cams = []
    try:
        import cv2
    except Exception:
        return cams
    for i in range(n):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            cams.append({"source": i, "name": f"Câmera {i}", "bus": "", "available": True})
        cap.release()
    return cams


# ---------------------------------------------------------- rede (ONVIF + RTSP)

_WS_DISCOVERY_ADDR = ("239.255.255.250", 3702)


def _local_ipv4s():
    """IPv4s não-loopback/não-docker das interfaces, com a primária (rota default) à frente."""
    addrs = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addrs.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "172.17.", "172.18.", "172.19.")):
                continue
            if ip not in addrs:
                addrs.append(ip)
    except socket.gaierror:
        pass
    return addrs


def discover_onvif(timeout=3.0):
    """WS-Discovery ONVIF: devolve câmeras que se anunciam. [{'ip','xaddr','source'}]."""
    msg_id = "urn:uuid:" + str(uuid.uuid4())
    probe = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
        ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
        ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        '<e:Header><w:MessageID>' + msg_id + '</w:MessageID>'
        '<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
        '<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
        '</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>'
        '</d:Probe></e:Body></e:Envelope>'
    ).encode()

    found, seen = [], set()
    for local_ip in _local_ipv4s() or [""]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if local_ip:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(local_ip))
            except OSError:
                pass
        sock.settimeout(timeout)
        try:
            sock.sendto(probe, _WS_DISCOVERY_ADDR)
            while True:
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    break
                ip = addr[0]
                if ip in seen:
                    continue
                seen.add(ip)
                xaddrs = re.findall(r"https?://[^\s<]+", data.decode(errors="replace"))
                found.append({"ip": ip, "xaddr": xaddrs[0] if xaddrs else "",
                              "source": f"rtsp://USUARIO:SENHA@{ip}:554/"})
        except OSError:
            pass
        finally:
            sock.close()
    return found


def _port_open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_rtsp_subnet(port=554, timeout=0.4, max_hosts=256):
    """Scan TCP da porta RTSP na /24 de cada IPv4 local. [{'ip','source'}]."""
    targets, seen_net = [], set()
    for ip in _local_ipv4s():
        try:
            net = ipaddress.ip_interface(f"{ip}/24").network
        except ValueError:
            continue
        if net in seen_net:
            continue
        seen_net.add(net)
        targets += [str(h) for h in net.hosts() if str(h) != ip][:max_hosts]

    hits = []
    if not targets:
        return hits
    with ThreadPoolExecutor(max_workers=64) as ex:
        for ip, ok in zip(targets, ex.map(lambda h: _port_open(h, port, timeout), targets)):
            if ok:
                hits.append({"ip": ip, "source": f"rtsp://USUARIO:SENHA@{ip}:{port}/"})
    return hits


def discover_network(timeout=3.0, scan_ports=True):
    """ONVIF + (opcional) scan de porta 554, mesclados e deduplicados por IP."""
    by_ip = {}
    for cam in discover_onvif(timeout=timeout):
        cam["onvif"] = True
        by_ip[cam["ip"]] = cam
    if scan_ports:
        for cam in scan_rtsp_subnet(timeout=timeout / 6 if timeout else 0.4):
            if cam["ip"] not in by_ip:
                cam["onvif"] = False
                by_ip[cam["ip"]] = cam
    return list(by_ip.values())
