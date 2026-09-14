# Single-Host is the product default

Tracewake was built around a two-machine topology (ADR 0003, ADR 0006): a
controller reaching a dedicated Box over SSH. That split is the right isolation
when the Selector shares a machine with production reach. It is the wrong thing
to teach when the Host has no other duties.

The product now teaches one setup: Single-Host (ADR 0019). INSTALL and README
describe that shape only. The Host runs the Selector, the Journal, the window,
and the Box; local dispatch is gated by the credential inventory.
`SELECTOR_BOX_COMMAND` defaults to `box-sources/local.sh`. A remote Box remains
possible as a substituted command (ADR 0004, `box-sources/ssh.sh`) and stays in
the tree and in the suite. It is not a documented topology.

The product ships no filled-in Instance. Instance facts - hostnames, mail,
repositories, allowlists - belong in the operator's `tracewake.env` and
`targets.toml` on the Host. Shipping defaults that look like an email, a
hostname, or an `owner/name` slug still fail
`selector/tests/test_configuration.py`.
