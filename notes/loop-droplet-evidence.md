# The Loop's droplet: provisioning evidence

`loop.etadventures.com` — DigitalOcean droplet `594834213`, `142.93.121.211`,
created 2026-08-24 from `tofu/hosts/loop.tf` (issue #76, spec issue #73).

This note is the record of the four things issue #76 asked to be proved before
anything is installed on the box. It is evidence, not documentation of how the
box works — that arrives with its ansible play.

## What it is

| | |
|---|---|
| Name / DNS | `loop.etadventures.com` → `142.93.121.211` (Route 53 A, TTL 300) |
| DO id / region / size | `594834213` · `nyc1` · `s-2vcpu-4gb` (2 vCPU, 3915 MB usable, 77 GB disk, $24/mo) |
| Image | `ubuntu-24-04-x64` — Ubuntu 24.04.4 LTS, kernel 6.8.0-124-generic |
| VPC | `b6705332-dc84-11e8-8650-3cfdfea9f8c8` (nyc1 default) |
| Backups | off, deliberately — see `tofu/hosts/loop.tf` |
| SSH | `root@loop.etadventures.com`, conductor's key from the orchestration VM |

Ubuntu rather than the fleet's Rocky 9 because `sbx` requires it (ADR 0004);
declared in `tofu/hosts` rather than by hand because ADR 0006 says so, and there
rather than in another stack because root ADR 0002 gives droplets-plus-their-DNS
to that stack.

## Declared, and the stack agrees

`./tofu.sh apply` created exactly two resources — the droplet and its A-record —
and the plan immediately afterwards was clean:

```
digitalocean_droplet.loop: Creation complete after 31s [id=594834213]
aws_route53_record.loop: Creation complete after 31s [id=Z3O3FWL0L1O9AE_loop.etadventures.com_A]
Apply complete! Resources: 2 added, 0 changed, 0 destroyed.

$ ./tofu.sh plan -detailed-exitcode ; echo $?
No changes. Your infrastructure matches the configuration.
0
```

`-detailed-exitcode` returning 0 is the assertion: 0 means no changes, 2 would
mean a diff. Note that `image` is *not* in this droplet's `ignore_changes`, as it
is on the two imported droplets — this one was created from the config, so the
API returns the slug and the clean plan is a real comparison rather than a
suppressed one.

## Reachable over SSH from the orchestration VM

```
$ ssh root@loop.etadventures.com 'hostname; date -u'
loop
Mon Aug 24 18:51:15 UTC 2026
```

By name, not just by address, so the A-record is doing its job. The key is
`~conductor/.ssh/id_ed25519` on the orchestration VM, already registered to the
DigitalOcean account, attached at create time by `loop.tf`. Nothing was copied
onto the box by hand.

## Nested virtualization: all three assertions hold

This is the reason the ticket exists before any install. `sbx` boots a microVM
per Iteration; without nested virtualization the Execution Boundary cannot start
at all, and finding that out after a day of provisioning is the expensive
version of the discovery.

```
$ grep -m1 -o -E 'vmx|svm' /proc/cpuinfo
vmx
$ lscpu | grep -i virtuali
Virtualization:                          VT-x
Virtualization type:                     full
$ ls -l /dev/kvm
crw-rw---- 1 root kvm 10, 232 Aug 24 18:48 /dev/kvm
$ cat /sys/module/kvm_intel/parameters/nested
Y
$ lsmod | grep -i kvm
kvm_intel             487424  0
kvm                  1404928  1 kvm_intel
irqbypass              12288  1 kvm
```

The CPU flag, the device node and the nested parameter are the three the ticket
named. A fourth check goes past presence to use — opening `/dev/kvm` and asking
the kernel to create a guest, with no package installed:

```
$ python3 -c "import fcntl,os; fd=os.open('/dev/kvm',os.O_RDWR); \
    print(fcntl.ioctl(fd,0xAE00,0)); print(fcntl.ioctl(fd,0xAE01,0))"
12
4
```

`KVM_GET_API_VERSION` returns 12 (the only value the ABI defines) and
`KVM_CREATE_VM` returns a file descriptor — the kernel built a real, empty guest
and handed it back. The hypervisor is not merely advertised, it works.

## What this settles

Spec issue #73 lists "whether droplet sizes below the orchestration VM's expose
nested virtualization" among its known-unverified items. They do: the
`s-2vcpu-4gb` Regular Intel droplet above exposes the same `vmx` /
`kvm_intel.nested = Y` as the `s-8vcpu-16gb` orchestration VM that ADR 0006 cites.
The Loop's host therefore costs $24/mo rather than $96/mo.

Two vCPUs and 4 GB is headroom for one microVM guest, not for many. If a Run is
ever found to be starved, the size is one line in `loop.tf` — but a resize
changes the disk too, so it is a reboot and a permanent commitment to the larger
disk, not a free dial.

## What this does *not* establish

- That `sbx` runs. Nested virtualization is its prerequisite, not its
  sufficient condition; the vendor tool is unverified until it is installed.
- Anything about the box's configuration. It is a stock Ubuntu image with one
  SSH key. No agent, no credential, no boundary, no `bats`.
- Anything about the credentials the Loop will hold. The box holds none of the
  three yet, and it must never hold a vault token, a database credential, or a
  fleet SSH key.

## Rebuilding or tearing down

Rebuild is `./tofu.sh apply` from `tofu/hosts` in a `va` session, then the
ansible play. Teardown needs `prevent_destroy = true` removed from `loop.tf`
first — that guard is there so a plan can never quietly propose replacing a
running box, which is the stack's standing rule.
