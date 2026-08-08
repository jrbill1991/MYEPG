#!/usr/bin/env python3
import gzip
import hashlib
import os
import re
import sqlite3
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

BASE = "https://epgshare01.online/epgshare01/"

# Requested feeds:
# Brazil (BR1 + BR2), USA, US Locals, US Sports, UK, Canada,
# Portugal, Germany, France.
SOURCES = [
    "epg_ripper_BR1.xml.gz",
    "epg_ripper_BR2.xml.gz",
    "epg_ripper_US2.xml.gz",
    "epg_ripper_US_LOCALS1.xml.gz",
    "epg_ripper_US_SPORTS1.xml.gz",
    "epg_ripper_UK1.xml.gz",
    "epg_ripper_CA2.xml.gz",
    "epg_ripper_PT1.xml.gz",
    "epg_ripper_DE1.xml.gz",
    "epg_ripper_FR1.xml.gz",
]

OUT = Path(os.environ.get("EPG_OUTPUT", "guide.xml.gz"))
WORK = Path(os.environ.get("EPG_WORK", ".epg-work"))
WORK.mkdir(parents=True, exist_ok=True)

# XMLTV commonly uses YYYYMMDDhhmmss +ZZZZ.
# We preserve shorter valid timestamps unchanged, but normalize 14-digit
# wall-clock overflow such as 00:60 -> 01:00.
TS_RE = re.compile(r"^(\d{4,14})(?:\s*([+-]\d{4}))?$")

def download(url: str, dest: Path) -> None:
    print(f"Downloading {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "EPGShare-Clean-Merger/1.0 (+XMLTV personal use)"}
    )
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

def valid_tz(tz: str | None) -> bool:
    if not tz:
        return True
    # XMLTV offsets: ±HHMM. Real-world offsets stay comfortably inside this.
    try:
        hh = int(tz[1:3])
        mm = int(tz[3:5])
    except Exception:
        return False
    return tz[0] in "+-" and 0 <= hh <= 23 and 0 <= mm <= 59

def clean_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    m = TS_RE.match(value)
    if not m:
        return None

    digits, tz = m.groups()
    if not valid_tz(tz):
        return None

    # XMLTV permits reduced precision. For those, don't invent missing fields;
    # just do basic range checks where possible.
    if len(digits) != 14:
        try:
            if len(digits) >= 4:
                int(digits[0:4])
            if len(digits) >= 6 and not (1 <= int(digits[4:6]) <= 12):
                return None
            if len(digits) >= 8 and not (1 <= int(digits[6:8]) <= 31):
                return None
            if len(digits) >= 10 and not (0 <= int(digits[8:10]) <= 23):
                return None
            if len(digits) >= 12 and not (0 <= int(digits[10:12]) <= 59):
                return None
            if len(digits) >= 14 and not (0 <= int(digits[12:14]) <= 59):
                return None
        except ValueError:
            return None
        return digits + (f" {tz}" if tz else "")

    try:
        y = int(digits[0:4])
        mo = int(digits[4:6])
        d = int(digits[6:8])
        hh = int(digits[8:10])
        mm = int(digits[10:12])
        ss = int(digits[12:14])

        # Month/day must identify a real base date.
        base = datetime(y, mo, d)

        # Normalize clock overflow. Example:
        # 20260807006000 +0000 -> 20260807010000 +0000
        normalized = base + timedelta(hours=hh, minutes=mm, seconds=ss)
        cleaned = normalized.strftime("%Y%m%d%H%M%S")
        return cleaned + (f" {tz}" if tz else "")
    except (ValueError, OverflowError):
        return None

def iter_tag(gz_path: Path, wanted: str):
    with gzip.open(gz_path, "rb") as f:
        inside_target = 0

        for event, elem in ET.iterparse(f, events=("start", "end")):
            local = elem.tag.rsplit("}", 1)[-1]

            if event == "start":
                if inside_target:
                    inside_target += 1
                elif local == wanted:
                    inside_target = 1
                continue

            if inside_target:
                inside_target -= 1
                if inside_target == 0:
                    yield elem
            else:
                elem.clear()

