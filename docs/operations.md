# Operations

Running the archive as a service: the recurring capture schedule, the
one-writer rule, notification delivery and deployment. What a single capture
does is covered in [capture.md](capture.md); what a build publishes is covered
in [publication.md](publication.md).

## The capture host

There is one canonical working tree, on one machine, and capture runs there on
a schedule. Everything about where that machine is and how to reach it is
operational and stays out of this tracked file: see `AGENTS.local.md`
(gitignored) beside this file on a machine that has access.

Two long-running services are owned by the init system there. Never start
ad-hoc replacements for either; restart the unit instead.

- `webbridge.service`: the capture browser on 127.0.0.1:10086, which is
  `capture-browser/webbridge.py` in this repository, running headless Chromium
  from its own virtual environment. capture.py and ingest-x.py use it
  unmodified. It relaunches a crashed browser itself, systemd restarts it on
  failure and recycles it daily. Sessions and challenge cookies persist in
  `.capture-browser/profile`, which is gitignored. See
  `capture-browser/README.md` for the protocol and the setup a clone needs
- the site preview service: serves the built site from `site/dist`, for
  assessing changes before any deploy. To see changes: rebuild
  (`npx astro build` in `site/` with `.env` exported), the service picks up
  the new `dist` without a restart. The Astro dev server stays broken on the
  VM too; do not try it there

Manual `just capture-one <id>` runs on this host (including driver-side first
captures after a guarded intake registration) use the same writer and lock as
the schedule below. Never run non-dry captures anywhere else.

## Scheduled capture

One timer wakes every 30 minutes and invokes `scheduled_runner.py`: a systemd
timer on the canonical Linux deployment. Only ever run one writer; the archive
lock is per-machine, and two machines capturing in parallel produce diverging
archives rather than a conflict error.
The runner stores due-state outside the repository under
`~/.local/state/coldcard-archive/`, catches up overdue work once after sleep,
and gives every active web source exactly one owning job. A successful capture
advances the job from its completion time. Exit 10 is a healthy success with
changes. Exit 20 with a valid structured result also advances the cadence: its
source failures remain recorded and freshness still controls publication, but
healthy peers are not repolled every 30 minutes. Exit 21, launch failure or a
missing or malformed result leaves the job immediately due for another attempt.
Sources marked `watch = "frozen"` remain registered and explicitly capturable
with `just capture-one <id>`, but broad and scheduled polls skip them.
Community intake registrations carry a seven-day `watch_until` window by
default. The source and every capture remain in the archive after that window;
only recurring polling stops. Extend the timestamp when a thread is still
producing incident-relevant primary material.
Older community registrations without an explicit lifecycle field use a
window measured from their first snapshot: seven days in Tier 2 and three days
in Tier 3. `watch = "active"` is the explicit override for a thread that
warrants continuing polls.

The active known-URL schedule is:

| Check | Cadence | Reason |
|---|---:|---|
| Mutable vendor advisories and documentation, tier 1 | 30 minutes | Highest revision and deletion risk |
| Repository, legal, reporting and analysis pages, tiers 2 and 3 | 6 hours | Lower mutability, lower load |
| Holding-address chain state | 30 minutes | BTC movement is time-sensitive; fiat conversion is excluded from comparison |

Install, inspect or remove it with the example units (replace USER and REPO
with the account and checkout path, per the comments in the unit files):

```bash
sudo cp scripts/archive-poll.service.example /etc/systemd/system/archive-poll.service
sudo cp scripts/archive-poll.timer.example /etc/systemd/system/archive-poll.timer
sudo systemctl daemon-reload && sudo systemctl enable --now archive-poll.timer
systemctl status archive-poll.timer
```

