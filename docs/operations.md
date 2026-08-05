# Operations

Running the archive as a service: the recurring capture schedule, the
one-writer rule, notification delivery and deployment. What a single capture
does is covered in [capture.md](capture.md); what a build publishes is covered
in [publication.md](publication.md).

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
notification where the host provides one; on a headless host the service
journal and the optional Signal relay carry the signal. A failed change-log
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
| Watched X accounts | Not chosen | Manual probation is built; prove authentication, failure classification, request volume and intake quality before adding a timer |
| Direct X availability recheck | Daily | Classify deletion, suspension, access restriction and authentication failure separately |
| GitHub and HN discovery inbox | 6 hours | Keep machine discovery separate from human source registration |
| Archive contract audit, static build and link check | Nightly | Define alerting and keep deployment outside the job |
| Off-machine archive backup | Daily | Choose a destination, retention policy and restore test |
| Private scheduler-result pruning | Weekly | Set retention periods for finalized ticks, per-job results and service logs; never prune pending outboxes |

## Manual watched-account X discovery

X discovery is installed but deliberately absent from every systemd unit. It
uses the official X API to read one shallow public user timeline from a small
`[[x_watch]]` registry, then hands new permalinks to the existing
`DISCOVERY.md` intake. It never captures directly and never uses a signed-in
browser session, home feed, search endpoint or account-writing action. X's
rules prohibit non-API website automation, so browser scripting and cookie
reuse are not fallback discovery methods.

Create an X developer App whose registered use case covers this public
source-discovery and transient AI-assisted relevance assessment, then set its
app-only bearer token in the untracked `.env` as `X_API_BEARER_TOKEN`. The
system does not train a model on X data. X currently makes API access and usage
plan-dependent, so confirm that the developer-console terms permit the declared
use, and review data obligations and spend limits before enabling live reads.
The watcher does not log or persist the token.

Begin with local, request-free checks:

```bash
just discover-x --list
just discover-x --check-auth
```

The first live run needs the API token and explicit `.env` opt-in. It establishes a baseline
for the least-recently attempted six profiles and queues nothing historical:

```bash
X_DISCOVERY_ENABLED=true just discover-x
```

Repeat until every configured profile reports `baselined`. Later runs queue
only unseen status ids. `--queue-initial` is a deliberate history import and
should not be used for ordinary setup. `--handle NAME` narrows a diagnostic to
one registered actor; `--no-state` performs the bounded read without changing
checkpoints or intake.

Private state is `.work/x-discovery.json`; ID-only candidate metadata is
appended to `.work/x-candidates.jsonl`. Hydrated API text and public metrics
are not persisted. The tracked intake contains a local relation label and
permalink, not post text. During explicitly approved X triage,
`discover-x --show ID` performs one official post lookup and emits the content
only to that assessment process. State is replaced atomically after intake is
updated, so an interrupted run repeats safely rather than checkpointing past
an unqueued post. A direct manual lookup uses
`X_DISCOVERY_ENABLED=true just discover-x --show ID`; the intake prompt applies
that one-command opt-in only after `--include-x` admitted the candidate.

The 12-hourly community service does not run `discover-x`, and its general
intake agent does not consume queued X permalinks. This separation does not
affect direct capture of an X link supplied by a person. That still uses
`just ingest-x` as documented in `docs/capture.md`, without an API App or this
queue. To authorize a read-only AI triage pass over watcher-generated X
permalinks, run:

```bash
just discovery-intake --include-x
```

That explicit flag authorizes the agent to assess a bounded batch and append
recommendation or dismissal verdicts to `DISCOVERY.md`. It cannot capture,
register or publish a post. The run is X-only, so a community backlog cannot
hide the admitted candidates. It requires the separate
`X_REVIEW_AGENT_BIN`; there is no fallback to the general review provider.
Point it at a local or otherwise approved processor for hydrated X content.
The 12-hourly community service never passes the flag, so an X candidate
cannot move into the archive unattended.

A recommendation is only a human review queue. Promotion and evidence capture
remain a separate manual decision under the repository's existing X capture
procedure and its own account-policy risk.

The script stops the whole run and writes a 24-hour cooldown on rate limits,
exhausted API quota, stale credentials, denied API access or transient service
failure. Protected, suspended and unavailable profiles remain distinct
per-profile outcomes. An empty successful API result is healthy; a full
incremental page with another page behind it is an overflow failure, so the
checkpoint never skips unseen posts. Inspect the cause and developer-console
state before retrying. `--clear-cooldown` removes only the local stop state and
performs no X request; it must not be used to push through an unresolved API
response.

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

### Publishing on a timer (opt-in, not enabled)

The funds page reads the community trackers' headline totals out of the
captures this archive holds, so those figures are only as fresh as the last
deploy. `scripts/publish-scheduled.sh` exists for operators who want that gap
closed without publishing by hand, and it is deliberately not installed:
`publish-scheduled.{service,timer}.example` are examples, like the capture
units beside them.

The script publishes only from a tree that nobody is working in. It exits 0
and logs a reason, rather than failing, when any of these hold:

- `.no-publish` exists at the repo root. The kill switch to reach for before
  starting a long edit; untracked, so it is never committed or published
- `HEAD` is not on `main`
- anything outside `archive/` is uncommitted, tracked or not. Capture dirties
  `archive/` continuously and publishing that churn is the point; a modified
  page, script or registry entry means somebody is mid-change
- a detected difference is still unreviewed, which `just audit` would refuse
  to build through anyway. Between a poll and the review pass this is normal,
  so it logs as a skip and the next tick picks it up
- nothing has changed since the last publish. The stamp in
  `.work/publish-scheduled.stamp` covers `HEAD`, the snapshot filenames (a
  poll that finds no change writes no file) and the two registries
- another build holds `/tmp/cc-build.lock`

Only a genuine publish failure fails the unit, and the stamp is written after
success, so a failed deploy retries on the next tick. Check the decision
without deploying: `just publish-scheduled --dry-run`.

One consequence worth stating plainly: on a working tree that is habitually
dirty, this timer will skip nearly every time. That is the guard doing its
job, not a fault, but it means scheduled publishing only works if incident
work is committed as it lands. If it skips for days, that is the signal.
