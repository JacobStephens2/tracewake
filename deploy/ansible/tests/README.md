# Single-Host check mode

From the repository root, with the Selector test dependencies installed and
`ansible-playbook` on PATH:

```bash
sudo docker build -t tracewake-ansible-check deploy/ansible/tests
sudo docker run -d --name tracewake-ansible-check \
  -v "$PWD:$PWD:ro" tracewake-ansible-check
TRACEWAKE_ANSIBLE_TEST_CONTAINER=tracewake-ansible-check \
  python -m pytest -q selector/tests/test_host_play.py
sudo docker rm -f tracewake-ansible-check
```

The test puts fixture inventory in the disposable container and runs the whole
`host.yml --check --diff` play for both the Claude default and inventory-selected
Grok. It checks the rendered Caddy configuration, loopback Dashboard unit, and
selected egress policy. No task exclusions, cloud, DNS, or agent credentials.
An ordinary suite run without the container variable skips these two integration
cases and still checks play syntax.

On an empty host, check mode cannot inspect binaries, virtual environments, or
services that the dry run only *would* install. Those dependent operations wait
for an apply; check mode does not install prerequisites behind the operator's
back. An apply remains necessary to prove that Sandboxes boots and TLS issues.
