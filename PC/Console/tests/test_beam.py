"""[波束控制新增] 使用真实 C++ Publisher/Controller；不访问硬件。"""
import asyncio
import copy
import http.client
import http.server
import json
import os
import pathlib
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server import State, receive_client, handler
from demo import packets
from protocol import encode, validate


class BeamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = State()
        self.state.loop = asyncio.get_running_loop()
        self.clients = set()
        self.server = await asyncio.start_server(
            lambda r, w: receive_client(r, w, self.state, self.clients), "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        executable = pathlib.Path(__file__).parent/"build/beam_contract.exe"
        if not executable.exists(): self.skipTest("Build beam_contract.exe first")
        self.process = await asyncio.create_subprocess_exec(
            str(executable), "--tcp", env=dict(os.environ, AOA_CONSOLE_HOST="127.0.0.1", AOA_CONSOLE_PORT=str(port),
                BEAM_TEST_FAULT={"test_follow_loss":"loss", "test_follow_ambiguous":"ambiguous",
                  "test_follow_reorder":"reorder", "test_capture_failure":"capture",
                  "test_auto_switches_to_strongest":"auto_switch", "test_auto_holds_and_recovers":"auto_hold",
                  "test_identify_rejects_incompatible_rate":"nonmodel_rate"}.get(self._testMethodName, "")),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await self.until(lambda: self.state.beam is not None)
        self.state.aoa = dict(packets()[0], received_at=time.time(), session=self.state.sources["aoa"]["session"])
        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler(self.state))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    async def asyncTearDown(self):
        if hasattr(self, "process") and self.process.returncode is None:
            self.process.terminate()
            await self.process.communicate()
        if hasattr(self, "http"):
            await asyncio.to_thread(self.http.shutdown)
            self.http.server_close()
        self.server.close()
        await self.server.wait_closed()

    async def until(self, predicate):
        for _ in range(300):
            if predicate(): return
            await asyncio.sleep(.01)
        self.fail("Timed out waiting for board fixture")

    async def command(self, request):
        if request["mode"] == "follow":
            request = dict(request, run_id=self.state.aoa["run_id"], candidate_id=f"C{request['candidate']+1}")
        c = await self.state.send_control(request)
        await self.until(lambda: self.state.control["id"] == c["id"] and self.state.control["status"] != "sent")
        return self.state.beam

    async def test_live_cpp_manual_follow_pause_equal(self):
        b = await self.command(dict(mode="manual", angle_deg=31))
        self.assertEqual(b["status"], "applied")
        self.assertTrue(b["registers_verified"])
        self.assertFalse(b["hardware_response_verified"])
        self.assertTrue(any(abs(p)>.1 for p in b["phase_rad"]))
        b = await self.command(dict(mode="follow", candidate=2))
        self.assertEqual(b["status"], "tracking")
        self.assertEqual(b["paths"], [1, 0])
        phases = b["phase_rad"]
        b = await self.command(dict(mode="pause"))
        self.assertFalse(b["tracking"])
        self.assertEqual(b["phase_rad"], phases)
        b = await self.command(dict(mode="equal"))
        self.assertEqual(b["phase_rad"], [0]*6)
        self.assertEqual(b["status"], "applied")

    async def test_validation_no_unintended_write(self):
        for request in (dict(mode="manual", angle_deg=90), dict(mode="manual", angle_deg=True),
                        dict(mode="manual", angle_deg=float("nan")), dict(mode="follow", candidate=9),
                        dict(mode="unknown")):
            with self.assertRaises(ValueError): await self.state.send_control(request)
        self.assertIsNone(self.state.control)
        session = self.state.aoa["session"]
        self.state.aoa["session"] = "previous-board-session"
        with self.assertRaises(ValueError): await self.state.send_control(dict(mode="follow", candidate=0))
        self.state.aoa["session"] = session
        self.state.mode = "demo"
        with self.assertRaises(ValueError): await self.state.send_control(dict(mode="equal"))

    async def test_auto_switches_to_strongest(self):
        b = await self.command(dict(mode="auto"))
        self.assertEqual(b["mode"], "auto")
        self.assertEqual(b["paths"], [1, 0])
        self.assertTrue(b["registers_verified"])
        self.assertGreater(b["target"]["energy_share"], .8)
        first = b["phase_rad"]
        await self.until(lambda: self.state.beam["paths"] == [0, 0])
        self.assertNotEqual(self.state.beam["phase_rad"], first)
        self.assertTrue(self.state.beam["tracking"])
        await self.command(dict(mode="pause"))
        self.assertFalse(self.state.beam["tracking"])

    async def test_auto_holds_and_recovers(self):
        b = await self.command(dict(mode="auto"))
        first = b["phase_rad"]
        await self.until(lambda: self.state.beam["status"] == "holding")
        self.assertEqual(self.state.beam["phase_rad"], first)
        self.assertTrue(self.state.beam["tracking"])
        await self.until(lambda: self.state.beam["status"] == "tracking")
        self.assertEqual(self.state.beam["phase_rad"], first)

    async def test_acquisition_modes_preserve_beam(self):
        b=await self.command(dict(mode="auto"))
        phases=b['phase_rad']
        b=await self.command(dict(mode="acquisition",acquisition_mode="locate"))
        self.assertEqual(b['acquisition']['applied_mode'],'locate')
        self.assertEqual(b['test_capture_rounds'],4)
        self.assertEqual(b['acquisition']['command_status'],'applied')
        self.assertEqual(b['mode'],'auto');self.assertEqual(b['phase_rad'],phases)
        b=await self.command(dict(mode="acquisition",acquisition_mode="identify"))
        self.assertEqual(b['acquisition']['applied_mode'],'identify')
        self.assertEqual(b['test_capture_rounds'],4)
        self.assertEqual(b['mode'],'auto')

    async def test_identify_rejects_incompatible_rate(self):
        b=await self.command(dict(mode="acquisition",acquisition_mode="identify"))
        self.assertEqual(b['acquisition']['command_status'],'rejected')
        self.assertIn('100 MS/s',b['acquisition']['error'])
        self.assertEqual(b['acquisition']['applied_mode'],'locate')
        self.assertEqual(b['test_capture_rounds'],4)

    async def test_fragmented_command_and_wrong_session(self):
        source = self.state.sources["aoa"]
        writer = self.state.writers[source["connection"]]
        cmd = dict(schema=1, type="beam_command", session=source["session"], id="fragmented", mode="manual", angle_deg=-30)
        frame = encode(cmd)
        for i in range(0, len(frame), 3):
            writer.write(frame[i:i+3])
            await writer.drain()
        await self.until(lambda: self.state.beam["command_id"] == "fragmented")
        self.assertEqual(self.state.beam["angle_deg"], -30)
        # 同 ID 不得重复执行；错误会明确反馈。
        writer.write(frame);await writer.drain()
        await self.until(lambda: self.state.beam["status"] == "rejected")
        self.assertIn("Duplicate", self.state.beam["error"])
        writer.write(encode(dict(cmd, session="old-session", id="stale")));await writer.drain()
        await self.until(lambda: not self.state.sources["aoa"]["connected"])
        self.assertNotEqual(self.state.beam["command_id"], "stale")

    async def test_http_same_origin_and_ack(self):
        port = self.http.server_port
        def post(origin):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            c.request("POST", "/api/beam", json.dumps(dict(mode="manual", angle_deg=20)),
                      {"Content-Type":"application/json", "Origin":origin})
            response = c.getresponse()
            data = response.read()
            c.close()
            return response.status, data
        code, _ = await asyncio.to_thread(post, "https://untrusted.example")
        self.assertEqual(code, 403)
        self.assertIsNone(self.state.control)
        code, body = await asyncio.to_thread(post, f"http://127.0.0.1:{port}")
        self.assertEqual(code, 202)
        self.assertEqual(json.loads(body)["status"], "sent")
        await self.until(lambda: self.state.beam["status"] == "applied")

    async def test_beam_packet_validation(self):
        packet = dict(schema=1, source="aoa", type="heartbeat", session="test", seq=1, run_id="",
                      beam={k:v for k,v in self.state.beam.items() if k not in ("received_at","session")})
        validate(packet)
        for key, value in (("phase_rad", [0]*5), ("angle_deg", float("nan")), ("registers_verified", "yes"),
                           ("paths", [0, 4]), ("pl_version", -1)):
            bad = copy.deepcopy(packet);bad["beam"][key] = value
            with self.assertRaises(ValueError): validate(bad)

    async def test_follow_loss(self):
        b = await self.command(dict(mode="follow", candidate=2))
        phase = b["phase_rad"]
        await self.until(lambda: self.state.beam["status"] == "paused")
        self.assertEqual(self.state.beam["phase_rad"], phase)
        self.assertIn("fresh", self.state.beam["error"])

    async def test_follow_ambiguous(self):
        b = await self.command(dict(mode="follow", candidate=2))
        phase = b["phase_rad"]
        await self.until(lambda: self.state.beam["status"] == "paused")
        self.assertEqual(self.state.beam["phase_rad"], phase)
        self.assertIn("Ambiguous", self.state.beam["error"])

    async def test_follow_reorder(self):
        b = await self.command(dict(mode="follow", candidate=2))
        phase = b["phase_rad"]
        await self.until(lambda: self.state.beam["paths"] == [0, 1])
        self.assertEqual(self.state.beam["phase_rad"], phase)
        self.assertTrue(self.state.beam["tracking"])

    async def test_capture_failure(self):
        b = await self.command(dict(mode="manual", angle_deg=20))
        self.assertEqual(b["command_status"], "error")
        self.assertFalse(b["registers_verified"])
        self.assertEqual(b["applied_run_id"], "")
        await asyncio.sleep(.25)
        self.assertFalse(self.state.beam["registers_verified"])
        self.assertEqual(self.state.beam["mode"], "equal")

    async def test_disconnect_pauses_without_replay(self):
        b = await self.command(dict(mode="follow", candidate=2))
        phase = b["phase_rad"]
        writer = self.state.writers[self.state.sources["aoa"]["connection"]]
        writer.transport.abort()
        await self.until(lambda: self.state.beam["status"] == "paused")
        self.assertEqual(self.state.beam["phase_rad"], phase)
        self.assertFalse(self.state.beam["tracking"])
        self.assertIn("disconnected", self.state.beam["error"])

    async def test_rejection_survives_automatic_tracking(self):
        await self.command(dict(mode="follow", candidate=2))
        source = self.state.sources["aoa"]
        writer = self.state.writers[source["connection"]]
        writer.write(encode(dict(schema=1, type="beam_command", session=source["session"],
                                id="reject-follow", mode="manual", angle_deg=99)))
        await writer.drain()
        await self.until(lambda: self.state.beam["command_id"] == "reject-follow")
        await asyncio.sleep(.3)
        self.assertTrue(self.state.beam["tracking"])
        self.assertEqual(self.state.beam["command_status"], "rejected")
        self.assertIn("coverage", self.state.beam["command_error"])

    async def test_follow_binds_displayed_batch(self):
        for run_id, candidate_id in (("previous-batch", "C3"), (self.state.aoa["run_id"], "C1")):
            with self.assertRaises(ValueError):
                await self.state.send_control(dict(mode="follow", candidate=2, run_id=run_id, candidate_id=candidate_id))
        self.assertIsNone(self.state.control)

    async def test_ack_timeout_resets_connection_without_replay(self):
        old = self.state.sources["aoa"]["connection"]
        writer = self.state.writers[old]
        await self.state.send_control(dict(mode="manual", angle_deg=20))
        self.state.control["sent_at"] -= 21
        snapshot = self.state.snapshot()
        self.assertEqual(snapshot["control"]["status"], "unknown")
        self.assertFalse(snapshot["sources"]["aoa"]["connected"])
        await asyncio.sleep(.01)
        self.assertTrue(writer.is_closing())
        await self.until(lambda: self.state.sources["aoa"]["connected"] and self.state.sources["aoa"]["connection"] != old)


if __name__ == "__main__": unittest.main(verbosity=2)
