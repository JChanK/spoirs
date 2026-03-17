from abc import ABC, abstractmethod
from common.Connection import Connection
from typing import Callable, Awaitable, Any

class Socket(ABC):
    @abstractmethod
    def connect(self) -> Connection:
        pass

    @abstractmethod
    def listen(self, handler: Callable[[Connection], Awaitable[Any]]):
        pass
