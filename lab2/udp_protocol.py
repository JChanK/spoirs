"""
Лабораторная работа №2 — UDP протокол с надёжной доставкой
Механизмы: ACK, повторная передача, скользящее окно (Go-Back-N)
Исправленная версия с корректной обработкой ACK
"""

import socket
import struct
import threading
import time

PACKET_TYPE_DATA   = 0x01
PACKET_TYPE_ACK    = 0x02
PACKET_TYPE_CMD    = 0x03
PACKET_TYPE_CMDACK = 0x04
PACKET_TYPE_FIN    = 0x05

HEADER_FORMAT = "!BBHII"
HEADER_SIZE   = struct.calcsize(HEADER_FORMAT)  # 12 байт

MAX_PAYLOAD_SIZE = 8192
BUFFER_SIZE      = MAX_PAYLOAD_SIZE

WINDOW_SIZE  = 64
TIMEOUT      = 0.5
MAX_RETRIES  = 20
DEFAULT_PORT = 9091


def pack_header(ptype, seq, length, flags=0, window=WINDOW_SIZE):
    return struct.pack(HEADER_FORMAT, ptype, flags, window, seq, length)


def unpack_header(data):
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Пакет слишком короткий: {len(data)} < {HEADER_SIZE}")
    ptype, flags, window, seq, length = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    return ptype, flags, window, seq, length, data[HEADER_SIZE:]


# В начало файла добавить:
MAX_PAYLOAD_SIZE = 8192  # 8 КБ


class SlidingWindowSender:
    def __init__(self, sock, addr, window_size=WINDOW_SIZE, timeout=TIMEOUT):
        self._sock = sock
        self._addr = addr
        self._window = window_size
        self._timeout = timeout
        self._interrupted = False
        self._acked_count = 0
        self._total_acked = 0

    def interrupt(self):
        """Прервать передачу"""
        self._interrupted = True

    def get_acked_count(self):
        """Получить количество подтвержденных пакетов"""
        return self._total_acked

    def send(self, data, ptype=PACKET_TYPE_DATA):
        chunks = [data[i:i + MAX_PAYLOAD_SIZE]
                  for i in range(0, max(len(data), 1), MAX_PAYLOAD_SIZE)]
        n = len(chunks)
        if n == 0:
            return 0, 0.0

        print(f"[PROTO] Отправка {len(data)} байт = {n} пакетов, окно={self._window}")

        original_timeout = self._sock.gettimeout()
        self._sock.settimeout(self._timeout)

        local_port = self._sock.getsockname()[1]
        print(f"[PROTO] DATA сокет на порту: {local_port}")

        start = time.monotonic()
        base = 0
        next_seq = 0
        retries = 0
        acked = set()
        self._total_acked = 0

        def send_pkt(seq):
            chunk = chunks[seq]
            pkt = pack_header(ptype, seq, len(chunk), window=local_port) + chunk
            try:
                self._sock.sendto(pkt, self._addr)
                if seq % 100 == 0:
                    print(f"[PROTO] Отправлен пакет seq={seq}")
            except OSError as e:
                print(f"[PROTO] Ошибка отправки: {e}")

        # Отправляем первые пакеты
        while next_seq < n and next_seq - base < self._window and not self._interrupted:
            send_pkt(next_seq)
            next_seq += 1

        while base < n and not self._interrupted:
            try:
                raw, addr = self._sock.recvfrom(HEADER_SIZE + 4)

                if addr[0] != self._addr[0]:
                    continue

                ptype_ack, _f, _w, seq, _l, _p = unpack_header(raw)

                if ptype_ack == PACKET_TYPE_ACK:
                    acked.add(seq)
                    # Обновляем общее количество подтвержденных пакетов
                    self._total_acked = max(self._total_acked, len(acked))

                    if seq % 100 == 0:
                        print(f"[PROTO] Получен ACK для seq={seq}")

                    # Сдвигаем base если подтвержден самый старый неподтвержденный
                    if seq == base or base in acked:
                        while base in acked:
                            acked.discard(base)
                            base += 1
                            retries = 0
                            if base % 100 == 0:
                                print(f"[PROTO] Прогресс: {base}/{n} пакетов")

            except socket.timeout:
                if base >= n:
                    break

                # Проверяем, не подтвердился ли base
                if base in acked:
                    while base in acked:
                        acked.discard(base)
                        base += 1
                        retries = 0
                else:
                    retries += 1
                    if retries > MAX_RETRIES:
                        self._sock.settimeout(original_timeout)
                        raise ConnectionError(f"Превышено {MAX_RETRIES} повторов на seq={base}")

                    print(f"[PROTO] Таймаут, повтор {retries}/{MAX_RETRIES} для seq={base}")

                    # Пересылаем все неподтвержденные пакеты в окне
                    for seq in range(base, min(base + self._window, next_seq)):
                        if seq not in acked:
                            send_pkt(seq)

            except Exception as e:
                print(f"[PROTO] Ошибка приема ACK: {e}")
                continue

            # Заполняем окно новыми пакетами
            while next_seq < n and next_seq - base < self._window and not self._interrupted:
                if next_seq not in acked:
                    send_pkt(next_seq)
                next_seq += 1

        self._sock.settimeout(original_timeout)

        elapsed = time.monotonic() - start or 0.001

        if self._interrupted:
            acked_bytes = self._total_acked * MAX_PAYLOAD_SIZE
            print(f"[PROTO] Передача прервана, подтверждено {self._total_acked} пакетов ({acked_bytes} байт)")
            return acked_bytes, elapsed

        print(f"[PROTO] Готово: {len(data)} байт за {elapsed:.2f}с, "
              f"{len(data) / 1024 / elapsed:.1f} КБ/с")
        return len(data), elapsed