Each tick uses an owner-only pending outbox, then moves its finalized result into
retained tick history beside the per-job capture results. Recovery scans only
pending work, not the growing history. Changed events are recorded once in
`archive/CHANGES.md`, and changes or failures trigger one aggregate desktop
notification where the host provides one. On a headless host the alert stream
carries the signal (8 Aug 2026): `scripts/alert.py` appends operator-visible
alerts to `~/.local/state/coldcard-archive/alerts.jsonl`, which the operator
UI (`~/coldcard-operator-ui`, a separate read-only repo, port 4322) renders at
`/alerts`. The service journal and the optional Signal relay remain alongside
it. A failed change-log
write remains pending and is retried before notification. A failed
notification also remains pending for retry. Either failure makes the runner
exit 20 so it is visible in the service log. Notification delivery is at least
once: a process interruption after the host accepts an alert but before the
receipt is saved can cause the recovered tick to send it again. Clean ticks are silent. Scheduler state and logs do not enter the
repository. Each job's aggregate event outbox is written before its due-state
advances, so a later tick can finish change recording and notification after an
interrupted process. Deployment is a separate manual publication decision.

The following jobs are deliberately not active yet:

| Candidate | Proposed cadence | Gate before activation |
|---|---:|---|
| GitHub and HN discovery inbox | 6 hours | Keep machine discovery separate from human source registration |
| Archive contract audit, static build and link check | Nightly | Define alerting and keep deployment outside the job |
| Off-machine archive backup | Daily | Choose a destination, retention policy and restore test |
| Private scheduler-result pruning | Weekly | Set retention periods for finalized ticks, per-job results and service logs; never prune pending outboxes |

## The unattended pipeline timers (8 Aug 2026)

Nine further units are installed and enabled under the operator's 8 Aug
2026 full-automation directive, alongside the pre-existing `archive-poll`,
`discover-community` and `claim-sweep` timers. Human
review of what they produce is retroactive — the alert stream, the journals
and git history — not a gate. Each has a `*.{service,timer}.example` pair
under `scripts/`, installed the same way as the capture units above.

The pipeline stages are pinned to run in order within the hour (9 Aug 2026):
poll `:23`, review `:29` (half-hourly since that day — the 30-minute poll
creates diffs continuously and a two-hourly review left an unreviewed
window that stalled the publish gate), commit `:37`, publish `:50`, so each
stage consumes the previous stage's output.

Discovery has its own writer lock at `.work/locks/discovery.lock`, separate
from the global agent-run lock, archive writer and `/tmp/cc-build.lock`. Code
that needs more than one acquires them in the global order agent-run, build,
discovery, archive. In particular, `record_commit.py` holds all four while it
validates and stages a coherent tree; writers never acquire them in the
reverse order. Intake holds the agent-run lock across its guarded registry
work, but takes the discovery lock only briefly for its head-bound read and
atomic verdict commit.

The one-time structured-store cutover is stricter than ordinary writes. Pause
and drain `discover-community.timer` and `discover-x.timer` before running
`.venv/bin/python scripts/migrate_discovery.py --write`. The installer also
refuses unless it
can simultaneously hold the global agent-run lock, the pre-cutover intake
lock and the structured discovery lock, so an already-running legacy writer
cannot change the queue, rotated verdicts or registry during the snapshot and
activation.

