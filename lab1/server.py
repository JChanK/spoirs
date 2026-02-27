"""
Лабораторная работа №1 — TCP Сервер
Поддерживает команды: ECHO, TIME, CLOSE, UPLOAD, DOWNLOAD
Один поток, последовательный сервер с поддержкой докачки файлов
"""

import socket
import os
import datetime
import hashlib
import struct
import time

# ─── Константы ────────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 9090
BUFFER_SIZE = 4096
FILES_DIR = "server_files"
SESSIONS_DIR = "sessions"
KEEPALIVE_IDLE = 10
KEEPALIVE_INTERVAL = 5
KEEPALIVE_COUNT = 3


# ─── Инициализация ────────────────────────────────────────────────────────────
def ensure_directories():
    os.makedirs(FILES_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def create_server_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(5)
    print(f"[SERVER] Слушаю на {HOST}:{PORT}")
    return s


# ─── Keepalive ────────────────────────────────────────────────────────────────
def configure_keepalive(conn):
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "TCP_KEEPIDLE"):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_IDLE)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVAL)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KEEPALIVE_COUNT)


# ─── Чтение строки из сокета ─────────────────────────────────────────────────
def recv_line(conn):
    buf = b""
    while True:
        ch = conn.recv(1)
        if not ch:
            return None
        if ch == b"\n":
            break
        buf += ch
    return buf.rstrip(b"\r").decode(errors="replace")


# ─── Отправка строки ──────────────────────────────────────────────────────────
def send_line(conn, text):
    conn.sendall((text + "\r\n").encode())


# ─── Надёжный recv ────────────────────────────────────────────────────────────
def recv_exact(conn, n):
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Соединение закрыто во время чтения")
        data += chunk
    return data


# ─── Сессии для докачки ───────────────────────────────────────────────────────
def session_path(client_id, filename):
    safe = hashlib.md5(f"{client_id}:{filename}".encode()).hexdigest()
    return os.path.join(SESSIONS_DIR, safe + ".session")


def load_session(client_id, filename):
    path = session_path(client_id, filename)
    if os.path.exists(path):
        with open(path) as f:
            return int(f.read().strip())
    return 0


def save_session(client_id, filename, offset):
    with open(session_path(client_id, filename), "w") as f:
        f.write(str(offset))


def delete_session(client_id, filename):
    path = session_path(client_id, filename)
    if os.path.exists(path):
        os.remove(path)


# ─── Команды ─────────────────────────────────────────────────────────────────
def handle_echo(conn, args):
    send_line(conn, args if args else "(пусто)")


def handle_time(conn, _args):
    send_line(conn, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def handle_upload(conn, args, client_id):
    if not args:
        send_line(conn, "ERROR: укажите имя файла")
        return

    filename = os.path.basename(args)
    filepath = os.path.join(FILES_DIR, filename)
    offset = load_session(client_id, "upload:" + filename)
    send_line(conn, f"OFFSET {offset}")

    total_size = struct.unpack(">Q", recv_exact(conn, 8))[0]

    start_time = time.monotonic()
    received = offset

    mode = "ab" if offset > 0 else "wb"
    with open(filepath, mode) as f:
        while received < total_size:
            chunk = conn.recv(min(BUFFER_SIZE, total_size - received))
            if not chunk:
                save_session(client_id, "upload:" + filename, received)
                raise ConnectionError("Обрыв во время UPLOAD")
            f.write(chunk)
            received += len(chunk)
            save_session(client_id, "upload:" + filename, received)

    elapsed = time.monotonic() - start_time or 0.001
    bitrate = (total_size - offset) / elapsed / 1024
    delete_session(client_id, "upload:" + filename)
    send_line(conn, f"OK размер={total_size} скорость={bitrate:.1f} КБ/с")


def handle_download(conn, args, client_id):
    if not args:
        send_line(conn, "ERROR: укажите имя файла")
        return

    filename = os.path.basename(args)
    filepath = os.path.join(FILES_DIR, filename)

    if not os.path.exists(filepath):
        send_line(conn, "ERROR: файл не найден")
        return

    total_size = os.path.getsize(filepath)

    offset_line = recv_line(conn)
    try:
        offset = int(offset_line.split()[1]) if offset_line else 0
    except (IndexError, ValueError):
        offset = 0

    send_line(conn, f"SIZE {total_size}")

    start_time = time.monotonic()
    sent = offset

    with open(filepath, "rb") as f:
        f.seek(offset)
        while True:
            chunk = f.read(BUFFER_SIZE)
            if not chunk:
                break
            conn.sendall(chunk)
            sent += len(chunk)
            save_session(client_id, "download:" + filename, sent)

    elapsed = time.monotonic() - start_time or 0.001
    bitrate = (total_size - offset) / elapsed / 1024
    delete_session(client_id, "download:" + filename)
    print(f"[SERVER] DOWNLOAD {filename} завершён, скорость {bitrate:.1f} КБ/с")


# ─── Диспетчер команд ─────────────────────────────────────────────────────────
def dispatch(conn, line, client_id):
    parts = line.strip().split(None, 1)
    if not parts:
        return True
    cmd = parts[0].upper()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "ECHO":
        handle_echo(conn, args)
    elif cmd == "TIME":
        handle_time(conn, args)
    elif cmd in ("CLOSE", "EXIT", "QUIT"):
        send_line(conn, "BYE")
        return False
    elif cmd == "UPLOAD":
        handle_upload(conn, args, client_id)
    elif cmd == "DOWNLOAD":
        handle_download(conn, args, client_id)
    else:
        send_line(conn, f"ERROR: неизвестная команда '{cmd}'")

    return True


# ─── Обработка клиента ────────────────────────────────────────────────────────
def handle_client(conn, addr):
    client_id = f"{addr[0]}:{addr[1]}"
    print(f"[SERVER] Подключён {client_id}")
    configure_keepalive(conn)
    send_line(conn, "Привет! Команды: ECHO <текст> | TIME | UPLOAD <файл> | DOWNLOAD <файл> | CLOSE")

    try:
        while True:
            line = recv_line(conn)
            if line is None:
                print(f"[SERVER] {client_id} отключился")
                break
            print(f"[SERVER] {client_id} -> {line!r}")
            if not dispatch(conn, line, client_id):
                break
    except (ConnectionError, OSError) as e:
        print(f"[SERVER] Ошибка соединения с {client_id}: {e}")
    finally:
        conn.close()
        print(f"[SERVER] {client_id} закрыт")


# ─── Главный цикл ─────────────────────────────────────────────────────────────
def main():
    ensure_directories()
    server_sock = create_server_socket()

    try:
        while True:
            conn, addr = server_sock.accept()
            handle_client(conn, addr)
    except KeyboardInterrupt:
        print("\n[SERVER] Остановлен")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()