"""Tournament icon (start.gg "profile" image) index: data/startgg/<Region>/tournament_images.jsonl.

One line per tournament, sorted by tournament_id:
    {"tournament_id": 949713, "url": "https://images.start.gg/...png", "width": 400, "height": 400}
"url" is null when the tournament has no icon (so "checked, none" differs from "not checked yet").

Only the URL is stored here; the image files themselves are not part of this repository.
The URL is kept exactly as start.gg returns it (older images carry an ?ehk=... suffix; the file
served is the same original either way).

Written by download.py for every tournament in the listing window (so a changed icon is picked up
while the tournament is still in the window) and by manual/backfill_tournament_images.py for the past.
"""
import json
import os

FILE_NAME = "tournament_images.jsonl"


def images_path_for(tournament_file_path):
    """The index lives next to tournaments.jsonl."""
    return os.path.join(os.path.dirname(os.path.abspath(tournament_file_path)), FILE_NAME)


def load(path):
    """{tournament_id(int): {"url", "width", "height"}}. Missing file = empty."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out[int(rec["tournament_id"])] = {k: rec.get(k) for k in ("url", "width", "height")}
    return out


def from_api(images):
    """start.gg `images(type: "profile") { url width height }` → record (first image, or url null)."""
    for img in images or []:
        if img and img.get("url"):
            return {"url": img["url"], "width": img.get("width"), "height": img.get("height")}
    return {"url": None, "width": None, "height": None}


def update(index, tournament_id, images):
    """Set the record for one tournament. Returns True when it changed."""
    rec = from_api(images)
    tid = int(tournament_id)
    if index.get(tid) == rec:
        return False
    index[tid] = rec
    return True


def save(index, path):
    """Full rewrite, sorted by tournament_id, atomic (temp file + os.replace)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for tid in sorted(index):
            f.write(json.dumps({"tournament_id": tid, **index[tid]}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
