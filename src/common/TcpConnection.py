from common.Connection import Connection, ConnectionClosed
from socket import socket


class TcpConnection(Connection):
    def __init__(self, connection: socket) -> None:
        self.connection = connection

    async def receiveLine(self) -> str:
        data = bytes()
        while len(data) == 0 or data[-1] != b'\n'[0]:
            part = self.connection.recv(1)
            if len(part) == 0:
                raise ConnectionClosed
            data += part
        return data.decode()

    async def receiveBytes(self, length: int) -> bytes:
        data = bytes()
        while len(data) < length:
            part = self.connection.recv(length - len(data))
            if len(part) == 0:
                raise ConnectionClosed
            data += part
        return data

    async def sendLine(self, str: str):
        self.connection.send(f'{str.strip()}\n'.encode())

    async def sendBytes(self, bytes):
        self.connection.send(bytes)

