#!/usr/bin/env python3
"""
Mirror a Google Drive folder (recursively) into a local directory.

Usage:
    python3 tools/fetch_drive.py <drive_folder_id> <dest_dir>

Authenticates with Application Default Credentials, which the
google-github-actions/auth step provides via Workload Identity Federation
(no key file). Google Docs/Sheets/shortcuts are skipped; only real files
(photos, videos, PDFs, ...) are downloaded.
"""
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import google.auth
from google.auth.transport.requests import AuthorizedSession

API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"

creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
session = AuthorizedSession(creds)


def list_children(folder_id):
    token = None
    while True:
        r = session.get(API, params={
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, size)",
            "pageSize": 1000,
            "pageToken": token,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        })
        r.raise_for_status()
        data = r.json()
        yield from data.get("files", [])
        token = data.get("nextPageToken")
        if not token:
            return


def walk(folder_id, rel=Path()):
    for f in list_children(folder_id):
        if f["name"].startswith("."):
            continue
        if f["mimeType"] == FOLDER:
            yield from walk(f["id"], rel / f["name"])
        elif not f["mimeType"].startswith("application/vnd.google-apps"):
            yield rel / f["name"], f


def download(dest, item):
    rel, f = item
    out = dest / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    with session.get(f"{API}/{f['id']}", params={"alt": "media", "supportsAllDrives": True},
                     stream=True) as r:
        r.raise_for_status()
        with open(out, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    print(f"  got {rel}")


def main(folder_id, dest):
    dest = Path(dest)
    items = list(walk(folder_id))
    print(f"{len(items)} files in Drive folder")
    if not items:
        sys.exit("No files found: is the folder shared with the service account?")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda it: download(dest, it), items))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
