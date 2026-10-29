"""Exercise fan-out and control arbitration over real local TCP connections."""
import asyncio
import contextlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'Board/runtime'))
from telemetry_hub import Hub, encode, read_frame


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = Hub()
        await self.hub.start(ingress_port=0, client_host='127.0.0.1', client_port=0)
        self.writers = []

    async def asyncTearDown(self):
        await self.hub.close()
        for writer in self.writers:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def connection(self, port):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        self.writers.append(writer)
        return reader, writer

    async def subscriber(self):
        reader, writer = await self.connection(self.hub.client_port)
        writer.write(encode(dict(schema=1, type='subscribe', client_id='test-pc')))
        await writer.drain()
        _, response = await self.read(reader)
        self.assertEqual(response, dict(schema=1, type='subscription', status='ready'))
        return reader, writer

    async def read(self, reader):
        return await asyncio.wait_for(read_frame(reader), 2)

    def packet(self, seq, source='aoa', **kwargs):
        return dict(schema=1, source=source, session='session-' + source, seq=seq,
                    type='heartbeat', run_id='', **kwargs)

    def command(self, identifier='one', **kwargs):
        return dict(schema=1, type='beam_command', id=identifier, session='session-aoa',
                    mode='auto', **kwargs)

    async def no_frame(self, reader):
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), .05)

    async def publish(self, writer, packet, readers):
        frame = encode(packet)
        writer.write(frame)
        await writer.drain()
        for reader in readers:
            self.assertEqual((await self.read(reader))[0], frame)

    async def test_same_bytes_fragmentation_and_disconnect_isolation(self):
        first, first_writer = await self.subscriber()
        second, _ = await self.subscriber()
        producer, writer = await self.connection(self.hub.ingress_port)
        frames = [encode(self.packet(i)) for i in (1, 2)]
        writer.write(frames[0][:5])
        await writer.drain()
        writer.write(frames[0][5:] + frames[1])
        await writer.drain()
        for reader in (first, second):
            for frame in frames:
                self.assertEqual((await self.read(reader))[0], frame)
        first_writer.close()
        await first_writer.wait_closed()
        await self.publish(writer, self.packet(3), [second])
        await self.no_frame(producer)
        self.assertFalse(writer.is_closing())

    async def test_receiver_address_comes_from_connection_and_is_removed_on_disconnect(self):
        changes = []
        self.hub.on_receivers = changes.append
        reader, writer = await self.connection(self.hub.client_port)
        writer.write(encode(dict(schema=1, type='subscribe', client_id='pc', archive_port=7012)))
        await writer.drain()
        await self.read(reader)
        self.assertEqual(changes[-1], ('http://127.0.0.1:7012/archive',))
        writer.close()
        await writer.wait_closed()
        for _ in range(50):
            if changes[-1] == ():
                break
            await asyncio.sleep(.01)
        self.assertEqual(changes[-1], ())

    async def test_single_pending_command_and_acknowledgement(self):
        first, write1 = await self.subscriber()
        second, write2 = await self.subscriber()
        producer, writer = await self.connection(self.hub.ingress_port)
        await self.publish(writer, self.packet(1), [first, second])
        one, two = self.command('one'), self.command('two')
        write1.write(encode(one))
        await write1.drain()
        self.assertEqual((await self.read(producer))[1], one)
        write2.write(encode(two))
        await write2.drain()
        self.assertEqual((await self.read(second))[1]['status'], 'rejected')
        await self.no_frame(producer)
        await self.publish(writer, self.packet(2, beam=dict(command_id='one', command_status='pending')),
                           [first, second])
        self.assertIsNotNone(self.hub.pending)
        await self.publish(writer, self.packet(3, beam=dict(command_id='one', command_status='applied')),
                           [first, second])
        self.assertIsNone(self.hub.pending)
        write2.write(encode(two))
        await write2.drain()
        self.assertEqual((await self.read(producer))[1], two)

    async def test_observation_ack_and_wrong_session(self):
        reader, subscriber = await self.subscriber()
        producer, writer = await self.connection(self.hub.ingress_port)
        await self.publish(writer, self.packet(1), [reader])
        command = self.command()
        command['session'] = 'old-session'
        subscriber.write(encode(command))
        await subscriber.drain()
        self.assertEqual((await self.read(reader))[1]['status'], 'rejected')
        await self.no_frame(producer)
        command.update(session='session-aoa', mode='observation', waveform_enabled=True, spectrum_enabled=True)
        subscriber.write(encode(command))
        await subscriber.drain()
        self.assertEqual((await self.read(producer))[1], command)
        ack = dict(command_id='one', command_status='applied')
        await self.publish(writer, self.packet(2, beam=dict(observation=ack)), [reader])
        self.assertIsNone(self.hub.pending)

    async def test_producer_loss_unknown_and_no_cached_replay(self):
        reader, subscriber = await self.subscriber()
        producer, writer = await self.connection(self.hub.ingress_port)
        await self.publish(writer, self.packet(1), [reader])
        subscriber.write(encode(self.command()))
        await subscriber.drain()
        await self.read(producer)
        writer.close()
        await writer.wait_closed()
        self.assertEqual((await self.read(reader))[1]['status'], 'unknown')
        self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b'')
        next_reader, _ = await self.subscriber()
        await self.no_frame(next_reader)
        self.assertIsNone(self.hub.pending)

    async def test_replacing_producer_resets_subscribers(self):
        reader, _ = await self.subscriber()
        _, old_writer = await self.connection(self.hub.ingress_port)
        await self.publish(old_writer, self.packet(1), [reader])
        _, new_writer = await self.connection(self.hub.ingress_port)
        packet = self.packet(1)
        packet['session'] = 'new-session'
        new_writer.write(encode(packet))
        await new_writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b'')
        next_reader, _ = await self.subscriber()
        await self.no_frame(next_reader)
        packet['seq'] = 2
        await self.publish(new_writer, packet, [next_reader])

    async def test_npu_and_aoa_share_subscription_and_no_clients_is_ok(self):
        _, writer = await self.connection(self.hub.ingress_port)
        for seq in range(1, 40):
            writer.write(encode(self.packet(seq)))
        await writer.drain()
        while not self.hub.producers or self.hub.producers['aoa'].sequence < 39:
            await asyncio.sleep(.01)
        reader, _ = await self.subscriber()
        _, npu_writer = await self.connection(self.hub.ingress_port)
        await self.publish(npu_writer, self.packet(1, source='npu'), [reader])
        await self.publish(writer, self.packet(40), [reader])

    async def test_slow_subscriber_does_not_block_another_client(self):
        slow_reader, slow_writer = await self.subscriber()
        fast_reader, _ = await self.subscriber()
        peer = slow_writer.get_extra_info('sockname')
        slow = next(c for c in self.hub.clients if c.writer.get_extra_info('peername') == peer)
        original = slow.writer

        class StalledWriter:
            def write(self, frame):
                original.write(frame)

            async def drain(self):
                await asyncio.Event().wait()

            def is_closing(self):
                return original.is_closing()

            def close(self):
                original.close()

        slow.writer = StalledWriter()
        _, producer = await self.connection(self.hub.ingress_port)
        for seq in range(1, 15):
            await self.publish(producer, self.packet(seq), [fast_reader])
        self.assertTrue(original.is_closing())
        await self.publish(producer, self.packet(15), [fast_reader])
        await asyncio.wait_for(slow_reader.read(), 2)


if __name__ == '__main__':
    unittest.main()
