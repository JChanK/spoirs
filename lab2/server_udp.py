import threading
import time
import socket
import os
import datetime
import hashlib
from collections import deque
from udp_protocol import (
    HEADER_SIZE, DEFAULT_PORT,
    PACKET_TYPE_CMD, PACKET_TYPE_CMDACK, PACKET_TYPE_DATA,
    pack_header, unpack_header,
    SlidingWindowSender, SlidingWindowReceiver,
)

HOST         = "0.0.0.0"
PORT         = DEFAULT_PORT
FILES_DIR    = "server_files_udp"
SESSIONS_DIR = "sessions_udp"
CMD_BUFSIZE  = HEADER_SIZE + 4096
CLIENT_TIMEOUT = 30 

_sock: socket.socket = None   # главный CMD-сокет
_send_lock = threading.Lock()

_clients      = {}
_clients_lock = threading.Lock()


def ensure_directories():
    os.makedirs(FILES_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def create_server_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 * 1024 * 1024)
    s.bind((HOST, PORT))
    print(f"[UDP-SERVER] Слушаю на {HOST}:{PORT}")
    return s


def make_data_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 * 1024 * 1024)
    s.settimeout(1.0)
    s.bind(('', 0))
    return s


def _send_cmd_pkt(addr, pkt):
    with _send_lock:
        _sock.sendto(pkt, addr)

def dispatcher(stop_evt):
    last_cleanup = time.monotonic()

    while not stop_evt.is_set():
        _sock.settimeout(0.1)
        try:
            raw, addr = _sock.recvfrom(CMD_BUFSIZE)

            client_ip = addr[0]
            with _clients_lock:
                if client_ip in _clients:
                    _clients[client_ip]["last_activity"] = time.monotonic()
                    # Добавляем порт в множество активных портов клиента
                    _clients[client_ip]["cmd_ports"].add(addr[1])

        except socket.timeout:
            now = time.monotonic()
            if now - last_cleanup > 5:
                cleanup_inactive_clients(now)
                last_cleanup = now
            continue
        except OSError:
            break

        try:
            ptype, _f, _w, seq, length, payload = unpack_header(raw)
        except ValueError:
            continue

        if ptype == PACKET_TYPE_CMD:
            _send_cmd_pkt(addr, pack_header(PACKET_TYPE_CMDACK, seq, 0))
            line = payload[:length].decode(errors="replace")

            client_ip = addr[0]
            with _clients_lock:
                entry = _clients.get(client_ip)

            if entry is not None:
                pass
            else:
                new_entry = {
                    "cmd_ports": {addr[1]},
                    "sessions": {},
                    "last_activity": time.monotonic(),
                    "thread": None,
                    "cmd_queue": deque()
                }
                with _clients_lock:
                    _clients[client_ip] = new_entry

                thread = threading.Thread(
                    target=client_session,
                    args=(client_ip,),
                    daemon=True,
                )
                thread.start()
                new_entry["thread"] = thread

            with _clients_lock:
                if client_ip in _clients:
                    _clients[client_ip]["cmd_queue"].append(line)

        elif ptype == PACKET_TYPE_CMDACK:
            client_ip = addr[0]
            with _clients_lock:
                entry = _clients.get(client_ip)
            if entry:
                pass


def cleanup_inactive_clients(now):
    with _clients_lock:
        inactive = [ip for ip, data in _clients.items()
                   if now - data.get("last_activity", 0) > CLIENT_TIMEOUT]
        for ip in inactive:
            print(f"[UDP-SERVER] Клиент {ip} неактивен, удаляем")
            _clients.pop(ip, None)


def send_cmd_to_ip(client_ip, text, retries=10):
    with _clients_lock:
        entry = _clients.get(client_ip)
    if not entry:
        return False

    ports = entry["cmd_ports"]
    if not ports:
        return False

    pl = text.encode()
    pkt = pack_header(PACKET_TYPE_CMD, 0, len(pl)) + pl

    for port in ports:
        addr = (client_ip, port)
        for _ in range(retries):
            _send_cmd_pkt(addr, pkt)
            # Ждем ACK (упрощенно)
            time.sleep(0.1)

    return True


