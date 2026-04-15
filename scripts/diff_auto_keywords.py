#!/usr/bin/env python3
"""
Diff two auto_keywords.json files (e.g. live vs shadow).

Usage:
    python tools/diff_auto_keywords.py config/auto_keywords.json config/auto_keywords.shadow.json

Output:
    Per-topic summary of additions, removals, and score deltas.
"""

import json
import sys


def load(path: str) -> dict[str, dict[str, dict]]:
    """Load auto_keywords.json and return {topic: {term: {score, last_seen, lang}}}."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    result: dict[str, dict[str, dict]] = {}
    for topic, entries in data.get('keywords', {}).items():
        kws: dict[str, dict] = {}
        for entry in entries:
            if isinstance(entry, str):
                kws[entry] = {'score': 1, 'last_seen': '', 'lang': 'en'}
            elif isinstance(entry, dict):
                term = entry.get('term', '')
                if term:
                    kws[term] = {
                        'score': entry.get('score', 1),
                        'last_seen': entry.get('last_seen', ''),
                        'lang': entry.get('lang', 'en'),
                    }
        result[topic] = kws
    return result


def diff(live_path: str, shadow_path: str) -> None:
    live = load(live_path)
    shadow = load(shadow_path)

    all_topics = sorted(set(live) | set(shadow))
    any_diff = False

    for topic in all_topics:
        live_kws = live.get(topic, {})
        shadow_kws = shadow.get(topic, {})

        added = {t: shadow_kws[t] for t in shadow_kws if t not in live_kws}
        removed = {t: live_kws[t] for t in live_kws if t not in shadow_kws}
        changed = {
            t: (live_kws[t]['score'], shadow_kws[t]['score'])
            for t in live_kws
            if t in shadow_kws and live_kws[t]['score'] != shadow_kws[t]['score']
        }

        if not added and not removed and not changed:
            continue

        any_diff = True
        print(f"\n[{topic}]")
        for term, kw in sorted(added.items()):
            print(f"  + {term!r:40s}  lang={kw['lang']}  score={kw['score']}")
        for term, kw in sorted(removed.items()):
            print(f"  - {term!r:40s}  lang={kw['lang']}  score={kw['score']}")
        for term, (old, new) in sorted(changed.items()):
            print(f"  ~ {term!r:40s}  score {old} → {new}")

    if not any_diff:
        print("No differences.")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <live.json> <shadow.json>", file=sys.stderr)
        sys.exit(1)
    diff(sys.argv[1], sys.argv[2])
