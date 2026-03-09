"""
Лабораторная работа №2 — UDP протокол с надёжной доставкой
"""

import socket
import struct
import time
import threading
from queue import Queue, Empty
import sys

# ─── Константы протокола ──────────────────────────────────────────────────────
PACKET_TYPE_DATA   = 0x01
PACKET_TYPE_ACK    = 0x02
PACKET_TYPE_CMD    = 0x03
PACKET_TYPE_CMDACK = 0x04
PACKET_TYPE_FIN    = 0x05

# ВАЖНО: правильный формат - 12 байт
HEADER_FORMAT   = "!BBHII"   # type(1) flags(1) window(2) seq(4) length(4)
HEADER_SIZE     = struct.calcsize(HEADER_FORMAT)

# Максимальный размер данных в одном пакете
# 1472 - максимальный UDP payload без фрагментации (1500 MTU - 20 IP - 8 UDP)
# Но для локальной сети можно больше
MAX_PAYLOAD_SIZE = 8192  # Оптимальный размер для jumbo frames
BUFFER_SIZE = MAX_PAYLOAD_SIZE

WINDOW_SIZE     = 64          # размер скользящего окна
TIMEOUT         = 0.2         # таймаут ожидания ACK
MAX_RETRIES     = 5          # максимум повторных попыток
DEFAULT_PORT    = 9091


# ─── Упаковка / распаковка заголовка ─────────────────────────────────────────
def pack_header(ptype, seq, length, flags=0, window=WINDOW_SIZE):
    """Упаковка заголовка в 12 байт"""
    return struct.pack(HEADER_FORMAT, ptype, flags, window, seq, length)


def unpack_header(data):
    """Распаковка заголовка из 12 байт"""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Пакет слишком короткий: {len(data)} < {HEADER_SIZE}")
    ptype, flags, window, seq, length = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    return ptype, flags, window, seq, length, data[HEADER_SIZE:]


# ─── Отправитель со скользящим окном ─────────────────────────────────────────
class SlidingWindowSender:
    def __init__(self, sock, addr, window_size=WINDOW_SIZE, timeout=TIMEOUT):
        self._sock = sock
        self._addr = addr
        self._window = window_size
        self._timeout = timeout
        self._lock = threading.Lock()
        self._acks = {}  # seq -> Event
        self._stop_event = threading.Event()
        self._recv_thread = None
        self._start_recv_thread()

    def _start_recv_thread(self):
        """Запуск потока для приема ACK"""
        self._recv_thread = threading.Thread(target=self._recv_acks, daemon=True)
        self._recv_thread.start()

    def _recv_acks(self):
        """Фоновый поток: читает ACK-пакеты"""
        self._sock.settimeout(0.1)
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(HEADER_SIZE + 4)
                if addr != self._addr:
                    continue

                ptype, _flags, _win, seq, _length, _payload = unpack_header(data)

                if ptype == PACKET_TYPE_ACK:
                    with self._lock:
                        ev = self._acks.get(seq)
                        if ev:
                            ev.set()

            except socket.timeout:
                continue
            except Exception as e:
                print(f"[DEBUG] Ошибка в _recv_acks: {e}")
                continue

    def send(self, data, ptype=PACKET_TYPE_DATA):
        """
        Отправка данных с использованием скользящего окна
        Возвращает (bytes_sent, elapsed_sec)
        """
        # Разбиваем данные на чанки
        chunks = []
        for i in range(0, len(data), BUFFER_SIZE):
            chunks.append(data[i:i+BUFFER_SIZE])

        n = len(chunks)
        if n == 0:
            return 0, 0

        print(f"[DEBUG] Отправка {len(data)} байт = {n} пакетов, размер окна {self._window}")

        start = time.monotonic()
        base = 0
        next_seq = 0

        while base < n:
            # Заполняем окно
            while next_seq < n and next_seq - base < self._window:
                ev = threading.Event()
                with self._lock:
                    self._acks[next_seq] = ev

                # Отправляем пакет - УБЕДИМСЯ что размер не превышает лимит
                chunk = chunks[next_seq]
                if len(chunk) > BUFFER_SIZE:
                    print(f"[ERROR] Пакет {next_seq} слишком большой: {len(chunk)} > {BUFFER_SIZE}")
                    chunk = chunk[:BUFFER_SIZE]

                header = pack_header(ptype, next_seq, len(chunk), window=self._window)
                packet = header + chunk

                # Проверка размера перед отправкой
                if len(packet) > 65507:  # Максимальный размер UDP пакета
                    print(f"[ERROR] Пакет {next_seq} превышает лимит UDP: {len(packet)}")
                    # Разбиваем на еще меньшие части
                    self._split_and_send(chunk, next_seq, ptype)
                else:
                    self._sock.sendto(packet, self._addr)

                next_seq += 1

            # Ждем ACK на base
            with self._lock:
                ev = self._acks.get(base)

            if ev and ev.wait(timeout=self._timeout * 3):
                with self._lock:
                    self._acks.pop(base, None)
                base += 1
                if base % 10 == 0:
                    print(f"[DEBUG] Прогресс: {base}/{n} пакетов")
            else:
                # Таймаут - повторная отправка
                print(f"[DEBUG] Таймаут на пакете {base}, повторная отправка")
                with self._lock:
                    for seq in range(base, next_seq):
                        self._acks[seq] = threading.Event()

                for seq in range(base, next_seq):
                    header = pack_header(ptype, seq, len(chunks[seq]), window=self._window)
                    self._sock.sendto(header + chunks[seq], self._addr)

        elapsed = time.monotonic() - start
        bitrate = (len(data) / 1024) / elapsed
        print(f"[DEBUG] Отправка завершена за {elapsed:.2f} сек, скорость {bitrate:.1f} КБ/с")

        # Останавливаем поток приема
        self._stop_event.set()
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=1)

        return len(data), elapsed

    def _split_and_send(self, data, original_seq, ptype):
        """Разбивает большой кусок на меньшие и отправляет"""
        print(f"[DEBUG] Разбиваем пакет {original_seq} размером {len(data)} на меньшие")
        sub_chunks = [data[i:i+1400] for i in range(0, len(data), 1400)]

        for i, sub_chunk in enumerate(sub_chunks):
            sub_seq = original_seq * 1000 + i  # Условный номер
            header = pack_header(ptype, sub_seq, len(sub_chunk), window=self._window)
            packet = header + sub_chunk
            self._sock.sendto(packet, self._addr)
            time.sleep(0.001)  # Небольшая задержка


