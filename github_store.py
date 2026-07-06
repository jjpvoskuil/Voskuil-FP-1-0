"""
github_store.py — generic persistent JSON storage in the GitHub repo via
the Contents API.

Streamlit Cloud's filesystem resets on every reboot/redeploy, so anything
that needs to survive that (the punch list, and now the Market Screener's
persistent scan cache) has to live in GitHub instead of local disk. This
module is a generic, reusable version of the read/write-with-SHA pattern
originally built for pages/6_Punch_List.py — any page needing "save this
JSON blob to the repo and read it back later" can use it instead of
writing a third copy of the GitHub API calls.
"""

import base64
import json
import streamlit as st
import requests


def github_get_json(path: str):
    """
    Fetch and parse a JSON file from the repo.
    Returns (data, sha, error):
      - data/sha are None and error is None if the file doesn't exist yet (404)
      - data/sha are None and error is a message on failure
      - on success, data is the parsed JSON, sha is the file's current blob SHA
    """
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo  = st.secrets.get("GITHUB_REPO", "jjpvoskuil/Voskuil-FP-1-0")
    if not token:
        return None, None, "GITHUB_TOKEN not found in Streamlit secrets."
    try:
        api   = f"https://api.github.com/repos/{repo}/contents/{path}"
        heads = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        r = requests.get(api, headers=heads, timeout=20)
        if r.status_code == 404:
            return None, None, None
        if r.status_code != 200:
            return None, None, f"GET failed: {r.status_code} {r.json().get('message', r.text)[:150]}"
        body    = r.json()
        sha     = body.get("sha")
        content = base64.b64decode(body.get("content", "")).decode()
        return json.loads(content), sha, None
    except Exception as e:
        return None, None, f"GET exception: {e}"


def github_put_json(path: str, data, commit_message: str, size_warn_mb: float = 20.0):
    """
    Write a JSON-serializable object to the repo, creating or updating the
    file as needed. Fetches the current SHA first so updates don't clobber
    a version pushed since we last read (same pattern as the punch list).

    Returns (success: bool, message: str).
    """
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo  = st.secrets.get("GITHUB_REPO", "jjpvoskuil/Voskuil-FP-1-0")
    if not token:
        return False, "GITHUB_TOKEN not found in Streamlit secrets."
    try:
        content_str = json.dumps(data)
        size_mb = len(content_str.encode()) / 1e6
        if size_mb > size_warn_mb:
            return False, (f"Payload is {size_mb:.1f}MB — larger than the {size_warn_mb:.0f}MB soft "
                            f"limit for a single GitHub Contents API write. Consider narrowing the "
                            f"scan (fewer tickers, stricter filters) before saving.")

        api   = f"https://api.github.com/repos/{repo}/contents/{path}"
        heads = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

        r = requests.get(api, headers=heads, timeout=20)
        if r.status_code not in (200, 404):
            return False, f"GitHub GET failed: {r.status_code} {r.json().get('message', r.text)[:150]}"
        sha = r.json().get("sha") if r.status_code == 200 else None

        payload = {
            "message": commit_message,
            "content": base64.b64encode(content_str.encode()).decode(),
        }
        if sha:
            payload["sha"] = sha

        put_r = requests.put(api, headers=heads, json=payload, timeout=60)
        if put_r.status_code not in (200, 201):
            return False, f"GitHub PUSH failed: {put_r.status_code} {put_r.json().get('message', put_r.text)[:200]}"
        return True, "Synced"
    except Exception as e:
        return False, f"PUSH exception: {e}"
