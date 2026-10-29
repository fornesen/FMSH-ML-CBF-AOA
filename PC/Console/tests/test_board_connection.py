"""Real TCP checks for subscriptions; no FPGA or acquisition requests."""
import asyncio
import contextlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from board_connection import read_packet, subscribe
from demo import packets
from protocol import encode
from server import State


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_half_open_stream_is_reconnected(self):
        with patch('board_connection.READ_TIMEOUT_SECONDS', .08):
            state, _, _ = await self.connect()
            await self.until(lambda: state.board_link['status'] == 'reconnecting')
            self.assertFalse(state.writers)

    async def asyncSetUp(self):
        self.clients = asyncio.Queue()
        self.writers = []
        self.tasks = []

        async def accept(reader, writer):
            self.writers.append(writer)
            request = json.loads(await read_packet(reader))
            await self.clients.put((request, reader, writer))
            writer.write(encode(dict(schema=1, type='subscription', status='ready')))
            await writer.drain()

        self.listener = await asyncio.start_server(accept, '127.0.0.1', 0)
        self.port = self.listener.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        self.listener.close()
        await self.listener.wait_closed()

    async def connect(self):
        state = State()
        state.loop = asyncio.get_running_loop()
        self.tasks.append(asyncio.create_task(subscribe(state, '127.0.0.1', self.port)))
        request, reader, writer = await asyncio.wait_for(self.clients.get(), 2)
        self.assertEqual(request['type'], 'subscribe')
        self.assertEqual(request['schema'], 1)
        self.assertLessEqual(len(request['client_id']), 64)
        return state, reader, writer

    async def until(self, predicate):
        async def check():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(check(), 2)

    async def test_two_clients_receive_identical_aoa_and_npu_without_start(self):
        first, read1, write1 = await self.connect()
        second, read2, write2 = await self.connect()
        aoa, npu = packets()
        framed = encode(aoa) + encode(npu)
        for writer in (write1, write2):
            writer.write(framed[:5])
            await writer.drain()
            writer.write(framed[5:])
            await writer.drain()
        await self.until(lambda: first.signal is not None and second.signal is not None)
        for state in (first, second):
            self.assertEqual({k: v for k, v in state.aoa.items() if k != 'received_at'}, aoa)
            self.assertEqual(state.snapshot()['npu']['run_id'], npu['run_id'])
            self.assertEqual(state.board_link['status'], 'connected')
        # Registering and receiving sends no START, PS launch, or control command.
        for reader in (read1, read2):
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(reader.read(1), .03)

    async def test_disconnect_marks_old_frames_stale(self):
        state, _, writer = await self.connect()
        aoa, _ = packets()
        writer.write(encode(aoa))
        await writer.drain()
        await self.until(lambda: state.aoa is not None)
        writer.close()
        await writer.wait_closed()
        await self.until(lambda: not state.sources['aoa']['connected'])
        self.assertEqual(state.aoa['run_id'], aoa['run_id'])
        self.assertEqual(state.board_link['status'], 'reconnecting')
        self.assertFalse(state.writers)

    async def test_rejection_is_local_and_requires_current_command_session(self):
        state, _, writer = await self.connect()
        aoa, _ = packets()
        writer.write(encode(aoa))
        await writer.drain()
        await self.until(lambda: state.aoa is not None)
        state.control = dict(id='pending', status='sent')
        reply = dict(schema=1, type='control_reply', id='pending', session='another-session',
                     mode='auto', status='rejected', error='busy')
        writer.write(encode(reply))
        reply['session'] = aoa['session']
        writer.write(encode(reply))
        await writer.drain()
        await self.until(lambda: state.control['status'] == 'rejected')
        self.assertEqual(state.control['error'], 'busy')
        self.assertEqual(state.sources['aoa']['seq'], aoa['seq'])
        self.assertEqual(state.aoa['run_id'], aoa['run_id'])


if __name__ == '__main__':
    unittest.main()
