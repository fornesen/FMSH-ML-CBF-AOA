"""scan6 same-batch PL FFT beam-scan contract and control tests (stdlib only)."""
import copy
import json
import math
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import packets
from joint_protocol import validate_processing
from protocol import candidates, decode, encode, validate
from server import State


def scan_curve():
    """Synthetic but self-consistent 161-angle power curve; PC never recomputes it in production."""
    angles = list(range(-80, 81))
    power = []
    for angle in angles:
        value = 0.18
        value += 3.2 * math.exp(-0.5 * ((angle + 14) / 2.5) ** 2)
        value += 2.35 * math.exp(-0.5 * ((angle + 35) / 3.5) ** 2)
        value += 0.012 * (1 + math.sin(angle * 0.37))
        power.append(value)
    peak = max(power)
    peak_index = power.index(peak)
    relative = [10 * math.log10(max(value, 1e-12) / peak) for value in power]
    return angles, power, relative, peak_index


def scan_packet():
    packet = copy.deepcopy(packets()[0])
    angles, power, relative, peak_index = scan_curve()
    at = {angle: index for index, angle in enumerate(angles)}
    second = at[-35]
    candidates = []
    for candidate_id, angle in enumerate((-14, -35)):
        index = at[angle]
        candidates.append(dict(id=candidate_id, angle_deg=angle,
                               normalized_power=power[index], relative_db=relative[index]))
    expected = [40000.0, 29000.0]
    checks = [dict(candidate_id=index, capture_id=f"fixture_pl_{index}", angle_deg=candidate["angle_deg"],
                   expected_power=expected[index], observed_power=expected[index] * (1 + 2.5e-7),
                   relative_error=2.5e-7, input_shape_error=0.0, registers_verified=True, valid=True,
                   fft_file="", pha_file="", source_input_file="", npu_eligible=False)
              for index, candidate in enumerate(candidates)]
    packet.update(
        processing_mode="scan6",
        direction_method="four_frame_beam_power_average",
        direction_frames=4,
        direction_rounds=[0, 1, 2, 3],
        groups=[],
        beam_scan=dict(
            model="phase_only_beam_power_scan",
            source="pl_independent_fft_same_batch",
            status="ok", valid=True,
            angles_deg=angles,
            normalized_power=power,
            relative_db=relative,
            peak_angle_deg=angles[peak_index],
            peak_normalized_power=power[peak_index],
            peak_gap_db=10 * math.log10(power[peak_index] / power[second]),
            ambiguous=False,
            input_power=12345.0,
            frame_count=4,
            elapsed_ms=1.1,
            local_snr_db=35.0,
            global_peak_frequency_hz=-500488.28125,
            candidates=candidates,
            verification=dict(enabled=True, status="matched", source_round=3,
                              restore_verified=True, elapsed_ms=70.0, checks=checks),
        ),
    )
    packet.pop("joint", None)
    return packet


def processing(mode="scan6", supported=None, joint_paths=3):
    value = dict(available=True, mode=mode, applied_mode=mode, joint_paths=joint_paths,
                 applied_joint_paths=joint_paths, command_status="idle", command_id="", error="")
    if supported is not None:
        value["supported_modes"] = list(supported)
    return value