| Timer | Cadence | What it runs |
|---|---:|---|
| `archive-review` | 30 minutes, :29 | `agent-review.sh`: mechanical noise classification, then the review agent over a bounded batch of unreviewed diffs |
| `record-commit` | hourly, :37 | `record_commit.py --yes`: commits guard-passed pipeline output from a fixed staging allowlist, its audit being `audit-core` (no review gate — classification is a publish concern). Blocks on `.no-publish`, a non-main `HEAD`, a red `just test` or `just audit-core`, a held build, discovery or archive lock, or an unresolved agent-guard run; a block exits 1, and `SuccessExitStatus=0 1` keeps a blocked tick from reading as a failed unit — the alert stream carries anything persistent |
| `publish-scheduled` | 3 hours, :50 | `publish-scheduled.sh`: publishes committed work and pushes after each successful deploy. It skips on `.no-publish`, non-main `HEAD`, any uncommitted state, unreviewed diffs or the build lock; the pre-deploy exactness gate refuses a build unless its `/version.json`, current `HEAD` and tracked tree agree. Only a genuine publish failure fails the unit |
| `alert-sweep` | 30 minutes | `alert.py sweep`: turns the repo's state files — failure streaks, stale host proposals, failing units, publish-skip streaks — into alerts on the operator-UI stream |
| `corroborate-gone` | 6 hours | `corroborate_gone.py`: re-resolves `dns-unresolved` streaks through public DNS-over-HTTPS resolvers and sets `gone = true` only when the streak and the independent resolvers agree, recording the transcript in `gone_note` and alerting |
| `discover-x` | 12 hours | `discover_x_browser.py` then up to eight separately guarded 15-item `agent-x-intake.sh` batches: home-timeline and watched-profile discovery through the capture browser, with bounded evidence packets, validated verdict outboxes and driver-side first captures. The drain stops on no progress. Kill switch `X_BROWSER_DISCOVERY_ENABLED`, off by default |
| `x-availability` | 12 hours | `check_x_availability.py`: re-checks that registered X posts are still observable; a single absence is info, two consecutive observations escalate. Kill switch `X_BROWSER_AVAILABILITY_ENABLED`, off by default |
| `x-media` | weekly | `capture-x.sh --skip-unchanged`: the gallery-dl media pull for every registered `[[x_post]]`, writing nothing when nothing new downloads. `SuccessExitStatus=0 21`: exit 21 is a poll holding the writer lock, a routine skip retried next week, not an alert |
| `corrections-watch` | Sun 06:40 UTC | `agent-corrections.sh`: the propose-only corrections role drafts corrections from the claim sweep's state-changed flags; `apply_corrections.py` applies validated proposals all-or-nothing, dry run unless `--yes`, with an alert per applied correction |
| `site-sync` | 06:20, 18:20 UTC | `agent-site-sync.sh`: the page-sync role edits editorial prose from the deterministic staleness packet (`report_site_staleness.py` to `.work/site-staleness.md`); page edits are gated post-run on `just check-claims` plus a full gated build, and a gate failure rejects the run with an urgent alert |

The X lanes' shared kill switches live in `.env`:
`X_BROWSER_DISCOVERY_ENABLED` and `X_BROWSER_AVAILABILITY_ENABLED` are both
`false` by default, and the session-health classes (login wall, challenge,
rate limit) fail every lane closed and share a 24-hour cooldown. The
operator kill switches for the commit and publish steps are `.no-publish`
at the repo root (untracked, never committed) and, for the whole line,
`systemctl stop` of the timers.

## Watched-account X discovery through the capture browser

X discovery moved to the capture browser on 8 Aug 2026. The official-API lane
(`scripts/discover_x.py`) is deprecated: no developer App was created, and
`X_API_BEARER_TOKEN` and `X_DISCOVERY_ENABLED` in `.env` are no longer used.
Reading a signed-in timeline carries X's automation-rule suspension risk,
which the operator accepted in writing on 8 Aug 2026. The manual API-lane
procedure this section replaced is in git history and annotated in
[design/discovery-and-x-watch.md](design/discovery-and-x-watch.md).

`scripts/discover_x_browser.py` reads the home timeline and the watched
`[[x_watch]]` profiles through the capture browser, driver-side only, as the
operator account. The lane is read-only in the same sense as `capture-x.sh`:
no posting, following or liking from the session. The kill switch is
`X_BROWSER_DISCOVERY_ENABLED`; disabled is the default.

The agent never reaches the browser. As with the community lanes, the driver
hydrates first and the agent receives one bounded packet: each candidate once,
its mechanical native-id registry match, and only the registry rows with a
non-zero historical saturation count. An unattended agent never reaches
`evaluate`/`cdp` and never holds the bridge token. New permalinks land in the
immutable discovery store with a relation label; root `DISCOVERY.md` and the
paged Pending views are generated projections. ID-only candidate metadata
stays under `.work/`, as before.

Session health is the lane's failure surface, and the classes stay distinct:
a login wall, a challenge and a rate limit each fail the run closed and write
a cooldown rather than pushing through. A login wall additionally needs a
person to renew the session; a challenge or rate limit may clear with the
cooldown. `--clear-cooldown` clears only local state and performs no request.

