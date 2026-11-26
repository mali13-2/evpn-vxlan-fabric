# Automated EVPN-VXLAN Data Center Fabric

IT 475 / IT 377 individual project, Illinois State University.

A leaf-spine data center network built with containerlab and FRRouting, deployed from code, and tested automatically.

## Status
- [x] Lab environment (GitHub Codespaces + containerlab)
- [ ] Lab 00: two-router eBGP test
- [ ] eBGP underlay (2 spines, 4 leaves) with ECMP
- [ ] EVPN-VXLAN overlay and tenant VRFs
- [ ] Python intent-based config generator
- [ ] Automated tests (pytest)
- [ ] Failover experiments vs. RSTP
- [ ] CI pipeline (GitHub Actions)

## Run it
Open this repository in GitHub Codespaces, then:

```bash
cd labs/00-hello
sudo containerlab deploy -t hello.clab.yml
```
