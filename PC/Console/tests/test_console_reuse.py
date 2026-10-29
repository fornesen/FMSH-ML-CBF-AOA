"""Console reuse and automatic HTTP ports without board or live-service access."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class ConsoleIdentityTests(unittest.TestCase):
    def identity(self):
        return dict(application='fpga-aoa-console', mode='live', tcp_port=9000,
                    board_link=dict(host='192.0.2.51', port=9000),
                    archive_port=24812, instance_root=str(server.ROOT.resolve()))

    def matches(self, data, **kwargs):
        response = Mock()
        response.read.return_value = json.dumps(data).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(server.urllib.request, 'build_opener', return_value=opener):
            return server.running_console('http://127.0.0.1:24800', 'live', 9000,
                                          '192.0.2.51', 9000, 24812, **kwargs)

    def test_matching_archive_and_source_are_required(self):
        self.assertTrue(self.matches(self.identity()))
        variants = [dict(mode='demo'), dict(tcp_port=9001), dict(archive_port=24813),
                    dict(instance_root=str(server.ROOT.parent / 'another Console')),
                    dict(instance_root='relative/Console'), dict(instance_root=None),
                    dict(board_link=dict(host='192.0.2.52', port=9000)),
                    dict(board_link=dict(host='192.0.2.51', port=9001)),
                    dict(board_link=['invalid'])]
        for changed in variants:
            with self.subTest(changed=changed):
                self.assertFalse(self.matches(dict(self.identity(), **changed)))

    def test_old_health_without_identity_is_not_reused(self):
        for field in ('archive_port', 'instance_root'):
            data = self.identity()
            del data[field]
            with self.subTest(field=field):
                self.assertFalse(self.matches(data))

    def test_startup_board_identity_is_available_before_subscription(self):
        data = dict(self.identity(), board_link=None, board_host='192.0.2.51', board_port=9000)
        self.assertTrue(self.matches(data))
        self.assertFalse(self.matches(dict(data, board_port=9001)))

    @unittest.skipUnless(os.name == 'nt', 'Windows paths ignore case')
    def test_windows_source_path_case_is_normalized(self):
        data = self.identity()
        data['instance_root'] = data['instance_root'].upper()
        self.assertTrue(self.matches(data))

    def test_health_and_snapshot_expose_identity_without_board_connection(self):
        state = server.State()
        state.archive_port = 24812
        handler_type = server.handler(state)
        request = handler_type.__new__(handler_type)
        request.path = '/api/health'
        request.wfile = io.BytesIO()
        request.send_response = Mock()
        request.send_header = Mock()
        request.end_headers = Mock()
        request.do_GET()
        health = json.loads(request.wfile.getvalue())
        for data in (health, state.snapshot()):
            self.assertEqual(data['archive_port'], 24812)
            self.assertEqual(data['instance_root'], str(server.ROOT.resolve()))
            self.assertIsNone(data['board_link'])
            self.assertIsNone(data['board_host'])
            self.assertEqual(data['board_port'], 9000)


class ConsolePortTests(unittest.TestCase):
    def run_main(self, argv, factory=None, matching=None, saved_port=None):
        httpd = Mock()
        httpd.server_port = 24801
        observed = []

        async def run_without_board(args, state):
            observed.append((args, state))

        with contextlib.ExitStack() as stack:
            constructor = stack.enter_context(patch.object(
                server, 'ConsoleHTTPServer',
                side_effect=factory or (lambda *_: httpd)))
            reuse = stack.enter_context(patch.object(
                server, 'running_console', side_effect=matching or (lambda *_: False)))
            stack.enter_context(patch.object(server, 'saved_console_port', return_value=saved_port))
            record = stack.enter_context(patch.object(server, 'remember_console_port'))
            browser = stack.enter_context(patch.object(server.webbrowser, 'open'))
            stack.enter_context(patch.object(server.threading, 'Thread'))
            stack.enter_context(patch.object(server, 'run', side_effect=run_without_board))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = server.main(['--http-port', '24800', '--board-host', '192.0.2.51',
                                  '--archive-port', '24812'] + argv)
        self.last_record = record
        return result, constructor, reuse, browser, observed, output.getvalue(), httpd

    def test_auto_port_skips_occupied_port_and_opens_actual_bound_port(self):
        httpd = Mock(server_port=24801)

        def bind(address, _handler):
            if address[1] == 24800:
                raise OSError('occupied by another application')
            return httpd

        result, constructor, _, browser, observed, output, _ = self.run_main(
            ['--auto-http-port', '--reuse-running', '--open'], factory=bind)
        self.assertEqual(result, 0)
        self.assertEqual([call.args[0][1] for call in constructor.call_args_list], [24800, 24801])
        browser.assert_called_once_with('http://127.0.0.1:24801')
        args, state = observed[0]
        self.assertEqual(args.http_port, 24801)
        self.assertEqual(state.archive_port, 24812)
        self.assertEqual(state.board_link['host'], '192.0.2.51')
        self.assertEqual(state.board_link['port'], 9000)
        self.assertIn('using 24801', output)
        httpd.shutdown.assert_called_once()
        httpd.server_close.assert_called_once()

    def test_auto_port_prefers_existing_matching_instance_before_new_binding(self):
        result, constructor, reuse, browser, observed, _, _ = self.run_main(
            ['--auto-http-port', '--reuse-running', '--open'],
            matching=lambda url, *_: url.endswith(':24804'), saved_port=24804)
        self.assertEqual(result, 0)
        constructor.assert_not_called()
        self.assertEqual(observed, [])
        browser.assert_called_once_with('http://127.0.0.1:24804')
        self.assertEqual(reuse.call_args.args[-2:], (24812, str(server.ROOT.resolve())))

    def test_double_click_race_rechecks_identity_after_bind_conflict(self):
        checks = []

        def matching(url, *_):
            checks.append(url)
            return len(checks) > 1 and url.endswith(':24800')

        def occupied(*_):
            raise OSError('another launch has just bound')

        result, constructor, _, _, observed, _, _ = self.run_main(
            ['--auto-http-port', '--reuse-running'], factory=occupied, matching=matching)
        self.assertEqual(result, 0)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(observed, [])

    def test_empty_candidates_are_bound_without_sixteen_http_timeout_probes(self):
        result, constructor, reuse, _, _, _, _ = self.run_main(
            ['--auto-http-port', '--reuse-running'])
        self.assertEqual(result, 0)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(reuse.call_count, 1)
        self.last_record.assert_called_once_with(
            server.ROOT.parent / 'local_config' / 'console_port.json',
            str(server.ROOT.resolve()), 24801)

    def test_stale_saved_port_is_health_checked_then_does_not_block_binding(self):
        result, constructor, reuse, _, _, _, _ = self.run_main(
            ['--auto-http-port', '--reuse-running'], saved_port=24950)
        self.assertEqual(result, 0)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual([call.args[0] for call in reuse.call_args_list],
                         ['http://127.0.0.1:24800', 'http://127.0.0.1:24950'])

    def test_auto_port_uses_os_assigned_port_after_bounded_candidates(self):
        httpd = Mock(server_port=24999)

        def bind(address, _handler):
            if address[1]:
                raise OSError('occupied')
            return httpd

        result, constructor, _, browser, _, _, _ = self.run_main(
            ['--auto-http-port', '--open'], factory=bind)
        self.assertEqual(result, 0)
        self.assertEqual([call.args[0][1] for call in constructor.call_args_list],
                         list(range(24800, 24816)) + [0])
        browser.assert_called_once_with('http://127.0.0.1:24999')

    def test_ordinary_cli_still_reports_fixed_port_failure(self):
        def occupied(*_):
            raise OSError('occupied')

        result, constructor, _, browser, observed, output, _ = self.run_main([], factory=occupied)
        self.assertEqual(result, 1)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(observed, [])
        browser.assert_not_called()
        self.assertIn('Cannot open web port 24800', output)

    def test_legacy_mode_keeps_board_link_empty_for_existing_web_ui(self):
        observed = []

        async def run_without_board(args, state):
            observed.append(state)

        httpd = Mock(server_port=24800)
        with patch.object(server, 'ConsoleHTTPServer', return_value=httpd), \
                patch.object(server.threading, 'Thread'), \
                patch.object(server, 'run', side_effect=run_without_board), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.main(['--http-port', '24800']), 0)
        self.assertIsNone(observed[0].board_link)
        self.assertIsNone(observed[0].board_host)


class ConsoleSocketOwnershipTests(unittest.TestCase):
    def test_windows_exclusive_option_precedes_bind(self):
        httpd = server.ConsoleHTTPServer.__new__(server.ConsoleHTTPServer)
        httpd.socket = Mock()
        actions = []
        httpd.socket.setsockopt.side_effect = lambda *_: actions.append('exclusive')
        with patch.object(server.os, 'name', 'nt'), \
                patch.object(server.socket, 'SO_EXCLUSIVEADDRUSE', 12345, create=True), \
                patch.object(server.http.server.ThreadingHTTPServer, 'server_bind',
                             autospec=True, side_effect=lambda _: actions.append('bind')):
            httpd.server_bind()
        httpd.socket.setsockopt.assert_called_once_with(server.socket.SOL_SOCKET, 12345, 1)
        self.assertEqual(actions, ['exclusive', 'bind'])
        self.assertFalse(httpd.allow_reuse_address)
        self.assertFalse(httpd.allow_reuse_port)

    def test_non_windows_uses_normal_bind_without_exclusive_option(self):
        httpd = server.ConsoleHTTPServer.__new__(server.ConsoleHTTPServer)
        httpd.socket = Mock()
        with patch.object(server.os, 'name', 'posix'), \
                patch.object(server.http.server.ThreadingHTTPServer, 'server_bind',
                             autospec=True) as bind:
            httpd.server_bind()
        httpd.socket.setsockopt.assert_not_called()
        bind.assert_called_once_with(httpd)

    def test_real_ephemeral_listener_cannot_be_overlapped(self):
        # Only a temporary loopback port; no worker, board or existing service.
        with server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.handler(server.State())) as old:
            with self.assertRaises(OSError):
                server.ConsoleHTTPServer(old.server_address, server.handler(server.State()))


class ConsolePortRecordTests(unittest.TestCase):
    def test_saved_port_is_scoped_to_source_directory(self):
        with tempfile.TemporaryDirectory(prefix='AOA console record ') as folder:
            record = Path(folder) / 'local_config' / 'console_port.json'
            root = str(server.ROOT.resolve())
            server.remember_console_port(record, root, 24950)
            self.assertEqual(server.saved_console_port(record, root), 24950)
            self.assertIsNone(server.saved_console_port(record, str(server.ROOT.parent / 'other')))
            for data in ('not JSON', '{}', '[]', json.dumps(dict(instance_root=root, port=0))):
                record.write_text(data, encoding='utf-8')
                self.assertIsNone(server.saved_console_port(record, root))

    def test_unwritable_record_does_not_fail_startup(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError('read only')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                server.remember_console_port(Path('unused') / 'console_port.json',
                                             str(server.ROOT.resolve()), 24950)
        self.assertIn('Cannot remember web port 24950', output.getvalue())


if __name__ == '__main__':
    unittest.main()
