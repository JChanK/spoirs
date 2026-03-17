from queue import Queue
import asyncio
from queue import PriorityQueue
from collections.abc import Awaitable, Callable
from threading import Thread
from typing import Any, Dict, List, Tuple
from common.AsyncConnection import AsyncConnection
from common.Connection import Connection
from common.Socket import Socket
from dataclasses import dataclass, field
from time import time
import socket


@dataclass
class Packet():
    number: int
    accepted: int
    data: bytes = field(compare=False)

    @staticmethod
    def from_bytes(data: bytes):
        return Packet(
            number=int.from_bytes(data[:8]),
            accepted=int.from_bytes(data[8:16]),
            data=data[16:]
        )

    def as_bytes(self) -> bytes:
        return self.number.to_bytes(8) + self.accepted.to_bytes(8) + self.data


class StreamDriver:
    def __init__(self, input: Queue[int], output: Queue[int], *, max_data_len = 64 * 1024):
        self.input = input
        self.output = output
        self.max_data_len = max_data_len
        self.input_packets: PriorityQueue[Packet] = PriorityQueue()
        self.unconfirmed_output_packets: List[Packet] = []
        self.next_input_index = 0
        self.next_output_index = 0


    async def consume(self, packet: Packet):
        self.input_packets.put(packet)
        while not self.input_packets.empty():
            packet = self.input_packets.get()
            self.__drop_confirmed_packets(packet.accepted)
            if packet.number > self.next_input_index:
                self.input_packets.put(packet)
                break
            if len(packet.data) == 0:
                break
            self.unconfirmed_output_packets.append(self.__create_packet(bytes()))
            if packet.number == self.next_input_index:
                self.next_input_index += 1
                for byte in packet.data:
                    await asyncio.to_thread(lambda: self.input.put(byte))
                

    async def produce(self) -> List[Packet]:
        async def get_output_part(count: int):
            for _ in range(min([count, self.output.qsize()])):
                yield await asyncio.to_thread(self.output.get)
        while not self.output.empty():
            data = bytes([x async for x in get_output_part(self.max_data_len)])
            self.unconfirmed_output_packets.append(self.__create_packet(data))
            self.next_output_index += 1
        if len(self.unconfirmed_output_packets) == 0:
            return [self.__create_packet(bytes())]
        return self.unconfirmed_output_packets


    def shutdown(self):
        self.input.shutdown(True)
        self.output.shutdown(True)


    def __drop_confirmed_packets(self, accepted: int):
        self.unconfirmed_output_packets = [p for p in self.unconfirmed_output_packets if p.number > accepted]


    def __create_packet(self, data: bytes) -> Packet:
        return Packet(
            number=self.next_output_index,
            accepted=self.next_input_index,
            data=data
        )


class UdpSocket(Socket):
    @dataclass
    class ConnectionEntry:
        connection: Connection
        driver: StreamDriver
        last_input_packet: float


    def __init__(self, ip: str, port: int, timeout: float = 1) -> None:
        super().__init__()
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.connectionMap: Dict[Tuple[str, int], UdpSocket.ConnectionEntry] = {}


    def connect(self) -> Connection:
        async def service():
            await entry.driver.consume(Packet(number=0, accepted=0, data=bytes()))
            await asyncio.wait([
                asyncio.create_task(self.__client_receiver(sock)),
                asyncio.create_task(self.__sender(sock)),
                asyncio.create_task(self.__garbage_collector()),
                asyncio.create_task(self.__client_closer())
            ], return_when=asyncio.FIRST_COMPLETED)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        address = (self.ip, self.port)
        entry = self.__create_connection_entry()
        self.connectionMap[address] = entry
        asyncio.create_task(service())
        return entry.connection


    def listen(self, handler: Callable[[Connection], Awaitable[Any]]):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind((self.ip, self.port))
        asyncio.get_event_loop().run_until_complete(asyncio.gather(
            self.__receiver(sock, handler),
            self.__sender(sock),
            self.__garbage_collector()
        ))


    async def __receiver(self, socket: socket.socket, handler: Callable[[Connection], Awaitable[Any]]):
        while True:
            data, address = await asyncio.get_event_loop().sock_recvfrom(socket, 0xFFFF)
            packet = Packet.from_bytes(data)
            if address not in self.connectionMap:
                if packet.number != 0:
                    break
                self.__connect(address, handler)
            entry = self.connectionMap[address]
            entry.last_input_packet = time()
            await entry.driver.consume(packet)


    async def __client_receiver(self, socket: socket.socket):
        while True:
            data, address = await asyncio.get_event_loop().sock_recvfrom(socket, 0xFFFF)
            packet = Packet.from_bytes(data)
            if address not in self.connectionMap:
                break
            entry = self.connectionMap[address]
            entry.last_input_packet = time()
            await entry.driver.consume(packet)


    async def __sender(self, socket: socket.socket):
        loop = asyncio.get_event_loop()
        while True:
            for address, entry in list(self.connectionMap.items()):
                packets = await entry.driver.produce()
                for packet in packets:
                    await loop.sock_sendto(socket, packet.as_bytes(), address)
            await asyncio.sleep(0.1)


    async def __garbage_collector(self):
        while True:
            for address, entry in list(self.connectionMap.items()):
                if self.__expired(entry.last_input_packet):
                    self.__disconnect(address)
            await asyncio.sleep(self.timeout)


    async def __client_closer(self):
        while True:
            _, entry = [*self.connectionMap.items()][0]
            if self.__expired(entry.last_input_packet):
                entry.driver.shutdown()
            await asyncio.sleep(self.timeout)


    def __create_connection_entry(self) -> ConnectionEntry:
        input = Queue()
        output = Queue()
        return UdpSocket.ConnectionEntry(
            connection=AsyncConnection(input, output),
            driver=StreamDriver(input, output),
            last_input_packet=time()
        )


    def __expired(self, last_action: float):
        return time() - last_action > self.timeout


    def __connect(self, address, handler):
        async def wrap():
            try:
                print('Run handler for', address)
                await handler(entry.connection)
            except:
                print('Drop', address, 'handler')
            finally:
                entry.driver.shutdown()
                loop.stop()

        entry = self.__create_connection_entry()
        self.connectionMap[address] = entry
        loop = asyncio.new_event_loop()
        Thread(target=loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(wrap(), loop)


    def __disconnect(self, address):
        entry = self.connectionMap[address]
        del self.connectionMap[address]
        entry.driver.shutdown()
        print('Host', address, 'was disconnected')