Promotion is automated (8 Aug 2026). The registering `xintake` guard role
assesses queued X candidates under the same containment as the community
intake. It submits one JSON verdict per packet candidate; the guard checks the
complete outbox and its registry relationships, and a deterministic
operator-side applier verifies each candidate's event head and commits the
complete verdict batch while holding `.work/locks/discovery.lock`. Generated
views are refreshed from that transaction. The
driver then captures each approved post with `just ingest-x`; the agent never
reaches the browser itself. The read-only xtriage
prompt and the `--include-x` admission flag are retired. What still never
moves through the agent is the browser session, a secret, or canonical
discovery state; each crosses the run boundary only through driver-side code.

Both intake drivers keep each prompt at 15 candidates, then may run up to
eight independently rendered and guarded batches in one scheduled tick. A
batch that makes no queue progress stops the drain, so an unset provider or an
evidence-hydration failure does not spin. This lets a recovery enumeration
clear promptly without turning hundreds of strangers' posts into one prompt or
one guard decision.

The lane runs on its own `discover-x.timer`, separate from
`discover-community`: an X session failure must not stall the community
lanes, and a community backlog must not hide X candidates. Direct capture of
an X link supplied by a person is unchanged: `just ingest-x` as documented in
[capture.md](capture.md), without this queue.

## nostr identity, posting and discovery

The project holds one nostr key, generated on the capture host 6 Aug 2026.
The public half is
`npub1pfuvza2kkeqjqnp6l2tlqr2ewgx5ue0kc7rwztxvjr8p5wcec3zsrvp9w2`, carried in
`.env` as `PUBLIC_NOSTR_NPUB` and `PUBLIC_NOSTR_PUBKEY_HEX`; the site build
serves its NIP-05 record as `_@cc-vuln.org` at `/.well-known/nostr.json` and
shows the npub on `/cite/`. The secret half lives only in the untracked
`.env` as `NOSTR_SECRET_KEY` (nsec), and the operator holds the offline
backup. nostr has no key revocation: rotation means generating a new key,
repointing the NIP-05 record and publishing the new npub.

All relay traffic goes through `nak`, the second sanctioned external binary
beside gallery-dl, pinned at v0.20.2 and installed at `~/.local/bin/nak`
(linux-amd64 sha256
`424db88043d26d9c2f1cbd2d9bc06582c39526f91f8e5523590439d4257da087`). Verify
the hash after any reinstall. `scripts/capture.py` stays stdlib-only and
never touches nostr.

First-time setup, in order:

1. `just nostr-keygen` prints a fresh keypair. It never writes `.env`; copy
   the values across by hand and store the nsec backup offline.
2. Set `NOSTR_SECRET_KEY`, `PUBLIC_NOSTR_PUBKEY_HEX` and `PUBLIC_NOSTR_NPUB`
   in `.env`.
3. `just nostr-publish-profile` publishes the kind-0 profile (name
   "cc-vuln", nip05 `_@cc-vuln.org`, website `https://cc-vuln.org`) and the
   kind-10002 relay list to `NOSTR_WRITE_RELAYS`. Both kinds are replaceable
   events, so re-running after a profile or relay change is fine. Publishing
   requires `--yes`, or an interactive confirmation on a terminal.
4. After the next site build, confirm that
   `https://cc-vuln.org/.well-known/nostr.json` resolves the npub.

Posting is a manual act: `just nostr-post` publishes a kind-1 note from the
project key and requires `--yes`, or an interactive confirmation on a
terminal. Use it for announcements of record updates only. There is no
scheduled or agent-driven posting.

Discovery is manual during probation — the posture `discover-x` held until it
moved to the capture browser on 8 Aug 2026:

```bash
NOSTR_DISCOVERY_ENABLED=true just discover-nostr
```

