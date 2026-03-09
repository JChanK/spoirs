"""
Лабораторная работа №1 — TCP Клиент
Поддерживает команды: ECHO, TIME, CLOSE, UPLOAD, DOWNLOAD
"""

import socket
import os
import struct
import time

# ─── Константы ────────────────────────────────────────────────────────────────
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9090
BUFFER_SIZE = 4096
DOWNLOAD_DIR = "downloads"
RECONNECT_TIMEOUT = 30
RECONNECT_DELAY = 3


# ─── Инициализация ────────────────────────────────────────────────────────────
def ensure_directories():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ─── Создание сокета ─────────────────────────────────────────────────────────
def create_connected_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    s.connect((host, port))
    return s


# ─── Чтение строки из сокета ─────────────────────────────────────────────────
def recv_line(s):
    buf = b""
    while True:
        ch = s.recv(1)
        if not ch:
            return None
        if ch == b"\n":
            break
        buf += ch
    return buf.rstrip(b"\r").decode(errors="replace")


# ─── Отправка строки ──────────────────────────────────────────────────────────
def send_line(s, text):
    s.sendall((text + "\r\n").encode())


# ─── Надёжный recv ────────────────────────────────────────────────────────────
def recv_exact(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Соединение закрыто во время чтения")
        data += chunk
    return data


# ─── UPLOAD ───────────────────────────────────────────────────────────────────
def do_upload(s, filepath):
    if not os.path.exists(filepath):
        print(f"[CLIENT] Файл '{filepath}' не найден")
        return

    filename = os.path.basename(filepath)
    total_size = os.path.getsize(filepath)

    send_line(s, f"UPLOAD {filename}")

    response = recv_line(s)
    if response is None or not response.startswith("OFFSET"):
        print(f"[CLIENT] Неожиданный ответ: {response}")
        return

    offset = int(response.split()[1])
    print(f"[CLIENT] Докачка с байта {offset} из {total_size}")

    s.sendall(struct.pack(">Q", total_size))

    start_time = time.monotonic()
    sent = offset

    with open(filepath, "rb") as f:
        f.seek(offset)
        while True:
            chunk = f.read(BUFFER_SIZE)
            if not chunk:
                break
            s.sendall(chunk)
            sent += len(chunk)
            print(f"\r[CLIENT] Отправлено {sent}/{total_size} байт", end="", flush=True)

    print()
    result = recv_line(s)
    elapsed = time.monotonic() - start_time or 0.001
    bitrate = (total_size - offset) / elapsed / 1024
    print(f"[CLIENT] {result}")
    print(f"[CLIENT] Скорость: {bitrate:.1f} КБ/с")


# ─── DOWNLOAD ─────────────────────────────────────────────────────────────────
def do_download(s, filename):
    local_path = os.path.join(DOWNLOAD_DIR, filename)
    offset = os.path.getsize(local_path) if os.path.exists(local_path) else 0

    send_line(s, f"DOWNLOAD {filename}")
    send_line(s, f"OFFSET {offset}")

    size_line = recv_line(s)
    if size_line is None:
        print("[CLIENT] Нет ответа от сервера")
        return
    if size_line.startswith("ERROR"):
        print(f"[CLIENT] {size_line}")
        return

    total_size = int(size_line.split()[1])
    print(f"[CLIENT] Скачиваю '{filename}' ({total_size} байт), оффсет {offset}")

    start_time = time.monotonic()
    received = offset

    mode = "ab" if offset > 0 else "wb"
    with open(local_path, mode) as f:
        while received < total_size:
            chunk = s.recv(min(BUFFER_SIZE, total_size - received))
            if not chunk:
                raise ConnectionError("Обрыв во время скачивания")
            f.write(chunk)
            received += len(chunk)
            print(f"\r[CLIENT] Получено {received}/{total_size} байт", end="", flush=True)

    print()
    elapsed = time.monotonic() - start_time or 0.001
    bitrate = (total_size - offset) / elapsed / 1024
    print(f"[CLIENT] Скачано в '{local_path}', скорость: {bitrate:.1f} КБ/с")


# ─── Переподключение ─────────────────────────────────────────────────────────
def reconnect_loop(host, port):
    start = time.monotonic()
    warned = False
    attempt = 0

    while True:
        attempt += 1
        elapsed = time.monotonic() - start

        if not warned and elapsed >= RECONNECT_TIMEOUT:
            warned = True
            print(f"\n[CLIENT] Соединение не восстановлено за {RECONNECT_TIMEOUT} сек.")
            answer = input("[CLIENT] Продолжать попытки? (y/n): ").strip().lower()
            if answer != "y":
                return None

        try:
            print(f"\r[CLIENT] Попытка #{attempt}...", end="", flush=True)
            s = create_connected_socket(host, port)
            _ = recv_line(s)
            print(f"\n[CLIENT] Переподключился!")
            return s
        except OSError:
            time.sleep(RECONNECT_DELAY)


# ─── Главный интерактивный цикл ───────────────────────────────────────────────
def interactive_loop(s, host, port):
    welcome = recv_line(s)
    if welcome:
        print(f"[SERVER] {welcome}")

    while True:
        try:
            user_input = input("> ").strip()
        except EOFError:
            break

        if not user_input:
            continue

        parts = user_input.split(None, 1)
        cmd = parts[0].upper()
        args = parts[1] if len(parts) > 1 else ""

        try:
            if cmd == "UPLOAD":
                do_upload(s, args)
            elif cmd == "DOWNLOAD":
                do_download(s, args)
            elif cmd in ("CLOSE", "EXIT", "QUIT"):
                send_line(s, "CLOSE")
                resp = recv_line(s)
                if resp:
                    print(f"[SERVER] {resp}")
                break
            else:
                send_line(s, user_input)
                resp = recv_line(s)
                if resp is not None:
                    print(f"[SERVER] {resp}")
                else:
                    raise ConnectionError("Соединение разорвано")
        except (ConnectionError, OSError) as e:
            print(f"\n[CLIENT] Ошибка: {e}")
            s.close()
            s = reconnect_loop(host, port)
            if s is None:
                print("[CLIENT] Завершение работы")
                break
            welcome = recv_line(s)
            if welcome:
                print(f"[SERVER] {welcome}")


def main():
    import sys
    ensure_directories()

    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    print(f"[CLIENT] Подключаюсь к {host}:{port}...")
    try:
        s = create_connected_socket(host, port)
    except OSError as e:
        print(f"[CLIENT] Не удалось подключиться: {e}")
        return

    interactive_loop(s, host, port)
    s.close()
    print("[CLIENT] Соединение закрыто")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CLIENT] Прервано пользователем")
