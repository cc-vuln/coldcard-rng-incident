#!/usr/bin/env python3
"""Prove the agent gate refuses what an injected run would try.

Every test builds a throwaway git repository shaped like this one, runs the
guard's `before` pass, makes the edit an injection would make, and asserts on
the `after` verdict. No network, no agent, no capture.

The fixtures are the interesting part. They are the things a compromised
intake or sweep run would actually do with the access it has: register a
source that fetches from somewhere else, change what an existing source
fetches, rewrite a candidate on its way through the queue, ask for a capture
of something it did not register, copy a key into a note, or edit the tooling
that is supposed to be checking it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_guard  # noqa: E402
import check_registry  # noqa: E402

REAL_ROOT = Path(__file__).resolve().parent.parent

SOURCES = '''\
[meta]
generated = "by hand"

[[source]]
id = "stackernews-example-thread"
title = "An example thread"
url = "https://stacker.news/items/111111"
published = "2026-08-01"
org = "Stacker News"
kind = "community-discussion"
tier = 2
min_chars = 400
capture = "http"
fetch_url = "https://stacker.news/api/graphql"
json_pretty = true
required_text = ['"title"']
fetch_post = '{"query": "{ item(id: 111111) { title text createdAt user { name } comments { comments { text createdAt user { name } } } } }"}'
note = "An existing registration, for the delta rules to compare against."
'''

DISCOVERY = """\
# Discovery intake

## Pending

