"""
Лабораторная работа №2 — UDP Клиент
Команды: ECHO, TIME, CLOSE, UPLOAD, DOWNLOAD

DATA-трафик идёт на отдельный порт сервера (DATAPORT из ответа),
поэтому не конкурирует с CMD-обменом.
"""

import socket, os, time, sys, hashlib, json
from collections import deque
import threading

from udp_protocol import (
    HEADER_SIZE, DEFAULT_PORT,
    PACKET_TYPE_CMD, PACKET_TYPE_CMDACK, PACKET_TYPE_DATA,
    pack_header, unpack_header,
    SlidingWindowSender, SlidingWindowReceiver,
)

DEFAULT_HOST = "127.0.0.1"
DOWNLOAD_DIR = "downloads_udp"
SESSION_DIR = "client_sessions_udp"
CMD_BUFSIZE  = HEADER_SIZE + 4096

# Флаг для отслеживания прерывания
_interrupted = False
_inbox: deque = deque()


def ensure_directories():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(SESSION_DIR, exist_ok=True)


def create_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16 * 1024 * 1024)
    except OSError:
        pass
    return s


# ─── Сессии для докачки ───────────────────────────────────────────────────────

def session_path(server_addr, filename, direction):
    key = f"{server_addr[0]}:{server_addr[1]}:{filename}:{direction}"
    h = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(SESSION_DIR, h + ".json")


def save_session(server_addr, filename, direction, offset, total_size=None):
    path = session_path(server_addr, filename, direction)
    data = {
        "offset": offset,
        "timestamp": time.time(),
        "filename": filename,
        "direction": direction,
        "server": f"{server_addr[0]}:{server_addr[1]}"
    }
    if total_size:
        data["total_size"] = total_size
    with open(path, "w") as f:
        json.dump(data, f)
    print(f"[CLIENT] Сессия сохранена: {filename} offset={offset}")


def load_session(server_addr, filename, direction):
    path = session_path(server_addr, filename, direction)
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            return data.get("offset", 0)
        except:
            return 0
    return 0


def delete_session(server_addr, filename, direction):
    path = session_path(server_addr, filename, direction)
    if os.path.exists(path):
        os.remove(path)
        print(f"[CLIENT] Сессия удалена: {filename}")


# ─── CMD обмен ───────────────────────────────────────────────────────────────

def send_cmd(sock, addr, text):
    payload = text.encode()
    packet  = pack_header(PACKET_TYPE_CMD, 0, len(payload)) + payload
    for _ in range(10):
        sock.sendto(packet, addr)
        deadline = time.monotonic() + 0.5
        while True:
            left = deadline - time.monotonic()
            if left <= 0: break
            sock.settimeout(left)
            try:
                raw, src = sock.recvfrom(CMD_BUFSIZE)
            except socket.timeout:
                break
            if src != addr: continue
            try:
                ptype, _f, _w, seq, length, pl = unpack_header(raw)
            except ValueError:
                continue
            if ptype == PACKET_TYPE_CMDACK:
                return True
            if ptype == PACKET_TYPE_CMD:
                sock.sendto(pack_header(PACKET_TYPE_CMDACK, seq, 0), addr)
                _inbox.append(pl[:length].decode(errors="replace"))
    return False


def recv_cmd(sock, addr, timeout=10.0):
    if _inbox:
        return _inbox.popleft()
    sock.settimeout(timeout)
    while True:
        try:
            raw, src = sock.recvfrom(CMD_BUFSIZE)
        except socket.timeout:
            return None
        if src != addr: continue
        try:
            ptype, _f, _w, seq, length, pl = unpack_header(raw)
        except ValueError:
            continue
        if ptype != PACKET_TYPE_CMD: continue
        sock.sendto(pack_header(PACKET_TYPE_CMDACK, seq, 0), addr)
        return pl[:length].decode(errors="replace")


def parse_dataport(response, keyword):
    parts = response.split()
    for i, p in enumerate(parts):
        if p == "DATAPORT" and i + 1 < len(parts):
            return int(parts[i + 1])
    return None


def make_data_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16 * 1024 * 1024)
    return s


# ─── UPLOAD ──────────────────────────────────────────────────────────────────