def serialize(elem: ET.Element) -> bytes:
    return ET.tostring(elem, encoding="utf-8", short_empty_elements=True)

def main():
    local_files = []
    for name in SOURCES:
        p = WORK / name
        download(BASE + name, p)
        local_files.append(p)

    channel_ids = set()
    channels = []
    print("Collecting channels...")
    for p in local_files:
        for ch in iter_tag(p, "channel"):
            cid = ch.get("id")
            if cid and cid not in channel_ids:
                channel_ids.add(cid)
                channels.append(serialize(ch))
            ch.clear()

    print(f"Unique channels: {len(channel_ids):,}")

    # Disk-backed exact de-duplication keeps RAM use reasonable even with
    # the large US_LOCALS feed.
    db_path = WORK / "dedupe.sqlite3"
    if db_path.exists():
        db_path.unlink()
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA temp_store=FILE")
    db.execute("CREATE TABLE seen (h BLOB PRIMARY KEY) WITHOUT ROWID")

    kept = 0
    repaired = 0
    dropped_bad_time = 0
    dropped_unknown_channel = 0
    duplicates = 0

    print(f"Writing {OUT}...")
    with gzip.open(OUT, "wb", compresslevel=6) as out:
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write(b'<tv generator-info-name="EPGShare Clean Merger">\n')

        for raw in channels:
            out.write(raw)
            out.write(b"\n")

        for p in local_files:
            print(f"Processing programmes: {p.name}")
            for prog in iter_tag(p, "programme"):
                cid = prog.get("channel")
                if not cid or cid not in channel_ids:
                    dropped_unknown_channel += 1
                    prog.clear()
                    continue

                old_start = prog.get("start")
                old_stop = prog.get("stop")
                new_start = clean_timestamp(old_start)
                new_stop = clean_timestamp(old_stop) if old_stop else None

                if new_start is None or (old_stop is not None and new_stop is None):
                    dropped_bad_time += 1
                    prog.clear()
                    continue

                if new_start != old_start:
                    repaired += 1
                    prog.set("start", new_start)
                if old_stop is not None and new_stop != old_stop:
                    repaired += 1
                    prog.set("stop", new_stop)

                title = ""
                for child in list(prog):
                    if child.tag.rsplit("}", 1)[-1] == "title":
                        title = child.text or ""
                        break

                key_text = "\0".join([
                    cid,
                    prog.get("start", ""),
                    prog.get("stop", ""),
                    title,
                ])
                h = hashlib.sha1(key_text.encode("utf-8", "replace")).digest()
                cur = db.execute("INSERT OR IGNORE INTO seen(h) VALUES (?)", (h,))
                if cur.rowcount == 0:
                    duplicates += 1
                    prog.clear()
                    continue

                out.write(serialize(prog))
                out.write(b"\n")
                kept += 1
                prog.clear()

        out.write(b"</tv>\n")

    db.commit()
    db.close()

    print("Done.")
    print(f"Programmes kept: {kept:,}")
    print(f"Timestamps repaired: {repaired:,}")
    print(f"Programmes dropped for bad time: {dropped_bad_time:,}")
    print(f"Programmes dropped for unknown channel: {dropped_unknown_channel:,}")
    print(f"Exact duplicate programmes removed: {duplicates:,}")
    print(f"Output size: {OUT.stat().st_size / (1024*1024):.1f} MiB")

    # GitHub rejects normal git blobs >=100 MiB. Fail explicitly so a user
    # notices instead of silently publishing nothing.
    if OUT.stat().st_size >= 100 * 1024 * 1024:
        print("ERROR: output is >=100 MiB, too large for a normal GitHub file.", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