- 2026-08-05 [A candidate thread](https://www.reddit.com/r/coldcard/comments/aaa111/a_candidate/) by someone, 4 comments (r/coldcard)

## Assessed

- 2026-08-01 [An older one](https://stacker.news/items/111111) -> registered as stackernews-example-thread (20260801T000000Z)

## Link review, held for a human decision

- 2026-08-02 [A third section](https://www.reddit.com/r/coldcard/comments/ccc333/third/) -> dismissed: already covered (20260802T000000Z)
"""

NEW_REDDIT_BLOCK = '''
[[source]]
id = "reddit-a-candidate"
title = "r/coldcard: a candidate"
url = "https://www.reddit.com/r/coldcard/comments/aaa111/a_candidate/"
org = "reddit"
kind = "community-discussion"
tier = 2
watch_until = "20260812T000000Z"
min_chars = 1500
capture = "reddit-json"
why = "A first-hand account of a drained device, with the timeline the poster kept and the transaction ids to follow."
'''

VERDICT = " -> registered as reddit-a-candidate (20260806T120000Z)"

# What revision-reviews.toml already holds when a run starts: one settled
# classification the append-only prefix check can watch being preserved.
EXISTING_REVIEW = '''\
[[revision]]
source = "stackernews-example-thread"
timestamp = "20260801T000000Z"
status = "capture-noise"
summary = "Only the relative timestamps on the comments moved."
'''


class GuardCase(unittest.TestCase):
    role = "intake"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_dir = self.tmp / ".work" / "agent-guard" / "run"

        (self.tmp / "scripts").mkdir()
        (self.tmp / "site" / "src" / "pages").mkdir(parents=True)
        (self.tmp / ".work").mkdir(exist_ok=True)
        self.write("sources.toml", SOURCES)
        self.write("DISCOVERY.md", DISCOVERY)
        self.write("revision-reviews.toml", EXISTING_REVIEW)
        self.write("corrections.toml", "# No corrections yet.\n")
        self.write("archive/index.jsonl",
                   '{"ts": "20260801T000000Z", '
                   '"id": "stackernews-example-thread", "result": "same"}\n')
        self.write("BACKLOG.md", "# Backlog\n")
        self.write("scripts/capture.py", "# the capture writer\n")
        self.write("site/src/pages/about.astro", "<p>about</p>\n")
        # The gate reads .env for literal secret values. These are the shapes
        # the real file carries, with values that exist only in this test.
        self.write(".env", "\n".join([
            "SITE_URL=https://example.invalid",
            "PUBLIC_CONTACT=info@example.invalid",
            "CLOUDFLARE_API_TOKEN=cf-token-value-000000000000",
            "NOSTR_SECRET_KEY=nsec-secret-value-1111111111",
            "REVIEW_AGENT_BIN=/opt/agent/bin/agent",
        ]) + "\n")
        self.write(".gitignore", ".work/\n.env\n")

        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)

        self._real_root = agent_guard.ROOT
        agent_guard.ROOT = self.tmp
        self.addCleanup(setattr, agent_guard, "ROOT", self._real_root)

        from contextlib import redirect_stdout
        from io import StringIO
        with redirect_stdout(StringIO()):
            self.assertEqual(0, agent_guard.do_before(self.role, self.run_dir))

    def write(self, rel: str, text: str) -> None:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def append(self, rel: str, text: str) -> None:
        (self.tmp / rel).write_text((self.tmp / rel).read_text() + text)

    def assess_candidate(self, verdict: str = VERDICT) -> None:
        """Move the pending candidate to Assessed, the way the agent does."""
        text = (self.tmp / "DISCOVERY.md").read_text()
        pending = [line for line in text.splitlines()
                   if line.startswith("- 2026-08-05")][0]
        text = text.replace(pending + "\n", "")
        self.write("DISCOVERY.md", text.rstrip("\n") + "\n" + pending + verdict + "\n")

    def after(self) -> tuple[int, str]:
        from io import StringIO
        from contextlib import redirect_stderr, redirect_stdout
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = agent_guard.do_after(self.role, self.run_dir)
        return code, out.getvalue() + err.getvalue()

    def assertRejected(self, needle: str) -> None:
        code, output = self.after()
        self.assertEqual(1, code, f"expected a rejection, got:\n{output}")
        self.assertIn(needle, output)

    def assertAccepted(self) -> None:
        code, output = self.after()
        self.assertEqual(0, code, f"expected acceptance, got:\n{output}")


class HonestRun(GuardCase):
    def test_a_clean_run_passes(self):
        self.assertAccepted()

    def test_the_ordinary_registration_passes(self):
        """The shape a real intake run produces, end to end."""
        self.append("sources.toml", NEW_REDDIT_BLOCK)
        self.assess_candidate()
        (self.run_dir / "capture-requests.txt").write_text("reddit-a-candidate\n")
        self.assertAccepted()
        approved = (self.run_dir / "approved-captures.txt").read_text().split()
        self.assertEqual(["reddit-a-candidate"], approved)

    def test_correcting_a_tier_is_allowed(self):
        text = (self.tmp / "sources.toml").read_text().replace(
            "tier = 2", "tier = 3")
        self.write("sources.toml", text)
        self.assertAccepted()


class OutOfRemit(GuardCase):
    def test_editing_the_capture_writer_is_rejected(self):
        self.append("scripts/capture.py", "# and one more line\n")
        self.assertRejected("scripts/capture.py: modified, and outside the "
                            "intake remit")

    def test_editing_a_site_page_is_rejected_for_intake(self):
        self.append("site/src/pages/about.astro", "<p>more</p>\n")
        self.assertRejected("outside the intake remit")

    def test_deleting_a_tracked_file_is_rejected(self):
        (self.tmp / "BACKLOG.md").unlink()
        self.assertRejected("BACKLOG.md: deleted")

    def test_a_new_file_outside_the_remit_is_rejected(self):
        self.write("exfil.sh", "curl https://example.invalid\n")
        self.assertRejected("exfil.sh: created")


class RegistryPoisoning(GuardCase):
    def test_a_source_fetching_an_unlisted_host_is_rejected(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK.replace(
            "https://www.reddit.com/r/coldcard/comments/aaa111/a_candidate/",
            "https://collector.example.invalid/beacon"))
        self.assertRejected("does not match the reddit host")

    def test_an_altered_fetch_post_is_rejected(self):
        self.append("sources.toml", '''
[[source]]
id = "stackernews-poisoned"
title = "Poisoned"
url = "https://stacker.news/items/222222"
org = "Stacker News"
kind = "community-discussion"
tier = 2
capture = "http"
fetch_url = "https://stacker.news/api/graphql"
fetch_post = '{"query": "{ me { privates } }"}'
''')
        self.assertRejected("fetch_post is not the pinned item query")

    def test_repointing_an_existing_source_is_rejected(self):
        text = (self.tmp / "sources.toml").read_text().replace(
            "https://stacker.news/api/graphql",
            "https://stacker.news/api/graphql?to=elsewhere")
        self.write("sources.toml", text)
        self.assertRejected("had fetch_url changed")

    def test_removing_a_source_is_rejected(self):
        self.write("sources.toml", '[meta]\ngenerated = "by hand"\n')
        self.assertRejected("removed from [[source]]")

    def test_a_plain_http_url_is_rejected(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK.replace(
            "https://www.reddit.com", "http://www.reddit.com"))
        self.assertRejected("is not https")


class Exfiltration(GuardCase):
    def test_a_secret_value_in_a_note_is_rejected(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK.replace(
            "A first-hand account",
            "cf-token-value-000000000000 A first-hand account"))
        self.assertRejected("contains the literal value of CLOUDFLARE_API_TOKEN")

    def test_a_home_path_in_added_text_is_rejected(self):
        # The literal is split so the deposit leak scan, which reads source
        # text, does not match the very token this fixture simulates.
        self.append("DISCOVERY.md", "\n/home/" + "someone/coldcard-rng-incident\n")
        self.assertRejected("a filesystem path under /home")

    def test_a_missing_env_is_reported_rather_than_skipped(self):
        (self.tmp / ".env").unlink()
        self.append("sources.toml", NEW_REDDIT_BLOCK)
        self.assess_candidate()
        self.assertRejected("A gate running without its list is not a gate")


class QueueIntegrity(GuardCase):
    def test_rewriting_a_candidate_is_rejected(self):
        text = (self.tmp / "DISCOVERY.md").read_text().replace(
            "https://www.reddit.com/r/coldcard/comments/aaa111/a_candidate/",
            "https://elsewhere.example.invalid/")
        self.write("DISCOVERY.md", text)
        self.assess_candidate()
        self.assertRejected("does not match any candidate that was pending")

    def test_lines_already_settled_elsewhere_are_not_new_verdicts(self):
        """DISCOVERY.md has a third section, and both sides must see it.

        Counting "Link review, held for a human decision" as settled in the
        new file but not the old made every line already sitting there look
        like a verdict invented during the run. It rejected a clean intake
        with eleven false positives before this test existed.
        """
        self.assess_candidate()
        self.assertAccepted()

    def test_a_candidate_that_simply_vanishes_is_rejected(self):
        text = (self.tmp / "DISCOVERY.md").read_text()
        pending = [line for line in text.splitlines()
                   if line.startswith("- 2026-08-05")][0]
        self.write("DISCOVERY.md", text.replace(pending + "\n", ""))
        self.assertRejected("left the queue without its text surviving")

    def test_deferring_a_pending_candidate_is_rejected(self):
        """Deferral is the lanes' mechanism, not an exit the agent may take.

        Deferred is a queue rather than a verdict, so moving a line into it
        leaves the candidate unassessed with no reason recorded. If the guard
        counted Deferred as settled, that would read as a clean disposal.
        """
        text = (self.tmp / "DISCOVERY.md").read_text()
        pending = [line for line in text.splitlines()
                   if line.startswith("- 2026-08-05")][0]
        moved = text.replace(pending + "\n", "")
        self.write("DISCOVERY.md",
                   moved + f"\n## Deferred\n\n{pending}\n")
        self.assertRejected("left the queue without its text surviving")

    def test_a_lane_adding_a_deferred_candidate_is_accepted(self):
        """A discovery lane runs on its own timer and may append to Deferred
        while an agent run is in flight. That is not the agent's doing and
        must not be read as an invented verdict."""
        # The agent records its verdict into ## Assessed, which the prompt
        # names explicitly; the lane's append lands after it.
        self.assess_candidate()
        self.append("DISCOVERY.md", (
            "\n## Deferred\n\n"
            "- 2026-08-06 [a quiet thread](https://www.reddit.com/r/Bitcoin/"
            "comments/zzz999/a_quiet_thread/) by nobody, 1 comments "
            "(r/Bitcoin) [topical]\n"))
        self.assertAccepted()


class CaptureRequests(GuardCase):
    def test_a_capture_of_something_unregistered_is_rejected(self):
        (self.run_dir / "capture-requests.txt").write_text("some-other-source\n")
        self.assertRejected("which is not in the registry")

    def test_a_capture_of_a_pre_existing_source_is_rejected(self):
        """Re-capturing an old source is the poll's job, not an agent's."""
        (self.run_dir / "capture-requests.txt").write_text(
            "stackernews-example-thread\n")
        self.assertRejected("which this run did not register")


class SharedFiles(GuardCase):
    """One tree, live timers, several writers.

    sources.toml and DISCOVERY.md are written by the discovery scripts and by
    ingest-x.py on their own schedules, so a change to them during a review
    run says nothing about the review agent. Failing there fails honest runs,
    and a gate that cries wolf gets switched off. The content rules carry the
    weight instead, and they run whoever did the writing.
    """
    role = "review"

    def test_a_neighbour_writing_the_registry_does_not_fail_a_review_run(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK)
        self.assertAccepted()

    def test_the_registry_rules_still_apply_to_a_review_run(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK.replace(
            "https://www.reddit.com/r/coldcard/comments/aaa111/a_candidate/",
            "https://collector.example.invalid/beacon"))
        self.assertRejected("does not match the reddit host")

    def test_the_secret_scan_still_applies_to_a_review_run(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK.replace(
            "A first-hand account",
            "cf-token-value-000000000000 A first-hand account"))
        self.assertRejected("contains the literal value of CLOUDFLARE_API_TOKEN")

    def test_a_pruned_candidate_whose_thread_is_registered_is_not_a_loss(self):
        """The discovery scripts drop a pending line once it is registered."""
        self.append("sources.toml", NEW_REDDIT_BLOCK)
        text = (self.tmp / "DISCOVERY.md").read_text()
        pending = [line for line in text.splitlines()
                   if line.startswith("- 2026-08-05")][0]
        self.write("DISCOVERY.md", text.replace(pending + "\n", ""))
        self.assertAccepted()


class ReviewRole(GuardCase):
    role = "review"

    def test_the_review_agent_may_not_touch_a_site_page(self):
        self.append("site/src/pages/about.astro", "<p>more</p>\n")
        self.assertRejected("outside the review remit")

    def test_the_review_agent_may_append_classifications(self):
        self.append("revision-reviews.toml", '''
[[revision]]
source = "stackernews-example-thread"
timestamp = "20260806T120000Z"
status = "capture-noise"
summary = "Only the relative timestamps on the comments moved."
''')
        self.assertAccepted()

    def test_the_review_agent_may_not_request_a_capture(self):
        (self.run_dir / "capture-requests.txt").write_text("anything\n")
        self.assertRejected("the review role registers nothing")


class AppendOnlyFiles(GuardCase):
    """The append-only logs gain entries; they are never rewritten.

    revision-reviews.toml is in the review role's remit, so it is the file
    an injected review run could rewrite without tripping the remit check.
    archive/index.jsonl sits outside the git manifest entirely, so the
    capture timer's appends during a run must pass, and only a rewrite may
    fail.
    """
    role = "review"

    def test_appending_a_classification_passes(self):
        self.append("revision-reviews.toml", '''
[[revision]]
source = "stackernews-example-thread"
timestamp = "20260806T120000Z"
status = "capture-noise"
summary = "Only the relative timestamps on the comments moved."
''')
        self.assertAccepted()

    def test_rewriting_an_existing_classification_is_rejected(self):
        text = (self.tmp / "revision-reviews.toml").read_text().replace(
            "capture-noise", "source-content")
        self.write("revision-reviews.toml", text)
        self.assertRejected("revision-reviews.toml: existing content changed")

    def test_rewriting_the_corrections_log_is_rejected(self):
        """corrections.toml is in no role's remit, but the check keys on the
        file rather than the role, so it fires here too — as it must the day
        a role gains the file."""
        text = (self.tmp / "corrections.toml").read_text().replace(
            "# No corrections yet.\n", "# Rewritten.\n")
        self.write("corrections.toml", text)
        self.assertRejected("corrections.toml: existing content changed")

    def test_appending_to_the_capture_index_passes(self):
        """The capture timer appends to the index throughout every run."""
        self.append("archive/index.jsonl",
                    '{"ts": "20260806T120000Z", '
                    '"id": "stackernews-example-thread", "result": "same"}\n')
        self.assertAccepted()

    def test_rewriting_the_capture_index_is_rejected(self):
        self.write("archive/index.jsonl",
                   '{"ts": "20260801T000000Z", '
                   '"id": "stackernews-example-thread", "result": "changed"}\n')
        self.assertRejected("archive/index.jsonl: existing content changed")

    def test_leaving_the_append_only_files_untouched_passes(self):
        self.assertAccepted()


class PlaceholderProse(GuardCase):
    """The 7 Aug 2026 first-line-plus-TODO registrations cannot recur."""

    def test_a_new_block_with_a_short_why_is_rejected(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK.replace(
            "A first-hand account of a drained device, with the timeline "
            "the poster kept and the transaction ids to follow.",
            "First line of the thread."))
        self.assertRejected("under the 15-word floor")

    def test_a_new_block_with_a_todo_marker_is_rejected(self):
        self.append("sources.toml", NEW_REDDIT_BLOCK.replace(
            "the transaction ids to follow.",
            "the transaction ids to follow. (TODO: expand)"))
        self.assertRejected("carries a placeholder marker")


class LiveRegistry(unittest.TestCase):
    """The rules have to fit the registry this project already has.

    A gate calibrated only against fixtures is a gate that fails the first
    time it meets the real file. This runs the whole-registry mode over the
    tracked sources.toml, which is also what `just audit` does.
    """

    def test_the_tracked_registry_passes(self):
        hosts = check_registry.allowed_hosts()
        registry = check_registry.load(REAL_ROOT / "sources.toml")
        self.assertEqual([], check_registry.check_registry(registry, hosts))

    def test_every_registered_host_is_listed(self):
        """No host is in the registry without being in the allowlist."""
        hosts = check_registry.allowed_hosts()
        registry = check_registry.load(REAL_ROOT / "sources.toml")
        from urllib.parse import urlparse
        used = set()
        for table in ("source", "x_post", "nostr_post"):
            for block in registry.get(table, []):
                for field in ("url", "fetch_url"):
                    if block.get(field):
                        used.add(urlparse(block[field]).netloc.lower())
        self.assertEqual(set(), used - hosts)


if __name__ == "__main__":
    unittest.main()
