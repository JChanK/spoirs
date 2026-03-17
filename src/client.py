from common.AsyncTcpSocket import AsyncTcpSocket
from common.Channel import Channel
from common.Connection import ConnectionClosed
import argparse
import asyncio
import time, os
import readline
from rich.progress import Progress
from aioconsole import ainput

from common.UdpSocket import UdpSocket
from common.messages import CloseMessage, EchoMessage, MessageType, TimeRequestMessage, DownloadMessage, UploadMessage

commands = ['HELP', 'CLOSE', 'TIME', 'ECHO', 'DOWNLOAD', 'UPLOAD']

def completer(text, state):
    options = [cmd for cmd in commands if cmd.startswith(text.upper())]
    return options[state] if state < len(options) else None

readline.parse_and_bind('tab: complete')
readline.set_completer(completer)

def format_size(size_in_bytes: int) -> str:
    if size_in_bytes < 1024:
        return f"{size_in_bytes} B"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.2f} KB"
    elif size_in_bytes < 1024 * 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"


async def download(channel: Channel, filename: str, offset: int, totalLength: int, part: int):
    with open(filename, 'a'):
        pass
    try:
        with Progress() as progress:
            downloadTask = progress.add_task('[blue]Downloading...', total=totalLength)
            while totalLength - offset > 0:
                await channel.send(DownloadMessage(filename=filename, offset=offset, length=min(part, totalLength - offset)))
                message = await channel.next()
                data = message.body()
                with open(filename, 'rb+') as f:
                    f.truncate(offset + len(data))
                    f.seek(offset)
                    f.write(data)
                offset += len(data)
                progress.update(downloadTask, completed=offset)
    except KeyboardInterrupt:
        print('Downloading interrupted')


async def upload(channel: Channel, filename: str, offset: int, part: int):
    try:
        with open(filename, 'rb') as f:
            length = os.path.getsize(filename)
            with Progress() as progress:
                uploadTask = progress.add_task('[green]Uploading...', total=length)
                while length - offset > 0:
                    data = f.read(min(part, length - offset))
                    await channel.send(UploadMessage(
                        filename=filename,
                        offset=offset,
                        totalLength=length,
                        data=data
                    ))
                    offset += len(data)
                    await channel.next()
                    progress.update(uploadTask, completed=offset)
    except KeyboardInterrupt:
        print('Uploading interrupted')


async def handleCommand(channel: Channel, command: str, args: list[str]):
    def onHelp():
        print('''
        Available commands:
          CLOSE     - Close the connection
          TIME      - Get the current server time
          ECHO      - Send a message and receive it back
          DOWNLOAD  - Download a file from the server
          UPLOAD    - Upload a file to the server
          HELP      - Show this help message
        ''')


    async def onClose(channel: Channel):
        await channel.send(CloseMessage())
        await channel.next()


    async def onTime(channel: Channel):
        await channel.send(TimeRequestMessage())
        message = await channel.next()
        print(time.asctime(time.localtime(float(message.body().decode()))))


    async def onEcho(channel: Channel):
        await channel.send(EchoMessage(text=' '.join(args))) 
        message = await channel.next()
        print(message.body().decode()) 


    async def onDownload(channel: Channel):    
        if len(args) < 1:
            print('Invalid filename')
            return
        filename = args[0]
        part = int(args[1]) if len(args) >= 2 else 64 * 1024
        offset = 0
        if os.path.exists(filename):
            match input('''
            A file with this name already exists
            Continue/Overwrite/Abort? [C/O/A, default A] ''').upper():
                case 'C': offset = os.path.getsize(filename)
                case 'O': offset = 0
                case 'A': return
                case _: return
        await channel.send(DownloadMessage(filename=filename, offset=offset, length=1))
        message = await channel.next()
        if message.type() == MessageType.ECHO:
            print(message.body().decode()) 
            return
        rawFilename, rawOffset, rawTotalLength = message.args()
        start_time = time.time()
        await download(
            channel=channel,
            filename=rawFilename[:-1],
            offset=int(rawOffset),
            totalLength=int(rawTotalLength),
            part=part
        )
        end_time = time.time()
        speed = (os.path.getsize(filename) - offset) / (end_time - start_time)
        print(f"Speed: {format_size(int(speed))}/s")

    async def onUpload(channel: Channel):
        if len(args) < 1:
            print('Invalid filename')
            return
        filename = args[0]
        part = int(args[1]) if len(args) >= 2 else 64 * 1024
        if not os.path.exists(filename):
            print(f'{filename} not found')
            return
        start_time = time.time()
        await upload(channel=channel, filename=filename, offset=0, part=part)
        end_time = time.time()
        speed = os.path.getsize(filename)/ (end_time - start_time)
        print(f"Speed: {format_size(int(speed))}/s")
        
              
    match command:
        case 'HELP': onHelp()
        case 'CLOSE': await onClose(channel)
        case 'TIME': await onTime(channel)
        case 'ECHO': await onEcho(channel)
        case 'DOWNLOAD': await onDownload(channel)
        case 'UPLOAD': await onUpload(channel)
        case _: print('Unknown command')


async def main():
    parser = argparse.ArgumentParser(description="TCP Client")
    parser.add_argument("ip", help="Server IP address")
    parser.add_argument("port", type=int, help="Server port")
    args = parser.parse_args()
    try:
        connection = UdpSocket(args.ip, args.port).connect()
    except Exception as e:
        print(f"Failed to connect to {args.ip}:{args.port}")
        raise e
    channel = Channel(connection)

    while True:
        rawCommand = await ainput('~> ')
        if rawCommand == '':
            continue
        command, *args = rawCommand.split()
        await handleCommand(channel, command.upper(), args)


if __name__ == '__main__':
    try:
        asyncio.new_event_loop().run_until_complete(main())
    except ConnectionClosed:
        print('Connection closed')
    except BrokenPipeError:
        print('Server DOWN')   
    except ConnectionRefusedError:
        print('Server unavailable')
