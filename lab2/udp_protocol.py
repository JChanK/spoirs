"""
Лабораторная работа №2 — UDP протокол с надёжной доставкой
Реализует: скользящее окно, подтверждения (ACK), повторную передачу пакетов
"""

import socket
import struct
import time
import threading

# ─── Константы протокола ──────────────────────────────────────────────────────
PACKET_TYPE_DATA   = 0x01
PACKET_TYPE_ACK    = 0x02
PACKET_TYPE_CMD    = 0x03
PACKET_TYPE_CMDACK = 0x04
PACKET_TYPE_FIN    = 0x05

HEADER_FORMAT   = "!BBHIQ"   # type(1) flags(1) window(2) seq(4) length(4)  -- 12 bytes
HEADER_SIZE     = struct.calcsize(HEADER_FORMAT)

BUFFER_SIZE     = 8192        # оптимальный размер UDP payload
WINDOW_SIZE     = 16          # размер скользящего окна (кол-во пакетов)
TIMEOUT         = 0.5         # таймаут ожидания ACK (сек)
MAX_RETRIES     = 10          # максимум повторных попыток
DEFAULT_PORT    = 9091


# ─── Упаковка / распаковка заголовка ─────────────────────────────────────────
def pack_header(ptype, seq, length, flags=0, window=WINDOW_SIZE):
    return struct.pack(HEADER_FORMAT, ptype, flags, window, seq, length)


def unpack_header(data):
    if len(data) < HEADER_SIZE:
        raise ValueError("Пакет слишком короткий")
    ptype, flags, window, seq, length = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    return ptype, flags, window, seq, length, data[HEADER_SIZE:]


# ─── Надёжная отправка одного пакета с повторными попытками ──────────────────
def send_reliable(sock, addr, ptype, seq, payload, ack_queue, stop_event):
    header = pack_header(ptype, seq, len(payload))
    packet = header + payload
    retries = 0

    while retries < MAX_RETRIES and not stop_event.is_set():
        sock.sendto(packet, addr)
        deadline = time.monotonic() + TIMEOUT

        while time.monotonic() < deadline and not stop_event.is_set():
            try:
                ack_seq = ack_queue.get(timeout=0.05)
                if ack_seq == seq:
                    return True
                ack_queue.put(ack_seq)   # вернуть чужой ACK
            except Exception:
                pass

        retries += 1

    return False


# ─── Класс ненадёжного канала (threading-safe очередь ACK) ───────────────────
class AckQueue:
    """Поточно-безопасная очередь для ACK-пакетов."""

    def __init__(self):
        import queue
        self._q = queue.Queue()

    def put(self, seq):
        self._q.put(seq)

    def get(self, timeout=0.05):
        return self._q.get(timeout=timeout)


# ─── Отправитель со скользящим окном ─────────────────────────────────────────
class SlidingWindowSender:
    """
    Отправляет данные чанками с использованием скользящего окна.
    Возвращает (bytes_sent, elapsed_sec) или бросает исключение.
    """

    def __init__(self, sock, addr, window_size=WINDOW_SIZE, timeout=TIMEOUT):
        self._sock   = sock
        self._addr   = addr
        self._window = window_size
        self._timeout = timeout
        self._lock   = threading.Lock()
        self._acks   = {}   # seq -> Event

    def _recv_acks(self, stop_event):
        """Фоновый поток: читает ACK-пакеты и сигналит нужным событиям."""
        self._sock.settimeout(0.1)
        while not stop_event.is_set():
            try:
                data, _ = self._sock.recvfrom(HEADER_SIZE + 4)
                ptype, _flags, _win, seq, _length, _payload = unpack_header(data)
                if ptype == PACKET_TYPE_ACK:
                    with self._lock:
                        ev = self._acks.get(seq)
                    if ev:
                        ev.set()
            except socket.timeout:
                pass
            except Exception:
                pass

    def send(self, data, ptype=PACKET_TYPE_DATA):
        chunks   = [data[i:i+BUFFER_SIZE] for i in range(0, len(data), BUFFER_SIZE)]
        n        = len(chunks)
        start    = time.monotonic()
        stop_ev  = threading.Event()

        t = threading.Thread(target=self._recv_acks, args=(stop_ev,), daemon=True)
        t.start()

        base   = 0
        next_  = 0

        while base < n:
            # Заполнить окно
            while next_ < n and next_ - base < self._window:
                ev = threading.Event()
                with self._lock:
                    self._acks[next_] = ev
                header  = pack_header(ptype, next_, len(chunks[next_]), window=self._window)
                packet  = header + chunks[next_]
                self._sock.sendto(packet, self._addr)
                next_ += 1

            # Ждём ACK на base
            with self._lock:
                ev = self._acks.get(base)

            if ev and ev.wait(timeout=self._timeout * MAX_RETRIES):
                with self._lock:
                    self._acks.pop(base, None)
                base += 1
            else:
                # Таймаут — повторная отправка всего окна
                with self._lock:
                    for seq in range(base, next_):
                        self._acks[seq] = threading.Event()
                for seq in range(base, next_):
                    header = pack_header(ptype, seq, len(chunks[seq]), window=self._window)
                    self._sock.sendto(header + chunks[seq], self._addr)
                next_ = base  # сдвинуть назад для заполнения окна

        stop_ev.set()
        t.join(timeout=1)
        return len(data), time.monotonic() - start


# ─── Приёмник со скользящим окном ────────────────────────────────────────────
class SlidingWindowReceiver:
    """
    Принимает чанки данных, отправляет ACK.
    Вызывать receive() в цикле; возвращает полный bytearray.
    """

    def __init__(self, sock, addr, total_bytes, ptype=PACKET_TYPE_DATA):
        self._sock   = sock
        self._addr   = addr
        self._total  = total_bytes
        self._ptype  = ptype
        self._buf    = {}
        self._received = 0

    def _send_ack(self, seq):
        header = pack_header(PACKET_TYPE_ACK, seq, 0)
        self._sock.sendto(header, self._addr)

    def receive(self):
        self._sock.settimeout(TIMEOUT * MAX_RETRIES)
        data      = bytearray()
        expected  = 0
        received  = 0

        while received < self._total:
            try:
                raw, addr = self._sock.recvfrom(HEADER_SIZE + BUFFER_SIZE)
            except socket.timeout:
                raise ConnectionError("Таймаут ожидания данных")

            ptype, _flags, _win, seq, length, payload = unpack_header(raw)

            if ptype != self._ptype:
                continue

            self._send_ack(seq)

            if seq == expected:
                data     += payload[:length]
                received += length
                expected += 1
                # Добавить буферизованные пакеты
                while expected in self._buf:
                    chunk     = self._buf.pop(expected)
                    data     += chunk
                    received += len(chunk)
                    expected += 1
            elif seq > expected:
                self._buf[seq] = payload[:length]

        return bytes(data)