class ScanPacketTests(unittest.TestCase):
    def test_scan6_roundtrip_and_display_contract(self):
        packet = scan_packet()
        validate(packet)
        self.assertEqual(decode(encode(packet)[8:]), packet)
        self.assertEqual(packet["processing_mode"], "scan6")
        self.assertEqual(packet["groups"], [])
        self.assertNotIn("joint", packet)
        self.assertEqual(packet["direction_method"], "four_frame_beam_power_average")
        scan = packet["beam_scan"]
        self.assertEqual(len(scan["angles_deg"]), 161)
        self.assertEqual(len(scan["normalized_power"]), 161)
        self.assertEqual(len(scan["relative_db"]), 161)
        self.assertEqual(scan["verification"]["status"], "matched")
        self.assertTrue(all(check["valid"] for check in scan["verification"]["checks"]))

    def test_scan6_rejects_ml_or_legacy_shapes(self):
        packet = scan_packet()
        packet["groups"] = copy.deepcopy(packets()[0]["groups"])
        with self.assertRaises(ValueError):
            validate(packet)
        packet = scan_packet()
        packet["joint"] = {}
        with self.assertRaises(ValueError):
            validate(packet)
        packet = scan_packet()
        packet["direction_method"] = "normalized_covariance_principal_vector"
        with self.assertRaises(ValueError):
            validate(packet)
        packet = scan_packet()
        packet.pop("beam_scan")
        with self.assertRaises(ValueError):
            validate(packet)

    def test_scan6_curve_peak_and_candidates_must_agree(self):
        packet = scan_packet()
        packet["beam_scan"]["peak_normalized_power"] += 0.5
        with self.assertRaises(ValueError):
            validate(packet)
        packet = scan_packet()
        index = packet["beam_scan"]["angles_deg"].index(-14)
        packet["beam_scan"]["normalized_power"][index] += 0.5
        with self.assertRaises(ValueError):
            validate(packet)
        packet = scan_packet()
        packet["beam_scan"]["candidates"][0]["normalized_power"] += 0.5
        with self.assertRaises(ValueError):
            validate(packet)
        packet = scan_packet()
        packet["beam_scan"]["candidates"][1]["angle_deg"] = -35.5
        with self.assertRaises(ValueError):
            validate(packet)
        packet = scan_packet()
        packet["beam_scan"]["normalized_power"][0] = float("nan")
        with self.assertRaises(ValueError):
            validate(packet)

    def test_scan6_verification_contract(self):
        for change in (dict(candidate_id=9), dict(angle_deg=-13.0),
                       dict(relative_error=float("nan")),
                       dict(valid=True, registers_verified=False)):
            packet = scan_packet()
            packet["beam_scan"]["verification"]["checks"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(packet)
        packet = scan_packet()
        packet["beam_scan"]["verification"]["enabled"] = False
        packet["beam_scan"]["verification"].update(status="disabled", checks=[])
        validate(packet)

    def test_scan6_does_not_create_position_trajectory(self):
        packet = scan_packet()
        state = State()
        state.ingest(packet)
        snapshot = state.snapshot()
        self.assertEqual(snapshot["candidates"], [])
        self.assertEqual(snapshot["trajectory"], [])
        self.assertEqual(snapshot["angle_trajectory"], [])
        # 旧空间定位包仍保持原行为。
        position = copy.deepcopy(packets()[0])
        position.update(session=packet["session"], seq=packet["seq"] + 1)
        state.ingest(position)
        self.assertEqual(len(state.snapshot()["candidates"]), 4)


class ProcessingCapabilityTests(unittest.TestCase):
    def test_scan6_requires_declared_capability(self):
        validate_processing(processing("scan6", ("position", "angle6", "scan6")))
        with self.assertRaises(ValueError):
            validate_processing(processing("scan6", ("position", "angle6")))
        with self.assertRaises(ValueError):
            validate_processing(processing("scan6"))
        with self.assertRaises(ValueError):
            validate_processing(processing("scan6", ("position", "angle6", "scan6"), joint_paths=1))
        with self.assertRaises(ValueError):
            validate_processing(processing("scan6", ("position", "angle6", "scan6", "unknown")))

    def test_legacy_processing_capability_still_valid(self):
        validate_processing(processing("angle6"))
        validate_processing(processing("position", ("position", "angle6"), joint_paths=2))
        validate_processing(processing("angle6", ("position", "angle6", "scan6")))


class FakeWriter:
    def __init__(self):
        self.frames = []
        self.closing = False

    def is_closing(self):
        return self.closing

    def write(self, data):
        self.frames.append(bytes(data))

    async def drain(self):
        return None


class ScanControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        now = time.time()
        self.writer = FakeWriter()
        self.state = State("live")
        self.state.loop = __import__("asyncio").get_running_loop()
        session = "scan-session"
        self.state.sources["aoa"] = dict(connected=True, last_seen=now, session=session,
                                         connection="c1", seq=1, peer="fixture",
                                         transport=dict(sent=1, dropped=0, failures=0))
        self.state.writers["c1"] = self.writer
        self.state.aoa = scan_packet()
        self.state.aoa.update(received_at=now, session=session)
        self.state.beam = dict(
            available=True, session=session, received_at=now,
            pl_version=0x00010001, required_pl_version=0x00010001,
            auto_supported=True, mode="equal", status="applied",
            processing=processing("scan6", ("position", "angle6", "scan6")),
        )

    def command(self):
        return json.loads(self.writer.frames[-1][8:])

    async def test_scan6_processing_command_and_manual_angle(self):
        result = await self.state.send_control(
            dict(mode="processing", processing_mode="scan6", joint_paths=3))
        self.assertEqual(result["mode"], "processing")
        command = self.command()
        self.assertEqual(command["mode"], "processing")
        self.assertEqual(command["processing_mode"], "scan6")
        self.assertEqual(command["joint_paths"], 3)

        self.state.control = None
        await self.state.send_control(dict(mode="manual", angle_deg=-14))
        command = self.command()
        self.assertEqual(command["mode"], "manual")
        self.assertEqual(command["angle_deg"], -14)

        self.state.control = None
        await self.state.send_control(dict(mode="auto"))
        self.assertEqual(self.command()["mode"], "auto")

    async def test_scan6_follow_and_undeclared_mode_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.state.send_control(dict(mode="follow", candidate=0))
        self.assertIsNone(self.state.control)
        self.state.beam["processing"]["supported_modes"] = ["position", "angle6"]
        with self.assertRaises(ValueError):
            await self.state.send_control(dict(mode="processing", processing_mode="scan6", joint_paths=3))
        self.assertEqual(self.writer.frames, [])

    async def test_legacy_control_has_no_scan6(self):
        self.state.beam["processing"] = processing("angle6")
        with self.assertRaises(ValueError):
            await self.state.send_control(dict(mode="processing", processing_mode="scan6", joint_paths=3))
        await self.state.send_control(dict(mode="processing", processing_mode="angle6", joint_paths=3))
        self.assertEqual(self.command()["processing_mode"], "angle6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
