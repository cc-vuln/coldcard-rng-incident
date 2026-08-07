# Containing the unattended agents

6 August 2026.

Four agents run on the capture host without anyone watching, and all four read
text that strangers wrote. `agent-review.sh` reads captured diffs.
`agent-discovery-intake.sh` reads Reddit, Stacker News, BitcoinTalk and nostr
threads. The X triage prompt reads post bodies. `claim-sweep.sh` reads the open
web. That is the archive working as intended: the material is the point.

It is also a prompt-injection surface, and the honest way to think about it is
that the injection lands. Not "might land": a project whose subject is an
attacker who drained hundreds of bitcoin is a project whose sources include
that attacker's audience, and a thread is free to contain a paragraph
addressed to whatever reads it. Anything that only works while the model
behaves is not a control.

So this design assumes a compromised agent run and asks what it can reach.

## What it could reach before

Every driver began with `set -a; source .env; set +a`. That exported into the
agent process:

- `NOSTR_SECRET_KEY`, the project's posting identity
- `CLOUDFLARE_API_TOKEN`, which deploys the public site
- `X_API_BEARER_TOKEN`
- `NOTIFY_SSH_HOST` and the relay paths beside it

`.env` and `AGENTS.local.md` were mode 644, so the environment was not even
the interesting route: the agent ran as the account that owns the tree and
could read both directly. `AGENTS.local.md` is the file that says where this
machine is, which is exactly the thing the rest of the project works to keep
out of published output.

With those in hand an injected run had every exit it needed: `curl` to
anywhere, `just nostr-post` to publish from the project key, `just deploy` to
publish a page, `notify.sh` to reach the relay over SSH.

Two more, less obvious and worse.

**The capture browser.** `webbridge.py` listens on 127.0.0.1:10086 with no
authentication, and two of its actions are `evaluate` (arbitrary JavaScript)
and `cdp` (raw DevTools), inside a browser holding signed-in X and Reddit
sessions belonging to a person. The docstring called it "read-only by
construction", which is true of the capture vocabulary and not true of the
protocol. Anything that could reach the port could post as that person.

**The registry.** The intake agent appends to `sources.toml`, and the poll
fetches whatever `sources.toml` names, every 30 minutes, forever. Sixty
entries carry a `fetch_post` body. A registered source with an attacker's
`fetch_url` and an attacker's POST body is a beacon that outlives the run,
the agent, and anyone's memory of the day it was added.

## What it can reach now

Five layers. None of them depends on the model, and no two of them fail the
same way.

### 1. It runs as a different account

`scripts/run-agent.sh` drops from the operator account to `cc-agent`, which
can read the tree, write four files and two directories, and read neither
`.env` nor `AGENTS.local.md` nor `.capture-browser/`. The environment is built
from an allowlist with `env -i`, so no project variable reaches the agent even
by accident.

`scripts/agent-permissions.sh` applies and re-checks the file modes this rests
on, because permissions drift and a boundary nobody re-checks is a boundary
that quietly stops existing. `just audit-sandbox` runs the check. It earned
its keep within the hour: minutes after the first apply it caught three
regressions, one of them a `BACKLOG.md` rewrite by a concurrent session that
silently reset the file's group.

The model credential moves to `/var/lib/cc-agent` with the account. That is
the real setup cost of this layer, and also a benefit: it stops sharing a
directory with the nostr key and the Cloudflare token. Three providers are set
up this way, each behind a wrapper in `/usr/local/bin` speaking the same `-p`
contract, so which model reviews the archive is one line in `.env` and the
containment does not change with it. Only what authenticates is copied across;
prompt history and session databases carry other work and stay put.

The drivers refuse to run when the account is missing rather than warning and
continuing. A refused run costs a retry: the queue waits, the next tick picks
it up, nothing is lost. `AGENT_SANDBOX=off` is the recorded opt-out for a
clone that has no such account.

### 2. It has nothing local to talk to

`archive-review.service` and `claim-sweep.service` deny loopback outright.
Neither needs it: the review agent classifies from packets rendered before it
starts, and the sweep agent's reading is all remote. The deny also covers
169.254.169.254, the cloud metadata endpoint, which hands out instance
credentials to anything that asks.

The obvious way to write that rule does nothing at all:

```
IPAddressAllow=any
IPAddressDeny=localhost
```

An allow entry beats a deny entry regardless of prefix length, so `any`
permits everything and the deny line is dead config that reads exactly like a
working control. It was in this design until it was measured. The rule that
works is a total deny plus the complement as an allow list, in
`scripts/agent-loopback-deny.conf.example`, with `127.0.0.53/32` added back
because systemd-resolved's stub listener is on loopback and without it the
agent cannot resolve a name.

