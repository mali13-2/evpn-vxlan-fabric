# Automated EVPN-VXLAN Data Center Fabric

IT 475 / IT 377 individual project, Illinois State University.

A leaf-spine data center network built with containerlab and FRRouting, deployed from code, and tested automatically.

## Status
- [x] Lab environment (GitHub Codespaces + containerlab)
- [x] Lab 00: two-router eBGP test
- [ ] eBGP underlay (2 spines, 4 leaves) with ECMP
- [ ] EVPN-VXLAN overlay and tenant VRFs
- [ ] Python intent-based config generator
- [ ] Automated tests (pytest)
- [ ] Failover experiments vs. RSTP
- [ ] CI pipeline (GitHub Actions)

## Run the fabric
Open this repository in GitHub Codespaces, then:

```bash
pip install -r requirements.txt
python fabric/generate.py
sudo containerlab deploy -t fabric/build/dc1.clab.yml
```

`fabric/intent.yml` is the single source of truth. `generate.py` turns it into
every router configuration and the containerlab topology, so the whole network
can be rebuilt from one file.
