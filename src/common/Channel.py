from common.Connection import Connection
from common.messages import InMemoryMessage, Message, MessageType


class Channel:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection


    async def next(self) -> Message:
        type, raw_argn, raw_body_len = (await self.connection.receiveLine()).split()
        argn, body_len = int(raw_argn), int(raw_body_len)
        args = []
        for _ in range(argn):
            args.append(await self.connection.receiveLine())
        body = await self.connection.receiveBytes(body_len)
        return InMemoryMessage(MessageType[type], args, body)


    async def send(self, message: Message):
        args = message.args()
        body = message.body()
        await self.connection.sendLine(f'{message.type().value} {len(args)} {len(body)}')
        for arg in args:
            await self.connection.sendLine(arg)
        await self.connection.sendBytes(body)