Two more things had to be measured rather than assumed. `NoNewPrivileges`
blocks the setuid transition `sudo` needs, so the units must set it to
`false` explicitly: the installed units set it true, and several hardening
options default it true. And the repository root is `3775`, setgid plus
sticky, because each provider CLI creates a scratch directory in the
workspace under a name of its own choosing. Group-writable so it can; sticky
so it can only remove entries it owns. As `cc-agent`, creating a scratch
directory succeeds and `rm AGENTS.md` fails with EPERM.

`discover-community.service` cannot, and this is worth stating plainly rather
than papering over. Two of its discovery steps and the intake driver's own
candidate hydration read Reddit through the capture browser, `IPAddress*`
applies to a whole unit, and the agent shares that unit. So for that lane the
separation is the browser's own token: a 32-byte secret in
`.capture-browser/`, which is mode 700. The driver can read it, the agent
cannot, and `webbridge.py` rejects a request without it. The token is skipped
when the file is absent, so an install predating it keeps working; the
permissions check reports the absence rather than assuming it away.

### 3. It does not fetch, and does not write the archive

The review agent never fetched anything: `render_review_packets.py` put the
evidence in the prompt. That pattern is now the rule.

`scripts/hydrate_candidates.py` fetches every intake candidate body before the
agent starts, one request each, from the driver. The intake prompt's `curl`
command and its four `--show` invocations are gone. The X lane gets the same
treatment, and the gain there is larger: reading a post needs the bearer
token, so pre-hydrating means the agent never holds a credential at all.

First captures moved too. The agent used to run `just capture-one`, which made
it an archive writer and, worse, made it the thing that first fetched an
address it had just chosen. Now it appends source ids to
`.work/capture-requests.txt` and the driver performs the captures afterwards,
for ids the gate confirmed this run registered in a registry that passed
`check_registry.py`. A poisoned block is refused before anything fetches it.

### 4. Everything it produced is checked

`scripts/agent_guard.py` records a hash of every file git can see before the
run and compares afterwards. It enforces:

- the role's path allowlist, in both directions, deletions included
- no secret value from `.env`, no key shape, no operator needle from
  `site/tools/private-tokens.json`, in anything added
- the registry rules below
- that every assessed `DISCOVERY.md` line still contains the candidate line it
  came from, so a candidate cannot be relabelled or repointed on its way
  through the queue
- that requested first captures name sources this run actually registered

`scripts/check_registry.py` is the registry half, and it also runs standalone
in `just audit` so a bad block cannot survive to a build however it arrived.
Every URL must be https with a host in `scripts/registry_hosts.toml`; a
community id prefix binds its host, so a `reddit-*` source may only name
reddit.com; `fetch_post` must be the pinned item query with only the id
changed; and an existing source's `url`, `fetch_url`, `fetch_post`, `capture`
or normalizers may not be touched at all. The agent may retier a thread, move
its `watch_until`, correct a `min_chars` floor or rewrite a note, which is
exactly the remit its prompt describes.

The allowlist lives in `scripts/`, which is read-only to the agent. It cannot
extend its own permissions.

**A rejected run changes nothing.** The edits stay on disk exactly as the
agent left them. Reverting would destroy the most interesting artefact
available, which is what the injection tried to do, and a dirty tree outside
`archive/` already stops `publish-scheduled.sh`. The failure is loud, the
evidence is intact, and a person decides.

**With one exception: an invalid registry is not evidence, it is a stopped
tree.** `sources.toml` is checked by `just audit`, by `just test` and by the
publish gate, so a single unlistable host in it fails all three until somebody
edits the file. On 7 Aug 2026 that was one OpenSats article, and it held the
tree for most of a day. "A person decides" is the right rule for judging a
run; it is the wrong rule for clearing a jam, because the person is then in
the loop for something that has no decision in it.

So `agent_finish` runs `scripts/quarantine_registry.py` after a rejection.
Any block **this run added** that the registry rules refuse is moved, verbatim
and with its reason and run id, into `quarantine/registry-YYYY-MM.toml`. The
rejection still stands, no capture is approved, and the evidence is preserved
and greppable — it is simply not in the file that decides what gets fetched
every 30 minutes.

Two properties keep that safe to do unattended, and both are tested:

- **it only removes.** No path in it adds a host, relaxes a rule, or edits a
  block that survives. The strictest thing it can produce is a smaller registry
- **it only removes what the run added.** Eligibility is "absent from this
  run's `before` registry", so a pre-existing source can never be evicted by
  an agent that breaks a rule on purpose. Without a `--before` baseline it
  refuses to move anything at all, because then everything would look new

It is not an approval mechanism. A quarantined source stays out until somebody
adds the host to `registry_hosts.toml` and moves the block back, which is
still a human edit, twice over. What changed is that nothing waits on them.
The intake prompt is also given the allowlist now, so the ordinary case is
that the agent reports the host instead of registering it.

