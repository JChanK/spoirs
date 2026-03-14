import os
import socket, time, sys
from udp_protocol import *

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server = (sys.argv[1], DEFAULT_PORT)

print("Клиент запущен. Введите 'DOWNLOAD имя_файла' или 'EXIT'")

while True:
    cmd = input("> ").split()
    if not cmd: continue
    if cmd[0] == "EXIT": break

    if cmd[0] == "DOWNLOAD":
        filename = cmd[1]
        sock.setblocking(False)
        try:
            while True: sock.recv(4096)
        except:
            pass
        sock.setblocking(True)

        sock.sendto(f"DOWNLOAD {filename}".encode(), server)
        resp, _ = sock.recvfrom(1024)

        parts = resp.decode().split()
        if len(parts) < 2 or parts[0] != "SIZE":
            print(f"Ошибка сервера или пустой ответ: {resp.decode()}")
            continue
        size = int(parts[1])

        print(f"[CLIENT] Скачиваю {filename} ({size} байт)...")
        start = time.monotonic()

        receiver = SlidingWindowReceiver(sock, server, size)
        data = receiver.receive()

        elapsed = time.monotonic() - start
        bitrate = (len(data) / 1024) / elapsed

        with open(f"down_{filename}", "wb") as f:
            f.write(data)
        print(f"[CLIENT] Готово! Время: {elapsed:.2f} сек. Скорость: {bitrate:.2f} КБ/с")
    if cmd[0] == "UPLOAD":
        filename = cmd[1]
        filepath = os.path.join(".", filename)
        if not os.path.exists(filepath):
            print("Файл не найден")
            continue

        data = open(filepath, "rb").read()
        size = len(data)

        sock.sendto(f"UPLOAD {filename} {size}".encode(), server)

        resp, _ = sock.recvfrom(1024)
        if resp == b"READY":
            print(f"[CLIENT] Отправляю {filename} ({size} байт)...")
            start = time.monotonic()

            sender = SlidingWindowSender(sock, server)
            sender.send(data)

            elapsed = time.monotonic() - start
            print(f"[CLIENT] Успешно! Передано: {size}, Скорость: {(size / 1024) / elapsed:.2f} КБ/с")
            print(f"[CLIENT] Время: {elapsed:.2f} сек.")
    elif cmd[0] == "ECHO":
        msg = " ".join(cmd[1:])
        sock.sendto(f"ECHO {msg}".encode(), server)
        resp, _ = sock.recvfrom(1024)
        print(f"[SERVER]: {resp.decode()}")

    elif cmd[0] == "TIME":
        sock.sendto(b"TIME", server)
        sock.settimeout(2.0)
        try:
            resp, _ = sock.recvfrom(1024)
            print(f"[SERVER ответ]: {resp.decode()}")
        except socket.timeout:
            print("[CLIENT] Ошибка: Сервер не ответил на команду TIME")
        finally:
            sock.settimeout(None)
