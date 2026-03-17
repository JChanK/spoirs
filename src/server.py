import asyncio
from typing import Union
from common.AsyncTcpSocket import AsyncTcpSocket
from common.Channel import Channel, Message
from common.Router import Router
from common.Connection import Connection, ConnectionClosed
import time, os

from common.UdpSocket import UdpSocket
from common.messages import EchoMessage, MessageType, TimeResponseMessage, UploadMessage


router = Router()


@router.on(MessageType.CLOSE)
async def onClose(_):
    raise ConnectionClosed


@router.on(MessageType.ECHO)
async def onEcho(message: Message):
    return message


@router.on(MessageType.GET_TIME)
async def onTime(_):
    return TimeResponseMessage(timestamp=time.time())


@router.on(MessageType.DOWNLOAD_PART)
async def onDownload(message: Message):
    print('download')
    rawFilename, rawOffset, rawLength = message.args()
    filename, offset, length = realFilename(rawFilename), int(rawOffset), int(rawLength)
    if not filename:
        return EchoMessage(text='Invalid filename')
    if not os.path.exists(filename):
        return EchoMessage(text=f'Not found {filename}')
    with open(filename, 'rb') as f:
        f.seek(offset)
        data = f.read(length if length > 0 else -1)
        return UploadMessage(
            filename=rawFilename,
            offset=offset,
            totalLength=os.path.getsize(filename),
            data=data
        )


@router.on(MessageType.UPLOAD_PART)
async def onUpload(message: Message):
    rawFilename, rawOffset, rawTotalLength = message.args()
    filename, offset, totalLength = realFilename(rawFilename), int(rawOffset), int(rawTotalLength)
    data = message.body()
    if not filename:
        return EchoMessage(text='Invalid filename')
    with open(filename, 'a'):
        pass
    with open(filename, 'rb+') as f:
        f.truncate(offset + len(data))
        f.seek(offset)
        f.write(data)
        return EchoMessage(text=f'Success to upload {len(data)} bytes')


@router.otherwise
async def unknown(message: Message):
    return EchoMessage(text=f'Unknown command: {message.type().value} {message.args()} {message.body()}')


async def handle(connection: Connection):
    channel = Channel(connection)
    while True:
        response = await router.route(await channel.next())
        if response:
            await channel.send(response)


def realFilename(rawFilename: str) -> Union[str, None]:
    normFilename = os.path.normpath(rawFilename.strip())
    if normFilename.startswith('../') or normFilename == '..':
        return None
    return f'kitchen-midden/{normFilename}'


def main():
    UdpSocket('0.0.0.0', 8080).listen(handle)


if __name__ == "__main__":
    main()
