from typing import Awaitable, Callable, Self, Union
from common.messages import Message, MessageType


Handler = Callable[[Message], Awaitable[Union[Message, None]]]


class Router:
    def __init__(self):
        self.map = dict()
        self._otherwise = None


    def on(self, type: MessageType):
        def decorator(handler: Handler):
            self.map[type] = handler
        return decorator


    def otherwise(self, handler: Handler):
        self._otherwise = handler


    async def route(self, message: Message) -> Union[Message, None]:
        return await self.map.get(message.type(), self._otherwise)(message)