It runs bounded NIP-50 keyword searches against `NOSTR_SEARCH_RELAYS` and
records njump permalinks as Pending discovery observations, which the standard
community intake agent assesses. `NOSTR_DISCOVERY_ENABLED=false` is the default
and the kill switch. There is no global nostr search: each relay answers
from its own index. The default search set (all verified with a live query
from this host, 6 Aug 2026, via the NIP-66 kind-30166 monitor events on
`relay.damus.io`): `search.nos.today`, `nostrja-kari-nip50.heguro.com`,
`antiprimal.net`, `relay.ditto.pub` and `nostr.wine`. Relays that connect
but return nothing here: `relay.noswhere.com`, `relay.nostrcheck.me`,
`relay.vertexlab.io`, `filter.nostr.wine`, `relay.orly.dev`,
`relay.mleku.dev`; `relay.nostr.band` is unreachable at TCP.
`relay.damus.io`, `nos.lol` and `relay.primal.net` all work for read and
write and are the default write set.

Capture one note with:

```bash
just ingest-nostr <note1|nevent1|hex> [slug] [tag] [why]
```

It writes `archive/nostr/<id>/<TS>/` with the signed event (`event.json`),
its flattened text (`event.txt`), the fetched replies where any exist
(`replies.json`) and a sidecar (`meta.json`). A re-capture is a new
timestamped directory. Registration is a `[[nostr_post]]` block in
`sources.toml` (schema comment at the end of the file); `capture.py`
validates the section but never polls it, so first capture is always
`just ingest-nostr`, never `just capture-one`.

Troubleshooting: an occasional connect failure against `search.nos.today`
is transient; the discoverer retries once per relay, and a manual retry of
the run is fine. `just discover-nostr --check` prints the local
configuration without touching the network, and `--show <note1-or-hex>`
fetches one candidate's body for inspection. A search relay returning
nothing for known
notes has usually fallen out of sync rather than proving absence, because
indexes are per-relay. To find fresh NIP-50 candidates, query the NIP-66
monitor events (`nak req -k 30166 -t N=50 wss://relay.damus.io`) and test
each with a live query before adding it to `NOSTR_SEARCH_RELAYS`;
`relay.nostr.band` does not answer this host at all.

## The agent account

The unattended agents (`agent-review.sh`, `agent-discovery-intake.sh`,
`claim-sweep.sh`) read text strangers wrote, so they do not run as the account
that owns the tree. The reasoning is in
[design/agent-sandbox.md](design/agent-sandbox.md); this is the setup.

Until it is done the drivers refuse to run, and a refusal is cheap: the queue
waits and the next tick retries. `AGENT_SANDBOX=off` in `.env` is the recorded
opt-out for a clone with no agent account.

One-time, as root:

```bash
sudo useradd --system --user-group --home-dir /var/lib/cc-agent \
    --create-home --shell /usr/sbin/nologin cc-agent
sudo usermod -aG cc-agent "$(id -un)"        # so you can edit what it writes
sudo install -m 0440 scripts/cc-agent.sudoers.example /etc/sudoers.d/cc-agent
sudo visudo -c                                # replace OPERATOR first
```

Edit `/etc/sudoers.d/cc-agent` to name your account in place of `OPERATOR`.
The rule only lets your account become `cc-agent`, which is a privilege drop;
nothing lets `cc-agent` become anyone.

Then, as the tree's owner:

```bash
./scripts/agent-permissions.sh          # apply the modes and create the token
sudo systemctl restart webbridge.service   # pick up the new browser token
just audit-sandbox                      # re-check at any time
```

The group change needs a fresh login (or a `systemctl restart` of the timers)
before it takes effect.

### The provider, and swapping between them

Three providers are installed in `/usr/local/bin` as `cc-agent-<name>`, each
speaking the same `<bin> -p "<prompt>"` contract the drivers expect, each
authenticating from its own credential directory under `/var/lib/cc-agent`.

Which providers those are is not recorded here. It lives in `AGENTS.local.md`
alongside the capture host's address, and their API hostnames live in
`scripts/agent-egress.local.toml`, for the reason `.env` exists: an archive
whose agents read attacker-adjacent material should not publish which models
and CLIs those agents are.

Swapping is one line in `.env` and nothing else:

```bash
REVIEW_AGENT_BIN=/usr/local/bin/cc-agent-<name>
```

