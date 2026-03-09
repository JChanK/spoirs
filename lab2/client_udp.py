"""
Лабораторная работа №2 — UDP Клиент
Поддерживает команды: ECHO, TIME, CLOSE, UPLOAD, DOWNLOAD
"""

import socket
import os
import time
import sys

from udp_protocol import (
    HEADER_SIZE, BUFFER_SIZE, DEFAULT_PORT,
    PACKET_TYPE_CMD, PACKET_TYPE_CMDACK, PACKET_TYPE_DATA,
    pack_header, unpack_header,
    SlidingWindowSender, SlidingWindowReceiver,
)

# ─── Константы ────────────────────────────────────────────────────────────────
DEFAULT_HOST    = "127.0.0.1"
DOWNLOAD_DIR    = "downloads_udp"
RECONNECT_TIMEOUT = 30
RECONNECT_DELAY   = 3


# ─── Инициализация ────────────────────────────────────────────────────────────
def ensure_directories():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def create_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # КРИТИЧЕСКИ ВАЖНО: увеличиваем буферы ДО 64 МБ
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 * 1024 * 1024)
    except:
        print("[CLIENT] Не удалось установить большой буфер, использую стандартный")

    # Делаем сокет неблокирующим с таймаутом
    s.settimeout(5.0)

    return s


# ─── Командный обмен ──────────────────────────────────────────────────────────
def send_cmd(sock, addr, text):
    """Отправить команду и дождаться CMDACK."""
    payload = text.encode()
    header  = pack_header(PACKET_TYPE_CMD, 0, len(payload))
    packet  = header + payload

    for _ in range(10):
        sock.sendto(packet, addr)
        sock.settimeout(0.5)
        try:
            raw, src = sock.recvfrom(HEADER_SIZE + 4)
            if src == addr:
                ptype, *_ = unpack_header(raw)
                if ptype == PACKET_TYPE_CMDACK:
                    return True
        except socket.timeout:
            pass
    return False


def recv_cmd(sock, addr, timeout=10.0):
    """Принять командный ответ от сервера."""
    sock.settimeout(timeout)
    while True:
        try:
            raw, src = sock.recvfrom(HEADER_SIZE + 512)
        except socket.timeout:
            return None
        if src != addr:
            continue
        ptype, _flags, _win, seq, length, payload = unpack_header(raw)
        if ptype != PACKET_TYPE_CMD:
            continue
        ack = pack_header(PACKET_TYPE_CMDACK, seq, 0)
        sock.sendto(ack, addr)
        return payload[:length].decode(errors="replace")


# ─── UPLOAD ───────────────────────────────────────────────────────────────────
def do_upload(sock, addr, filepath):
    if not os.path.exists(filepath):
        print(f"[CLIENT] Файл '{filepath}' не найден")
        return

    filename   = os.path.basename(filepath)
    total_size = os.path.getsize(filepath)

    # Шаг 1: Отправляем команду UPLOAD
    if not send_cmd(sock, addr, f"UPLOAD {filename}"):
        print("[CLIENT] Не удалось отправить команду UPLOAD")
        return

    # Шаг 2: Ждем подтверждение от сервера
    response = recv_cmd(sock, addr)
    if response is None:
        print("[CLIENT] Нет ответа от сервера")
        return

    print(f"[CLIENT] Ответ сервера: {response}")

    # Шаг 3: Отправляем размер файла
    if not send_cmd(sock, addr, f"SIZE {total_size}"):
        print("[CLIENT] Не удалось отправить размер файла")
        return

    # Шаг 4: Ждем ответ с OFFSET
    response = recv_cmd(sock, addr)
    if response is None:
        print("[CLIENT] Нет ответа от сервера (OFFSET)")
        return

    if not response.startswith("OFFSET"):
        print(f"[CLIENT] Неожиданный ответ: {response}")
        return

    offset = int(response.split()[1])
    print(f"[CLIENT] Докачка с байта {offset} из {total_size}")

    with open(filepath, "rb") as f:
        f.seek(offset)
        data = f.read()

    sender = SlidingWindowSender(sock, addr)
    start  = time.monotonic()
    print(f"[CLIENT] Отправляю '{filename}'...")

    _sent, elapsed = sender.send(data, PACKET_TYPE_DATA)

    result  = recv_cmd(sock, addr)
    bitrate = (total_size - offset) / elapsed / 1024
    print(f"[CLIENT] {result}")
    print(f"[CLIENT] Скорость: {bitrate:.1f} КБ/с")


# ─── DOWNLOAD ─────────────────────────────────────────────────────────────────
def do_download(sock, addr, filename):
    local_path = os.path.join(DOWNLOAD_DIR, filename)
    offset     = os.path.getsize(local_path) if os.path.exists(local_path) else 0

    send_cmd(sock, addr, f"DOWNLOAD {filename}")
    send_cmd(sock, addr, f"OFFSET {offset}")

    size_resp = recv_cmd(sock, addr)
    if size_resp is None:
        print("[CLIENT] Нет ответа от сервера")
        return
    if size_resp.startswith("ERROR"):
        print(f"[CLIENT] {size_resp}")
        return

    total_size = int(size_resp.split()[1])
    remaining  = total_size - offset
    print(f"[CLIENT] Скачиваю '{filename}' ({total_size} байт), оффсет {offset}")

    receiver = SlidingWindowReceiver(sock, addr, remaining, PACKET_TYPE_DATA)
    start    = time.monotonic()

    try:
        data = receiver.receive()
    except ConnectionError as e:
        print(f"[CLIENT] Ошибка: {e}")
        return

    mode = "ab" if offset > 0 else "wb"
    with open(local_path, mode) as f:
        f.write(data)

    elapsed = time.monotonic() - start or 0.001
    bitrate = remaining / elapsed / 1024
    print(f"[CLIENT] Сохранено в '{local_path}', скорость: {bitrate:.1f} КБ/с")


# ─── Главный интерактивный цикл ───────────────────────────────────────────────
def interactive_loop(sock, addr):
    welcome = recv_cmd(sock, addr)
    if welcome:
        print(f"[SERVER] {welcome}")

    while True:
        try:
            # Пробуем прочитать с обработкой ошибок кодировки
            user_input = input("> ").encode('utf-8', errors='ignore').decode('utf-8').strip()
        except EOFError:
            break
        except UnicodeDecodeError:
            print("[CLIENT] Ошибка кодировки ввода. Используйте латиницу.")
            continue

        if not user_input:
            continue

        parts = user_input.split(None, 1)
        cmd   = parts[0].upper()
        args  = parts[1] if len(parts) > 1 else ""

        if cmd == "UPLOAD":
            do_upload(sock, addr, args)
        elif cmd == "DOWNLOAD":
            do_download(sock, addr, args)
        elif cmd in ("CLOSE", "EXIT", "QUIT"):
            send_cmd(sock, addr, "CLOSE")
            resp = recv_cmd(sock, addr)
            if resp:
                print(f"[SERVER] {resp}")
            break
        else:
            send_cmd(sock, addr, user_input)
            resp = recv_cmd(sock, addr)
            if resp is not None:
                print(f"[SERVER] {resp}")
            else:
                print("[CLIENT] Нет ответа от сервера")


def main():
    ensure_directories()
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    sock = create_socket()
    addr = (host, port)
    print(f"[CLIENT] UDP → {host}:{port}")

    interactive_loop(sock, addr)
    sock.close()
    print("[CLIENT] Готово")


if __name__ == "__main__":
    main()
