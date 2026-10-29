"""上位机：订阅复旦微结果或接收旧版 TCP 推送，无第三方 Python 依赖。"""
import argparse
import asyncio
import collections
import http.server
import json
import os
import pathlib
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser

from protocol import MAGIC, MAX_PACKET, candidates, automatic_candidate, decode, encode, finite
from joint_protocol import eligible_paths, automatic_path

ROOT = pathlib.Path(__file__).resolve().parent


class ConsoleHTTPServer(http.server.ThreadingHTTPServer):
    # On Windows SO_REUSEADDR can overlap a listener owned by another package.
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class State:
    def __init__(self, mode="live"):
        self.lock = threading.RLock()
        self.mode = mode
        self.aoa = None
        self.npu = collections.OrderedDict()
        self.signal = None
        self.sources = {}
        self.events = collections.deque(maxlen=30)
        self.revision = 0
        self.received_bytes = 0
        self.rejected = 0
        self.tcp_port = 0
        self.archive_port = 0
        self.instance_root = str(ROOT.resolve())
        self.board_host = None
        self.board_port = 9000
        # [波束控制新增] TCP 原连接反向发命令；不新增板端监听端口。
        self.loop = None
        self.writers = {}
        self.beam = None
        self.control = None
        # [轨迹新增] 每批仅保留最高评分的有效估计；失效批次也记录，以断开轨迹。
        self.trajectory = collections.deque(maxlen=512)
        self.trajectory_key = None
        self.angle_trajectory = collections.deque(maxlen=512)
        self.board_link = None

    def error(self, text):
        with self.lock:
            self.rejected += 1
            self.events.appendleft({"time": time.time(), "text": str(text)[:300]})
            self.revision += 1

    def ingest(self, p, peer="demo", connection="demo", byte_count=0):
        now = time.time()
        with self.lock:
            key = p["source"]
            old = self.sources.get(key, {})
            # 单个工作站服务一块板。避免第二块板/重复进程悄悄覆盖当前数据。
            if old.get("connected") and old.get("session") != p["session"] and now-old["last_seen"] < 6:
                raise ValueError(f"{key}: another active session")
            if old.get("session") == p["session"] and p["seq"] <= old.get("seq", 0):
                raise ValueError("duplicate/out-of-order sequence")
            if key == "aoa" and (old.get("session") != p["session"] or old.get("connection") != connection):
                self.control = None
            self.sources[key] = {"peer": peer, "connection": connection, "session": p["session"],
                                 "seq": p["seq"], "last_seen": now, "connected": True,
                                 "transport": p.get("transport", {})}
            self.received_bytes += byte_count
            p = dict(p, received_at=now)
            if key == "aoa" and "beam" in p:
                self.beam = dict(p["beam"], received_at=now, session=p["session"])
                ack=self.beam.get("acquisition",{}) if self.control and self.control["mode"]=="acquisition" else self.beam
                if self.control and self.control["mode"] == "processing":
                    ack = self.beam.get("processing", {})
                if self.control and self.control["mode"] == "observation":
                    ack = self.beam.get("observation", {})
                if self.control and self.control["mode"] == "archive":
                    ack = self.beam.get("archive", {})
                if self.control and self.control["id"] == ack.get("command_id") and ack.get("command_status") != "pending":
                    self.control["status"] = ack["command_status"]
                    self.control["error"] = ack.get("command_error",ack.get("error",""))
            if p["type"] == "aoa":
                self.aoa = p
                key = (p["session"], p["run_id"])
                if key != self.trajectory_key:
                    self.trajectory_key = key
                    if p.get("processing_mode") == "angle6":
                        strongest = automatic_path(p)
                        self.angle_trajectory.append(dict(run_id=p["run_id"], session=p["session"], time=now,
                            valid=strongest is not None, angle_deg=strongest["angle_deg"] if strongest else None,
                            path_id=strongest["id"] if strongest else None,
                            energy_share=strongest["energy_share"] if strongest else None))
                    else:
                        if self.angle_trajectory and self.angle_trajectory[-1]["valid"]:
                            # [六天线测角新增] 退出测角也断开角度历史，回来时不跨模式连线。
                            self.angle_trajectory.append(dict(run_id=p["run_id"], session=p["session"],
                                time=now, valid=False, angle_deg=None, path_id=None, energy_share=None))
                        # [快速扫描新增] 扫描只有同批等效功率峰，不生成空间定位轨迹点。
                        if p.get("processing_mode") != "scan6":
                            chosen = automatic_candidate(p)
                            self.trajectory.append(dict(run_id=p["run_id"], session=p["session"], time=now,
                                valid=chosen is not None, candidate_id=chosen["id"] if chosen else None,
                                x=chosen["x"] if chosen else None, y=chosen["y"] if chosen else None,
                                energy_share=chosen.get("energy_share") if chosen else None))
                    if p.get("processing_mode") in ("scan6", "angle6") and self.trajectory and self.trajectory[-1]['valid']:
                        self.trajectory.append(dict(run_id=p['run_id'],session=p['session'],time=now,
                            valid=False,candidate_id=None,x=None,y=None,energy_share=None))
            elif p["type"] == "npu":
                previous = self.npu.get(p["run_id"])
                # 重复文件的“未再执行”提示不是一次新推理，不撤销已有完整结果。
                # 真正重跑失败仍覆盖为 error，避免显示旧成功冒充本次成功。
                if p.get("status") == "already_consumed" and previous and previous.get("status") == "inferred":
                    self.events.appendleft({"time": now, "text": "重复批次未重跑，保留已有结果："+p["run_id"]})
                    self.revision += 1
                    return
                self.npu[p["run_id"]] = p
                self.npu.move_to_end(p["run_id"])
                while len(self.npu) > 32:
                    self.npu.popitem(last=False)
                # Keep the last classification even when optional NPU previews are disabled.
                if p.get("classification") or p.get("signal"):
                    self.signal = p
            elif p["type"] == "service_error":
                self.events.appendleft({"time": now, "text": p.get("error", "service error")[:300]})
            self.revision += 1

    def disconnected(self, connection):
        with self.lock:
            for s in self.sources.values():
                if s["connection"] == connection:
                    s["connected"] = False
            if self.control and self.control.get("connection") == connection and self.control["status"] == "sent":
                self.control["status"] = "unknown"
                self.control["error"] = "连接中断，命令结果未知"
            self.revision += 1

    def snapshot(self):
        with self.lock:
            self.expire_control(time.time())
            matched = self.npu.get(self.aoa["run_id"]) if self.aoa else None
            # NPU 结果可晚到，但绝不使用其他 run_id 的类别标注当前候选点。
            return {"mode": self.mode, "revision": self.revision, "server_time": time.time(),
                    "aoa": self.aoa, "npu": matched, "signal_npu": self.signal,
                    "candidates": candidates(self.aoa), "sources": dict(self.sources),
                    "events": list(self.events), "received_bytes": self.received_bytes,
                    "rejected": self.rejected, "tcp_port": self.tcp_port,
                    "archive_port": self.archive_port, "instance_root": self.instance_root,
                    "board_host": self.board_host, "board_port": self.board_port,
                    "beam": self.beam, "control": self.control, "trajectory": list(self.trajectory),
                    "board_link": dict(self.board_link) if self.board_link else None,
                    "angle_trajectory": list(self.angle_trajectory),
                    "auto_candidate_id": (automatic_candidate(self.aoa) or {}).get("id")}

    def expire_control(self, now):
        # 超时不自动重发。断开订阅不代表板端取消命令；结果未知直到真实 ACK 到达。
        if self.control and self.control["status"] == "sent" and now-self.control["sent_at"] > 20:
            self.control["status"] = "unknown"
            self.control["error"] = "回执超时，控制连接已重置；结果未知"
            connection = self.control["connection"]
            writer = self.writers.get(connection)
            if writer and self.loop: self.loop.call_soon_threadsafe(writer.close)
            source = self.sources.get("aoa", {})
            if source.get("connection") == connection: source["connected"] = False

    async def send_control(self, request):
        """[波束控制新增] 在 socket 所属事件循环执行；HTTP 成功只表示已发送。"""
        if not isinstance(request, dict) or request.get("mode") not in ("equal", "manual", "follow", "auto", "pause", "acquisition", "processing", "observation", "archive"):
            raise ValueError("无效波束模式")
        with self.lock:
            now = time.time()
            self.expire_control(now)
            source = self.sources.get("aoa", {})
            b = self.beam
            if self.mode != "live" or not source.get("connected") or now-source.get("last_seen", 0)>6:
                raise ValueError("板端未连接；演示模式不发送硬件命令")
            if not b or not b.get("available") or b["session"] != source["session"] or now-b["received_at"]>6:
                raise ValueError("当前板端不提供波束控制")
            if b["pl_version"] != b["required_pl_version"]:
                raise ValueError("等待采集读取 PL 版本" if b["pl_version"] == 0 else "PL 与 PS 版本不匹配")
            if request["mode"] == "auto" and not b.get("auto_supported"):
                raise ValueError("请先更新支持自动最高能量跟踪的 PS 程序")
            if self.control and self.control["status"] == "sent":
                raise ValueError("上一条命令尚未得到板端结果")
            writer = self.writers.get(source["connection"])
            if writer is None or writer.is_closing(): raise ValueError("控制连接不可用")
            # Optional caller-generated correlation ID lets an experiment recover
            # an acknowledgement after an interrupted local HTTP response.
            command_id = request.get('client_command_id', uuid.uuid4().hex)
            if (not isinstance(command_id, str) or len(command_id) != 32
                    or any(c not in '0123456789abcdef' for c in command_id)):
                raise ValueError('Invalid client command ID')
            cmd = dict(schema=1, type="beam_command", session=source["session"],
                       id=command_id, mode=request["mode"])
            if cmd['mode'] == 'archive':
                if not b.get('archive', {}).get('available'):
                    raise ValueError('当前 PS 尚未提供原始归档开关')
                if type(request.get('archive_enabled')) is not bool:
                    raise ValueError('归档开关必须为布尔值')
                cmd['archive_enabled'] = request['archive_enabled']
            if cmd['mode'] == 'observation':
                if not b.get('observation', {}).get('available'):
                    raise ValueError('当前 PS 尚未提供合成 IQ 观测开关')
                if any(type(request.get(k)) is not bool for k in ('waveform_enabled', 'spectrum_enabled')):
                    raise ValueError('观测开关必须为布尔值')
                cmd.update(waveform_enabled=request['waveform_enabled'], spectrum_enabled=request['spectrum_enabled'])
            if cmd["mode"] == "processing":
                processing = b.get("processing", {})
                if not processing.get("available"):
                    raise ValueError("当前 PS 尚未提供六天线测角/扫描模式")
                # 旧 PS 没 supported_modes，只保留 position/angle6；scan6 必须由新版明确声明。
                supported = processing.get("supported_modes")
                if supported is None:
                    supported = ("position", "angle6")
                requested = request.get("processing_mode")
                if requested not in ("position", "angle6", "scan6") or type(request.get("joint_paths")) is not int or request["joint_paths"] not in (1, 2, 3):
                    raise ValueError("无效处理模式/来波数")
                if requested not in supported:
                    raise ValueError("板端不支持该分析模式")
                if requested == "scan6" and request["joint_paths"] != 3:
                    raise ValueError("扫描模式固定 joint_paths=3")
                cmd.update(processing_mode=requested, joint_paths=request["joint_paths"])
            if cmd["mode"]=="acquisition":
                if not b.get("acquisition",{}).get("available"):raise ValueError("请先更新双模式 PS 程序")
                if request.get("acquisition_mode") not in ("locate","identify"):raise ValueError("无效采集模式")
                cmd["acquisition_mode"]=request["acquisition_mode"]
            if cmd["mode"] in ("manual", "follow") and (self.aoa or {}).get("session") != source["session"]:
                raise ValueError("等待当前板端会话的测向配置")
            if cmd["mode"] == "manual":
                angle = request.get("angle_deg")
                search = (self.aoa or {}).get("search", {})
                lo, step, count = (search.get(k) for k in ("start", "step", "count"))
                if not finite(angle) or lo is None or not lo<=angle<=lo+step*(count-1):
                    raise ValueError("角度超出板端搜索范围")
                cmd["angle_deg"] = angle
            if cmd["mode"] == "follow" and (self.aoa or {}).get("processing_mode") == "scan6":
                raise ValueError("扫描模式暂不支持自动跟随，请使用手动角度或自动模式")
            if cmd["mode"] == "follow" and (self.aoa or {}).get("processing_mode") == "angle6":
                if request.get("run_id") != self.aoa["run_id"] or now-self.aoa["received_at"] > 6:
                    raise ValueError("测角批次已更新，请重新选择")
                index = request.get("path_index")
                selected = next((p for p in eligible_paths(self.aoa) if type(index) is int and p["id"] == index), None)
                if selected is None:
                    raise ValueError("路径不满足联合测角跟踪条件")
                cmd.update(path_index=index, angle_deg=selected["angle_deg"], run_id=self.aoa["run_id"])
            elif cmd["mode"] == "follow":
                if request.get("run_id") != (self.aoa or {}).get("run_id"):
                    raise ValueError("候选批次已更新，请重新选择")
                index = request.get("candidate")
                cs = candidates(self.aoa)
                if type(index) is not int or not 0<=index<len(cs) or now-self.aoa["received_at"]>6:
                    raise ValueError("没有新鲜候选点")
                c = cs[index]
                if request.get("candidate_id") != c["id"]: raise ValueError("候选身份已更新")
                if not (c.get('valid') if self.aoa.get('v12', {}).get('version') == '1.2' else c["status"] == "candidate"):
                    raise ValueError("候选几何或测向质量不满足跟踪条件")
                groups = {g["id"]:g for g in self.aoa["groups"]}
                cmd["paths"] = c["paths"]
                cmd["bearing_deg"] = [groups[g]["paths"][c["paths"][g]]["angle_deg"] for g in range(2)]
            control = self.control = dict(id=cmd["id"], mode=cmd["mode"], status="sent", error="",
                                          connection=source["connection"], session=source["session"], sent_at=now)
        try:
            writer.write(encode(cmd))
            await asyncio.wait_for(writer.drain(), 1)
        except (OSError, TimeoutError) as e:
            with self.lock:
                control["status"] = "unknown"
                control["error"] = str(e)
            raise ValueError("命令发送中断，结果未知") from e
        return dict(control)


