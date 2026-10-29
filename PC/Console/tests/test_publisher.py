"""真实 C++ publisher 的断线重连与无接收端压力测试。"""
import asyncio
import json
import os
import pathlib
import socket
import struct
import subprocess
import time
import unittest

EXE=pathlib.Path(__file__).parent/'build'/'publisher_probe.exe'


@unittest.skipUnless(EXE.exists(),'compile publisher_probe.cpp first')
class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_after_receiver_reset(self):
        sessions=[];received=[]
        async def receive(reader,writer):
            sessions.append(writer)
            try:
                while True:
                    h=await asyncio.wait_for(reader.readexactly(8),5)
                    self.assertEqual(h[:4],b'AOA1')
                    p=json.loads(await reader.readexactly(struct.unpack('!I',h[4:])[0]))
                    received.append(p)
                    if len(sessions)==1:
                        writer.transport.abort()
                        return
            except (asyncio.IncompleteReadError,TimeoutError,ConnectionResetError):pass
            finally:writer.close()
        server=await asyncio.start_server(receive,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        env=dict(os.environ,AOA_CONSOLE_HOST='127.0.0.1',AOA_CONSOLE_PORT=str(port))
        process=await asyncio.create_subprocess_exec(str(EXE),'stream',env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            stdout,stderr=await asyncio.wait_for(process.communicate(),10)
            self.assertEqual(process.returncode,0,stderr)
            self.assertGreaterEqual(len(sessions),2)
            self.assertGreater(len(received),10)
            self.assertEqual(len({p['session'] for p in received}),1)
            self.assertEqual([p['seq'] for p in received],sorted(set(p['seq'] for p in received)))
            self.assertGreater(received[-1]['transport']['failures'],0)
        finally:
            server.close();await server.wait_closed()
            for writer in sessions:writer.close()
            if process.returncode is None:process.kill();await process.wait()

    async def test_no_receiver_does_not_block_producer(self):
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        # 端口绑定但不 listen，确保测试目标不是用户服务。
        env=dict(os.environ,AOA_CONSOLE_HOST='127.0.0.1',AOA_CONSOLE_PORT=str(port))
        start=time.monotonic()
        process=await asyncio.create_subprocess_exec(str(EXE),env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            stdout,stderr=await asyncio.wait_for(process.communicate(),8)
            self.assertEqual(process.returncode,0,stderr)
            self.assertLess(float(stdout),3000)  # 20,000 次生产；网络失败不能逐次阻塞。
            self.assertLess(time.monotonic()-start,6)
        finally:
            sock.close()
            if process.returncode is None:process.kill();await process.wait()


if __name__=='__main__':unittest.main(verbosity=2)
