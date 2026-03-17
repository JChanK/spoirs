from dataclasses import dataclass
from queue import Queue
import socket, asyncio, select
from threading import Thread

from common.AsyncConnection import AsyncConnection
from common.TcpConnection import TcpConnection
from common.Socket import Socket


class CancelationToken:
    def __init__(self): self.__canceled = False
    def canceled(self): return self.__canceled
    def cancel(self): self.__canceled = True


class AsyncTcpSocket(Socket):
    @dataclass
    class ConnectionEntry:
        socket: socket.socket
        input: Queue[int]
        output: Queue[int]


    def __init__(self, ip, port) -> None:
        super().__init__()
        self.ip = ip
        self.port = port
        self.active_connections: dict[socket.socket, AsyncTcpSocket.ConnectionEntry] = {}


    def connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.connect((self.ip, self.port))
        return TcpConnection(sock)


    def listen(self, handler):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.ip, self.port))
        sock.listen()

        token = CancelationToken()
        try:
            worker_loop = asyncio.new_event_loop()
            sender_loop = asyncio.new_event_loop()
            Thread(target=worker_loop.run_forever, daemon=True).start()
            Thread(target=sender_loop.run_forever, daemon=True).start()
            Thread(target=self.__receiver, args=[token], daemon=True).start()

            while True:
                connection, addr = sock.accept()
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)

                entry = AsyncTcpSocket.ConnectionEntry(
                    socket=connection,
                    input=Queue(),
                    output=Queue()
                )
                self.active_connections[entry.socket] = entry

                async def sender():
                    while not token.canceled():
                        await asyncio.to_thread(lambda: connection.send(bytes([entry.output.get()])))

                async def wrap():
                    try:
                        print('Open connection', addr)
                        await handler(AsyncConnection(entry.input, entry.output))
                    finally:
                        print('Close connection', addr)

                asyncio.run_coroutine_threadsafe(wrap(), worker_loop)
                asyncio.run_coroutine_threadsafe(sender(), sender_loop)
        finally:
            token.cancel()


    def __receiver(self, token: CancelationToken):
        while not token.canceled():
            _reads, _, _ = select.select(
                [e.socket for e in self.active_connections.values()],
                [],
                [],
                1
            )
            reads: list[socket.socket] = _reads
            if len(reads) == 0: continue
            for read in reads:
                entry = self.active_connections[read]
                data = read.recv(1024)
                if len(data) == 0:
                    entry.input.shutdown()
                    entry.output.shutdown()
                    continue
                for byte in data:
                    entry.input.put(byte)


