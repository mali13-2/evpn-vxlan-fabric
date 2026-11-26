"""Automated tests for the EVPN-VXLAN fabric.

Every expectation is derived from fabric/intent.yml, so the tests check that
the running network matches the intended design (intent-based verification).

Run with the lab deployed:
    pytest -v
"""
import itertools
import json
import subprocess
from pathlib import Path

import pytest
import yaml

INTENT = yaml.safe_load((Path(__file__).parent.parent / "fabric" / "intent.yml").read_text())
LAB = INTENT["fabric"]["name"]
SPINES = INTENT["spines"]
LEAVES = INTENT["leaves"]
TENANTS = INTENT.get("tenants", [])
HOSTS = [dict(h, tenant=t["name"], vni=t["vni"]) for t in TENANTS for h in t["hosts"]]


# ---------- helpers ----------

def run(node, *cmd):
    """Run a command inside a lab container and return the result."""
    return subprocess.run(["docker", "exec", f"clab-{LAB}-{node}", *cmd],
                          capture_output=True, text=True, timeout=30)


def vtysh_json(node, command):
    """Run an FRR show command with JSON output and parse it."""
    out = run(node, "vtysh", "-c", f"{command} json").stdout
    return json.loads(out)


def loopback(name):
    prefix = INTENT["fabric"]["loopback_prefix"]
    if name in SPINES:
        return f"{prefix}.{SPINES.index(name) + 1}"
    return f"{prefix}.{10 + LEAVES.index(name) + 1}"


def ping(src, dst_ip):
    return run(src, "ping", "-c", "3", "-W", "1", dst_ip).returncode == 0


@pytest.fixture(scope="session", autouse=True)
def lab_is_running():
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True).stdout.split()
    missing = [n for n in SPINES + LEAVES if f"clab-{LAB}-{n}" not in names]
    if missing:
        pytest.exit(f"Lab is not running (missing {missing}). Deploy it first.", returncode=2)


# ---------- underlay ----------

@pytest.mark.parametrize("node", SPINES + LEAVES)
def test_underlay_bgp_sessions_established(node):
    """Every spine peers with every leaf, and every leaf with every spine."""
    peers = vtysh_json(node, "show bgp ipv4 unicast summary")["peers"]
    expected = len(LEAVES) if node in SPINES else len(SPINES)
    established = [p for p, d in peers.items() if d.get("state") == "Established"]
    assert len(established) == expected, f"{node}: {len(established)}/{expected} sessions up"


@pytest.mark.parametrize("src,dst", [(a, b) for a in LEAVES for b in LEAVES if a != b])
def test_ecmp_uses_all_spines(src, dst):
    """Each leaf reaches every other leaf through ALL spines at once (no blocked links)."""
    prefix = f"{loopback(dst)}/32"
    route = vtysh_json(src, f"show ip route {prefix}")[prefix][0]
    active = [nh for nh in route["nexthops"] if nh.get("active")]
    assert len(active) == len(SPINES), f"{src}->{dst}: {len(active)} paths, expected {len(SPINES)}"


# ---------- overlay ----------

@pytest.mark.parametrize("node", SPINES + LEAVES)
def test_evpn_bgp_sessions_established(node):
    peers = vtysh_json(node, "show bgp l2vpn evpn summary")["peers"]
    expected = len(LEAVES) if node in SPINES else len(SPINES)
    established = [p for p, d in peers.items() if d.get("state") == "Established"]
    assert len(established) == expected, f"{node}: {len(established)}/{expected} EVPN sessions up"


VNI_CASES = sorted({(h["leaf"], h["vni"]) for h in HOSTS})


@pytest.mark.parametrize("leaf,vni", VNI_CASES)
def test_vni_present_with_remote_vteps(leaf, vni):
    """Each leaf with a tenant host has that VNI and sees every other leaf serving it."""
    vnis = vtysh_json(leaf, "show evpn vni")
    assert str(vni) in vnis, f"{leaf}: VNI {vni} missing"
    others = {h["leaf"] for h in HOSTS if h["vni"] == vni} - {leaf}
    assert vnis[str(vni)].get("numRemoteVteps") == len(others), \
        f"{leaf}: VNI {vni} sees {vnis[str(vni)].get('numRemoteVteps')} remote VTEPs, expected {len(others)}"


SAME = [(a, b) for a, b in itertools.permutations(HOSTS, 2) if a["tenant"] == b["tenant"]]
DIFF = [(a, b) for a, b in itertools.permutations(HOSTS, 2) if a["tenant"] != b["tenant"]]


@pytest.mark.parametrize("a,b", SAME, ids=[f"{a['name']}->{b['name']}" for a, b in SAME])
def test_same_tenant_hosts_can_talk(a, b):
    assert ping(a["name"], b["ip"].split("/")[0]), f"{a['name']} cannot reach {b['name']}"


@pytest.mark.parametrize("a,b", DIFF, ids=[f"{a['name']}->{b['name']}" for a, b in DIFF])
def test_different_tenants_are_isolated(a, b):
    """Zero-trust segmentation: tenants must never reach each other."""
    assert not ping(a["name"], b["ip"].split("/")[0]), \
        f"ISOLATION BREACH: {a['name']} ({a['tenant']}) reached {b['name']} ({b['tenant']})"
