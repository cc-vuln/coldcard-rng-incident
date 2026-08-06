#!/usr/bin/env python3
"""The only way out for an unattended agent run.

The containment in docs/design/agent-sandbox.md takes the secrets, the capture
browser and the archive away from an injected agent, and left one gap: it
could still POST to a host of its choosing. Nothing worth sending was in
reach, so the gap was tolerable rather than fine. This closes it.

Two halves, and neither works without the other:

  nftables    drops every packet from the agent account except DNS and this
              proxy, keyed on uid rather than on a systemd unit. The unit is
              the wrong key: discover-community runs the driver's own Reddit
              hydration and the agent in one cgroup, so IPAddress* cannot tell
              them apart. A uid can
  this proxy  refuses a CONNECT to any host not named in
              scripts/registry_hosts.toml or scripts/agent_egress_hosts.toml

The policy is one sentence: an agent may reach its model provider, and may
read what the registry is allowed to name. The second half falls out of the
work rather than being invented for it, because anything a sweep reads has to
be registerable to be worth reading, and that list is already maintained by
hand. Both files live in scripts/, which is read-only to the agent, so a run
cannot widen its own reach by editing them.

It binds 127.0.0.2 rather than 127.0.0.1 deliberately. The capture browser is
on 127.0.0.1:10086 with signed-in sessions behind it, and the nftables rule
permits a single address: allowing 127.0.0.1 to reach a proxy would allow it
to reach the browser too, because packet filters match addresses and not
ports as reliably as one would like across the loopback interface.

CONNECT only. Every agent request today is https, a plain proxied GET would
let the proxy see and alter cleartext this project has no business touching,
and refusing it keeps the audit question simple: the log records which host
was asked for and whether it was allowed, never what was said.

Refusals are logged with the reason. A refusal is a finding: either a
provider changed an endpoint, or a run tried to reach somewhere it should
not, and the two are worth telling apart.

Run it from scripts/agent-proxy.service.example. Stdlib only, like everything
else that has to still work in ten years.
"""
from __future__ import annotations

import argparse
import selectors
import socket
import sys
import threading
import tomllib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY_HOSTS = HERE / "registry_hosts.toml"
EGRESS_HOSTS = HERE / "agent_egress_hosts.toml"
# Gitignored. Holds the model provider's API hostnames, which are deliberately
# not in the tracked tree: see agent_egress_hosts.toml for why. Absent is
# survivable and reported, never assumed away, because an allowlist that has
# quietly lost the provider looks exactly like the provider being down.
LOCAL_HOSTS = HERE / "agent-egress.local.toml"

DEFAULT_ADDRESS = "127.0.0.2"
DEFAULT_PORT = 8118
ALLOWED_PORTS = frozenset({443})
CONNECT_TIMEOUT = 30
IDLE_TIMEOUT = 300
BUFFER = 65536


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {message}", flush=True)


def load_allowlist(paths: tuple[Path, ...]) -> tuple[frozenset[str], frozenset[str]]:
    """Read both policy files into exact names and domain suffixes.

    A leading dot means the domain and everything under it. Anything else has
    to match in full: a substring rule would make `evil-github.com` look like
    `github.com`, which is the classic way an allowlist becomes decoration.
    """
    exact: set[str] = set()
    suffixes: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        groups = tomllib.loads(path.read_text()).get("hosts", {})
        for group in groups.values():
            for entry in group:
                host = str(entry).strip().lower()
                if not host:
                    continue
                if host.startswith("."):
                    suffixes.add(host)
                else:
                    exact.add(host)
    return frozenset(exact), frozenset(suffixes)


def is_allowed(host: str, exact: frozenset[str], suffixes: frozenset[str]) -> bool:
    host = host.strip().lower().rstrip(".")
    if not host or "/" in host:
        return False
    if host in exact:
        return True
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in suffixes)


