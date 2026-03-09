"""
Лабораторная работа №2 — UDP Сервер
Поддерживает команды: ECHO, TIME, CLOSE, UPLOAD, DOWNLOAD
Реализует скользящее окно, ACK, повторную передачу, докачку файлов
"""

import socket
import os
import datetime
import hashlib
import time
import struct

from udp_protocol import (
    HEADER_SIZE, BUFFER_SIZE, DEFAULT_PORT,
    PACKET_TYPE_CMD, PACKET_TYPE_CMDACK, PACKET_TYPE_DATA, PACKET_TYPE_FIN,
    pack_header, unpack_header,
    SlidingWindowSender, SlidingWindowReceiver,
)

# ─── Константы ────────────────────────────────────────────────────────────────
HOST        = "0.0.0.0"
PORT        = DEFAULT_PORT
FILES_DIR   = "server_files_udp"
SESSIONS_DIR = "sessions_udp"
CMD_TIMEOUT = 10.0   # сек ожидания командного пакета


# ─── Инициализация ────────────────────────────────────────────────────────────
def ensure_directories():
    os.makedirs(FILES_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def create_server_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    print(f"[UDP-SERVER] Слушаю на {HOST}:{PORT}")
    return s


# ─── Командный обмен ──────────────────────────────────────────────────────────
def recv_cmd(sock, addr, timeout=CMD_TIMEOUT):
    """Принять командный пакет от addr, вернуть строку или None."""
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


def send_cmd(sock, addr, text):
    """Отправить командный пакет и дождаться CMDACK."""
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


# ─── Сессии докачки ───────────────────────────────────────────────────────────
def session_path(client_id, key):
    h = hashlib.md5(f"{client_id}:{key}".encode()).hexdigest()
    return os.path.join(SESSIONS_DIR, h + ".session")


def load_session(client_id, key):
    p = session_path(client_id, key)
    if os.path.exists(p):
        with open(p) as f:
            return int(f.read().strip())
    return 0


def save_session(client_id, key, offset):
    with open(session_path(client_id, key), "w") as f:
        f.write(str(offset))


def delete_session(client_id, key):
    p = session_path(client_id, key)
    if os.path.exists(p):
        os.remove(p)


# ─── Обработчики команд ───────────────────────────────────────────────────────
def handle_echo(sock, addr, args):
    send_cmd(sock, addr, args if args else "(пусто)")


def handle_time(sock, addr, _args):
    send_cmd(sock, addr, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def handle_upload(sock, addr, args, client_id):
    if not args:
        send_cmd(sock, addr, "ERROR: укажите имя файла")
        return

    filename = os.path.basename(args)
    filepath = os.path.join(FILES_DIR, filename)
    offset   = load_session(client_id, "upload:" + filename)

    # Получить размер файла
    size_str = recv_cmd(sock, addr)
    if not size_str or not size_str.startswith("SIZE"):
        send_cmd(sock, addr, "ERROR: ожидался SIZE")
        return
    total_size = int(size_str.split()[1])

    send_cmd(sock, addr, f"OFFSET {offset}")

    remaining = total_size - offset
    if remaining <= 0:
        delete_session(client_id, "upload:" + filename)
        send_cmd(sock, addr, "OK уже загружен")
        return

    receiver = SlidingWindowReceiver(sock, addr, remaining, PACKET_TYPE_DATA)
    start    = time.monotonic()

    try:
        data = receiver.receive()
    except ConnectionError as e:
        save_session(client_id, "upload:" + filename, offset)
        print(f"[UDP-SERVER] Обрыв UPLOAD {filename}: {e}")
        return

    mode = "ab" if offset > 0 else "wb"
    with open(filepath, mode) as f:
        f.write(data)

    elapsed = time.monotonic() - start or 0.001
    bitrate = remaining / elapsed / 1024
    delete_session(client_id, "upload:" + filename)
    send_cmd(sock, addr, f"OK размер={total_size} скорость={bitrate:.1f} КБ/с")
    print(f"[UDP-SERVER] UPLOAD {filename} завершён, {bitrate:.1f} КБ/с")


def handle_download(sock, addr, args, client_id):
    if not args:
        send_cmd(sock, addr, "ERROR: укажите имя файла")
        return

    filename = os.path.basename(args)
    filepath = os.path.join(FILES_DIR, filename)

    if not os.path.exists(filepath):
        send_cmd(sock, addr, "ERROR: файл не найден")
        return

    total_size = os.path.getsize(filepath)

    offset_str = recv_cmd(sock, addr)
    try:
        offset = int(offset_str.split()[1]) if offset_str else 0
    except (IndexError, ValueError):
        offset = 0

    send_cmd(sock, addr, f"SIZE {total_size}")

    with open(filepath, "rb") as f:
        f.seek(offset)
        data = f.read()

    sender = SlidingWindowSender(sock, addr)
    start  = time.monotonic()

    try:
        sent, elapsed = sender.send(data, PACKET_TYPE_DATA)
    except Exception as e:
        save_session(client_id, "download:" + filename, offset)
        print(f"[UDP-SERVER] Обрыв DOWNLOAD {filename}: {e}")
        return

    bitrate = (total_size - offset) / elapsed / 1024
    delete_session(client_id, "download:" + filename)
    print(f"[UDP-SERVER] DOWNLOAD {filename} завершён, {bitrate:.1f} КБ/с")


# ─── Диспетчер команд ─────────────────────────────────────────────────────────
def dispatch(sock, line, addr, client_id):
    parts = line.strip().split(None, 1)
    if not parts:
        return True
    cmd  = parts[0].upper()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "ECHO":
        handle_echo(sock, addr, args)
    elif cmd == "TIME":
        handle_time(sock, addr, args)
    elif cmd in ("CLOSE", "EXIT", "QUIT"):
        send_cmd(sock, addr, "BYE")
        return False
    elif cmd == "UPLOAD":
        handle_upload(sock, addr, args, client_id)
    elif cmd == "DOWNLOAD":
        handle_download(sock, addr, args, client_id)
    else:
        send_cmd(sock, addr, f"ERROR: неизвестная команда '{cmd}'")

    return True


# ─── Обработка одного клиента ─────────────────────────────────────────────────
def handle_client(sock, addr, first_cmd):
    client_id = f"{addr[0]}:{addr[1]}"
    print(f"[UDP-SERVER] Новый клиент {client_id}")
    send_cmd(sock, addr, "Привет! Команды: ECHO TIME UPLOAD DOWNLOAD CLOSE")

    line = first_cmd
    while line is not None:
        print(f"[UDP-SERVER] {client_id} -> {line!r}")
        if not dispatch(sock, line, addr, client_id):
            break
        line = recv_cmd(sock, addr)

    print(f"[UDP-SERVER] {client_id} отключился")


# ─── Главный цикл ─────────────────────────────────────────────────────────────
def main():
    ensure_directories()
    sock = create_server_socket()

    current_client = None

    try:
        while True:
            sock.settimeout(None)
            raw, addr = sock.recvfrom(HEADER_SIZE + 512)
            ptype, _flags, _win, seq, length, payload = unpack_header(raw)

            if ptype != PACKET_TYPE_CMD:
                continue

            ack = pack_header(PACKET_TYPE_CMDACK, seq, 0)
            sock.sendto(ack, addr)

            if current_client and current_client != addr:
                send_cmd(sock, addr, "ERROR: сервер занят")
                continue

            current_client = addr
            line = payload[:length].decode(errors="replace")
            handle_client(sock, addr, line)
            current_client = None

    except KeyboardInterrupt:
        print("\n[UDP-SERVER] Остановлен")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
