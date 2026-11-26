#!/usr/bin/env python3
"""Generate the full fabric (topology + every router config) from intent.yml.

Usage:
    python fabric/generate.py

Output goes to fabric/build/: one containerlab topology file plus a folder
per router holding its frr.conf and daemons file.
"""
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).parent
BUILD = HERE / "build"


def load_intent(path):
    intent = yaml.safe_load(path.read_text())
    if len(intent["spines"]) < 2:
        raise SystemExit("intent error: need at least 2 spines for redundancy")
    if len(intent["leaves"]) < 2:
        raise SystemExit("intent error: need at least 2 leaves")
    return intent


def build_model(intent):
    """Turn the short intent into a full per-node model (IPs, peers, cables)."""
    f = intent["fabric"]
    nodes, cables = {}, []

    for s, name in enumerate(intent["spines"], start=1):
        nodes[name] = {"name": name, "role": "spine", "asn": f["spine_asn"],
                       "loopback": f"{f['loopback_prefix']}.{s}", "links": []}
    for l, name in enumerate(intent["leaves"], start=1):
        nodes[name] = {"name": name, "role": "leaf", "asn": f["leaf_asn_base"] + l,
                       "loopback": f"{f['loopback_prefix']}.{10 + l}", "links": []}

    # Full mesh between tiers: every leaf connects to every spine.
    # Spine S uses eth<L> towards leaf L; leaf L uses eth<S> towards spine S.
    # Each link is a /31: spine gets the even address, leaf the odd one.
    for s, spine in enumerate(intent["spines"], start=1):
        for l, leaf in enumerate(intent["leaves"], start=1):
            base = 2 * (l - 1)
            spine_ip = f"{f['p2p_prefix']}.{s}.{base}"
            leaf_ip = f"{f['p2p_prefix']}.{s}.{base + 1}"
            nodes[spine]["links"].append({
                "ifname": f"eth{l}", "ip": spine_ip, "peer": leaf,
                "peer_ip": leaf_ip, "peer_asn": nodes[leaf]["asn"]})
            nodes[leaf]["links"].append({
                "ifname": f"eth{s}", "ip": leaf_ip, "peer": spine,
                "peer_ip": spine_ip, "peer_asn": nodes[spine]["asn"]})
            cables.append({"a": f"{spine}:eth{l}", "b": f"{leaf}:eth{s}"})

    return list(nodes.values()), cables


def main():
    intent = load_intent(HERE / "intent.yml")
    nodes, cables = build_model(intent)

    env = Environment(loader=FileSystemLoader(HERE / "templates"),
                      trim_blocks=True, lstrip_blocks=True,
                      undefined=StrictUndefined, keep_trailing_newline=True)

    for n in nodes:
        out = BUILD / n["name"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "frr.conf").write_text(env.get_template("frr.conf.j2").render(**n))
        (out / "daemons").write_text(env.get_template("daemons.j2").render())

    topo = BUILD / f"{intent['fabric']['name']}.clab.yml"
    topo.write_text(env.get_template("topology.clab.yml.j2").render(
        fabric=intent["fabric"], nodes=nodes, cables=cables))

    print(f"Generated {len(nodes)} routers and {len(cables)} links -> {topo}")
    for n in nodes:
        print(f"  {n['name']:<7} AS {n['asn']:<6} loopback {n['loopback']}")


if __name__ == "__main__":
    main()
