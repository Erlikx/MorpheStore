"""
update_catalog.py
------------------

Builds this site's data/*.json from the live Erlikx/Builder-Morphe repo.

Builder-Morphe keeps exactly one active GitHub release: every successful
pipeline run creates a brand-new release, uploads every app's patched APK
to it as an asset, then deletes whatever release existed before (see
finalize_release.py / core/release.py::delete_other_releases in that repo).
That means, unlike a normal "one release per version" project:

  * there is no build history to read back from the GitHub API — the
    previous release is gone by the time we look;
  * every asset's download_count resets to 0 the moment ANY app in the
    pipeline is rebuilt, even if a given app itself did not change.

So this script does not just mirror the latest release: it also reads the
data/apps.json this script wrote last time, and uses it to keep two things
Builder-Morphe itself cannot: a running lifetime download total per app,
and a short changelog of version bumps we've personally observed. Re-runs
are therefore not idempotent by design — they accumulate state on purpose.

Network calls only ever touch public, unauthenticated-friendly endpoints
(raw.githubusercontent.com and api.github.com). A GITHUB_TOKEN is read
from the environment when present purely to raise the API rate limit
(GitHub Actions supplies one automatically); the script works without it.

Nothing here ever executes code from Builder-Morphe's repo. core/config.py
is parsed with `ast.literal_eval`, never `exec`/`import`, so a change to
that file can only ever change data, never behaviour of this script.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

SOURCE_OWNER = "Erlikx"
SOURCE_REPO = "Builder-Morphe"
SOURCE_BRANCH = "main"
SOURCE_CONFIG_PATH = "core/config.py"

OBTAINIUM_AUTHOR = SOURCE_OWNER
OBTAINIUM_URL = f"https://github.com/{SOURCE_OWNER}/{SOURCE_REPO}"

API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"

# How many of a patch source's own releases to keep in its local history.
PATCH_SOURCE_HISTORY_DEPTH = 5
# How many version-bump entries to retain per app in history.json.
HISTORY_DEPTH_PER_APP = 15
# Release bodies from third-party patch sources are untrusted text; cap
# them so one huge changelog can't balloon the catalog file.
MAX_CHANGELOG_CHARS = 4000

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
APPS_JSON = DATA_DIR / "apps.json"
HISTORY_JSON = DATA_DIR / "history.json"
OBTAINIUM_JSON = DATA_DIR / "obtainium.json"

SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class FetchError(RuntimeError):
    """Raised for any network/HTTP failure. Distinguished from a clean
    "this doesn't exist yet" (handled separately as a normal empty state)."""


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": "builder-morphe-web-catalog",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_json(url: str, *, allow_404: bool = False) -> Any:
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404 and allow_404:
            return None
        raise FetchError(f"GET {url} -> HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"GET {url} -> {e}") from e


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        raise FetchError(f"GET {url} -> {e}") from e


# --------------------------------------------------------------------------
# core/config.py parsing (ast.literal_eval only — never exec/import)
# --------------------------------------------------------------------------


def parse_apps_config(source: str) -> dict[str, Any]:
    """Pull APPS_CONFIG, DISPLAY_NAMES, PATCH_SOURCES and PROCESS_ORDER out
    of Builder-Morphe's core/config.py as plain data, ignoring the helper
    functions defined alongside them (we reimplement those two small
    functions natively below instead of running the source file)."""

    tree = ast.parse(source, filename=SOURCE_CONFIG_PATH)
    wanted = {"APPS_CONFIG", "DISPLAY_NAMES", "PATCH_SOURCES", "PROCESS_ORDER"}
    found: dict[str, Any] = {}

    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue

        for name in targets:
            if name in wanted and node.value is not None:
                found[name] = ast.literal_eval(node.value)

    missing = wanted - found.keys()
    if missing:
        raise ValueError(f"core/config.py is missing expected definitions: {sorted(missing)}")

    return found


def patch_sources_for(app_key: str, apps_config: dict) -> list[str]:
    """Mirrors core/config.py::patch_sources_for."""
    source = apps_config[app_key]["patch_source"]
    return source if isinstance(source, list) else [source]


def get_release_naming(
    app_key: str, apps_config: dict, display_names: dict, patch_sources: dict
) -> tuple[str, str | None]:
    """Mirrors core/config.py::get_release_naming — same sibling-collision
    rule Builder-Morphe itself uses to name/disambiguate release assets,
    reimplemented here (not imported) so parsing config.py can never run
    arbitrary code from that repo. Takes patch_sources explicitly (rather
    than reading a module-level global, as the original does) so this
    stays a pure function callers can test in isolation."""
    config = apps_config[app_key]
    display_name = display_names.get(app_key, config["name"])

    siblings = [k for k, c in apps_config.items() if display_names.get(k, c["name"]) == display_name]
    if len(siblings) <= 1:
        return display_name, None

    primary_source = patch_sources_for(app_key, apps_config)[0]
    owner = patch_sources[primary_source][0]
    return display_name, owner


def match_asset(file_name: str, candidates: list[tuple[str, str, str | None]]) -> tuple[str, str, str] | None:
    """Mirrors finalize_release.py::match_asset — given one release asset's
    file name, work out which app_key/display_name/version it is. Reusing
    Builder-Morphe's own matching rule (rather than inventing a new one)
    means this stays correct even for display names that collide only
    after lower-casing, exactly as the uploader treats them."""
    if not file_name.lower().endswith(".apk"):
        return None
    if file_name.lower().startswith("microg"):
        return None

    base = file_name[:-4]

    for app_key, display_name, tag in candidates:
        prefix = display_name + "-"
        if not base.lower().startswith(prefix.lower()):
            continue

        remainder = base[len(prefix):]

        if tag:
            suffix = f"-{tag}"
            if not remainder.lower().endswith(suffix.lower()):
                continue
            remainder = remainder[: -len(suffix)]

        return app_key, display_name, remainder

    return None


def build_asset_candidates(
    apps_config: dict, display_names: dict, patch_sources: dict
) -> list[tuple[str, str, str | None]]:
    candidates = []
    for app_key in apps_config:
        display_name, tag = get_release_naming(app_key, apps_config, display_names, patch_sources)
        candidates.append((app_key, display_name, tag))
    candidates.sort(key=lambda c: -len(c[1]))
    return candidates


# --------------------------------------------------------------------------
# Obtainium
# --------------------------------------------------------------------------


def build_obtainium_config(*, pkg: str, display_name: str, owner_suffix: str | None) -> dict:
    """Shape matches Obtainium's `obtainium://app/<json>` import format:
    top-level id/url/author/name + a JSON-*string* of additionalSettings.
    apkFilterRegEx has to pick this app's asset out of Builder-Morphe's one
    shared release, so it's anchored on the exact same prefix Builder-
    Morphe's own uploader uses (see match_asset above) rather than a
    generic arch filter. invertAPKFilter/autoApkFilterByArch are set
    explicitly (off / on) instead of left to Obtainium's defaults, and
    appName is set so the app doesn't fall back to a generic repo-derived
    name — the two things this project asked to make sure were covered."""
    pattern = f"^{re.escape(display_name)}-.*"
    if owner_suffix:
        pattern += f"-{re.escape(owner_suffix)}"
    pattern += r"\.apk$"

    friendly_name = f"{display_name} (Builder-Morphe)" if not owner_suffix else f"{display_name} (Builder-Morphe · {owner_suffix})"

    return {
        "id": pkg,
        "url": OBTAINIUM_URL,
        "author": OBTAINIUM_AUTHOR,
        "name": friendly_name,
        "additionalSettings": {
            "apkFilterRegEx": pattern,
            "invertAPKFilter": False,
            "autoApkFilterByArch": True,
            "appName": friendly_name,
        },
    }


# --------------------------------------------------------------------------
# Data classes for the bits we persist between runs
# --------------------------------------------------------------------------


@dataclass
class DownloadState:
    cumulative_baseline: int = 0
    last_release_tag: str | None = None
    downloads_this_release: int = 0

    @property
    def downloads_total(self) -> int:
        return self.cumulative_baseline + self.downloads_this_release


def load_previous() -> dict[str, Any] | None:
    if not APPS_JSON.exists():
        return None
    try:
        return json.loads(APPS_JSON.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def previous_app_index(previous: dict[str, Any] | None) -> dict[str, dict]:
    if not previous:
        return {}
    return {app["appKey"]: app for app in previous.get("apps", [])}


# --------------------------------------------------------------------------
# Patch source changelog fetching
# --------------------------------------------------------------------------


def fetch_patch_source(owner: str, repo: str, label: str) -> dict[str, Any] | None:
    url = f"{API_ROOT}/repos/{owner}/{repo}/releases?per_page={PATCH_SOURCE_HISTORY_DEPTH}"
    try:
        releases = fetch_json(url, allow_404=True)
    except FetchError as e:
        print(f"  ! could not fetch releases for {owner}/{repo}: {e}", file=sys.stderr)
        return None

    if not releases:
        return None

    def _trim(body: str | None) -> str:
        body = body or ""
        if len(body) > MAX_CHANGELOG_CHARS:
            return body[:MAX_CHANGELOG_CHARS].rstrip() + "\n\n…"
        return body

    history = [
        {
            "tag": r.get("tag_name") or "",
            "publishedAt": r.get("published_at"),
            "url": r.get("html_url"),
            "changelog": _trim(r.get("body")),
            "prerelease": bool(r.get("prerelease")),
        }
        for r in releases
    ]

    latest = history[0]
    return {
        "owner": owner,
        "repo": repo,
        "label": label,
        "repoUrl": f"https://github.com/{owner}/{repo}",
        "latestTag": latest["tag"],
        "latestPublishedAt": latest["publishedAt"],
        "latestUrl": latest["url"],
        "changelog": latest["changelog"],
        "history": history[1:],
    }


# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_catalog() -> tuple[dict, dict, dict]:
    print(f"Fetching {SOURCE_CONFIG_PATH} from {SOURCE_OWNER}/{SOURCE_REPO}@{SOURCE_BRANCH}...")
    config_source = fetch_text(f"{RAW_ROOT}/{SOURCE_OWNER}/{SOURCE_REPO}/{SOURCE_BRANCH}/{SOURCE_CONFIG_PATH}")
    parsed = parse_apps_config(config_source)
    apps_config: dict = parsed["APPS_CONFIG"]
    display_names: dict = parsed["DISPLAY_NAMES"]
    patch_sources: dict = parsed["PATCH_SOURCES"]
    process_order: list = parsed["PROCESS_ORDER"]

    print(f"  found {len(apps_config)} apps, {len(patch_sources)} patch sources")

    print(f"Fetching latest release for {SOURCE_OWNER}/{SOURCE_REPO}...")
    release = fetch_json(f"{API_ROOT}/repos/{SOURCE_OWNER}/{SOURCE_REPO}/releases/latest", allow_404=True)
    has_release = release is not None
    assets: list[dict] = release.get("assets", []) if has_release else []
    if has_release:
        print(f"  latest release: {release.get('tag_name')} ({len(assets)} assets)")
    else:
        print("  no release published yet")

    candidates = build_asset_candidates(apps_config, display_names, patch_sources)

    matched_by_app: dict[str, dict] = {}
    microg_asset: dict | None = None
    for asset in assets:
        name = asset.get("name", "")
        if name.lower() == "microg.apk":
            microg_asset = asset
            continue
        result = match_asset(name, candidates)
        if result:
            app_key, disp_name, version = result
            matched_by_app[app_key] = {"asset": asset, "display_name": disp_name, "version": version}

    previous = load_previous()
    prev_apps = previous_app_index(previous)
    generated_at = now_iso()
    release_tag = release.get("tag_name") if has_release else None

    apps_out: list[dict] = []
    history_events: list[dict] = []

    for app_key in process_order:
        if app_key not in apps_config:
            continue
        config = apps_config[app_key]
        display_name, owner_suffix = get_release_naming(app_key, apps_config, display_names, patch_sources)
        sources = patch_sources_for(app_key, apps_config)
        prev = prev_apps.get(app_key)
        match = matched_by_app.get(app_key)

        if match:
            asset = match["asset"]
            current_count = int(asset.get("download_count") or 0)
            prev_dl = (prev or {}).get("downloads") or {}
            same_release = prev_dl.get("lastReleaseTag") == release_tag
            baseline = int(prev_dl.get("cumulativeBaseline") or 0)
            if prev_dl and not same_release:
                baseline += int(prev_dl.get("downloadsThisRelease") or 0)

            downloads = {
                "cumulativeBaseline": baseline,
                "lastReleaseTag": release_tag,
                "downloadsThisRelease": current_count,
                "total": baseline + current_count,
            }

            version = match["version"]
            prev_version = (prev or {}).get("version")
            if prev_version and prev_version != version:
                history_events.append(
                    {
                        "appKey": app_key,
                        "displayName": display_name,
                        "fromVersion": prev_version,
                        "toVersion": version,
                        "at": generated_at,
                        "releaseTag": release_tag,
                    }
                )
            elif not prev:
                history_events.append(
                    {
                        "appKey": app_key,
                        "displayName": display_name,
                        "fromVersion": None,
                        "toVersion": version,
                        "at": generated_at,
                        "releaseTag": release_tag,
                    }
                )

            entry = {
                "appKey": app_key,
                "displayName": display_name,
                "pkg": config["pkg"],
                "icon": config["icon"],
                "arch": config.get("arch"),
                "status": "published",
                "stale": False,
                "version": version,
                "assetName": asset.get("name"),
                "downloadUrl": asset.get("browser_download_url"),
                "size": asset.get("size"),
                "publishedAt": release.get("published_at") if has_release else None,
                "downloads": downloads,
                "patchSources": sources,
                "obtainium": build_obtainium_config(pkg=config["pkg"], display_name=display_name, owner_suffix=owner_suffix),
            }
        elif prev and prev.get("status") == "published":
            # Was published in an earlier run but missing from this run's
            # release (failed, skipped, or removed from the matrix this
            # time) — keep showing the last known-good data instead of
            # letting a transient failure blank the card. A prev entry
            # that was only ever "pending" falls through to the plain
            # pending branch below instead — there is no known-good
            # version to fall back to for those.
            entry = dict(prev)
            entry["status"] = "published"
            entry["stale"] = True
            entry["patchSources"] = sources
            entry["icon"] = config["icon"]
            entry["displayName"] = display_name
            entry["obtainium"] = build_obtainium_config(pkg=config["pkg"], display_name=display_name, owner_suffix=owner_suffix)
        else:
            entry = {
                "appKey": app_key,
                "displayName": display_name,
                "pkg": config["pkg"],
                "icon": config["icon"],
                "arch": config.get("arch"),
                "status": "pending",
                "stale": False,
                "version": None,
                "assetName": None,
                "downloadUrl": None,
                "size": None,
                "publishedAt": None,
                "downloads": {"cumulativeBaseline": 0, "lastReleaseTag": None, "downloadsThisRelease": 0, "total": 0},
                "patchSources": sources,
                "obtainium": build_obtainium_config(pkg=config["pkg"], display_name=display_name, owner_suffix=owner_suffix),
            }

        apps_out.append(entry)

    print("Fetching patch source changelogs...")
    patch_source_out: dict[str, Any] = {}
    for key, (owner, repo, label) in patch_sources.items():
        info = fetch_patch_source(owner, repo, label)
        if info:
            patch_source_out[key] = info
            print(f"  {key}: {info['latestTag']}")

    microg_out = None
    if microg_asset:
        microg_out = {
            "downloadUrl": microg_asset.get("browser_download_url"),
            "size": microg_asset.get("size"),
        }

    total_downloads = sum(a["downloads"]["total"] for a in apps_out)
    published_count = sum(1 for a in apps_out if a["status"] == "published" and not a["stale"])

    catalog = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "source": {
            "owner": SOURCE_OWNER,
            "repo": SOURCE_REPO,
            "repoUrl": OBTAINIUM_URL,
            "hasRelease": has_release,
            "releaseTag": release_tag,
            "releaseName": release.get("name") if has_release else None,
            "releaseUrl": release.get("html_url") if has_release else None,
            "publishedAt": release.get("published_at") if has_release else None,
        },
        "stats": {
            "totalApps": len(apps_out),
            "publishedApps": published_count,
            "totalDownloads": total_downloads,
        },
        "microg": microg_out,
        "patchSources": patch_source_out,
        "apps": apps_out,
    }

    # ---- history.json: append this run's version-bump events, capped ----
    prev_history = {"entries": []}
    if HISTORY_JSON.exists():
        try:
            prev_history = json.loads(HISTORY_JSON.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    by_app: dict[str, list[dict]] = {}
    for ev in prev_history.get("entries", []) + history_events:
        by_app.setdefault(ev["appKey"], []).append(ev)

    trimmed: list[dict] = []
    for app_key, events in by_app.items():
        events.sort(key=lambda e: e["at"])
        trimmed.extend(events[-HISTORY_DEPTH_PER_APP:])
    trimmed.sort(key=lambda e: e["at"], reverse=True)

    history_out = {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "entries": trimmed}

    # ---- obtainium.json: bulk import file for every published app ----
    obtainium_out = {
        "apps": [
            {**app["obtainium"], "additionalSettings": json.dumps(app["obtainium"]["additionalSettings"])}
            for app in apps_out
            if app["status"] == "published"
        ]
    }

    return catalog, history_out, obtainium_out


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    try:
        catalog, history_out, obtainium_out = build_catalog()
    except FetchError as e:
        # Network/API failure: leave whatever data/*.json already exists in
        # place rather than overwrite good data with a failed run.
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    except (ValueError, SyntaxError) as e:
        print(f"FATAL: could not parse Builder-Morphe's config.py: {e}", file=sys.stderr)
        return 1

    write_json(APPS_JSON, catalog)
    write_json(HISTORY_JSON, history_out)
    write_json(OBTAINIUM_JSON, obtainium_out)

    print(
        f"Wrote {APPS_JSON.name}, {HISTORY_JSON.name}, {OBTAINIUM_JSON.name} — "
        f"{catalog['stats']['publishedApps']}/{catalog['stats']['totalApps']} apps published, "
        f"{catalog['stats']['totalDownloads']} downloads tracked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
