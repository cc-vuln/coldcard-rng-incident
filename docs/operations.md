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
and gives every registered web source exactly one owning job. A successful
capture advances the job from its completion time. Exit 10 is a healthy
success with changes. Exit 20, 21 or 2 leaves the job due for another attempt.

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
| Known X posts and watched accounts | 2 hours plus jitter | Prove unattended authentication and distinguish stale sessions from no new posts |
| Direct X availability recheck | Daily | Classify deletion, suspension, access restriction and authentication failure separately |
| GitHub and HN discovery inbox | 6 hours | Keep machine discovery separate from human source registration |
| Archive contract audit, static build and link check | Nightly | Define alerting and keep deployment outside the job |
| Off-machine archive backup | Daily | Choose a destination, retention policy and restore test |
| Private scheduler-result pruning | Weekly | Set retention periods for finalized ticks, per-job results and service logs; never prune pending outboxes |

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
- `just publish`: capture gate, audit and the indexable public build for
  `SITE_URL`

Both builds run the full gate chain (`just test`, `just audit`,
`just check-claims`, `check-public-output.mjs`, `check-links.mjs`), and an
incomplete poll blocks publishing through the capture gate, while exit 10
(healthy changes) does not. The Pages project is created once and named in
`CF_PAGES_PROJECT`; the credentials are a token scoped to Pages:Edit plus the
account id in `.env` (see `.env.example`), or `npx wrangler login` once.
Publication flags such as `PUBLIC_FULL_TEXT` and `PUBLIC_X_MEDIA` stay off for
public builds (see [publication.md](publication.md)).

Deployment is always a separate manual decision. No capture or review timer
publishes anything, and the scheduled jobs above keep deployment outside
their scope by design.