async def receive_client(reader, writer, state, clients):
    connection = uuid.uuid4().hex
    peer = writer.get_extra_info("peername")
    if len(clients) >= 8:
        writer.close()
        await writer.wait_closed()
        return
    clients.add(connection)
    identity = None
    try:
        while True:
            header = await asyncio.wait_for(reader.readexactly(8), 10)
            if header[:4] != MAGIC:
                raise ValueError("bad magic")
            length = struct.unpack("!I", header[4:])[0]
            if not 0 < length <= MAX_PACKET:
                raise ValueError("oversized/empty frame")
            body = await asyncio.wait_for(reader.readexactly(length), 10)
            p = decode(body)
            current = p["source"], p["session"]
            if identity is not None and current != identity:
                raise ValueError("connection changed identity")
            identity = current
            state.ingest(p, str(peer[0]), connection, length+8)
            if p["source"] == "aoa": state.writers[connection] = writer
    except asyncio.IncompleteReadError as e:
        if e.partial:
            state.error(f"{peer[0]}: truncated frame")
    except (ValueError, KeyError, TypeError, RecursionError, TimeoutError, OSError) as e:
        state.error(f"{peer[0]}: {e}")
    finally:
        state.disconnected(connection)
        state.writers.pop(connection, None)
        clients.discard(connection)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def handler(state):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT/"web"), **kwargs)

        def log_message(self, *_):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/api/state", "/api/snapshot", "/api/health"):
                value = ({"application": "fpga-aoa-console", "mode": state.mode,
                          "tcp_port": state.tcp_port, "board_link": state.board_link,
                          "archive_port": state.archive_port,
                          "instance_root": state.instance_root,
                          "board_host": state.board_host,
                          "board_port": state.board_port} if path == "/api/health" else state.snapshot())
                body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if path.endswith("snapshot"):
                    self.send_header("Content-Disposition", 'attachment; filename="aoa-snapshot.json"')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                super().do_GET()

        def do_POST(self):
            # [波束控制新增] 只接受本机同源 JSON，避免外部页面/DNS 重绑定提交控制。
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            host = self.headers.get("Host")
            if host not in allowed or (origin and origin != "http://"+host):
                self.send_error(403); return
            if self.path != "/api/beam" or self.headers.get_content_type() != "application/json":
                self.send_error(400); return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0<size<=2048: raise ValueError("请求大小错误")
                self.connection.settimeout(3)
                raw = self.rfile.read(size)
                if len(raw)!=size: raise ValueError("请求不完整")
                request = json.loads(raw)
                if state.loop is None: raise ValueError("服务未就绪")
                future = asyncio.run_coroutine_threadsafe(state.send_control(request), state.loop)
                result = future.result(timeout=3)
                code = 202
            except (ValueError, TypeError, TimeoutError, OSError) as e:
                result = {"error": str(e)}
                code = 400
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(code);self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)));self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
    return Handler


