#!/usr/bin/env python3
"""The egress allowlist has to refuse what it claims to refuse.

An allowlist that matches on substrings, or that lets a suffix rule swallow a
lookalike domain, is decoration. These tests are mostly about the ways a host
can be made to look like one on the list.

The end-to-end cases start the real proxy on an ephemeral port and speak the
CONNECT protocol to it. No outbound network: the one allowed destination is a
socket this test opened itself.
"""
from __future__ import annotations

import http.client
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_proxy  # noqa: E402

POLICY = """\
[hosts]
model_api = [".provider-one.test", ".provider-two.test"]
community = ["stacker.news", "www.reddit.com"]
"""


class AllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.policy = self.tmp / "policy.toml"
        self.policy.write_text(POLICY)
        self.exact, self.suffixes = agent_proxy.load_allowlist((self.policy,))

    def allowed(self, host: str) -> bool:
        return agent_proxy.is_allowed(host, self.exact, self.suffixes)

    def test_an_exact_host_is_allowed(self):
        self.assertTrue(self.allowed("stacker.news"))
        self.assertTrue(self.allowed("www.reddit.com"))

    def test_a_suffix_covers_the_domain_and_its_subdomains(self):
        self.assertTrue(self.allowed("provider-one.test"))
        self.assertTrue(self.allowed("api.provider-one.test"))
        self.assertTrue(self.allowed("a.b.provider-one.test"))

    def test_a_lookalike_domain_is_refused(self):
        """The failure that makes an allowlist decoration."""
        for host in ("evil-provider-one.test", "provider-one.test.evil.test",
                     "notstacker.news", "stacker.news.evil.test",
                     "xstacker.news"):
            with self.subTest(host=host):
                self.assertFalse(self.allowed(host))

    def test_a_subdomain_of_an_exact_entry_is_refused(self):
        """`stacker.news` is exact, so `evil.stacker.news` is not implied."""
        self.assertFalse(self.allowed("evil.stacker.news"))

    def test_case_and_trailing_dot_do_not_evade(self):
        self.assertTrue(self.allowed("API.PROVIDER-ONE.TEST"))
        self.assertTrue(self.allowed("stacker.news."))

    def test_junk_is_refused(self):
        for host in ("", "   ", "stacker.news/../evil", "169.254.169.254"):
            with self.subTest(host=host):
                self.assertFalse(self.allowed(host))

    def test_the_live_policy_files_load_and_cover_the_record(self):
        """The tracked policy covers what the registry may name, and no more.

        Deliberately does not name the model provider. Those hostnames live in
        the gitignored local file for the same reason `.env` exists, so a test
        that asserted them would put them back in the repository.
        """
        exact, suffixes = agent_proxy.load_allowlist(
            (agent_proxy.REGISTRY_HOSTS, agent_proxy.EGRESS_HOSTS))
        for host in ("www.reddit.com", "stacker.news", "bitcointalk.org",
                     "njump.me"):
            with self.subTest(host=host):
                self.assertTrue(agent_proxy.is_allowed(host, exact, suffixes))
        for host in ("collector.example.invalid", "169.254.169.254",
                     "evil-github.com"):
            with self.subTest(host=host):
                self.assertFalse(agent_proxy.is_allowed(host, exact, suffixes))

    def test_the_local_provider_list_is_what_adds_the_model_api(self):
        """Absent it, an agent reaches no provider at all.

        The failure mode worth pinning: the tracked policy alone must not
        happen to allow a provider, and the local file must actually widen it.
        Asserted by counting, so the provider is never named here.
        """
        tracked, tracked_suffixes = agent_proxy.load_allowlist(
            (agent_proxy.REGISTRY_HOSTS, agent_proxy.EGRESS_HOSTS))
        full, full_suffixes = agent_proxy.load_allowlist(
            (agent_proxy.REGISTRY_HOSTS, agent_proxy.EGRESS_HOSTS,
             agent_proxy.LOCAL_HOSTS))
        if not agent_proxy.LOCAL_HOSTS.exists():
            self.skipTest("no local provider list on this machine")
        self.assertGreater(len(full) + len(full_suffixes),
                           len(tracked) + len(tracked_suffixes))

    def test_a_missing_local_list_is_survivable(self):
        """An absent file must not crash the proxy, only narrow it."""
        exact, suffixes = agent_proxy.load_allowlist(
            (agent_proxy.REGISTRY_HOSTS, self.tmp / "does-not-exist.toml"))
        self.assertTrue(agent_proxy.is_allowed("stacker.news", exact, suffixes))