def do_upload(sock, addr, filepath):
    global _interrupted
    _interrupted = False

    if not os.path.exists(filepath):
        print(f"[CLIENT] Файл '{filepath}' не найден"); return

    filename   = os.path.basename(filepath)
    total_size = os.path.getsize(filepath)

    saved_offset = load_session(addr, filename, "upload")
    if saved_offset > 0:
        print(f"[CLIENT] Найдена прерванная сессия для {filename}, offset={saved_offset}")

    if not send_cmd(sock, addr, f"UPLOAD {filename}"):
        print("[CLIENT] Нет ответа на UPLOAD"); return

    r = recv_cmd(sock, addr)
    if not r or not r.startswith("READY"):
        print(f"[CLIENT] Ожидал READY, получил: {r!r}"); return
    print(f"[CLIENT] {r}")

    if not send_cmd(sock, addr, f"SIZE {total_size}"):
        print("[CLIENT] Не удалось отправить SIZE"); return

    r = recv_cmd(sock, addr)
    if not r or not r.startswith("OFFSET"):
        print(f"[CLIENT] Ожидал OFFSET, получил: {r!r}"); return

    parts     = r.split()
    server_offset = int(parts[1])
    data_port = parse_dataport(r, "OFFSET")
    if data_port is None:
        print(f"[CLIENT] Нет DATAPORT в ответе: {r!r}"); return

    offset = max(saved_offset, server_offset)
    print(f"[CLIENT] Загрузка с байта {offset}, data-порт сервера: {data_port}")

    if offset >= total_size:
        print("[CLIENT] Файл уже загружен полностью")
        delete_session(addr, filename, "upload")
        return

    with open(filepath, "rb") as f:
        f.seek(offset); data = f.read()

    data_sock = make_data_socket()
    data_sock.bind(('', 0))
    client_data_port = data_sock.getsockname()[1]
    print(f"[CLIENT] DATA сокет на порту: {client_data_port}")

    srv_data_addr = (addr[0], data_port)
    sender = SlidingWindowSender(data_sock, srv_data_addr)

    # Сохраняем сессию перед началом
    save_session(addr, filename, "upload", offset, total_size)

    try:
        _sent, elapsed = sender.send(data, PACKET_TYPE_DATA)
        if _interrupted:
            print(f"\n[CLIENT] Передача прервана пользователем")
            save_session(addr, filename, "upload", offset, total_size)
            data_sock.close()
            return
    except ConnectionError as e:
        print(f"[CLIENT] Ошибка передачи: {e}")
        save_session(addr, filename, "upload", offset, total_size)
        data_sock.close()
        return
    except Exception as e:
        print(f"[CLIENT] Ошибка: {e}")
        save_session(addr, filename, "upload", offset, total_size)
        data_sock.close()
        return

    data_sock.close()

    r = recv_cmd(sock, addr, timeout=60.0)
    print(f"[CLIENT] {r}")
    print(f"[CLIENT] Скорость: {(total_size-offset)/elapsed/1024:.1f} КБ/с")

    delete_session(addr, filename, "upload")


# ─── DOWNLOAD ────────────────────────────────────────────────────────────────