def pump(client: socket.socket, upstream: socket.socket) -> None:
    """Move bytes both ways until either side closes."""
    selector = selectors.DefaultSelector()
    selector.register(client, selectors.EVENT_READ)
    selector.register(upstream, selectors.EVENT_READ)
    try:
        while True:
            ready = selector.select(timeout=IDLE_TIMEOUT)
            if not ready:
                return
            for key, _ in ready:
                source = key.fileobj
                target = upstream if source is client else client
                try:
                    chunk = source.recv(BUFFER)
                except OSError:
                    return
                if not chunk:
                    return
                try:
                    target.sendall(chunk)
                except OSError:
                    return
    finally:
        selector.close()


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    allow: tuple[frozenset[str], frozenset[str]] = (frozenset(), frozenset())
    # A class attribute so the tests can open a tunnel to a socket they own
    # without needing to bind 443. In service it is exactly {443}.
    ports: frozenset[int] = ALLOWED_PORTS

    def log_message(self, *args) -> None:  # our own lines instead
        pass

    def _refuse(self, code: int, reason: str, target: str) -> None:
        log(f"REFUSED {target}: {reason}")
        body = f"agent-proxy: {reason}\n".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self) -> None:
        target = self.path
        host, _, raw_port = target.rpartition(":")
        if not host or not raw_port.isdigit():
            self._refuse(400, "malformed CONNECT target", target)
            return
        port = int(raw_port)
        if port not in self.ports:
            self._refuse(403, f"port {port} is not allowed", target)
            return
        if not is_allowed(host, *self.allow):
            self._refuse(
                403,
                "host is not in registry_hosts.toml or agent_egress_hosts.toml; "
                "adding one is a human edit",
                target)
            return
        try:
            upstream = socket.create_connection((host, port), CONNECT_TIMEOUT)
        except OSError as exc:
            self._refuse(502, f"upstream unreachable ({exc})", target)
            return
        log(f"allowed {target}")
        self.send_response(200, "Connection Established")
        self.end_headers()
        try:
            self.connection.settimeout(None)
            upstream.settimeout(None)
            pump(self.connection, upstream)
        finally:
            upstream.close()
        self.close_connection = True

    def _no_plain_http(self) -> None:
        self._refuse(
            403,
            "plain HTTP is not proxied, only CONNECT. This keeps cleartext "
            "the project has no business reading out of the proxy",
            self.path)
        self.close_connection = True

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = _no_plain_http


def serve(address: str, port: int, paths: tuple[Path, ...]) -> int:
    exact, suffixes = load_allowlist(paths)
    Proxy.allow = (exact, suffixes)
    server = ThreadingHTTPServer((address, port), Proxy)
    server.daemon_threads = True
    log(f"agent proxy on {address}:{port}, "
        f"{len(exact)} exact host(s) and {len(suffixes)} domain(s) allowed")
    log("policy: the model provider, plus what the registry may name")
    if not LOCAL_HOSTS.exists():
        log(f"WARNING: no {LOCAL_HOSTS.name}, so no model provider is "
            f"allowed and every agent run will fail to reach one. Copy "
            f"{LOCAL_HOSTS.name}.example and fill it in")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--check", action="store_true",
                        help="load the allowlist, report it, and exit")
    parser.add_argument("--test-host", action="append", default=[],
                        help="report whether a host would be allowed, and exit")
    args = parser.parse_args()

    paths = (REGISTRY_HOSTS, EGRESS_HOSTS, LOCAL_HOSTS)
    exact, suffixes = load_allowlist(paths)

    if args.test_host:
        failed = False
        for host in args.test_host:
            ok = is_allowed(host, exact, suffixes)
            print(f"{'allow' if ok else 'DENY '}  {host}")
            failed |= not ok
        return 1 if failed else 0

    if args.check:
        local = "present" if LOCAL_HOSTS.exists() else "MISSING"
        print(f"agent proxy allowlist ok: {len(exact)} exact host(s), "
              f"{len(suffixes)} domain(s); provider list {local}")
        return 0

    return serve(args.address, args.port, paths)


if __name__ == "__main__":
    raise SystemExit(main())


# Kept out of main() so a test can start the server without argparse.
def start_for_test(address: str = "127.0.0.1", port: int = 0,
                   paths: tuple[Path, ...] = (REGISTRY_HOSTS, EGRESS_HOSTS, LOCAL_HOSTS)):
    exact, suffixes = load_allowlist(paths)
    Proxy.allow = (exact, suffixes)
    server = ThreadingHTTPServer((address, port), Proxy)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