class ProxyProtocolTests(unittest.TestCase):
    """Speak CONNECT to the real server, no outbound network involved."""

    @classmethod
    def setUpClass(cls) -> None:
        # A destination this test owns, added to the policy by name so an
        # allowed CONNECT has somewhere real to land.
        cls.sink = socket.socket()
        cls.sink.bind(("127.0.0.1", 0))
        cls.sink.listen(8)
        cls.sink_port = cls.sink.getsockname()[1]
        cls.accepted: list[socket.socket] = []

        def accept_loop():
            while True:
                try:
                    conn, _ = cls.sink.accept()
                except OSError:
                    return
                cls.accepted.append(conn)
                threading.Thread(target=cls.echo, args=(conn,), daemon=True).start()

        threading.Thread(target=accept_loop, daemon=True).start()

        cls.tmp = Path(tempfile.mkdtemp())
        policy = cls.tmp / "policy.toml"
        policy.write_text('[hosts]\nlocal = ["localhost"]\n')
        cls.server, _ = agent_proxy.start_for_test(paths=(policy,))
        cls.proxy_port = cls.server.server_address[1]

    @staticmethod
    def echo(conn: socket.socket) -> None:
        try:
            while chunk := conn.recv(4096):
                conn.sendall(chunk)
        except OSError:
            pass
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.sink.close()

    def connect(self, target: str) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.proxy_port, timeout=10)
        conn.request("CONNECT", target)
        response = conn.getresponse()
        status, body = response.status, response.read().decode(errors="replace")
        conn.close()
        return status, body

    def test_an_allowed_host_gets_a_working_tunnel(self):
        """The bytes actually flow, not just a 200.

        A proxy that returns Connection Established and then drops the payload
        would pass every refusal test above while breaking every agent run.
        """
        agent_proxy.Proxy.ports = frozenset({self.sink_port})
        self.addCleanup(setattr, agent_proxy.Proxy, "ports",
                        agent_proxy.ALLOWED_PORTS)
        raw = socket.create_connection(("127.0.0.1", self.proxy_port), timeout=10)
        self.addCleanup(raw.close)
        raw.sendall(f"CONNECT localhost:{self.sink_port} HTTP/1.1\r\n"
                    f"Host: localhost\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += raw.recv(1024)
        self.assertIn(b"200", head.split(b"\r\n")[0])
        raw.sendall(b"round trip")
        self.assertEqual(b"round trip", raw.recv(64))

    def test_a_denied_host_is_refused_with_a_reason(self):
        status, body = self.connect("collector.example.invalid:443")
        self.assertEqual(403, status)
        self.assertIn("not in registry_hosts.toml", body)

    def test_a_non_443_port_is_refused(self):
        status, body = self.connect("localhost:22")
        self.assertEqual(403, status)
        self.assertIn("port 22 is not allowed", body)

    def test_plain_http_is_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.proxy_port, timeout=10)
        conn.request("GET", "http://stacker.news/")
        response = conn.getresponse()
        self.assertEqual(403, response.status)
        self.assertIn("only CONNECT", response.read().decode())
        conn.close()

    def test_a_malformed_target_is_refused(self):
        status, _ = self.connect("no-port-here")
        self.assertEqual(400, status)


if __name__ == "__main__":
    unittest.main()
