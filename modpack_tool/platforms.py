"""Modrinth and CurseForge API access, plus CurseForge's murmur2 file fingerprint.

Batch endpoints are used throughout, so a whole pack needs one or two requests
instead of one per mod.
"""

import base64
import json
import struct
import time

import requests

from .ui import ToolError

MODRINTH = "https://api.modrinth.com/v2"
CURSEFORGE = "https://api.curseforge.com/v1"
USER_AGENT = "HaXrDEV/Modpack-Tool (+https://github.com/HaXrDEV/Modpack-Tool)"

# CurseForge's API needs a key. This is packwiz's public community key; a key
# in tool_config.yml or cf-api-key.txt replaces it.
_PACKWIZ_CF_KEY = base64.b64decode(
    "JDJhJDEwJHNBWVhqblU1N0EzSmpzcmJYM3JVdk92UWk2NHBLS3BnQ2VpbGc1TUM1UGNKL0RYTmlGWWxh"
).decode("utf-8")
_cf_key = ""

# CurseForge file releaseType values.
CF_RELEASE_TYPES = {1: "release", 2: "beta", 3: "alpha"}

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def set_curseforge_key(key):
    global _cf_key
    _cf_key = str(key or "").strip()


def _request(method, url, *, params=None, body=None, headers=None):
    """Send a request with retries for rate limits and network hiccups; None on 404."""
    for attempt in range(5):
        try:
            response = _session.request(method, url, params=params, json=body, headers=headers, timeout=60)
        except requests.RequestException as ex:
            if attempt == 4:
                raise ToolError(f"Network error talking to {url}: {ex}") from ex
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429:
            wait = response.headers.get("Retry-After") or response.headers.get("X-Ratelimit-Reset")
            time.sleep(min(max(float(wait or 2 ** attempt), 1.0), 60.0))
            continue
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ToolError(f"{method} {url} returned HTTP {response.status_code}: {response.text[:200]}")
        return response.json()
    raise ToolError(f"{url} kept rate-limiting requests; try again in a minute.")


def _chunks(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _cf_headers():
    return {"x-api-key": _cf_key or _PACKWIZ_CF_KEY, "Accept": "application/json"}


############################################################
# Modrinth

def modrinth_versions(version_ids):
    """{version id: version} for the given ids."""
    found = {}
    for chunk in _chunks(sorted({str(v) for v in version_ids if v}), 100):
        for version in _request("GET", f"{MODRINTH}/versions", params={"ids": json.dumps(chunk)}) or []:
            found[version["id"]] = version
    return found


def modrinth_projects(project_ids):
    """{project id: project} for the given ids."""
    found = {}
    for chunk in _chunks(sorted({str(p) for p in project_ids if p}), 100):
        for project in _request("GET", f"{MODRINTH}/projects", params={"ids": json.dumps(chunk)}) or []:
            found[project["id"]] = project
    return found


def modrinth_project_versions(project_id, game_versions=None, loaders=None):
    """A project's versions, newest first, optionally filtered by game version and loader."""
    params = {}
    if game_versions:
        params["game_versions"] = json.dumps(list(game_versions))
    if loaders:
        params["loaders"] = json.dumps(list(loaders))
    return _request("GET", f"{MODRINTH}/project/{project_id}/version", params=params) or []


############################################################
# CurseForge

def curseforge_files(file_ids):
    """{file id: file} for the given CurseForge file ids."""
    found = {}
    for chunk in _chunks(sorted({int(f) for f in file_ids if f}), 250):
        data = _request("POST", f"{CURSEFORGE}/mods/files", body={"fileIds": chunk}, headers=_cf_headers())
        for file in (data or {}).get("data", []):
            found[int(file["id"])] = file
    return found


def curseforge_mods(mod_ids):
    """{project id: project} for the given CurseForge project ids."""
    found = {}
    for chunk in _chunks(sorted({int(m) for m in mod_ids if m}), 250):
        data = _request("POST", f"{CURSEFORGE}/mods", body={"modIds": chunk}, headers=_cf_headers())
        for mod in (data or {}).get("data", []):
            found[int(mod["id"])] = mod
    return found


def curseforge_fingerprint_matches(fingerprints):
    """{fingerprint: (project id, file id)} for fingerprints CurseForge knows exactly."""
    found = {}
    for chunk in _chunks(sorted(set(fingerprints)), 50):
        data = _request("POST", f"{CURSEFORGE}/fingerprints", body={"fingerprints": chunk},
                        headers=_cf_headers()) or {}
        data = data.get("data") or {}
        # exactFingerprints[i] is the fingerprint that matched exactMatches[i].
        for fingerprint, match in zip(data.get("exactFingerprints", []), data.get("exactMatches", [])):
            file = match.get("file") or {}
            if file.get("id") and file.get("modId"):
                found[int(fingerprint)] = (int(file["modId"]), int(file["id"]))
    return found


def murmur2(data):
    """CurseForge's file fingerprint: murmur2 (seed 1) over the file minus whitespace bytes."""
    data = bytes(data).translate(None, b"\t\n\r ")
    m = 0x5BD1E995
    length = len(data)
    h = (1 ^ length) & 0xFFFFFFFF
    body = length - (length & 3)
    for (k,) in struct.iter_unpack("<I", data[:body]):
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> 24
        k = (k * m) & 0xFFFFFFFF
        h = ((h * m) & 0xFFFFFFFF) ^ k
    tail = data[body:]
    if len(tail) >= 3:
        h ^= tail[2] << 16
    if len(tail) >= 2:
        h ^= tail[1] << 8
    if tail:
        h ^= tail[0]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h


############################################################
# Release channels

def allowed_channels(current_channel):
    """Channels an update may land on: alpha only when the mod is already on alpha."""
    if current_channel == "alpha":
        return {"release", "beta", "alpha"}
    return {"release", "beta"}


def download(url):
    """Download a file's bytes, with the same retries as the API calls."""
    for attempt in range(4):
        try:
            response = _session.get(url, timeout=120)
            if response.status_code == 200:
                return response.content
            if response.status_code not in (429, 500, 502, 503, 504):
                raise ToolError(f"Downloading {url} failed: HTTP {response.status_code}")
        except requests.RequestException as ex:
            if attempt == 3:
                raise ToolError(f"Downloading {url} failed: {ex}") from ex
        time.sleep(2 ** attempt)
    raise ToolError(f"Downloading {url} kept failing; try again later.")
