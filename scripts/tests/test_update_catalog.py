import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from update_catalog import (  # noqa: E402
    build_asset_candidates,
    build_obtainium_config,
    get_release_naming,
    match_asset,
    parse_apps_config,
    patch_sources_for,
)

# A trimmed-down but structurally real excerpt of Builder-Morphe's
# core/config.py, used so parse_apps_config is tested against the same
# shape it will see in production, not a hand-simplified stand-in.
SAMPLE_CONFIG_SOURCE = '''
from typing import TypedDict


class _AppConfigRequired(TypedDict):
    pkg: str
    name: str
    patch_source: str | list[str]
    arch: str
    icon: str


class AppConfig(_AppConfigRequired, total=False):
    exclude: list[str]
    enable: list[str]
    force_version: str


DISPLAY_NAMES: dict[str, str] = {
    "youtube": "YouTube",
    "twitter": "Twitter",
    "reddit": "Reddit",
    "reddit-adobo": "Reddit-Adobo",
}

APKMIRROR_APPS: list[str] = ["youtube"]

APPS_CONFIG: dict[str, AppConfig] = {
    "youtube": {
        "pkg": "com.google.android.youtube",
        "name": "youtube",
        "patch_source": "morphe",
        "arch": "arm64-v8a",
        "icon": "https://cdn.simpleicons.org/youtube/FF0000",
        "exclude": [],
    },
    "twitter": {
        "pkg": "com.twitter.android",
        "name": "twitter",
        "patch_source": "piko",
        "arch": "arm64-v8a",
        "icon": "https://cdn.simpleicons.org/x/000000",
    },
    "twitter-x": {
        "pkg": "com.twitter.android",
        "name": "twitter",
        "patch_source": ["piko-newx", "morphe"],
        "arch": "arm64-v8a",
        "icon": "https://cdn.simpleicons.org/x/000000",
    },
    "reddit": {
        "pkg": "com.reddit.frontpage",
        "name": "reddit",
        "patch_source": "morphe",
        "arch": "arm64-v8a",
        "icon": "https://cdn.simpleicons.org/reddit/FF4500",
    },
    "reddit-adobo": {
        "pkg": "com.reddit.frontpage",
        "name": "reddit",
        "patch_source": "adobo",
        "arch": "arm64-v8a",
        "icon": "https://cdn.simpleicons.org/reddit/FF4500",
    },
}

PROCESS_ORDER: list[str] = ["youtube", "twitter", "twitter-x", "reddit", "reddit-adobo"]

PATCH_SOURCES: dict[str, tuple[str, str, str]] = {
    "morphe": ("MorpheApp", "morphe-patches", "\U0001F7E2 Morphe"),
    "piko": ("crimera", "piko", "\u2716\ufe0f Piko"),
    "piko-newx": ("crimera", "piko-newx", "\U0001F195 Piko NewX"),
    "adobo": ("jkennethcarino", "adobo", "\U0001F958 Adobo"),
}


def patch_sources_for(app_key):
    source = APPS_CONFIG[app_key]["patch_source"]
    return source if isinstance(source, list) else [source]
'''


def _parsed():
    return parse_apps_config(SAMPLE_CONFIG_SOURCE)


def test_parse_apps_config_extracts_all_four_names():
    parsed = _parsed()
    assert set(parsed.keys()) == {"APPS_CONFIG", "DISPLAY_NAMES", "PATCH_SOURCES", "PROCESS_ORDER"}
    assert parsed["APPS_CONFIG"]["youtube"]["pkg"] == "com.google.android.youtube"
    assert parsed["PATCH_SOURCES"]["morphe"] == ("MorpheApp", "morphe-patches", "\U0001F7E2 Morphe")
    assert parsed["PROCESS_ORDER"][0] == "youtube"


def test_parse_apps_config_never_executes_the_helper_function():
    # SAMPLE_CONFIG_SOURCE defines its own patch_sources_for(); if
    # parse_apps_config executed the module it would clobber the one
    # imported above. Calling the imported version here proves ast-based
    # parsing never ran the file as code.
    parsed = _parsed()
    assert patch_sources_for("twitter-x", parsed["APPS_CONFIG"]) == ["piko-newx", "morphe"]