`REVIEW_AGENT_BIN` and `CLAIM_SWEEP_AGENT_BIN` are
independent, so the sweep can run on one provider while intake runs on
another. (`X_REVIEW_AGENT_BIN` retired with the xtriage prompt on 8 Aug 2026.)
That is also how to compare them: point two lanes at two providers
and read the reports side by side.

They live in `/usr/local/bin` rather than an operator's `~/.local/bin`
because `cc-agent` cannot traverse a 700 home. The wrappers hold no secret;
each authenticates from `$HOME`, which `run-agent.sh` sets to
`/var/lib/cc-agent`. Adding one is a wrapper plus a credential directory,
both owned by `cc-agent` and mode 600, plus its API hostnames in the local
egress file.

Keeping the credentials there rather than in your home is the point: the model
credential stops sharing a directory with the nostr key and the Cloudflare
token. Copy only what authenticates. Prompt history, session databases and
caches carry material from your other work and do not belong on the agent
account.

The provider CLIs create a scratch directory inside the workspace and each
picks its own name, so the repository root is `3775`: group-writable so they
can, sticky so the agent can only remove entries it owns. Those directory
names are excluded in `.git/info/exclude` rather than `.gitignore`, because a
tracked ignore rule naming a provider discloses the tooling too.

### Egress

An agent reaches the network through one proxy and nothing else.

```bash
sudo cp scripts/agent-proxy.service.example /etc/systemd/system/agent-proxy.service
# replace USER and REPO, then:
sudo systemctl daemon-reload && sudo systemctl enable --now agent-proxy

sudo mkdir -p /etc/nftables.d
sed "s/AGENT_UID/$(id -u cc-agent)/" scripts/agent-egress.nft.example \
  | sudo tee /etc/nftables.d/agent-egress.nft >/dev/null
sudo nft -c -f /etc/nftables.d/agent-egress.nft     # check before applying
sudo nft -f /etc/nftables.d/agent-egress.nft
```

To survive a reboot, `/etc/nftables.conf` needs `include "/etc/nftables.d/*.nft"`
and `nftables.service` needs enabling.

The live allowlist is only the gitignored
`scripts/agent-egress.local.toml`, which names the configured model provider's
required endpoints. Registered source hosts do not widen it: candidate bodies
and claim evidence are hydrated by the driver before an agent runs. Restart
`agent-proxy` after changing the local provider file because policy is read
once at startup. `scripts/agent_egress_hosts.toml` remains an auditable legacy
mirror of hosts admitted before this boundary tightened; the proxy does not
read it.

What each run reached, and what it was refused:

```bash
sudo journalctl -u agent-proxy --since -1h | grep -E 'allowed|REFUSED'
sudo nft list table inet cc_agent_egress | grep counter   # direct attempts
```

A refusal is a finding, not noise. Either a provider moved an endpoint, or a
run tried to reach somewhere it should not, and those are worth telling apart.
Provider telemetry is refused deliberately and will show up here.

### When a run is rejected

`agent-guard: the <role> run is REJECTED` in the journal means the agent wrote
outside its remit, or something it wrote tripped the secret, registry or queue
checks. Nothing was reverted and no first capture was made. The run directory
under `.work/agent-guard/` holds the before-copies, so:

```bash
git diff -- sources.toml DISCOVERY.md discovery/ revision-reviews.toml site/src/pages
```

is the whole of what happened. Read it before deciding. A rejection is more
often a person editing during a run than an attack, and the report names the
file either way. Revert or keep by hand; the queue entries stay pending and
the next tick retries.

## Signal alerting (not enabled)

`NOTIFY=relay` routes through an internal notification relay reached over
SSH, configured via `NOTIFY_SSH_HOST`, `NOTIFY_REMOTE_BIN` and
`NOTIFY_REMOTE_CONFIG` in `.env` (see `.env.example`). It is deliberately off
by default because enabling it means adding a notification route to the
relay's own route config on that host and restarting it, which is a change to
a production notification stack. Before switching it on:

1. Set `NOTIFY_SSH_HOST`, `NOTIFY_REMOTE_BIN` and `NOTIFY_REMOTE_CONFIG` in
   `.env` to point at your own relay.
