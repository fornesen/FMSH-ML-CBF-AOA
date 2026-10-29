"""Board-owned AOA1 fan-out. Subscribers never start an ADC capture."""
import argparse
import asyncio
import contextlib
from dataclasses import dataclass
import json
import struct

MAGIC = b'AOA1'
MAX_PACKET = 1024 * 1024
MODES = {'equal', 'manual', 'follow', 'auto', 'pause', 'acquisition', 'processing', 'observation', 'archive'}


def encode(value):
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    return MAGIC + struct.pack('!I', len(body)) + body


async def read_frame(reader, limit=MAX_PACKET):
    header = await reader.readexactly(8)
    size = struct.unpack('!I', header[4:])[0]
    if header[:4] != MAGIC or not 0 < size <= limit:
        raise ValueError('Invalid AOA1 frame')
    body = await reader.readexactly(size)
    value = json.loads(body)
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1:
        raise ValueError('Invalid AOA1 object')
    return header + body, value


@dataclass(eq=False)
class Subscriber:
    writer: object
    queue: asyncio.Queue
    sender: object = None
    archive_url: str = ''

    def offer(self, frame, delivered=None):
        if self.writer.is_closing():
            return False
        try:
            self.queue.put_nowait((frame, delivered))
            return True
        except asyncio.QueueFull:
            self.writer.close()
            return False


@dataclass(eq=False)
class Producer:
    writer: object
    source: str
    session: str
    sequence: int = 0


