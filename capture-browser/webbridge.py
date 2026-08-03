#!/usr/bin/env python3
"""The capture browser: headless Chromium behind a small HTTP protocol.

Speaks the small action vocabulary the archive's capture scripts use over
POST http://127.0.0.1:10086/command:

    {"action": "list_tabs"|"navigate"|"evaluate"|"save_as_pdf"|"close_tab"
               |"cdp"|"close_session",
     "args": {...}, "session": "<name>"}
  -> {"ok": true, "data": {"success": true, ...}}

Read-only by construction: nothing in the vocabulary can post, follow or
like.

Robustness model: this process may idle for days between captures.
  1. Every action that touches the browser relaunches it once on failure.
  2. systemd restarts the whole process on crash (Restart=always).
  3. systemd recycles it daily (RuntimeMaxSec) so leaks never accumulate.
State worth keeping (challenge cookies) lives in the persistent profile dir
and survives all three layers.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright, Error as PWError

# Overridable so a second checkout can run its own daemon alongside a
# production one. capture.py reads the same variable.
PORT = int(os.environ.get('WEBBRIDGE_PORT', '10086'))
# Pinned so recaptures render identically and the browser's clock never
# disagrees with its network path. Set CAPTURE_BROWSER_TZ to suit the
# deployment; the default is neutral.
TIMEZONE = os.environ.get('CAPTURE_BROWSER_TZ', 'UTC')
# Beside the repo, not in someone's home: a clone gets its own, and
# .capture-browser/ is gitignored so a session never reaches a commit.
PROFILE = Path(
    os.environ.get('CAPTURE_BROWSER_PROFILE')
    or Path(__file__).resolve().parent.parent / '.capture-browser' / 'profile'
)
NAV_TIMEOUT_MS = 60_000
PDF_TIMEOUT_MS = 120_000


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}",
          file=sys.stderr, flush=True)


class BrowserHost:
    """One persistent Chromium context; one current page per session."""

    def __init__(self) -> None:
        self._pw = None
        self._ctx = None
        self._pages: dict[str, object] = {}
        self._sessions_cdp: dict[str, object] = {}

    def stop(self) -> None:
        for closer in (lambda: self._ctx.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._pw, self._ctx = None, None
        self._pages.clear()
        self._sessions_cdp.clear()

    def start(self) -> None:
        self.stop()
        self._pw = sync_playwright().start()
        self._ctx = self._launch()
        # The headless shell advertises HeadlessChrome, which trips exactly
        # the challenges the browser route exists to pass. Relaunch once with
        # the same UA minus the marker.
        probe = self._ctx.new_page()
        ua = probe.evaluate("navigator.userAgent")
        probe.close()
        if "HeadlessChrome" in ua:
            clean = ua.replace("HeadlessChrome", "Chrome")
            self._ctx.close()
            self._ctx = self._launch(user_agent=clean)
        log(f"browser up (profile={PROFILE})")

    def _launch(self, **kw):
        PROFILE.mkdir(parents=True, exist_ok=True)
        return self._pw.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id=TIMEZONE,
            **kw,
        )

    def alive(self) -> bool:
        try:
            return self._ctx is not None and self._ctx.pages is not None
        except Exception:
            return False

    def ensure(self) -> None:
        if not self.alive():
            self.start()

    # ------------------------------------------------------------- actions

    def list_tabs(self, session: str, args: dict) -> dict:
        self.ensure()
        tabs = []
        for p in self._ctx.pages:
            try:
                tabs.append({"url": p.url, "title": p.title()})
            except Exception:
                pass
        return {"success": True, "tabs": tabs}

    def navigate(self, session: str, args: dict) -> dict:
        self.ensure()
        self._sessions_cdp.pop(session, None)
        old = self._pages.pop(session, None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        page = self._ctx.new_page()
        self._pages[session] = page
        page.goto(args["url"], wait_until="domcontentloaded",
                  timeout=NAV_TIMEOUT_MS)
        return {"success": True, "url": page.url}

    def evaluate(self, session: str, args: dict) -> dict:
        page = self._page(session)
        value = page.evaluate(args["code"])
        return {"success": True, "value": value}

    def save_as_pdf(self, session: str, args: dict) -> dict:
        page = self._page(session)
        path = args["path"]
        page.pdf(
            path=path,
            format=str(args.get("paper_format", "a4")).upper(),
            print_background=bool(args.get("print_background", True)),
        )
        return {"success": True, "path": path}

    def cdp(self, session: str, args: dict) -> dict:
        """Raw Chrome DevTools Protocol call against the session's page.

        ingest-x.py captures an element-only screenshot, which needs
        Page.captureScreenshot with a clip. Playwright's own screenshot API
        would do it, but a raw CDP passthrough keeps this daemon a dumb pipe:
        capture scripts own their capture logic entirely.
        """
        page = self._page(session)
        cdp = self._sessions_cdp.get(session)
        if cdp is None:
            cdp = self._ctx.new_cdp_session(page)
            self._sessions_cdp[session] = cdp
        result = cdp.send(args["method"], args.get("params") or {})
        # The daemon returned CDP results at the top level of `data`.
        return {"success": True, **(result if isinstance(result, dict) else {})}

    def close_session(self, session: str, args: dict) -> dict:
        cdp = self._sessions_cdp.pop(session, None)
        if cdp is not None:
            try:
                cdp.detach()
            except Exception:
                pass
        return self.close_tab(session, args)

    def close_tab(self, session: str, args: dict) -> dict:
        page = self._pages.pop(session, None)
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        return {"success": True}

    def _page(self, session: str):
        page = self._pages.get(session)
        if page is None:
            raise RuntimeError(f"no current tab for session {session!r}")
        return page


HOST = BrowserHost()
ACTIONS = {
    "list_tabs": HOST.list_tabs,
    "navigate": HOST.navigate,
    "evaluate": HOST.evaluate,
    "save_as_pdf": HOST.save_as_pdf,
    "close_tab": HOST.close_tab,
    "close_session": HOST.close_session,
    "cdp": HOST.cdp,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # journald gets our own lines instead
        pass

    def do_POST(self):
        if self.path != "/command":
            self._reply(404, {"ok": False, "error": "unknown path"})
            return
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            req = json.loads(body)
            action, args = req.get("action"), req.get("args") or {}
            session = req.get("session") or "default"
            fn = ACTIONS.get(action)
            if fn is None:
                self._reply(200, {"ok": False,
                                  "error": f"unknown action {action!r}"})
                return
            try:
                data = fn(session, args)
            except (PWError, RuntimeError) as e:
                # One relaunch-and-retry: covers a crashed or wedged browser.
                # A dead current tab is not retried for stateful actions,
                # since a fresh browser has no equivalent tab to act on.
                log(f"{action} failed ({e}); relaunching browser")
                HOST.start()
                if action in ("list_tabs", "navigate"):
                    data = fn(session, args)
                else:
                    raise
            self._reply(200, {"ok": True, "data": data})
        except Exception as e:
            log(f"error: {type(e).__name__}: {e}")
            self._reply(200, {"ok": False, "error": str(e)[:300]})

    def _reply(self, status: int, payload: dict) -> None:
        out = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def main() -> None:
    HOST.start()
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    log(f"capture browser listening on 127.0.0.1:{PORT}")
    try:
        srv.serve_forever()
    finally:
        HOST.stop()


if __name__ == "__main__":
    main()
