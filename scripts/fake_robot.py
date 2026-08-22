#!/usr/bin/env python3
"""Post a meeting to the transcript endpoint the way a robot would.

The synthetic replay driver emits on a tidy schedule. A real transcriber does
not: it emits a burst while someone talks over someone else, then nothing for
half a minute while people think. This feeds the same endpoint the robot will
use, with that irregularity, so the interval-based extraction is exercised the
way it will be in the room.

    python scripts/fake_robot.py app/modules/listen/data/ticketing_meeting.jsonl
    python scripts/fake_robot.py <script> --speed 4 --jitter 0.4 --silence 3
    python scripts/fake_robot.py --interactive          # type lines yourself
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:7003/transcript"


def post(url: str, speaker: str, text: str) -> bool:
    body = json.dumps({"speaker": speaker, "text": text, "ts": time.time()}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status < 300
    except urllib.error.URLError as e:
        print(f"  ! could not reach {url}: {e}", file=sys.stderr)
        return False


def replay(args: argparse.Namespace) -> None:
    lines = [
        json.loads(l) for l in open(args.script).read().splitlines() if l.strip()
    ]
    lines.sort(key=lambda s: s["t"])
    print(f"feeding {len(lines)} utterances to {args.url} at {args.speed}x\n")

    started = time.monotonic()
    drift = 0.0
    for i, entry in enumerate(lines):
        target = started + entry["t"] / args.speed + drift
        # Jitter models transcriber lag; an occasional silence models a pause
        # in the room, which is what the extraction timer has to sit through.
        if random.random() < args.silence_chance:
            pause = random.uniform(1.0, args.silence)
            drift += pause
            target += pause
            print(f"  … {pause:.1f}s silence")
        target += random.uniform(-args.jitter, args.jitter)

        sleep = target - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)

        ok = post(args.url, entry["speaker"], entry["text"])
        mark = " " if ok else "!"
        print(f"{mark} [{i:02d}] {entry['speaker'][:28]:30} {entry['text'][:72]}")

    print(f"\ndone — {len(lines)} utterances sent")


def interactive(args: argparse.Namespace) -> None:
    print(f"posting to {args.url}. Format: 'Speaker: what they said'. Ctrl-D to stop.\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        speaker, _, text = line.partition(":")
        if not text:
            speaker, text = "speaker", line
        ok = post(args.url, speaker.strip(), text.strip())
        print("  sent" if ok else "  FAILED")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("script", nargs="?", help="meeting jsonl to replay")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--speed", type=float, default=6.0, help="replay speed multiplier")
    p.add_argument("--jitter", type=float, default=0.6, help="+/- seconds of transcriber lag")
    p.add_argument("--silence", type=float, default=6.0, help="max length of a pause")
    p.add_argument(
        "--silence-chance", type=float, default=0.12, help="probability of a pause per line"
    )
    p.add_argument("--interactive", action="store_true", help="type utterances by hand")
    args = p.parse_args()

    if args.interactive:
        interactive(args)
    elif args.script:
        replay(args)
    else:
        p.error("give a script to replay, or --interactive")


if __name__ == "__main__":
    main()
