import asyncio
from common.Connection import Connection, ConnectionClosed
from queue import Queue, ShutDown


class AsyncConnection(Connection):

    def __init__(self, input: Queue[int], output: Queue[int]) -> None:
        super().__init__()
        self.input = input
        self.output = output

    async def receiveLine(self) -> str:
        data = bytes()
        while len(data) == 0 or data[-1] != b'\n'[0]:
            try:
                byte = bytes([await asyncio.to_thread(self.input.get)])
            except ShutDown:
                raise ConnectionClosed
            data += byte
        return data.decode()

    async def receiveBytes(self, length: int) -> bytes:
        data = bytes()
        while len(data) < length:
            try:
                byte = bytes([await asyncio.to_thread(self.input.get)])
            except ShutDown:
                raise ConnectionClosed
            data += byte
        return data

    async def sendLine(self, str: str):
        await self.sendBytes(f'{str.strip()}\n'.encode())

    async def sendBytes(self, bytes):
        for byte in bytes:
            try:
                await asyncio.to_thread(lambda: self.output.put(byte))
            except ShutDown:
                raise ConnectionClosed


