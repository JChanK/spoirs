from abc import ABC, abstractmethod
from enum import Enum


class MessageType(Enum):
    CLOSE = 'CLOSE'
    ECHO = 'ECHO'
    GET_TIME = 'GET_TIME'
    RETURN_TIME = 'RETURN_TIME'
    DOWNLOAD_PART = 'DOWNLOAD_PART'
    UPLOAD_PART = 'UPLOAD_PART'


class Message(ABC):
    @abstractmethod
    def type(self) -> MessageType:
        pass

    @abstractmethod
    def args(self) -> list[str]:
        pass

    @abstractmethod
    def body(self) -> bytes:
        pass


class InMemoryMessage(Message):
    def __init__(self, type: MessageType, args: list[str], body: bytes) -> None:
        self._type = type
        self._args = args
        self._body = body

    def type(self) -> MessageType:
        return self._type

    def args(self) -> list[str]:
        return self._args

    def body(self) -> bytes:
        return self._body


class CloseMessage(InMemoryMessage):
    def __init__(self) -> None:
        super().__init__(MessageType.CLOSE, [], b'')


class EchoMessage(InMemoryMessage):
    def __init__(self, *, text: str) -> None:
        super().__init__(MessageType.ECHO, [], text.encode())


class TimeRequestMessage(InMemoryMessage):
    def __init__(self) -> None:
        super().__init__(MessageType.GET_TIME, [], b'')


class TimeResponseMessage(InMemoryMessage):
    def __init__(self, *, timestamp: float) -> None:
        super().__init__(MessageType.RETURN_TIME, [], str(timestamp).encode())


class DownloadMessage(InMemoryMessage):
    def __init__(self, *, filename: str, offset: int = 0, length = 0) -> None:
        super().__init__(MessageType.DOWNLOAD_PART, [filename, str(offset), str(length)], b'')


class UploadMessage(InMemoryMessage):
    def __init__(self, filename: str, offset: int, totalLength: int, data: bytes) -> None:
        super().__init__(MessageType.UPLOAD_PART, [filename, str(offset), str(totalLength)], data)