# ─── Приёмник со скользящим окном ────────────────────────────────────────────
class SlidingWindowReceiver:
    def __init__(self, sock, addr, total_bytes, ptype=PACKET_TYPE_DATA):
        self._sock = sock
        self._addr = addr
        self._total = total_bytes
        self._ptype = ptype
        self._buf = {}
        self._received = 0
        # Увеличиваем буфер сокета
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)  # 1 МБ
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)  # 1 МБ
        self._sock.settimeout(TIMEOUT * MAX_RETRIES)

    def _send_ack(self, seq):
        """Отправка подтверждения"""
        header = pack_header(PACKET_TYPE_ACK, seq, 0)
        try:
            self._sock.sendto(header, self._addr)
        except Exception as e:
            print(f"[DEBUG] Ошибка отправки ACK: {e}")

    def receive(self):
        """Прием данных с подтверждением"""
        data = bytearray()
        expected = 0
        received_bytes = 0
        last_report = time.monotonic()

        print(f"[DEBUG] Прием {self._total} байт")

        while received_bytes < self._total:
            try:
                raw, addr = self._sock.recvfrom(HEADER_SIZE + BUFFER_SIZE + 1024)  # +1024 запас

                if addr != self._addr:
                    continue

                # Проверяем минимальную длину
                if len(raw) < HEADER_SIZE:
                    print(f"[DEBUG] Слишком короткий пакет: {len(raw)} байт")
                    continue

                try:
                    ptype, _flags, _win, seq, length, payload = unpack_header(raw)
                except Exception as e:
                    print(f"[DEBUG] Ошибка распаковки: {e}")
                    continue

                # Проверяем длину payload
                if len(payload) < length:
                    print(f"[DEBUG] Неполный payload: ожидалось {length}, получено {len(payload)}")
                    continue

                if ptype != self._ptype:
                    print(f"[DEBUG] Неверный тип пакета: {ptype}")
                    continue

                # Отправляем ACK
                self._send_ack(seq)

                # Обработка пакета
                if seq == expected:
                    data.extend(payload[:length])
                    received_bytes += length
                    expected += 1

                    # Добавляем буферизованные пакеты
                    while expected in self._buf:
                        chunk = self._buf.pop(expected)
                        data.extend(chunk)
                        received_bytes += len(chunk)
                        expected += 1

                    # Отчет о прогрессе каждые 5 секунд
                    now = time.monotonic()
                    if now - last_report > 5:
                        progress = (received_bytes / self._total) * 100
                        print(f"[DEBUG] Прогресс: {progress:.1f}% ({received_bytes}/{self._total} байт)")
                        last_report = now

                elif seq > expected:
                    self._buf[seq] = payload[:length]
                    if len(self._buf) % 10 == 0:
                        print(f"[DEBUG] Буфер: {len(self._buf)} пакетов")

            except socket.timeout:
                print(f"[DEBUG] Таймаут приема, ожидаем пакет {expected}")
                continue
            except Exception as e:
                print(f"[DEBUG] Ошибка в receive: {e}")
                raise ConnectionError(f"Ошибка приема данных: {e}")

        print(f"[DEBUG] Прием завершен, получено {received_bytes} байт")
        return bytes(data)