2. Add a `coldcard-archive-change` entry to the relay's route config, with a
   prompt telling it to summarise `detail` in one short mobile message.
3. Back up the route config first and validate it parses.
4. Restart the relay service (a new notification id does not register until
   it restarts).
5. Test with `NOTIFY=relay ./scripts/notify.sh` after forcing a change.

## Deployment

The site is a static Astro build, pushed to Cloudflare Pages by direct upload
(`wrangler pages deploy`) so no source repository is ever exposed. Two entry
points:

- `just preview`: capture gate, audit and a noindex review build at
  `<CF_PAGES_PROJECT>.pages.dev`
- `just publish`: audit and the indexable public build for `SITE_URL`, with no
  pre-publish capture
- `just publish-fresh`: the strict path, which captures first and refuses to
  deploy an incomplete poll

Both builds run the full gate chain (`just test`, `just audit`,
`just check-claims`, `check-public-output.mjs`, `check-links.mjs`), and an
incomplete poll blocks publishing through the capture gate, while exit 10
(healthy changes) does not. The Pages project is created once and named in
`CF_PAGES_PROJECT`; the credentials are a token scoped to Pages:Edit plus the
account id in `.env` (see `.env.example`), or `npx wrangler login` once.
Publication flags such as `PUBLIC_FULL_TEXT` and `PUBLIC_X_MEDIA` stay off for
public builds (see [publication.md](publication.md)).

Deployment is a separate decision from capture. No capture or review timer
publishes anything, and the scheduled jobs above keep deployment outside their
scope by design.

### Publishing on a timer

> Amended 8 Aug 2026: the operator's directive moved publication onto this
> timer as part of the unattended pipeline, and the timer is now installed
> and enabled (three-hourly). Guard-passed agent output is committed hourly
> by `scripts/record_commit.py`, and a git push follows each successful
> deploy. Human review is retroactive — the operator UI, the journals and
> git history — not a gate. The skip conditions below are unchanged, and a
> `.no-publish` file or a failed publish gate still stops the line.

The funds page reads the community trackers' headline totals out of the
captures this archive holds, so those figures are only as fresh as the last
deploy. `scripts/publish-scheduled.sh` closes that gap without publishing
by hand. Until the 8 Aug 2026 directive it was deliberately not installed:
`publish-scheduled.{service,timer}.example` shipped as examples, like the
capture units beside them.

The script publishes only from a tree that nobody is working in. It exits 0
and logs a reason, rather than failing, when any of these hold:

- `.no-publish` exists at the repo root. The kill switch to reach for before
  starting a long edit; untracked, so it is never committed or published
- `HEAD` is not on `main`
- anything is uncommitted, including archive churn. The hourly record
  committer turns captures into a reconstructible state before publication;
  publishing directly from its uncommitted interval would make the commit in
  `/version.json` incomplete
- a detected difference is still unreviewed, which `just audit` would refuse
  to build through anyway. Between a poll and the review pass this is normal,
  so it logs as a skip and the next tick picks it up
- nothing has changed since the last publish. The stamp in
  `.work/publish-scheduled.stamp` covers `HEAD`, the snapshot filenames (a
  poll that finds no change writes no file) and the two registries
- another build holds `/tmp/cc-build.lock`

The publisher regenerates the tracked media index under that lock and commits
it before building. Immediately before upload, `just check-version-exact`
requires the built `/version.json` to say `matches_commit: true`, requires its
commit to equal current `HEAD`, and requires the tracked tree still to be
clean. A capture or commit landing during the build therefore refuses the
deploy before upload and is retried from a committed state on the next tick.

Only a genuine publish failure fails the unit, and the stamp is written after
success, so a failed deploy retries on the next tick. Check the decision
without deploying: `just publish-scheduled --dry-run`.

One consequence worth stating plainly: on a working tree that is habitually
dirty, this timer will skip nearly every time. That is the guard doing its
job, not a fault, but it means scheduled publishing only works if incident
work is committed as it lands. If it skips for days, that is the signal.
