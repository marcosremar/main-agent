"""Provision a POOL of N ready golden sandboxes in parallel, write their sids into a
config's `pool_sids`. Re-runnable. Pool sandboxes are stopped ($0) and reused.

Usage:
  python3 provision_pool.py <config.json> [N]   # default N = config.max_concurrent or 15
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from daytona import Daytona
from creds import claude_credentials_b64, opencode_auth_b64, gh_token
from setup_golden import provision_one, KEY


def main():
    cfg_path = sys.argv[1]
    cfg = json.load(open(cfg_path))
    n = int(sys.argv[2]) if len(sys.argv) > 2 else cfg.get("max_concurrent", 15)
    dt = Daytona(cfg.get("daytona_key", KEY))
    gh, cc, oc = gh_token(), claude_credentials_b64(), opencode_auth_b64()

    wave = int(sys.argv[3]) if len(sys.argv) > 3 else 5   # avoid Daytona concurrent-create cap
    print(f"provisioning {n} sandboxes in waves of {wave}...")
    sids = []
    idx = 0
    while len(sids) < n:
        batch = min(wave, n - len(sids))
        with ThreadPoolExecutor(max_workers=batch) as ex:
            futs = [ex.submit(provision_one, dt, gh, cc, oc, f"[{idx+j}] ") for j in range(batch)]
            for f in as_completed(futs):
                try:
                    sids.append(f.result())
                except Exception as e:
                    print("WARN provision failed (will not retry this slot):", e)
        idx += batch
        # one retry pass for the shortfall, then give up to avoid infinite loops
        if len(sids) < n and idx >= n:
            short = n - len(sids)
            print(f"retrying {short} failed provisions once...")
            with ThreadPoolExecutor(max_workers=min(wave, short)) as ex:
                futs = [ex.submit(provision_one, dt, gh, cc, oc, f"[retry{j}] ") for j in range(short)]
                for f in as_completed(futs):
                    try: sids.append(f.result())
                    except Exception as e: print("WARN retry failed:", e)
            break

    print(f"\n{len(sids)}/{n} sandboxes ready")
    cfg["pool_sids"] = sids
    cfg["golden_sid"] = sids[0] if sids else cfg.get("golden_sid")
    json.dump(cfg, open(cfg_path, "w"), indent=2)
    print(f"wrote pool_sids ({len(sids)}) into {cfg_path}")
    for s in sids:
        print(" ", s)


if __name__ == "__main__":
    main()
