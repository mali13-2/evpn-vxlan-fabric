#!/usr/bin/env python3
"""Generate the full fabric (topology + every router config) from intent.yml.

Usage:
    python fabric/generate.py

Output goes to fabric/build/: one containerlab topology file plus a folder
per router holding its frr.conf, vtysh.conf and daemons file.
"""
import ipaddress
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).parent
BUILD = HERE / "build"


def fail(msg):
    raise SystemExit(f"intent error: {msg}")


def load_intent(path):
    """Read intent.yml and reject designs that would break the fabric."""
    intent = yaml.safe_load(path.read_text())
    if len(intent["spines"]) < 2:
        fail("need at least 2 spines for redundancy")
    if len(intent["leaves"]) < 2:
        fail("need at least 2 leaves")

    seen_vnis, seen_hosts = set(), set()
    for t in intent.get("tenants", []):
        if t["vni"] in seen_vnis:
            fail(f"VNI {t['vni']} is used by more than one tenant")
        if not 1 <= t["vni"] <= 16_777_215:
            fail(f"VNI {t['vni']} is outside the 24-bit range")
        seen_vnis.add(t["vni"])
        for h in t["hosts"]:
            if h["leaf"] not in intent["leaves"]:
                fail(f"host {h['name']} is attached to unknown leaf {h['leaf']}")
            if h["name"] in seen_hosts:
                fail(f"host name {h['name']} is used twice")
            seen_hosts.add(h["name"])
            ipaddress.ip_interface(h["ip"])  # raises if the address is invalid
    return intent


def build_model(intent):
    """Turn the short intent into a full per-node model (IPs, peers, cables, VNIs)."""
    f = intent["fabric"]
    nodes, cables, hosts = {}, [], []

    for s, name in enumerate(intent["spines"], start=1):
        nodes[name] = {"name": name, "role": "spine", "asn": f["spine_asn"],
                       "loopback": f"{f['loopback_prefix']}.{s}",
                       "links": [], "vnis": [], "rt_asn": f["rt_asn"], "bfd": f["bfd"]}
    for l, name in enumerate(intent["leaves"], start=1):
        nodes[name] = {"name": name, "role": "leaf", "asn": f["leaf_asn_base"] + l,
                       "loopback": f"{f['loopback_prefix']}.{10 + l}",
                       "links": [], "vnis": [], "rt_asn": f["rt_asn"], "bfd": f["bfd"],
                       "next_port": len(intent["spines"]) + 1}

    # Underlay: every leaf connects to every spine.
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

    # Overlay: each host gets the next free port on its leaf, and that leaf
    # gets a bridge + VXLAN interface for the host's tenant VNI.
    for t in intent.get("tenants", []):
        for h in t["hosts"]:
            leaf = nodes[h["leaf"]]
            port = f"eth{leaf['next_port']}"
            leaf["next_port"] += 1
            vni = next((v for v in leaf["vnis"] if v["vni"] == t["vni"]), None)
            if vni is None:
                vni = {"vni": t["vni"], "tenant": t["name"], "ports": []}
                leaf["vnis"].append(vni)
            vni["ports"].append(port)
            hosts.append({"name": h["name"], "ip": h["ip"], "tenant": t["name"]})
            cables.append({"a": f"{h['leaf']}:{port}", "b": f"{h['name']}:eth1"})

    return list(nodes.values()), cables, hosts


def main():
    intent = load_intent(HERE / "intent.yml")
    nodes, cables, hosts = build_model(intent)

    env = Environment(loader=FileSystemLoader(HERE / "templates"),
                      trim_blocks=True, lstrip_blocks=True,
                      undefined=StrictUndefined, keep_trailing_newline=True)

    for n in nodes:
        out = BUILD / n["name"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "frr.conf").write_text(env.get_template("frr.conf.j2").render(**n))
        (out / "daemons").write_text(env.get_template("daemons.j2").render())
        (out / "vtysh.conf").write_text(env.get_template("vtysh.conf.j2").render())

    topo = BUILD / f"{intent['fabric']['name']}.clab.yml"
    topo.write_text(env.get_template("topology.clab.yml.j2").render(
        fabric=intent["fabric"], nodes=nodes, hosts=hosts, cables=cables))

    print(f"Generated {len(nodes)} routers, {len(hosts)} hosts, {len(cables)} links -> {topo}")
    for n in nodes:
        vnis = ", ".join(f"{v['tenant']}={v['vni']}" for v in n["vnis"]) or "-"
        print(f"  {n['name']:<7} AS {n['asn']:<6} loopback {n['loopback']:<10} VNIs: {vnis}")
    for h in hosts:
        print(f"  {h['name']:<7} tenant {h['tenant']:<4} {h['ip']}")


if __name__ == "__main__":
    main()
