"""Subscribe once to Fudan telemetry; opening a PC does not start ADC capture."""
import asyncio
import json
import socket
import struct
import uuid

from protocol import MAGIC, MAX_PACKET, decode, encode

READ_TIMEOUT_SECONDS = 15


async def read_packet(reader):
    header = await reader.readexactly(8)
    length = struct.unpack('!I', header[4:])[0]
    if header[:4] != MAGIC or not 0 < length <= MAX_PACKET:
        raise ValueError('Invalid telemetry frame')
    return await reader.readexactly(length)


def control_reply(state, packet):
    if (packet.get('schema') != 1 or packet.get('status') not in ('rejected', 'unknown')
            or not isinstance(packet.get('id'), str) or len(packet['id']) > 64
            or not isinstance(packet.get('error'), str) or len(packet['error']) > 1024):
        raise ValueError('Invalid control reply')
    with state.lock:
        source = state.sources.get('aoa', {})
        if (state.control and state.control['id'] == packet['id']
                and packet.get('session') == source.get('session')):
            state.control.update(status=packet['status'], error=packet['error'])
            state.revision += 1


async def subscribe(state, host, port, archive_port=0):
    client_id = socket.gethostname()[:32] + '-' + uuid.uuid4().hex[:16]
    state.board_link = dict(host=host, port=port, status='connecting', error='')
    while True:
        writer = None
        connection = uuid.uuid4().hex
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
            writer.write(encode(dict(schema=1, type='subscribe', client_id=client_id,
                                     archive_port=archive_port)))
            await asyncio.wait_for(writer.drain(), 3)
            response = json.loads(await asyncio.wait_for(read_packet(reader), 5))
            if response != dict(schema=1, type='subscription', status='ready'):
                raise ValueError('Board did not acknowledge the subscription')
            state.board_link.update(status='connected', error='')
            while True:
                # A powered-off/rebooted board can leave a half-open TCP stream.
                # Bound idle reads so the archive address is registered again.
                body = await asyncio.wait_for(read_packet(reader), READ_TIMEOUT_SECONDS)
                value = json.loads(body)
                if isinstance(value, dict) and value.get('type') == 'control_reply':
                    control_reply(state, value)
                    continue
                packet = decode(body)
                state.ingest(packet, host, connection, len(body) + 8)
                if packet['source'] == 'aoa':
                    state.writers[connection] = writer
        except (OSError, ValueError, KeyError, TypeError, RecursionError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
            state.board_link.update(status='reconnecting', error=str(error)[:300])
        finally:
            state.disconnected(connection)
            state.writers.pop(connection, None)
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        await asyncio.sleep(2)
