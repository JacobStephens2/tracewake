# Provision the Loop's box through the existing OpenTofu and Ansible layout

`notes/lessons.md` ranks reproducibility infrastructure fourth among what cost
time building the ETA factory and advises deferring it - "one box I set up by
hand is fine until it isn't. Add Ansible the day you rebuild a *second* time."
We are not deferring it for the Loop's host. The advice was priced against
`eta-factory`, where OpenTofu and Ansible arrived bundled with a CI pipeline
(`infrastructure.yml`) and a governance gate, and it was that bundle that was
slow. In `orchestration` the bundle does not exist: the repo has **no
`.github/workflows/` directory at all**, and `terraform/` and `ansible/` are
already established, so declaring one more DigitalOcean droplet costs an entry in
a layout that is already there and zero CI time.

## Consequences

`lessons.md` item 4 should be read as *defer reproducibility ceremony*, not
*defer reproducibility tooling*. The two were conflated because at ETA they
always shipped together. Where a declarative layout already exists and nothing
gates changes to it, using it is cheaper than the hand-built alternative, because
the alternative's real cost is the undocumented state you discover on the first
rebuild.

The box is Ubuntu 24.04 rather than the fleet's Rocky 9, required by `sbx`
(ADR 0004). Nested virtualization must be confirmed on the chosen droplet size
before anything is installed - `vmx` in `/proc/cpuinfo`, `/dev/kvm` present, and
`kvm_intel.nested = Y`, all three of which hold on the `s-8vcpu-16gb`
orchestration droplet - because `sbx` cannot run without them and the failure
surfaces late. Size with headroom for a microVM guest.

Provisioning runs from an agent session launched through `vaulted-agent`, which
resolves the DigitalOcean credential at launch. No new credential is created and
none is added to the Loop's box, which holds only its model credential, its
scoped GitHub PAT, and its dedicated signing key.
