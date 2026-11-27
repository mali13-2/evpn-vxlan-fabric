#!/usr/bin/env python3
"""Failover experiment: how much traffic is lost when part of the fabric fails?

For each scenario, a host sends 100 pings per second to a host in the same
tenant on another leaf, a failure is injected on the path the traffic is using,
and lost packets x 10 ms = outage time.

Scenarios (each run with BFD off and BFD on):
  link    - a leaf-to-spine cable is cut (the interface goes down)
  silent  - a spine silently stops forwarding and sending packets while its
            links stay up (a "gray failure", e.g. a hung switch ASIC). This is
            the hard case: neighbors only notice when keepalives stop arriving.
            Implemented with a policy-routing blackhole on the spine, because
            "docker pause" freezes processes but NOT kernel packet forwarding.

Usage (lab deployed):
    python experiments/failover.py --runs 5
Results: results/failover.csv
"""
import argparse
import csv
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fabric"))
from generate import build_model, load_intent  # noqa: E402

INTERVAL = 0.01  # seconds between pings -> 10 ms resolution
INTENT = load_intent(ROOT / "fabric" / "intent.yml")
NODES, _, _ = build_model(INTENT)
BY_NAME = {n["name"]: n for n in NODES}
LAB = INTENT["fabric"]["name"]

# Traffic: first host of the first tenant -> a same-tenant host on another leaf
TENANT = INTENT["tenants"][0]
SRC = TENANT["hosts"][0]
DST = next(h for h in TENANT["hosts"] if h["leaf"] != SRC["leaf"])
DST_IP = DST["ip"].split("/")[0]
SRC_LEAF = BY_NAME[SRC["leaf"]]


def container(node):
    return f"clab-{LAB}-{node}"


def dexec(node, *cmd, check=True):
    return subprocess.run(["docker", "exec", container(node), *cmd],
                          capture_output=True, text=True, check=check)


def vtysh(node, *commands):
    args = []
    for c in commands:
        args += ["-c", c]
    return dexec(node, "vtysh", *args)


def set_bfd(enabled):
    """Turn BFD on or off on every BGP session, live, without redeploying."""
    for n in NODES:
        cmds = ["configure terminal", f"router bgp {n['asn']}"]
        for link in n["links"]:
            cmds.append(f"neighbor {link['peer_ip']} bfd profile fast" if enabled
                        else f"no neighbor {link['peer_ip']} bfd")
        vtysh(n["name"], *cmds)


def healthy(bfd):
    """All BGP sessions established (and all BFD sessions up when BFD is on)."""
    for n in NODES:
        peers = json.loads(dexec(n["name"], "vtysh", "-c", "show bgp ipv4 unicast summary json").stdout)["peers"]
        if sum(p.get("state") == "Established" for p in peers.values()) != len(n["links"]):
            return False
        if bfd:
            bfd_peers = json.loads(dexec(n["name"], "vtysh", "-c", "show bfd peers json").stdout)
            if sum(p.get("status") == "up" for p in bfd_peers) != len(n["links"]):
                return False
    return True


def wait_healthy(bfd, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        if healthy(bfd):
            time.sleep(3)  # let routes settle into the forwarding table
            return
        time.sleep(1)
    raise SystemExit("fabric did not recover in time - check it with: pytest -v")


def tx_packets(ifname):
    return int(dexec(SRC_LEAF["name"], "cat", f"/sys/class/net/{ifname}/statistics/tx_packets").stdout)


def active_uplink():
    """Find which spine uplink the test traffic is actually using right now."""
    before = {l["ifname"]: tx_packets(l["ifname"]) for l in SRC_LEAF["links"]}
    dexec(SRC["name"], "ping", "-q", "-c", "200", "-i", str(INTERVAL), DST_IP, check=False)
    delta = {i: tx_packets(i) - b for i, b in before.items()}
    ifname = max(delta, key=delta.get)
    return next(l for l in SRC_LEAF["links"] if l["ifname"] == ifname)


def silent_failure(spine):
    """Spine keeps links up but drops every packet it would forward or send."""
    dexec(spine, "ip", "route", "add", "blackhole", "default", "table", "100")
    dexec(spine, "ip", "rule", "add", "pref", "100", "table", "100")


def silent_restore(spine):
    dexec(spine, "ip", "rule", "del", "pref", "100", check=False)
    dexec(spine, "ip", "route", "flush", "table", "100", check=False)


def measure(fail, restore, duration):
    """Ping at 100/s, inject the failure after 2 s, return (sent, received, outage_ms)."""
    count = int(duration / INTERVAL)
    ping = subprocess.Popen(
        ["docker", "exec", container(SRC["name"]), "ping", "-q", "-c", str(count),
         "-i", str(INTERVAL), "-W", "1", DST_IP],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(2)
    fail()
    out, _ = ping.communicate()
    restore()
    m = re.search(r"(\d+) packets transmitted, (\d+) received", out)
    if not m:
        raise SystemExit(f"could not parse ping output:\n{out}")
    sent, received = int(m.group(1)), int(m.group(2))
    return sent, received, round((sent - received) * INTERVAL * 1000)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=5, help="runs per scenario (default 5)")
    args = ap.parse_args()

    out_file = ROOT / "results" / "failover.csv"
    out_file.parent.mkdir(exist_ok=True)
    rows = []
    print(f"Traffic: {SRC['name']} ({SRC['leaf']}) -> {DST['name']} ({DST['leaf']}), 100 pings/s\n")

    for failure in ("link", "silent"):
        for bfd in (False, True):
            set_bfd(bfd)
            wait_healthy(bfd)
            for run in range(1, args.runs + 1):
                uplink = active_uplink()
                spine = uplink["peer"]
                if failure == "link":
                    fail = lambda: dexec(SRC_LEAF["name"], "ip", "link", "set", uplink["ifname"], "down")
                    restore = lambda: dexec(SRC_LEAF["name"], "ip", "link", "set", uplink["ifname"], "up")
                else:
                    fail = lambda: silent_failure(spine)
                    restore = lambda: silent_restore(spine)
                sent, received, outage = measure(fail, restore, duration=16)
                rows.append({"failure": failure, "bfd": "on" if bfd else "off", "run": run,
                             "failed_spine": spine, "sent": sent, "received": received,
                             "outage_ms": outage})
                print(f"{failure:<6} BFD {'on ' if bfd else 'off'} run {run}: "
                      f"failed path via {spine}, lost {sent - received} pkts -> {outage} ms")
                wait_healthy(bfd)

    set_bfd(INTENT["fabric"]["bfd"]["enabled"])  # back to the intended design
    with out_file.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"\nSaved {len(rows)} runs to {out_file.relative_to(ROOT)}\n")
    print(f"{'failure':<8}{'BFD':<5}{'mean ms':>9}{'min':>8}{'max':>8}")
    for failure in ("link", "silent"):
        for bfd in ("off", "on"):
            v = [r["outage_ms"] for r in rows if r["failure"] == failure and r["bfd"] == bfd]
            print(f"{failure:<8}{bfd:<5}{statistics.mean(v):>9.0f}{min(v):>8}{max(v):>8}")


if __name__ == "__main__":
    main()
