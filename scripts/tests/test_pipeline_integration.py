"""
End-to-end test of build_catalog() with the network layer swapped out for
canned responses — no real HTTP calls are made. This is what actually
exercises main()'s wiring (cumulative downloads across two runs, history
diffing, JSON shapes) rather than each helper in isolation.

fixture_real_config.py is a frozen, point-in-time copy of Builder-Morphe's
real core/config.py (taken from the project this site was built for). It
is not meant to be kept in sync with that file going forward — its job is
to prove parse_apps_config handles a real, full-sized config, not just the
small hand-written excerpt in test_update_catalog.py.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import update_catalog as uc  # noqa: E402

FIXTURE_CONFIG = (Path(__file__).parent / "fixture_real_config.py").read_text("utf-8")


def _patch_ed(monkey_targets: dict):
    """Small hand-rolled stand-in for pytest's monkeypatch fixture (not
    available in every environment this might be run in); restores
    whatever it overwrote once the `with` block exits."""

    class _Patcher:
        def __enter__(self):
            self._originals = {}
            for owner, attr, value in monkey_targets:
                self._originals[(owner, attr)] = getattr(owner, attr)
                setattr(owner, attr, value)
            return self

        def __exit__(self, *exc):
            for (owner, attr), value in self._originals.items():
                setattr(owner, attr, value)

    return _Patcher()


def _release(tag: str, assets: list[dict]) -> dict:
    return {
        "tag_name": tag,
        "name": f"Patched APKs - {tag}",
        "html_url": f"https://github.com/Erlikx/Builder-Morphe/releases/tag/{tag}",
        "published_at": "2026-09-10T12:00:00Z",
        "assets": assets,
    }


def _asset(name: str, download_count: int, size: int = 42_000_000) -> dict:
    return {
        "name": name,
        "download_count": download_count,
        "size": size,
        "browser_download_url": f"https://github.com/Erlikx/Builder-Morphe/releases/download/x/{name}",
    }


def _fake_release_list(owner: str, repo: str, tag: str = "v1.0.0") -> list[dict]:
    return [
        {
            "tag_name": tag,
            "published_at": "2026-09-09T00:00:00Z",
            "html_url": f"https://github.com/{owner}/{repo}/releases/tag/{tag}",
            "body": f"### {tag}\n\n* Fixed something in {repo}.",
            "prerelease": False,
        }
    ]


def test_pipeline_end_to_end_with_download_reset_and_version_bump():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        apps_json = tmp_path / "apps.json"
        history_json = tmp_path / "history.json"
        obtainium_json = tmp_path / "obtainium.json"

        # ---- run 1 -------------------------------------------------
        run1_assets = [
            _asset("YouTube-19.47.52.apk", download_count=100),
            _asset("YT.Music-7.30.51.apk", download_count=50),
            _asset("MicroG.apk", download_count=10),
        ]
        run1_release = _release("build-2026-09-10T00-00-00", run1_assets)

        def fake_fetch_text_1(url):
            assert "core/config.py" in url
            return FIXTURE_CONFIG

        def fake_fetch_json_1(url, allow_404=False):
            if url.endswith("/releases/latest"):
                return run1_release
            if "/releases?per_page=" in url:
                owner_repo = url.split("/repos/")[1].split("/releases")[0]
                owner, repo = owner_repo.split("/")
                return _fake_release_list(owner, repo)
            raise AssertionError(f"unexpected URL in test: {url}")

        with _patch_ed_targets(apps_json, history_json, obtainium_json, fake_fetch_text_1, fake_fetch_json_1):
            catalog1, history1, obtainium1 = uc.build_catalog()
            uc.write_json(uc.APPS_JSON, catalog1)
            uc.write_json(uc.HISTORY_JSON, history1)
            uc.write_json(uc.OBTAINIUM_JSON, obtainium1)

        yt1 = next(a for a in catalog1["apps"] if a["appKey"] == "youtube")
        assert yt1["status"] == "published"
        assert yt1["version"] == "19.47.52"
        assert yt1["downloads"]["total"] == 100
        assert catalog1["microg"]["downloadUrl"].endswith("MicroG.apk")

        # An app not present in run1's assets at all (e.g. "reddit") must
        # still appear in the catalog, marked pending rather than dropped.
        reddit1 = next(a for a in catalog1["apps"] if a["appKey"] == "reddit")
        assert reddit1["status"] == "pending"
        assert reddit1["version"] is None

        assert json.loads(obtainium_json.read_text("utf-8"))["apps"], "obtainium.json should list published apps"
        for app in json.loads(obtainium_json.read_text("utf-8"))["apps"]:
            # additionalSettings must be a JSON *string* per Obtainium's
            # import format, not a nested object.
            assert isinstance(app["additionalSettings"], str)
            json.loads(app["additionalSettings"])  # must parse cleanly

        # First time YouTube is ever seen -> one history entry, fromVersion None.
        yt_events_1 = [e for e in history1["entries"] if e["appKey"] == "youtube"]
        assert len(yt_events_1) == 1
        assert yt_events_1[0]["fromVersion"] is None
        assert yt_events_1[0]["toVersion"] == "19.47.52"

        # ---- run 2: new release, YouTube updated, downloads "reset" ----
        run2_assets = [
            _asset("YouTube-19.48.00.apk", download_count=5),  # reset by a fresh release
            _asset("YT.Music-7.30.51.apk", download_count=52),  # same version, count only went up
            _asset("MicroG.apk", download_count=1),
        ]
        run2_release = _release("build-2026-09-11T00-00-00", run2_assets)

        def fake_fetch_json_2(url, allow_404=False):
            if url.endswith("/releases/latest"):
                return run2_release
            if "/releases?per_page=" in url:
                owner_repo = url.split("/repos/")[1].split("/releases")[0]
                owner, repo = owner_repo.split("/")
                return _fake_release_list(owner, repo, tag="v1.0.1")
            raise AssertionError(f"unexpected URL in test: {url}")

        with _patch_ed_targets(apps_json, history_json, obtainium_json, fake_fetch_text_1, fake_fetch_json_2):
            catalog2, history2, _ = uc.build_catalog()
            uc.write_json(uc.APPS_JSON, catalog2)
            uc.write_json(uc.HISTORY_JSON, history2)

        yt2 = next(a for a in catalog2["apps"] if a["appKey"] == "youtube")
        assert yt2["version"] == "19.48.00"
        # cumulative total must fold in run1's 100 downloads even though
        # the raw asset counter reset to 5 under the new release.
        assert yt2["downloads"]["downloadsThisRelease"] == 5
        assert yt2["downloads"]["cumulativeBaseline"] == 100
        assert yt2["downloads"]["total"] == 105

        ytm2 = next(a for a in catalog2["apps"] if a["appKey"] == "youtube-music")
        # Same release tag as before it changed? No — release tag DID
        # change (build-...-10 -> build-...-11), so YT Music's baseline
        # folds in too even though ITS filename/version didn't change.
        assert ytm2["downloads"]["cumulativeBaseline"] == 50
        assert ytm2["downloads"]["total"] == 102

        # Both runs' events must survive (history is additive, not
        # overwritten) — matched by content rather than "latest by
        # timestamp", since two runs executed back-to-back in a test can
        # legitimately land in the same second-resolution timestamp.
        yt_events_2 = [e for e in history2["entries"] if e["appKey"] == "youtube"]
        assert len(yt_events_2) == 2
        assert any(e["fromVersion"] is None and e["toVersion"] == "19.47.52" for e in yt_events_2)
        assert any(e["fromVersion"] == "19.47.52" and e["toVersion"] == "19.48.00" for e in yt_events_2)


def test_app_pending_in_two_consecutive_runs_stays_pending_not_stale_published():
    # Regression test: an app absent from the release both times must
    # stay "pending", not get promoted to "published"/"stale" just
    # because a previous (also-pending) entry existed to copy from.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        apps_json = tmp_path / "apps.json"
        history_json = tmp_path / "history.json"
        obtainium_json = tmp_path / "obtainium.json"

        def fake_fetch_text(url):
            return FIXTURE_CONFIG

        def fake_fetch_json_no_release(url, allow_404=False):
            if url.endswith("/releases/latest"):
                return None
            if "/releases?per_page=" in url:
                return None
            raise AssertionError(f"unexpected URL in test: {url}")

        patch = _patch_ed_targets(apps_json, history_json, obtainium_json, fake_fetch_text, fake_fetch_json_no_release)
        with patch:
            catalog1, *_ = uc.build_catalog()
            uc.write_json(uc.APPS_JSON, catalog1)
            uc.write_json(uc.HISTORY_JSON, uc.build_catalog()[1])  # noqa: keep history file present for load_previous parity

        reddit1 = next(a for a in catalog1["apps"] if a["appKey"] == "reddit")
        assert reddit1["status"] == "pending"

        with patch:
            catalog2, history2, obtainium2 = uc.build_catalog()

        reddit2 = next(a for a in catalog2["apps"] if a["appKey"] == "reddit")
        assert reddit2["status"] == "pending"
        assert reddit2["stale"] is False
        assert reddit2["version"] is None
        assert obtainium2["apps"] == [], "no app should show up in the bulk Obtainium export before anything is published"


def test_pending_app_that_gets_published_for_the_first_time_gets_a_history_entry():
    # Regression test for the exact scenario the shipped seed data hits:
    # run 1 has no release yet (every app "pending", per the seed this
    # site ships with) -> run 2 is the real pipeline's first successful
    # release. An app going pending -> published for the first time must
    # produce a history.json entry (fromVersion: null), the same as an
    # app that was never seen before at all — not "not prev" alone, since
    # a pending stub already counts as a `prev` entry.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        apps_json = tmp_path / "apps.json"
        history_json = tmp_path / "history.json"
        obtainium_json = tmp_path / "obtainium.json"

        def fake_fetch_text(url):
            return FIXTURE_CONFIG

        def fake_fetch_json_no_release(url, allow_404=False):
            if url.endswith("/releases/latest"):
                return None
            if "/releases?per_page=" in url:
                return None
            raise AssertionError(f"unexpected URL in test: {url}")

        with _patch_ed_targets(apps_json, history_json, obtainium_json, fake_fetch_text, fake_fetch_json_no_release):
            catalog1, history1, _ = uc.build_catalog()
            uc.write_json(uc.APPS_JSON, catalog1)
            uc.write_json(uc.HISTORY_JSON, history1)

        reddit1 = next(a for a in catalog1["apps"] if a["appKey"] == "reddit")
        assert reddit1["status"] == "pending"
        assert history1["entries"] == []

        run2_assets = [_asset("Reddit-2026.35.0.apk", download_count=1)]
        run2_release = _release("build-2026-09-12T10-42-41", run2_assets)

        def fake_fetch_json_first_real_run(url, allow_404=False):
            if url.endswith("/releases/latest"):
                return run2_release
            if "/releases?per_page=" in url:
                return []
            raise AssertionError(f"unexpected URL in test: {url}")

        with _patch_ed_targets(apps_json, history_json, obtainium_json, fake_fetch_text, fake_fetch_json_first_real_run):
            catalog2, history2, _ = uc.build_catalog()

        reddit2 = next(a for a in catalog2["apps"] if a["appKey"] == "reddit")
        assert reddit2["status"] == "published"
        assert reddit2["version"] == "2026.35.0"

        reddit_events = [e for e in history2["entries"] if e["appKey"] == "reddit"]
        assert len(reddit_events) == 1, f"expected exactly one history entry for reddit, got {reddit_events}"
        assert reddit_events[0]["fromVersion"] is None
        assert reddit_events[0]["toVersion"] == "2026.35.0"


def _patch_ed_targets(apps_json, history_json, obtainium_json, fetch_text_fn, fetch_json_fn):
    return _patch_ed(
        [
            (uc, "APPS_JSON", apps_json),
            (uc, "HISTORY_JSON", history_json),
            (uc, "OBTAINIUM_JSON", obtainium_json),
            (uc, "fetch_text", fetch_text_fn),
            (uc, "fetch_json", fetch_json_fn),
        ]
    )
