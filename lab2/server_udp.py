import socket, threading, os, time, datetime
from udp_protocol import *

FILES_DIR = "server_files"
os.makedirs(FILES_DIR, exist_ok=True)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16 * 1024 * 1024)
sock.bind(('0.0.0.0', DEFAULT_PORT))

print(f"[SERVER] Запущен на {DEFAULT_PORT}")

while True:
    try:
        raw, addr = sock.recvfrom(4096)
        t = raw[0]

        if t in (PACKET_TYPE_DATA, PACKET_TYPE_FIN, PACKET_TYPE_ACK):
            continue

        cmd_str = raw.decode(errors='ignore').strip()
        cmd_parts = cmd_str.split(None, 1)
        if not cmd_parts: continue

        cmd = cmd_parts[0].upper()
        args = cmd_parts[1] if len(cmd_parts) > 1 else ""

        print(f"[SERVER] Запрос от {addr}: {cmd} {args}")

        if cmd == "ECHO":
            sock.setblocking(True)
            sock.sendto(f"ECHO: {args}".encode(), addr)

        elif cmd == "TIME":
            sock.setblocking(True)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sock.sendto(f"TIME: {now}".encode(), addr)

        elif cmd == "DOWNLOAD":
            path = os.path.join(FILES_DIR, args)
            if os.path.exists(path):
                size = os.path.getsize(path)
                sock.sendto(f"SIZE {size}".encode(), addr)
                data = open(path, "rb").read()
                print(f"[SERVER] Начинаю передачу {args}...")
                sender = SlidingWindowSender(sock, addr)
                sender.send(data)
                print(f"[SERVER] Передача завершена.")
            else:
                sock.sendto(b"ERROR: FILE NOT FOUND", addr)

        elif cmd == "UPLOAD":
            parts = args.split()
            if len(parts) < 2:
                sock.sendto(b"ERROR: INVALID UPLOAD FORMAT", addr)
                continue
            filename, size = parts[0], int(parts[1])
            path = os.path.join(FILES_DIR, filename)

            print(f"[SERVER] Ожидаю UPLOAD {filename} ({size} байт)...")
            sock.sendto(b"READY", addr)

            receiver = SlidingWindowReceiver(sock, addr, size)
            data = receiver.receive()
            with open(path, "wb") as f:
                f.write(data)
            print(f"[SERVER] UPLOAD {filename} завершен.")

        else:
            sock.sendto(b"ERROR: UNKNOWN COMMAND", addr)

    except Exception as e:
        print(f"[SERVER] Ошибка: {e}")