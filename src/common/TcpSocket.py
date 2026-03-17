import socket, asyncio, traceback

from common.TcpConnection import ConnectionClosed, TcpConnection
from common.Socket import Socket


class TcpSocket(Socket):
    def __init__(self, ip, port) -> None:
        super().__init__()
        self.ip = ip
        self.port = port

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
        while True:
            connection, addr = sock.accept()
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            conn = TcpConnection(connection)
            try:
                print('Open connection', addr)
                with connection:
                    asyncio.get_event_loop().run_until_complete(handler(conn))
            except ConnectionClosed:
                pass
            except Exception:
                print(traceback.format_exc())
            finally:
                print('Close connection', addr)