def recv_cmd_for_ip(client_ip, timeout=120.0):
    with _clients_lock:
        entry = _clients.get(client_ip)
    if not entry:
        return None

    q = entry["cmd_queue"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if q:
            return q.popleft()
        time.sleep(0.005)
    return None

def _sf(cid, key):
    h = hashlib.md5(f"{cid}:{key}".encode()).hexdigest()
    return os.path.join(SESSIONS_DIR, h + ".session")

def load_session(cid, key):
    p = _sf(cid, key)
    return int(open(p).read().strip()) if os.path.exists(p) else 0

def save_session(cid, key, v):
    open(_sf(cid, key), "w").write(str(v))

def delete_session(cid, key):
    p = _sf(cid, key)
    if os.path.exists(p): os.remove(p)


def handle_echo(client_ip, args):
    send_cmd_to_ip(client_ip, args if args else "(пусто)")


def handle_time(client_ip):
    send_cmd_to_ip(client_ip, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def handle_upload(client_ip, filename, client_id):
    if not filename:
        send_cmd_to_ip(client_ip, "ERROR: укажите имя файла"); return

    filename = os.path.basename(filename)
    filepath = os.path.join(FILES_DIR, filename)
    offset   = load_session(client_id, "upload:" + filename)

    send_cmd_to_ip(client_ip, f"READY {filename}")

    size_str = recv_cmd_for_ip(client_ip, timeout=30)
    if not size_str or not size_str.startswith("SIZE"):
        send_cmd_to_ip(client_ip, f"ERROR: ожидался SIZE, получил {size_str!r}"); return
    total_size = int(size_str.split()[1])

    data_sock = make_data_socket()
    data_port = data_sock.getsockname()[1]
    send_cmd_to_ip(client_ip, f"OFFSET {offset} DATAPORT {data_port}")

    remaining = total_size - offset
    if remaining <= 0:
        delete_session(client_id, "upload:" + filename)
        data_sock.close()
        send_cmd_to_ip(client_ip, "OK уже загружен полностью"); return

    print(f"[UDP-SERVER] Ожидаю DATA на порту {data_port} от {client_ip}, осталось {remaining} байт")

    receiver = SlidingWindowReceiver(data_sock, (client_ip, 0), remaining, PACKET_TYPE_DATA)

    start = time.monotonic()
    try:
        data = receiver.receive()
    except (ConnectionError, TimeoutError) as e:
        save_session(client_id, "upload:" + filename, offset)
        data_sock.close()
        print(f"[UDP-SERVER] Обрыв UPLOAD {filename}: {e}")
        try:
            send_cmd_to_ip(client_ip, f"ERROR: передача прервана")
        except:
            pass
        return
    except Exception as e:
        print(f"[UDP-SERVER] Ошибка UPLOAD {filename}: {e}")
        data_sock.close()
        return

    data_sock.close()
    mode = "ab" if offset > 0 else "wb"
    with open(filepath, mode) as f:
        f.write(data)

    elapsed = time.monotonic() - start or 0.001
    bitrate = remaining / elapsed / 1024
    delete_session(client_id, "upload:" + filename)
    send_cmd_to_ip(client_ip, f"OK размер={total_size} скорость={bitrate:.1f} КБ/с")
    print(f"[UDP-SERVER] UPLOAD {filename} завершён, {bitrate:.1f} КБ/с")


def handle_download(client_ip, filename, client_id):
    if not filename:
        send_cmd_to_ip(client_ip, "ERROR: укажите имя файла"); return

    filename = os.path.basename(filename)
    filepath = os.path.join(FILES_DIR, filename)
    if not os.path.exists(filepath):
        send_cmd_to_ip(client_ip, "ERROR: файл не найден"); return

    total_size = os.path.getsize(filepath)

    offset_str = recv_cmd_for_ip(client_ip)
    try:
        offset = int(offset_str.split()[1]) if offset_str else 0
    except (IndexError, ValueError):
        offset = 0

    data_sock = make_data_socket()
    data_port = data_sock.getsockname()[1]
    send_cmd_to_ip(client_ip, f"SIZE {total_size} DATAPORT {data_port}")

    with open(filepath, "rb") as f:
        f.seek(offset); data = f.read()

    with _clients_lock:
        ports = _clients.get(client_ip, {}).get("cmd_ports", set())
    if ports:
        client_addr = (client_ip, next(iter(ports)))
    else:
        client_addr = (client_ip, 0)

    sender = SlidingWindowSender(data_sock, client_addr)
    start  = time.monotonic()
    try:
        _sent, elapsed = sender.send(data, PACKET_TYPE_DATA)
    except ConnectionError as e:
        save_session(client_id, "download:" + filename, offset)
        data_sock.close()
        print(f"[UDP-SERVER] Обрыв DOWNLOAD {filename}: {e}")
        return

    data_sock.close()
    bitrate = len(data) / elapsed / 1024
    delete_session(client_id, "download:" + filename)
    print(f"[UDP-SERVER] DOWNLOAD {filename} завершён, {bitrate:.1f} КБ/с")

def dispatch(line, client_ip, client_id):
    parts = line.strip().split(None, 1)
    if not parts: return True
    cmd  = parts[0].upper()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "HELLO":
        pass
    elif cmd == "ECHO":
        handle_echo(client_ip, args)
    elif cmd == "TIME":
        handle_time(client_ip)
    elif cmd in ("CLOSE", "EXIT", "QUIT"):
        send_cmd_to_ip(client_ip, "BYE"); return False
    elif cmd == "UPLOAD":
        handle_upload(client_ip, args, client_id)
    elif cmd == "DOWNLOAD":
        handle_download(client_ip, args, client_id)
    else:
        send_cmd_to_ip(client_ip, f"ERROR: неизвестная команда '{cmd}'")
    return True
    
def client_session(client_ip):
    client_id = client_ip  # Используем IP как ID
    print(f"[UDP-SERVER] Новый клиент {client_ip}")
    send_cmd_to_ip(client_ip, "Привет! Команды: ECHO TIME UPLOAD DOWNLOAD CLOSE")

    while True:
        line = recv_cmd_for_ip(client_ip, timeout=CLIENT_TIMEOUT)
        if line is None:
            # Таймаут - проверяем, активен ли еще клиент
            with _clients_lock:
                if client_ip not in _clients:
                    break
                last_act = _clients[client_ip].get("last_activity", 0)
                if time.monotonic() - last_act > CLIENT_TIMEOUT:
                    print(f"[UDP-SERVER] Клиент {client_ip} неактивен, завершаем сессию")
                    break
            continue

        print(f"[UDP-SERVER] {client_ip} -> {line!r}")
        if not dispatch(line, client_ip, client_id):
            break

    with _clients_lock:
        _clients.pop(client_ip, None)
    print(f"[UDP-SERVER] {client_ip} отключился")


def main():
    global _sock
    ensure_directories()
    _sock = create_server_socket()
    stop_evt = threading.Event()

    disp = threading.Thread(target=dispatcher, args=(stop_evt,), daemon=True)
    disp.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[UDP-SERVER] Остановлен по Ctrl+C")
    finally:
        stop_evt.set()
        _sock.close()
        time.sleep(1)


if __name__ == "__main__":
    main()