async def run(args, state):
    state.loop = asyncio.get_running_loop()
    if args.demo:
        from demo import packets
        while True:
            for p in packets():
                state.ingest(p)
            await asyncio.sleep(1)
    elif args.board_host:
        from board_connection import subscribe
        await subscribe(state, args.board_host, args.board_port, args.archive_port)
    else:
        clients = set()
        server = await asyncio.start_server(lambda r,w: receive_client(r,w,state,clients), args.listen, args.tcp_port)
        async with server:
            print(f"TCP {args.listen}:{args.tcp_port} ready", flush=True)
            await server.serve_forever()


def running_console(url, mode, tcp_port, board_host=None, board_port=9000,
                    archive_port=0, instance_root=None):
    """[启动修正] Reuse this application's matching instance, never an unrelated web app."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for endpoint in ("/api/health", "/api/state"):
        try:
            with opener.open(url + endpoint, timeout=1) as response:
                data = json.loads(response.read(2 * 1024 * 1024))
            if not isinstance(data, dict):
                continue
            known = (data.get("application") == "fpga-aoa-console" if endpoint == "/api/health"
                     else {"aoa", "npu", "sources", "trajectory", "rejected"} <= data.keys())
            if known:
                link = data.get('board_link') or {}
                source = data.get('instance_root')
                if not isinstance(link, dict) or not isinstance(source, str):
                    return False
                source_path = pathlib.Path(source)
                if not source_path.is_absolute():
                    return False
                expected_root = ROOT if instance_root is None else pathlib.Path(instance_root)
                return (data.get("mode") == mode and data.get("tcp_port") == tcp_port
                        and data.get('board_host', link.get('host')) == board_host
                        and data.get('board_port', link.get('port')) == board_port
                        and type(data.get('archive_port')) is int
                        and data['archive_port'] == archive_port
                        and os.path.normcase(str(source_path.resolve()))
                        == os.path.normcase(str(expected_root.resolve())))
        except (OSError, ValueError) as error:
            if hasattr(error, "close"):
                error.close()
            continue
    return False


def saved_console_port(path, instance_root):
    """A saved port is only a discovery hint; reuse still checks HTTP identity."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('instance_root'), str):
            return None
        source = pathlib.Path(data['instance_root'])
        port = data.get('port')
        if (source.is_absolute() and type(port) is int and 1 <= port <= 65535
                and os.path.normcase(str(source.resolve()))
                == os.path.normcase(str(pathlib.Path(instance_root).resolve()))):
            return port
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def remember_console_port(path, instance_root, port):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(instance_root=instance_root, port=port),
                                   ensure_ascii=False), encoding='utf-8')
    except OSError as error:
        print(f'Cannot remember web port {port}: {error}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="AOA 本地上位机")
    parser.add_argument("--demo", action="store_true", help="明确标注的合成演示，不监听板端 TCP")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--tcp-port", type=int, default=9000)
    parser.add_argument("--http-port", type=int, default=8775)
    parser.add_argument("--auto-http-port", action="store_true",
                        help="网页端口占用时自动选择空闲端口，不停止其他进程")
    parser.add_argument("--http-listen", default="127.0.0.1")
    parser.add_argument("--board-host", help="复旦微 IPv4；配置后主动订阅，不再监听旧版遥测端口")
    parser.add_argument("--board-port", type=int, default=9000)
    parser.add_argument("--archive-port", type=int, default=0, help="登记本机归档端口；0 为仅订阅显示")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--reuse-running", action="store_true",
                        help="打开同端口上已运行的同模式 AOA 上位机")
    args = parser.parse_args(argv)
    if any(not 1 <= v <= 65535 for v in (args.tcp_port,args.http_port,args.board_port)):
        parser.error("port must be 1..65535")
    if not 0 <= args.archive_port <= 65535:
        parser.error("archive port must be 0..65535")
    mode = "demo" if args.demo else "live"
    state = State(mode)
    state.tcp_port = args.tcp_port
    state.archive_port = args.archive_port
    state.board_host = args.board_host
    state.board_port = args.board_port
    if args.board_host:
        state.board_link = dict(host=args.board_host, port=args.board_port,
                                status='connecting', error='')
    preferred_port = args.http_port
    ports = (list(range(preferred_port, min(preferred_port + 16, 65536)))
             if args.auto_http_port else [preferred_port])
    port_record = ROOT.parent / 'local_config' / 'console_port.json'

    def reuse(port):
        url = f"http://127.0.0.1:{port}"
        if args.reuse_running and running_console(
                url, mode, args.tcp_port, args.board_host, args.board_port,
                args.archive_port, state.instance_root):
            print(f"AOA Console is already running: {url}", flush=True)
            if args.open:
                webbrowser.open(url)
            return True
        return False

    # Avoid HTTP timeout probes on every free candidate. The previous bound port
    # finds this package's alternate instance even after the preferred port frees.
    if args.reuse_running:
        known_ports = [preferred_port]
        if args.auto_http_port:
            saved = saved_console_port(port_record, state.instance_root)
            if saved is not None and saved not in known_ports:
                known_ports.append(saved)
        for port in known_ports:
            if reuse(port):
                return 0
    for port in ports + ([0] if args.auto_http_port else []):
        try:
            httpd = ConsoleHTTPServer((args.http_listen, port), handler(state))
            break
        except OSError as error:
            # Another double-click may have bound since the first identity check.
            if port and reuse(port):
                return 0
            if not args.auto_http_port or port == 0:
                print(f"Cannot open web port {port}: {error}\n"
                      "The port may be occupied or reserved. Use --http-port with a free port.",
                      flush=True)
                return 1
    args.http_port = httpd.server_port
    if args.auto_http_port:
        remember_console_port(port_record, state.instance_root, args.http_port)
    url = f"http://127.0.0.1:{args.http_port}"
    if args.http_port != preferred_port:
        print(f"Web port {preferred_port} is occupied; using {args.http_port}", flush=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"AOA Console: {url} | {'DEMO / 合成数据' if args.demo else 'LIVE / 等待板端'}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        asyncio.run(run(args,state))
    except KeyboardInterrupt:
        pass
    except OSError as error:
        print(f"Cannot start board TCP listener {args.listen}:{args.tcp_port}: {error}\n"
              "Keep only one live AOA Console for this board TCP port.", flush=True)
        return 1
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