def do_download(sock, addr, filename):
    global _interrupted
    _interrupted = False

    local_path = os.path.join(DOWNLOAD_DIR, filename)

    saved_offset = load_session(addr, filename, "download")
    local_offset = os.path.getsize(local_path) if os.path.exists(local_path) else 0
    offset = max(saved_offset, local_offset)

    if offset > 0:
        print(f"[CLIENT] Найдена прерванная сессия для {filename}, offset={offset}")

    if not send_cmd(sock, addr, f"DOWNLOAD {filename}"):
        print("[CLIENT] Нет ответа на DOWNLOAD"); return

    if not send_cmd(sock, addr, f"OFFSET {offset}"):
        print("[CLIENT] Не удалось отправить OFFSET"); return

    r = recv_cmd(sock, addr)
    if not r:
        print("[CLIENT] Нет ответа SIZE"); return
    if r.startswith("ERROR"):
        print(f"[CLIENT] {r}"); return
    if not r.startswith("SIZE"):
        print(f"[CLIENT] Ожидал SIZE, получил: {r!r}"); return

    parts      = r.split()
    total_size = int(parts[1])
    data_port  = parse_dataport(r, "SIZE")
    remaining  = total_size - offset

    if remaining <= 0:
        print(f"[CLIENT] Файл '{filename}' уже полностью скачан")
        delete_session(addr, filename, "download")
        return

    print(f"[CLIENT] Скачиваю '{filename}' ({total_size} байт), data-порт: {data_port}")

    data_sock = make_data_socket()
    data_sock.bind(('', 0))

    srv_data_addr = (addr[0], data_port)
    receiver = SlidingWindowReceiver(data_sock, srv_data_addr, remaining, PACKET_TYPE_DATA)

    save_session(addr, filename, "download", offset, total_size)

    start = time.monotonic()
    try:
        data = receiver.receive()
        if _interrupted:
            print(f"\n[CLIENT] Скачивание прервано пользователем")
            data_sock.close()
            return
    except ConnectionError as e:
        print(f"[CLIENT] Ошибка: {e}")
        data_sock.close()
        return
    except Exception as e:
        print(f"[CLIENT] Ошибка: {e}")
        data_sock.close()
        return

    data_sock.close()

    mode = "ab" if offset > 0 else "wb"
    with open(local_path, mode) as f:
        f.write(data)

    elapsed = time.monotonic() - start or 0.001
    print(f"[CLIENT] Сохранено: '{local_path}', {remaining/elapsed/1024:.1f} КБ/с")

    delete_session(addr, filename, "download")


# ─── Интерактивный цикл ──────────────────────────────────────────────────────

def flush_inbox(sock, addr):
    _inbox.clear()
    sock.settimeout(0.1)
    while True:
        try:
            raw, src = sock.recvfrom(CMD_BUFSIZE)
            if src == addr:
                try:
                    ptype, _f, _w, seq, _l, _p = unpack_header(raw)
                    if ptype == PACKET_TYPE_CMD:
                        sock.sendto(pack_header(PACKET_TYPE_CMDACK, seq, 0), addr)
                except ValueError:
                    pass
        except socket.timeout:
            break


def interactive_loop(sock, addr):
    global _inbox, _interrupted
    _inbox = deque()
    _interrupted = False

    print("[CLIENT] Подключение...")

    welcome = None
    for attempt in range(30):
        if not send_cmd(sock, addr, "HELLO"):
            print(f"[CLIENT] Сервер не отвечает (попытка {attempt+1}/30)...")
            time.sleep(2)
            continue
        welcome = recv_cmd(sock, addr)
        if welcome and welcome.startswith("ERROR"):
            print(f"[CLIENT] {welcome}, повтор через 3 сек...")
            time.sleep(3)
            continue
        break
    else:
        print("[CLIENT] Сервер недоступен"); return

    if welcome:
        print(f"[SERVER] {welcome}")

    time.sleep(0.2)
    flush_inbox(sock, addr)

    while True:
        try:
            # Используем raw_input для избежания проблем с кодировкой
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[CLIENT] Прервано пользователем")
            _interrupted = True
            break
        except UnicodeDecodeError:
            print("[CLIENT] Ошибка кодировки ввода, игнорирую")
            continue

        if not user_input: continue

        parts = user_input.split(None, 1)
        cmd   = parts[0].upper()
        args  = parts[1] if len(parts) > 1 else ""

        if cmd == "UPLOAD":
            do_upload(sock, addr, args)
        elif cmd == "DOWNLOAD":
            do_download(sock, addr, args)
        elif cmd in ("CLOSE", "EXIT", "QUIT"):
            send_cmd(sock, addr, "CLOSE")
            r = recv_cmd(sock, addr)
            if r: print(f"[SERVER] {r}")
            break
        else:
            if not send_cmd(sock, addr, user_input):
                print("[CLIENT] Нет ответа (таймаут)"); continue
            r = recv_cmd(sock, addr)
            if r is not None:
                print(f"[SERVER] {r}")
            else:
                print("[CLIENT] Нет ответа от сервера")


def main():
    ensure_directories()
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    sock = create_socket()
    addr = (host, port)
    print(f"[CLIENT] UDP → {host}:{port}")

    try:
        interactive_loop(sock, addr)
    except KeyboardInterrupt:
        print("\n[CLIENT] Прервано пользователем")
    finally:
        sock.close()
        print("[CLIENT] Готово")


if __name__ == "__main__":
    main()