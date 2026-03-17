from abc import ABC, abstractmethod


class ConnectionClosed(Exception):
    pass


class Connection(ABC):
    @abstractmethod
    async def receiveLine(self) -> str:
        pass

    @abstractmethod
    async def receiveBytes(self, length) -> bytes:
        pass

    @abstractmethod
    async def sendLine(self, str):
        pass

    @abstractmethod
    async def sendBytes(self, bytes):
        pass
