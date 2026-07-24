"""
migrate_edgar_shards.py — one-off migration: redistribute the existing
edgar_facts_cache/shard_00.json..shard_39.json content into the new
500-shard scheme (edgar_scan_core.EDGAR_FACTS_CACHE_NUM_SHARDS, punch
list #76 follow-up) instead of throwing it away and re-fetching from
EDGAR. Run once, from the repo root:

    python3 migrate_edgar_shards.py [--dry-run]

Reads every old shard_NN.json (2-digit, 40 files), recomputes each
ticker's new shard path via the CURRENT _facts_cache_shard_path()
(already updated to 500 shards / 3-digit naming), writes the new
shard_NNN.json files, verifies the total ticker count is preserved
exactly, then deletes the old 40 files. Local filesystem only -- no
GitHub API calls, no network -- run against a local clone and commit
the result normally (git add -A significantly cheaper than waiting on
another ~90-minute full EDGAR re-fetch).
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edgar_scan_core import _facts_cache_shard_path, EDGAR_FACTS_CACHE_NUM_SHARDS

DRY_RUN = "--dry-run" in sys.argv

OLD_PATTERN = "edgar_facts_cache/shard_[0-9][0-9].json"  # exactly 2 digits -- old scheme only


def main():
    old_files = sorted(glob.glob(OLD_PATTERN))
    print(f"Found {len(old_files)} old shard file(s) matching {OLD_PATTERN}")
    if not old_files:
        print("Nothing to migrate.")
        return

    combined = {}
    dupes = 0
    for f in old_files:
        with open(f) as fh:
            data = json.load(fh)
        for ticker, entry in data.items():
            if ticker in combined:
                dupes += 1
            combined[ticker] = entry

    total_before = len(combined)
    print(f"Total distinct tickers across old shards: {total_before}"
          + (f" ({dupes} duplicate ticker(s) across old shard files, last one wins)" if dupes else ""))
    print(f"New shard count: {EDGAR_FACTS_CACHE_NUM_SHARDS}")

    new_shards = {}
    for ticker, entry in combined.items():
        path = _facts_cache_shard_path(ticker)
        new_shards.setdefault(path, {})[ticker] = entry

    total_after = sum(len(v) for v in new_shards.values())
    print(f"Total tickers across {len(new_shards)} new shard file(s) that will be written: {total_after}")

    if total_after != total_before:
        print(f"ABORT: ticker count mismatch ({total_before} -> {total_after}). Not writing anything.")
        sys.exit(1)

    if DRY_RUN:
        print("\n--dry-run: not writing or deleting anything. Shard size distribution:")
        for path, d in sorted(new_shards.items()):
            print(f"  {path}: {len(d)} ticker(s)")
        return

    for path, d in new_shards.items():
        with open(path, "w") as fh:
            json.dump(d, fh)

    print(f"Wrote {len(new_shards)} new shard file(s).")

    for f in old_files:
        os.remove(f)
    print(f"Deleted {len(old_files)} old shard file(s).")

    # Final sanity check: re-read everything back off disk fresh.
    verify = {}
    for f in glob.glob("edgar_facts_cache/shard_*.json"):
        with open(f) as fh:
            verify.update(json.load(fh))
    print(f"Post-migration verification: {len(verify)} tickers readable from disk "
          f"({'MATCH' if len(verify) == total_before else 'MISMATCH!!'})")


if __name__ == "__main__":
    main()