class SlidingWindowReceiver:
    def __init__(self, sock, addr, total_bytes, ptype=PACKET_TYPE_DATA, timeout=30):
        self._sock = sock
        self._addr = addr  # (ip, port) клиента
        self._total = total_bytes
        self._ptype = ptype
        self._buf = {}
        self._last_report = time.monotonic()
        self._received = 0
        self._timeout = timeout
        self._last_packet_time = time.monotonic()

    def receive(self):
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
        except OSError:
            pass

        original_timeout = self._sock.gettimeout()
        self._sock.settimeout(1.0)  # Таймаут 1 секунда для возможности прерывания

        data = bytearray()
        expected = 0
        received = 0
        idle = 0
        max_idle = self._timeout  # Максимальное время простоя в секундах

        print(f"[PROTO] Ожидаю {self._total} байт от {self._addr[0]}")

        while received < self._total:
            try:
                raw, addr = self._sock.recvfrom(65536)
                self._last_packet_time = time.monotonic()
                idle = 0
            except socket.timeout:
                idle += 1
                # Проверяем общий таймаут
                if time.monotonic() - self._last_packet_time > max_idle:
                    self._sock.settimeout(original_timeout)
                    raise TimeoutError(f"Таймаут приема: {received}/{self._total}")
                if idle % 10 == 0:
                    print(f"[PROTO] Ожидание пакета seq={expected} (таймаут {idle})")
                continue
            except OSError as e:
                self._sock.settimeout(original_timeout)
                raise ConnectionError(str(e))

            # Проверяем IP отправителя
            if addr[0] != self._addr[0]:
                continue

            try:
                ptype, _f, ack_port, seq, length, payload = unpack_header(raw)
            except ValueError:
                continue

            if ptype != self._ptype:
                continue
            if len(payload) < length:
                continue

            # Отправляем ACK на порт, указанный в пакете
            try:
                ack_pkt = pack_header(PACKET_TYPE_ACK, seq, 0)
                self._sock.sendto(ack_pkt, (addr[0], ack_port))

                if seq % 100 == 0 or seq == expected:
                    print(f"[PROTO] Отправлен ACK для seq={seq} на порт {ack_port}")

            except OSError as e:
                print(f"[PROTO] Ошибка отправки ACK: {e}")

            # Обработка полученного пакета
            if seq == expected:
                data.extend(payload[:length])
                received += len(payload[:length])
                expected += 1

                # Добавляем буферизованные пакеты
                while expected in self._buf:
                    chunk = self._buf.pop(expected)
                    data.extend(chunk)
                    received += len(chunk)
                    expected += 1

            elif seq > expected:
                # Буферизуем пакеты, пришедшие не по порядку
                if seq not in self._buf:
                    self._buf[seq] = payload[:length]
                    if len(self._buf) % 50 == 0:
                        print(f"[PROTO] Буфер: {len(self._buf)} пакетов, ожидаем {expected}")

            # Отчет о прогрессе
            now = time.monotonic()
            if now - self._last_report > 2:
                progress = (received / self._total) * 100
                print(f"[PROTO] {progress:.1f}% ({received}/{self._total})")
                self._last_report = now

        self._sock.settimeout(original_timeout)
        print(f"[PROTO] Принято {received} байт")
        return bytes(data)