`archive/` is outside the manifest on purpose: the capture timer writes there
throughout an agent run, so a change under it carries no signal about the
agent. The agent is kept out of it by ownership and by never being the process
that calls `capture.py`.

### 5. The prompts say so too

`scripts/agent-prompt-rules.md` is injected into all four prompts by
`agent_render`, so there is one copy. Untrusted material is fenced with a
per-run nonce named in the trusted preamble, and any occurrence of that marker
inside the content, or of anything shaped like a renderer placeholder, is
mangled before fencing. The markdown fence around diff lines is gone, because
a fence made of backticks is closed by text anybody can write.

`render_agent_prompt.py` replaces the old `awk -v candidates=` interpolation,
which expanded backslash escapes inside a value that a candidate line
controls.

This layer is the weakest and is kept for two reasons that are not "it might
work". A run that should stop, stops. And an attempt gets reported, in a
project that exists to preserve what people did.

## What is still open

**Remote egress: closed, 6 Aug 2026.** This was the open gap when the five
layers first shipped, and building it turned out to be smaller than the
BACKLOG entry assumed, for two reasons that only became true once hydration
moved to the driver.

The review and intake agents need no web access at all now. Their evidence is
in the prompt. Only `claim-sweep` reads the open web, so there is one policy
question rather than three.

And the sweep's allowlist already existed. Its prompt names its targets, and
anything it wants to register has to be in `scripts/registry_hosts.toml`
anyway. So the policy is one sentence, and it falls out of the work rather
than being invented for it: **an agent may reach its model provider, and may
read what the registry is allowed to name.** A host that is on neither list is
a host a person adds, which is the same answer the sweep prompt already gave.

Two halves, and neither is any use alone:

- `scripts/agent_proxy.py`, on 127.0.0.2:8118, refuses a CONNECT to anything
  not on those two lists. CONNECT only, port 443 only, and every refusal
  logged with its reason. It binds 127.0.0.2 rather than 127.0.0.1 because the
  capture browser is on 127.0.0.1 and the firewall rule below permits one
  address
- `scripts/agent-egress.nft.example` drops everything else from the agent
  account, keyed on **uid** rather than on a systemd unit. The unit is the
  wrong key: `discover-community` runs the driver's Reddit hydration and the
  agent in one cgroup, and `IPAddress*` cannot tell a parent from a child. A
  uid can, and it also covers a manual `just discovery-intake`, which no unit
  setting reaches

Measured after applying: as `cc-agent`, a direct HTTPS connection, the capture
browser and 169.254.169.254 all time out; through the proxy, an allowed host
returns 200 and a denied one is refused; the operator account is untouched.
All three providers complete a full run.

Provider telemetry is refused on purpose. An agent here reads victim accounts
and attribution disputes, and a crash reporter is a route for fragments of
that to leave the host to a party with no relationship to this archive. All
three providers work with it blocked; if a future one does not, that is worth
knowing before choosing it.

What remains is narrower and stated plainly: the proxy authorises a host, not
a payload. An injected agent could still put data in a request to a host on
the list, for example by opening a GitHub issue. That is bounded by the
account holding no secret, by every such host being one this project already
reads in public, and by the run record showing what was written.

**`NoNewPrivileges` is off on the three agent units.** It blocks the setuid
transition `sudo` needs, and `sudo` is how the privilege drop happens. Keeping
the flag would mean running the agent with the operator's own access to `.env`
and the browser profile. The account boundary is worth more than the flag, and
the trade is recorded in each unit file rather than left to be rediscovered.

**One tree, several writers.** This repository has one working tree with live
timers in it, and the first real review run after this shipped was rejected
for a change to `sources.toml` that the review agent did not make: `ingest-x.py`
had registered forty posts while it worked. That is the failure mode worth
taking seriously, because a gate that cries wolf is a gate somebody switches
off, and then none of this exists.

So `sources.toml` and `DISCOVERY.md` are treated as shared. An out-of-remit
change to either is reported and not fatal, and the content rules carry the
weight instead: `check_registry.py` runs on the registry delta for every role,
and the queue's line-integrity check runs on every role, whoever did the
writing. A pending line that vanished is not a loss if its URL is now
registered, because that is exactly what the discovery scripts do on their own
timer.

What this gives up is narrow. An out-of-remit review agent could register a
thread that already satisfies every registry rule: allowlisted host, pinned
query, visible in the next `git diff`. That is not a capability worth the
noise of failing honest runs. Everything else outside a role's remit,
`scripts/` and the gates included, still fails hard.

**The token is not rotation.** `.capture-browser/token` is read once at
startup, deliberately, so it cannot be swapped under a running daemon.
Changing it means restarting `webbridge.service`.