class Hub:
    def __init__(self, queue_frames=8, write_timeout=2, on_receivers=None):
        self.queue_frames = queue_frames
        self.write_timeout = write_timeout
        self.clients = set()
        self.producers = {}
        self.pending = None
        self.servers = []
        self.connections = set()
        self.tasks = set()
        self.closing = False
        self.on_receivers = on_receivers

    def receivers_changed(self):
        if self.on_receivers is not None:
            self.on_receivers(tuple(sorted({c.archive_url for c in self.clients
                                            if c.archive_url and not c.writer.is_closing()})))

    async def start(self, ingress_host='127.0.0.1', ingress_port=9001,
                    client_host='0.0.0.0', client_port=9000):
        try:
            ingress = await asyncio.start_server(self.receive_producer, ingress_host, ingress_port)
            self.servers.append(ingress)
            clients = await asyncio.start_server(self.receive_subscriber, client_host, client_port)
            self.servers.append(clients)
            self.ingress_port = ingress.sockets[0].getsockname()[1]
            self.client_port = clients.sockets[0].getsockname()[1]
        except BaseException:
            await self.close()
            raise

    async def close(self):
        self.closing = True
        self.pending = None
        for server in self.servers:
            server.close()
        for writer in tuple(self.connections):
            writer.close()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for server in self.servers:
            await server.wait_closed()
        self.servers.clear()

    async def send_queue(self, client):
        delivered = None
        try:
            while True:
                frame, delivered = await client.queue.get()
                client.writer.write(frame)
                await asyncio.wait_for(client.writer.drain(), self.write_timeout)
                if delivered is not None and not delivered.done():
                    delivered.set_result(True)
                delivered = None
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            if delivered is not None and not delivered.done():
                delivered.set_result(False)
            while not client.queue.empty():
                _, waiting = client.queue.get_nowait()
                if waiting is not None and not waiting.done():
                    waiting.set_result(False)
            client.writer.close()

    async def reply(self, client, command, status, error):
        value = dict(schema=1, type='control_reply', session=command.get('session'),
                     id=command.get('id'), mode=command.get('mode'), status=status, error=error)
        delivered = asyncio.get_running_loop().create_future()
        if client.offer(encode(value), delivered):
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(delivered, self.write_timeout)

    async def release_producer(self, producer):
        if self.producers.get(producer.source) is not producer:
            return
        del self.producers[producer.source]
        producer.writer.close()
        if self.pending and self.pending['producer'] is producer:
            pending, self.pending = self.pending, None
            await self.reply(pending['client'], pending['command'], 'unknown', '板端处理连接中断，结果未知')
        # Reconnect makes old data visibly stale; no cached frame is replayed as fresh.
        for client in tuple(self.clients):
            client.writer.close()

    def acknowledge(self, producer, packet):
        pending = self.pending
        if not pending or pending['producer'] is not producer:
            return
        command = pending['command']
        if packet.get('session') != command['session']:
            return
        ack = packet.get('beam', {})
        if command['mode'] in ('acquisition', 'processing', 'observation', 'archive'):
            ack = ack.get(command['mode'], {}) if isinstance(ack, dict) else {}
        if (isinstance(ack, dict) and ack.get('command_id') == command['id']
                and isinstance(ack.get('command_status'), str)
                and ack['command_status'] not in ('', 'pending', 'idle')):
            self.pending = None

    async def control(self, client, frame, command):
        if (command.get('type') != 'beam_command' or not isinstance(command.get('mode'), str)
                or command['mode'] not in MODES
                or not isinstance(command.get('id'), str) or not 1 <= len(command['id']) <= 64
                or not isinstance(command.get('session'), str) or not 1 <= len(command['session']) <= 128):
            raise ValueError('Invalid control command')
        producer = self.producers.get('aoa')
        error = ('板端采集进程未连接' if producer is None else
                 '板端会话已更新' if command['session'] != producer.session else
                 '上一条命令尚未完成' if self.pending else '')
        if error:
            await self.reply(client, command, 'rejected', error)
            return
        if command['mode'] == 'archive':
            if type(command.get('archive_enabled')) is not bool:
                await self.reply(client, command, 'rejected', '归档开关必须为布尔值')
                return
            if command['archive_enabled'] and not any(c.archive_url and not c.writer.is_closing()
                                                       for c in self.clients):
                await self.reply(client, command, 'rejected', '请先连接电脑原始归档接收端')
                return
        self.pending = dict(producer=producer, client=client, command=command)
        try:
            producer.writer.write(frame)
            await asyncio.wait_for(producer.writer.drain(), self.write_timeout)
        except (OSError, asyncio.TimeoutError):
            await self.release_producer(producer)

    async def receive_subscriber(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections.add(writer)
        client = None
        try:
            _, request = await asyncio.wait_for(read_frame(reader, 4096), 5)
            if (request.get('type') != 'subscribe' or not isinstance(request.get('client_id'), str)
                    or not 1 <= len(request['client_id']) <= 64 or len(self.clients) >= 8):
                raise ValueError('Invalid subscription')
            client = Subscriber(writer, asyncio.Queue(self.queue_frames))
            port = request.get('archive_port', 0)
            if type(port) is not int or not 0 <= port <= 65535:
                raise ValueError('Invalid archive port')
            if port:
                host = writer.get_extra_info('peername')[0]
                client.archive_url = 'http://%s:%d/archive' % (host, port)
            client.offer(encode(dict(schema=1, type='subscription', status='ready')))
            client.sender = asyncio.create_task(self.send_queue(client))
            self.clients.add(client)
            self.receivers_changed()
            while True:
                frame, command = await read_frame(reader, 4096)
                await self.control(client, frame, command)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            if client is not None:
                self.clients.discard(client)
                self.receivers_changed()
                if client.sender is not None:
                    client.sender.cancel()
                    await asyncio.gather(client.sender, return_exceptions=True)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.connections.discard(writer)
            self.tasks.discard(task)

    async def receive_producer(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections.add(writer)
        producer = None
        try:
            while True:
                frame, packet = await read_frame(reader)
                source, session = packet.get('source'), packet.get('session')
                if (source not in ('aoa', 'npu') or not isinstance(session, str)
                        or not 1 <= len(session) <= 128 or type(packet.get('seq')) is not int):
                    raise ValueError('Invalid producer')
                if producer is None:
                    old = self.producers.get(source)
                    if old is not None:
                        await self.release_producer(old)
                    producer = Producer(writer, source, session)
                    self.producers[source] = producer
                if (source != producer.source or session != producer.session
                        or packet['seq'] <= producer.sequence):
                    raise ValueError('Producer identity or sequence changed')
                producer.sequence = packet['seq']
                self.acknowledge(producer, packet)
                for client in tuple(self.clients):
                    client.offer(frame)
        except (OSError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            if producer is not None:
                await self.release_producer(producer)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.connections.discard(writer)
            self.tasks.discard(task)


async def serve(args):
    hub = Hub()
    try:
        await hub.start(client_host=args.listen, client_port=args.port, ingress_port=args.ingress_port)
        print('HUB_READY %s:%d' % (args.listen, hub.client_port), flush=True)
        await asyncio.Event().wait()
    finally:
        await hub.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--ingress-port', type=int, default=9001)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(parser.parse_args()))
