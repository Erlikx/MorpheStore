/*
  obtainium.js — turns a catalog app's precomputed `obtainium` config
  (built server-side in scripts/update_catalog.py) into a working
  obtainium://app/ deep link.

  Link shape follows Obtainium's own import format: a JSON object with
  id/url/author/name plus an `additionalSettings` field that is itself a
  JSON *string* (not a nested object) — that's how Obtainium's "Add App"
  screen and its own crowdsourced-config site (apps.obtainium.imranr.dev)
  both encode it. The apps.obtainium.imranr.dev/redirect helper (run by
  Obtainium's own maintainer) is used to open the obtainium:// scheme
  reliably from a plain browser link click, rather than linking to the
  custom scheme directly.
*/

const OBTAINIUM_REDIRECT_BASE = "https://apps.obtainium.imranr.dev/redirect?r=";

function obtainiumDeepLink(app) {
  const cfg = app.obtainium;
  if (!cfg) return null;
  const payload = {
    id: cfg.id,
    url: cfg.url,
    author: cfg.author,
    name: cfg.name,
    additionalSettings: JSON.stringify(cfg.additionalSettings),
  };
  const raw = "obtainium://app/" + JSON.stringify(payload);
  return OBTAINIUM_REDIRECT_BASE + encodeURIComponent(raw);
}

// The bulk multi-app import (data/obtainium.json, already in Obtainium's
// own {"apps":[...]} import shape) is served as a plain static file with
// a download link in index.html — no JS needed to produce it.
