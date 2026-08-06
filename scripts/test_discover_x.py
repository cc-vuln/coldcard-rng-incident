import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("discover_x.py")
sys.path.insert(0, str(SCRIPT.parent))
import discover_x  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_registry_validates_and_collects_registered_posts(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "sources.toml"
            path.write_text(
                """
[[x_post]]
id = "known"
url = "https://x.com/SomeOne/status/123"

[[x_watch]]
handle = "Researcher"
org = "Lab"
since = "2026-07-30"
include_replies = false
max_posts = 12
why = "Publishes primary technical work."

[[x_watch]]
handle = "Disabled"
active = false
why = "Retained configuration, not currently read."
""",
                encoding="utf-8",
            )
            watches, ids, urls = discover_x.load_registry(path)

        self.assertEqual([watch.handle for watch in watches], ["Researcher"])
        self.assertFalse(watches[0].include_replies)
        self.assertEqual(watches[0].max_posts, 12)
        self.assertEqual(ids, {"123"})
        self.assertEqual(urls, {"https://x.com/SomeOne/status/123"})

    def test_registry_rejects_duplicate_handles_case_insensitively(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "sources.toml"
            path.write_text(
                """
[[x_watch]]
handle = "Researcher"
why = "one"
[[x_watch]]
handle = "researcher"
why = "two"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(discover_x.ConfigError, "duplicate"):
                discover_x.load_registry(path)

    def test_registry_rejects_impossible_calendar_date(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "sources.toml"
            path.write_text(
                """
[[x_watch]]
handle = "Researcher"
since = "2026-99-40"
why = "technical work"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(discover_x.ConfigError, "calendar"):
                discover_x.load_registry(path)


class OfficialApiTests(unittest.TestCase):
    def setUp(self):
        self.watch = discover_x.Watch(
            handle="Researcher", why="Primary technical work", org="Lab",
            since="2026-07-29",
        )
        self.user = discover_x.ResolvedUser("10", "Researcher")

    def test_timeline_request_is_bounded_and_uses_no_home_feed_or_search(self):
        params = discover_x.timeline_params(self.watch, None)
        self.assertEqual(params["max_results"], "20")
        self.assertEqual(params["start_time"], "2026-07-29T00:00:00Z")
        self.assertNotIn("exclude", params)

        calls = []

        def requester(path, supplied, token):
            calls.append((path, supplied, token))
            return {"data": [], "meta": {"result_count": 0}}

        result = discover_x.fetch_watch(
            self.watch, self.user, "token", None, requester=requester
        )
        self.assertTrue(result.healthy)
        self.assertEqual(calls[0][0], "/users/10/tweets")
        rendered = json.dumps(calls[0])
        self.assertNotIn("home", rendered)
        self.assertNotIn("search", rendered)
        self.assertNotIn("POST", rendered)

    def test_repost_keeps_outer_id_and_attributes_original_author(self):
        payload = {
            "data": [{
                "id": "2084000000000001234",
                "author_id": "10",
                "created_at": "2026-08-03T12:30:00Z",
                "text": "RT @OriginalAuthor: abbreviated",
                "referenced_tweets": [
                    {"type": "retweeted", "id": "2070000000000000000"}
                ],
                "public_metrics": {
                    "reply_count": 2,
                    "retweet_count": 3,
                    "quote_count": 1,
                },
            }],
            "includes": {
                "tweets": [{
                    "id": "2070000000000000000",
                    "author_id": "20",
                    "text": "short",
                    "note_tweet": {"text": "Full original technical result"},
                }],
                "users": [
                    {"id": "10", "username": "Researcher"},
                    {"id": "20", "username": "OriginalAuthor"},
                ],
            },
            "meta": {"result_count": 1},
        }
        result = discover_x.parse_timeline(
            payload, self.watch, self.user, incremental=False
        )

        self.assertTrue(result.healthy)
        post = result.posts[0]
        self.assertEqual(post["id"], "2084000000000001234")
        self.assertEqual(post["relation"], "repost")
        self.assertEqual(post["originalAuthor"], "OriginalAuthor")
        self.assertEqual(post["content"], "Full original technical result")

    def test_incremental_full_page_never_advances_checkpoint(self):
        payload = {
            "data": [],
            "meta": {"result_count": 20, "next_token": "more"},
        }
        result = discover_x.parse_timeline(
            payload, self.watch, self.user, incremental=True
        )

        self.assertEqual(result.status, "result-window-exceeded")
        self.assertFalse(result.healthy)

    def test_empty_official_timeline_is_healthy(self):
        result = discover_x.parse_timeline(
            {"data": [], "meta": {"result_count": 0}},
            self.watch,
            self.user,
            incremental=True,
        )
        self.assertTrue(result.healthy)
        self.assertEqual(result.posts, ())

    def test_failure_classes_are_specific(self):
        cases = [
            (429, "Too Many Requests", "rate-limited"),
            (402, "credits exhausted", "quota-exhausted"),
            (401, "Unauthorized", "auth-stale"),
            (403, "client forbidden", "api-access-denied"),
            (403, "This account is protected", "profile-protected"),
            (404, "Could not find user", "profile-unavailable"),
            (503, "Service unavailable", "transient-error"),
        ]
        for status, detail, expected in cases:
            with self.subTest(status=status, detail=detail):
                self.assertEqual(
                    discover_x.classify_api_failure(status, detail), expected
                )

    def test_api_transport_sends_bearer_only_in_authorization_header(self):
        captured = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response(b'{"data": []}')

        result = discover_x.api_get(
            "/users/by",
            {"usernames": "Researcher"},
            "top-secret",
            opener=opener,
        )

        request = captured["request"]
        self.assertEqual(result, {"data": []})
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer top-secret")
        self.assertNotIn("top-secret", request.full_url)
        self.assertTrue(request.full_url.startswith("https://api.x.com/2/"))

    def test_handle_resolution_is_batched_and_cached(self):
        state = {"version": 1, "seen": [], "watches": {}}
        calls = []

        def requester(path, params, token):
            calls.append((path, params, token))
            return {
                "data": [{
                    "id": "10",
                    "username": "Researcher",
                    "protected": False,
                }]
            }

        resolved, failures = discover_x.resolve_users(
            [self.watch], state, "token", requester=requester
        )
        discover_x.resolve_users([self.watch], state, "token", requester=requester)

        self.assertEqual(failures, {})
        self.assertEqual(resolved["researcher"].id, "10")
        self.assertEqual(calls[0][0], "/users/by")
        self.assertEqual(len(calls), 1)
        self.assertEqual(state["watches"]["researcher"]["user_id"], "10")

    def test_candidate_log_omits_hydrated_post_text_and_metrics(self):
        post = {
            "id": "2084000000000001234",
            "url": "https://x.com/Researcher/status/2084000000000001234",
            "actor": "Researcher",
            "org": "Lab",
            "relation": "post",
            "content": "hydrated API text must not persist",
            "createdAt": "2026-08-03T12:30:00Z",
            "replyId": None,
            "quoteId": None,
            "sourceTweetId": "2084000000000001234",
            "replyCount": 99,
            "repostCount": 88,
            "quoteCount": 77,
            "watchWhy": "Primary technical work",
            "label": "X @Researcher",
        }
        candidate = discover_x.candidate_for_intake(post, "20260805T120000Z")

        self.assertNotIn("content", candidate)
        self.assertNotIn("replyCount", candidate)
        self.assertNotIn("repostCount", candidate)
        self.assertNotIn("quoteCount", candidate)
        self.assertIn("text available during approved intake", candidate["title"])

    def test_show_hydrates_once_without_rewriting_candidate_log(self):
        candidate = {
            "id": "2084000000000001234",
            "url": "https://x.com/Researcher/status/2084000000000001234",
            "platform": "x",
            "actor": "Researcher",
            "org": "Lab",
            "relation": "post",
            "createdAt": "2026-08-03T12:30:00Z",
            "sourceTweetId": "2084000000000001234",
            "watchWhy": "Primary technical work",
            "label": "X @Researcher",
            "foundAt": "20260805T120000Z",
            "title": "@Researcher post (text available during approved intake)",
        }
        payload = {
            "data": {
                "id": candidate["id"],
                "author_id": "10",
                "created_at": candidate["createdAt"],
                "text": "Hydrated only for immediate assessment",
                "public_metrics": {},
            },
            "includes": {
                "users": [{"id": "10", "username": "Researcher"}]
            },
        }
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "candidates.jsonl"
            original = json.dumps(candidate) + "\n"
            path.write_text(original, encoding="utf-8")

            def requester(api_path, params, token):
                self.assertEqual(api_path, f"/tweets/{candidate['id']}")
                self.assertEqual(token, "token")
                return payload

            output = io.StringIO()
            with redirect_stdout(output):
                status = discover_x.hydrate_candidate(
                    candidate["id"], "token", path=path, requester=requester
                )

            self.assertEqual(path.read_text(encoding="utf-8"), original)

        self.assertEqual(status, 0)
        self.assertIn("Hydrated only for immediate assessment", output.getvalue())


class StateAndQueueTests(unittest.TestCase):
    NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.watch = discover_x.Watch(
            handle="Researcher", why="Primary technical work"
        )
        self.post = {
            "id": "2085000000000000000",
            "url": "https://x.com/Researcher/status/2085000000000000000",
            "platform": "x",
            "actor": "Researcher",
            "author": "Researcher",
            "originalAuthor": "Researcher",
            "org": None,
            "relation": "post",
            "content": "New incident analysis",
            "createdAt": "2026-08-05T11:00:00Z",
            "replyTo": None,
            "replyId": None,
            "quoteId": None,
            "sourceTweetId": "2085000000000000000",
            "replyCount": 1,
            "repostCount": 0,
            "quoteCount": 0,
            "watchWhy": "Primary technical work",
            "label": "X @Researcher",
        }

    def run_discovery(self, directory, state, result, *, queue_initial=False):
        state_path = Path(directory) / "state.json"
        candidates_path = Path(directory) / "candidates.jsonl"
        with mock.patch("discovery_common.update_intake") as update, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            attempted, queued, failures = discover_x.discover(
                [self.watch],
                state,
                set(),
                set(),
                queue_initial=queue_initial,
                no_state=False,
                profile_delay=10,
                cooldown_hours=24,
                clock=lambda: self.NOW,
                fetcher=lambda watch: result,
                state_path=state_path,
                candidates_path=candidates_path,
            )
        return attempted, queued, failures, state_path, candidates_path, update

    def test_first_success_baselines_without_queueing_history(self):
        state = {"version": 1, "seen": [], "watches": {}}
        result = discover_x.FetchResult("healthy", posts=(self.post,))
        with tempfile.TemporaryDirectory() as raw:
            attempted, queued, failures, state_path, candidates_path, update = (
                self.run_discovery(raw, state, result)
            )
            saved = json.loads(state_path.read_text())

        self.assertEqual((attempted, queued, failures), (1, 0, 0))
        self.assertIn(self.post["id"], saved["seen"])
        self.assertFalse(candidates_path.exists())
        update.assert_not_called()

    def test_later_success_queues_only_unseen_posts(self):
        state = {
            "version": 1,
            "seen": ["2084000000000000000"],
            "watches": {
                "researcher": {
                    "last_success": "20260805T060000Z",
                    "last_attempt": "20260805T060000Z",
                    "newest_id": "2084000000000000000",
                }
            },
        }
        result = discover_x.FetchResult("healthy", posts=(self.post,))
        with tempfile.TemporaryDirectory() as raw:
            attempted, queued, failures, _, candidates_path, update = (
                self.run_discovery(raw, state, result)
            )
            lines = candidates_path.read_text().splitlines()

        self.assertEqual((attempted, queued, failures), (1, 1, 0))
        logged = json.loads(lines[0])
        self.assertEqual(logged["id"], self.post["id"])
        self.assertNotIn("content", logged)
        self.assertEqual(update.call_args.args[0][0]["platform"], "x")

    def test_rate_limit_sets_persistent_cooldown_and_reports_failure(self):
        state = {"version": 1, "seen": [], "watches": {}}
        result = discover_x.FetchResult("rate-limited", detail="HTTP 429")
        with tempfile.TemporaryDirectory() as raw:
            attempted, queued, failures, state_path, _, _ = self.run_discovery(
                raw, state, result
            )
            saved = json.loads(state_path.read_text())

        self.assertEqual((attempted, queued, failures), (1, 0, 1))
        self.assertEqual(saved["cooldown_reason"], "rate-limited")
        self.assertEqual(saved["cooldown_until"], "20260806T120000Z")

    def test_atomic_state_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "state.json"
            path.write_text("not json", encoding="utf-8")
            discover_x.atomic_json(path, {"version": 1, "seen": []})
            self.assertEqual(json.loads(path.read_text())["version"], 1)
            self.assertEqual(list(Path(raw).glob("state.json.*")), [])

    def test_invalid_integer_environment_value_is_rejected(self):
        with mock.patch.dict(os.environ, {"X_DISCOVERY_MAX_WATCHES": "1.5"}):
            with self.assertRaisesRegex(discover_x.ConfigError, "integer"):
                discover_x.parse_int_env("X_DISCOVERY_MAX_WATCHES", 6)

    def test_credential_preflight_is_local_and_requires_official_api_token(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(discover_x.ConfigError, "official X API"):
                discover_x.bearer_token()
        with mock.patch.dict(
            os.environ, {"X_API_BEARER_TOKEN": "present"}, clear=True
        ):
            self.assertEqual(discover_x.bearer_token(), "present")


if __name__ == "__main__":
    unittest.main()
