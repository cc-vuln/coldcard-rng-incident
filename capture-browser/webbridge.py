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

# Ad and tracker request blocking. The list is the committed
# capture-browser/ad-hosts.txt, curated from Peter Lowe's ad and tracking
# server list plus hosts observed in this archive's own captures, so every
# snapshot can be replayed against exactly the list that was active. Mode
# "active" maps the listed hosts to localhost through Chromium's own
# --host-resolver-rules at launch, so ad requests fail at resolution with
# zero per-request overhead; "off" disables the feature. Two mechanisms
# failed before this one (4 Aug 2026): Playwright route interception
# crashed the renderer on heavy pages, and the full 3,517-host list blew
# the 128 KB single-argument exec limit, which is why the list is curated
# rather than fetched wholesale. Blocked hosts serve ads or tracking,
# never content; the list and its date are reported per capture so the
# archive audit can explain any absent ad.
BLOCK_MODE = os.environ.get('WEBBRIDGE_BLOCK_MODE', 'active').lower()
BLOCKLIST_FILE = Path(__file__).resolve().parent / 'ad-hosts.txt'

BLOCK_HOSTS: set = set()
BLOCK_RETRIEVED = "never"


def load_blocklist() -> None:
    """Load the committed ad/tracker host list."""
    global BLOCK_HOSTS, BLOCK_RETRIEVED
    try:
        hosts = set()
        for line in BLOCKLIST_FILE.read_text().splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                hosts.add(line)
        BLOCK_HOSTS = hosts
        BLOCK_RETRIEVED = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(BLOCKLIST_FILE.stat().st_mtime))
    except OSError:
        BLOCK_HOSTS = set()
        log("ad-hosts.txt missing; running unblocked")


# Dismiss cookie-consent walls (reject only, never accept) and hide the
# containers so the wall text never reaches extraction or the PDF.
CONSENT_CLEANUP_JS = """(() => {
  const sels = ['#onetrust-reject-all-handler',
    '#CybotCookiebotDialogBodyButtonDecline',
    '.qc-cmp2-summary-buttons button[mode="secondary"]',
    '#didomi-notice-disagree-button', '.cky-btn-reject'];
  let clicked = false;
  for (const s of sels) {
    const b = document.querySelector(s);
    if (b) { b.click(); clicked = true; }
  }
  if (!clicked) {
    const reject = [/reject all/i, /^reject$/i, /decline/i, /disagree/i,
      /no thanks/i];
    for (const b of document.querySelectorAll('button, a')) {
      const t = (b.textContent || '').trim();
      if (t.length > 0 && t.length < 30 && reject.some(r => r.test(t))) {
        const box = b.closest('[id*="consent" i], [class*="consent" i],' +
          ' [id*="cookie" i], [class*="cookie" i]');
        if (box) { b.click(); break; }
      }
    }
  }
  for (const el of document.querySelectorAll('#onetrust-consent-sdk,' +
      ' #CybotCookiebotDialog, .qc-cmp-ui-container,' +
      ' [class*="cookie-consent" i]')) {
    el.style.display = 'none';
  }
  return 'consent-cleanup';
})()"""


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
        if BLOCK_MODE != "off":
            load_blocklist()
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
        log(f"browser up (profile={PROFILE}, block_mode={BLOCK_MODE}, "
            f"block_hosts={len(BLOCK_HOSTS)})")

    def _launch(self, **kw):
        PROFILE.mkdir(parents=True, exist_ok=True)
        args = list(kw.pop("args", []))
        if BLOCK_MODE == "active" and BLOCK_HOSTS:
            rules = ",".join(
                f"MAP {h} 127.0.0.1,MAP *.{h} 127.0.0.1"
                for h in sorted(BLOCK_HOSTS)
            )
            args.append(f"--host-resolver-rules={rules}")
        return self._pw.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True,
            args=args,
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
        try:
            page.evaluate(CONSENT_CLEANUP_JS)
        except PWError:
            pass
        # A consent choice can reload the page; let any reload settle so the
        # caller never reads a dying execution context.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except PWError:
            pass
        time.sleep(1)
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

    def fetch_json(self, session: str, args: dict) -> dict:
        """Fetch a same-origin JSON URL through the session's page.

        The page context carries the cleared session's cookies, so the
        request is the site's own frontend asking for its data, which is
        what passes Reddit's edge challenges from this host. Read-only:
        one GET, no state change.
        """
        self.navigate(session, {"url": args["url"]})
        page = self._page(session)
        target = args.get("fetch", ".json?limit=500&raw_json=1")
        data = page.evaluate(
            "fetch(" + json.dumps(target) + ", {credentials: 'include'})"
            ".then(r => r.text().then(t => ({status: r.status,"
            " type: r.headers.get('content-type'), body: t})))")
        ctype = data.get("type") or ""
        return {"success": True, "status": data.get("status"),
                "content_type": ctype, "body": data.get("body", ""),
                "json_ok": data.get("status") == 200 and "json" in ctype}

    def blocklist_info(self, session: str, args: dict) -> dict:
        return {"success": True, "mode": BLOCK_MODE,
                "mechanism": ("host-resolver-rules"
                              if BLOCK_MODE == "active" else None),
                "name": "capture-browser/ad-hosts.txt" if BLOCK_HOSTS else None,
                "retrieved": BLOCK_RETRIEVED,
                "hosts": len(BLOCK_HOSTS)}

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
    "blocklist_info": HOST.blocklist_info,
    "fetch_json": HOST.fetch_json,
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
