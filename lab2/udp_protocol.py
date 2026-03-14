import socket, struct, time

PACKET_TYPE_DATA = 1
PACKET_TYPE_ACK = 2
PACKET_TYPE_FIN = 5
HEADER_FMT = "!BBHII"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
PAYLOAD_SIZE = 1400  
DEFAULT_PORT = 9091

class SlidingWindowSender:
    def __init__(self, sock, addr):
        self.sock, self.addr = sock, addr
        self.sock.setblocking(False)

    def send(self, data):
        self.sock.setblocking(False)

        chunks = [data[i:i + PAYLOAD_SIZE] for i in range(0, len(data), PAYLOAD_SIZE)]
        n = len(chunks)
        base = 0
        next_seq = 0
        window = 100

        print(f"[SENDER] Всего {n} пакетов")
        while base < n:
            while next_seq < base + window and next_seq < n:
                pkt = struct.pack(HEADER_FMT, PACKET_TYPE_DATA, 0, 0, next_seq, len(chunks[next_seq])) + chunks[
                    next_seq]
                try:
                    self.sock.sendto(pkt, self.addr)
                    next_seq += 1
                except:
                    break

            try:
                raw, _ = self.sock.recvfrom(64)
                _, _, _, ack_seq, _ = struct.unpack(HEADER_FMT, raw[:HEADER_SIZE])
                if ack_seq >= base:
                    base = ack_seq + 1
                    if base % 100 == 0: print(f"[SENDER] Прогресс: {base}/{n}")
            except:
                time.sleep(0.001)

        for _ in range(10): self.sock.sendto(struct.pack(HEADER_FMT, PACKET_TYPE_FIN, 0, 0, 0, 0), self.addr)
        print("[SENDER] Передача завершена")

        self.sock.setblocking(True)


class SlidingWindowReceiver:
    def __init__(self, sock, addr, total_bytes):
        self.sock, self.addr = sock, addr
        self.total = total_bytes

    def receive(self):
        buf = {}
        expected = 0
        received_bytes = 0
        data = bytearray()

        while received_bytes < self.total:
            try:
                raw, addr = self.sock.recvfrom(65536)
                t, _, _, seq, length = struct.unpack(HEADER_FMT, raw[:HEADER_SIZE])
                if t == PACKET_TYPE_FIN: break

                if seq not in buf:
                    buf[seq] = raw[HEADER_SIZE:HEADER_SIZE + length]
                    self.sock.sendto(struct.pack(HEADER_FMT, PACKET_TYPE_ACK, 0, 0, seq, 0), addr)

                while expected in buf:
                    chunk = buf.pop(expected)
                    received_bytes += len(chunk)
                    data.extend(chunk)
                    expected += 1
            except:
                continue
        return bytes(data)