def test_get_release_naming_no_collision():
    parsed = _parsed()
    name, tag = get_release_naming("youtube", parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert name == "YouTube"
    assert tag is None


def test_get_release_naming_reddit_and_reddit_adobo_have_distinct_display_names():
    # DISPLAY_NAMES gives these their own distinct names ("Reddit" vs.
    # "Reddit-Adobo"), so — unlike the synthetic collision test below —
    # neither needs an owner-suffix to stay unambiguous.
    parsed = _parsed()
    name, tag = get_release_naming("reddit", parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert (name, tag) == ("Reddit", None)
    name2, tag2 = get_release_naming(
        "reddit-adobo", parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"]
    )
    assert (name2, tag2) == ("Reddit-Adobo", None)


def test_get_release_naming_disambiguates_a_genuine_display_name_collision():
    # Deliberately construct two app_keys that DO resolve to the same
    # display name (unlike any pair in the real config today), to test
    # the owner-suffix disambiguation path in isolation.
    apps_config = {
        "clone-a": {"pkg": "com.example.a", "name": "clone-a", "patch_source": "morphe", "arch": "arm64-v8a", "icon": ""},
        "clone-b": {"pkg": "com.example.b", "name": "clone-b", "patch_source": "piko", "arch": "arm64-v8a", "icon": ""},
    }
    display_names = {"clone-a": "Clone App", "clone-b": "Clone App"}
    patch_sources = {
        "morphe": ("MorpheApp", "morphe-patches", "Morphe"),
        "piko": ("crimera", "piko", "Piko"),
    }
    name_a, tag_a = get_release_naming("clone-a", apps_config, display_names, patch_sources)
    name_b, tag_b = get_release_naming("clone-b", apps_config, display_names, patch_sources)
    assert (name_a, tag_a) == ("Clone App", "MorpheApp")
    assert (name_b, tag_b) == ("Clone App", "crimera")


def test_get_release_naming_twitter_pair_does_not_collide():
    # "twitter" has a DISPLAY_NAMES entry ("Twitter"); "twitter-x" does
    # not, so it falls back to config["name"] = "twitter" (lowercase).
    # "Twitter" != "twitter" as plain strings, so today these do NOT
    # register as siblings. This mirrors Builder-Morphe's own config.py
    # exactly (including this latent case-sensitivity quirk) rather than
    # a corrected/idealized version, since the catalog has to match
    # whatever Builder-Morphe actually uploads.
    parsed = _parsed()
    name, tag = get_release_naming("twitter", parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert (name, tag) == ("Twitter", None)
    name2, tag2 = get_release_naming(
        "twitter-x", parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"]
    )
    assert (name2, tag2) == ("twitter", None)


def test_match_asset_simple_app():
    parsed = _parsed()
    candidates = build_asset_candidates(parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert match_asset("YouTube-19.35.36.apk", candidates) == ("youtube", "YouTube", "19.35.36")


def test_match_asset_prefix_collision_resolved_by_longest_match_first():
    parsed = _parsed()
    candidates = build_asset_candidates(parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert match_asset("Reddit-Adobo-2024.15.0.apk", candidates) == (
        "reddit-adobo",
        "Reddit-Adobo",
        "2024.15.0",
    )


def test_match_asset_plain_reddit_still_resolves_correctly():
    parsed = _parsed()
    candidates = build_asset_candidates(parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert match_asset("Reddit-2024.15.0.apk", candidates) == ("reddit", "Reddit", "2024.15.0")


def test_match_asset_ignores_microg():
    parsed = _parsed()
    candidates = build_asset_candidates(parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert match_asset("MicroG.apk", candidates) is None


def test_match_asset_unknown_app_returns_none():
    parsed = _parsed()
    candidates = build_asset_candidates(parsed["APPS_CONFIG"], parsed["DISPLAY_NAMES"], parsed["PATCH_SOURCES"])
    assert match_asset("SomeRandomApp-1.0.apk", candidates) is None


def test_build_obtainium_config_shape_and_regex():
    cfg = build_obtainium_config(pkg="com.google.android.youtube", display_name="YouTube", owner_suffix=None)
    assert cfg["id"] == "com.google.android.youtube"
    assert cfg["author"] == "Erlikx"
    assert cfg["url"] == "https://github.com/Erlikx/Builder-Morphe"
    assert cfg["name"] == "YouTube (Builder-Morphe)"

    settings = cfg["additionalSettings"]
    assert settings["invertAPKFilter"] is False
    assert settings["autoApkFilterByArch"] is True
    assert settings["appName"] == "YouTube (Builder-Morphe)"
    assert settings["apkFilterRegEx"] == r"^YouTube-.*\.apk$"

    # additionalSettings must survive being serialised as a JSON *string*
    # (that's the format obtainium://app/ expects), not a nested object.
    outer = {**cfg, "additionalSettings": json.dumps(settings)}
    reparsed = json.loads(outer["additionalSettings"])
    assert reparsed == settings


def test_build_obtainium_config_with_owner_suffix_adjusts_regex_and_name():
    cfg = build_obtainium_config(pkg="com.reddit.frontpage", display_name="reddit", owner_suffix="MorpheApp")
    assert cfg["additionalSettings"]["apkFilterRegEx"] == r"^reddit-.*-MorpheApp\.apk$"
    assert cfg["name"] == "reddit (Builder-Morphe · MorpheApp)"


def test_build_obtainium_config_escapes_regex_metacharacters_in_name():
    # Defensive: a display name with regex-special characters must not
    # break the generated filter (none of Builder-Morphe's current
    # DISPLAY_NAMES have any, but this should hold regardless).
    cfg = build_obtainium_config(pkg="com.example.app", display_name="1.1.1.1", owner_suffix=None)
    assert cfg["additionalSettings"]["apkFilterRegEx"] == r"^1\.1\.1\.1-.*\.apk$"
