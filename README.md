# OneView

**Branch servers and another cloud's servers, managed from Azure as if they were Azure resources: Azure Arc,
patching with a test ring, security baselines and central logs.**

[![CI](https://github.com/omdkpatel810-ops/azure-oneview/actions/workflows/ci.yml/badge.svg)](https://github.com/omdkpatel810-ops/azure-oneview/actions/workflows/ci.yml)

> **Status: milestones 1 to 3 of 4 are built and tested offline.** Onboarding (the identity, Key Vault,
> the eight-machine lab and the onboarding script), patching (two rings in Azure Update Manager and a
> compliance report) and security baselines (Azure Policy machine configuration and a score report)
> are done: 20 Terraform tests run against mocked Azure and AWS providers, and 40 Python tests check
> the scripts. The first real run waits on the Azure subscription; its measured results will go here.

## The problem

A credit union has a server in each of 18 branches, plus servers in another cloud from a company it
acquired. Its auditors found unpatched machines, no central logs, and no proof of how any server is
configured. Each server is managed by hand, if at all.

OneView makes every server, wherever it runs, show up in Azure. From there:
- patches roll out on a schedule, to a test ring first;
- security baselines are checked continuously;
- every machine's logs land in one place.

It deploys into [Guardrails](https://github.com/omdkpatel810-ops/azure-guardrails): its resource group
is a vended environment, so it comes with an owner, a budget and Guardrails' policies.

## Milestone 1: every server in Azure

### The lab

Eight servers, listed once in [`lab/inventory.json`](lab/inventory.json):

| Servers | Where | Built by |
|---|---|---|
| 4 Linux and 2 Windows "branch servers": Winnipeg, Brandon, Steinbach, Thompson, Winkler, Selkirk | VirtualBox on a laptop | [`lab/branches/Vagrantfile`](lab/branches/Vagrantfile) |
| 2 Linux servers from the "acquired company" | AWS free tier, Canada (Central) | [`lab/aws`](lab/aws/main.tf) |

Three of them, one of each kind, form the **test ring** that gets patches first.

Like servers nobody has looked after, they start neglected:
- automatic updates are off;
- the AWS servers are built from a 2024 image, so they arrive with a year of missing patches;
- the branch servers have weak settings (root SSH logins, password logins, a readable `/etc/shadow`,
  and blank passwords allowed on Windows).

Finding and fixing that is milestones 2 and 3.

### Connecting them safely

[`infra/`](infra/main.tf) creates what onboarding needs:

- **An identity that can only onboard.** `oneview-arc-onboarding` has the *Azure Connected Machine
  Onboarding* role on OneView's resource group and nowhere else. It can register machines there, but
  can't read, change or delete them afterwards.
- **A secret that lives only in Key Vault and doesn't last.** It expires within 7 days, in Entra ID and
  in Key Vault alike. Setting `onboarding_open = false` deletes it from both once every machine is
  connected, and bumping `onboarding_round` rotates it before the next branch is added.
- **A locked-down vault.** Access is by Azure roles only, from the operator's IP address only. It has
  purge protection, and Terraform refuses to destroy it.

[`onboarding/onboard.py`](onboarding/onboard.py) then connects each server over SSH:

1. It skips servers whose Connected Machine agent already reports *Connected*, so it's safe to run again.
2. It installs the agent (Microsoft's Linux script, or the Windows installer) and connects it, tagged with
   the server's site, patch ring, operating system and cloud. Milestone 2's patch schedules select
   servers by those tags.
3. The secret is read from Key Vault into memory and reaches each server only on SSH's standard input,
   base64-encoded and decoded on the server. It never appears in a command line, in a file on the
   operator's computer, or in the output. Even the agent's error messages have it masked.
4. It times each server and saves the results to `onboarding/results/`.

```bash
python onboarding/onboard.py --dry-run    # every script, with the secret shown as ***
python onboarding/onboard.py              # connect the lot
```

## Milestone 2: patching with a test ring

[`infra/patching.tf`](infra/patching.tf) sets up Azure Update Manager with two weekly windows:

| Ring | Servers | Window (Winnipeg time) |
|---|---|---|
| test | branch-01-winnipeg, branch-05-winkler, acquired-02: one of each kind | Tuesdays, 10 p.m. to 1 a.m. |
| branch | the other five | Thursdays, 10 p.m. to 1 a.m. |

- **Servers join a window by their tag.** Each window picks up Arc servers in OneView's resource group
  whose `ring` tag matches, so a new branch server is patched on the right night as soon as
  `onboard.py` connects it. There is no list of servers to keep up to date.
- **Critical and security updates only**, rebooting only when an update needs it.
- **The test ring goes two days ahead.** If it finds a bad patch, set `paused_rings = ["branch"]` and
  apply before Thursday: the branches' window stays, with no servers in it. Update Manager doesn't hold
  the second ring back by itself, so this step is the gate.
- **Terraform refuses unsafe windows**: one during business hours (it must start at 7 p.m. or later, or
  end by 7 a.m.), one shorter than 90 minutes or longer than 6 hours, or a plan without a test ring.
- **Every server checks for missing updates daily**, through Microsoft's built-in policy for Arc
  servers, so the compliance report is never more than a day old.

[`oneview/patching.py`](oneview/patching.py) reports compliance from Azure Resource Graph. A server is
compliant when its latest check found no missing critical or security updates. A server that has never
been checked counts as not compliant, because an unknown state isn't a good one.

```bash
python -m oneview.patching snapshot --assess        # ask every server to check now
python -m oneview.patching snapshot --save before   # a few minutes later
# ...after both windows have run
python -m oneview.patching snapshot --save after
python -m oneview.patching compare results/patches-before.json results/patches-after.json
```

The report gives compliance for the fleet and for each ring, and each server's missing critical,
security and other updates. The comparison shows each server before and after.

## Milestone 3: security baselines

[`infra/baseline.tf`](infra/baseline.tf) assigns Microsoft's Azure compute security baseline, which is
built from the CIS benchmarks, to every Arc server in OneView's resource group, one policy for Linux and
one for Windows:

- **The servers check themselves.** Azure Policy machine configuration is built into the Connected
  Machine agent, so there's no extension to install. Each server runs the baseline's rules about every 15
  minutes and reports each one as passing or failing, with the reason.
- **Arc servers have to be asked for.** Both policies skip Arc servers unless `IncludeArcMachines` is
  `"true"`; a test makes sure it stays that way.
- **The policies only audit.** They change nothing on the servers, so fixing a server is a deliberate
  step: `vagrant provision --provision-with harden` runs
  [`harden-linux.sh`](lab/branches/harden-linux.sh) and [`harden-windows.ps1`](lab/branches/harden-windows.ps1),
  which undo the weak settings the lab started with.
- **Defender for Servers is optional.** `defender_for_servers = "P1"` or `"P2"` turns it on. It's off by
  default because it covers the whole subscription, not just OneView, and costs money for every server.

[`oneview/baseline.py`](oneview/baseline.py) scores each server: the share of the baseline's rules it
passes, which rules fail and why, and how many Defender for Cloud recommendations it fails, by severity.

```bash
python -m oneview.baseline snapshot --save before
(cd lab/branches && vagrant provision --provision-with harden)
python -m oneview.baseline snapshot --save after      # after the next check, about 15 minutes later
python -m oneview.baseline compare results/baseline-before.json results/baseline-after.json
```

The comparison shows each server's score before and after, the rules it fixed, and any that started
failing.

## Running it

You need Guardrails deployed (OneView's resource group is vended from its
[`environments/oneview-prod.json`](https://github.com/omdkpatel810-ops/azure-guardrails/blob/main/environments/oneview-prod.json)),
VirtualBox and Vagrant on your laptop, and optionally an AWS account for the two "acquired" servers.

```bash
# 1. Azure: the onboarding identity and Key Vault, with Guardrails' state storage as the backend
terraform -chdir=infra init -backend-config=... # the same settings Guardrails' pipeline uses
terraform -chdir=infra apply -var 'operator_ip_ranges=["'"$(curl -s https://ifconfig.me)"'/32"]'

# 2. The branch servers, on your laptop
(cd lab/branches && vagrant up)

# 3. The acquired company's servers (optional)
terraform -chdir=lab/aws apply -var "operator_cidr=$(curl -s https://ifconfig.me)/32" \
  -var "ssh_public_key=$(cat ~/.ssh/id_ed25519.pub)"

# 4. Connect them all, then close onboarding
python onboarding/onboard.py
terraform -chdir=infra apply -var onboarding_open=false -var 'operator_ip_ranges=[...]'

# 5. Patch compliance before the first window, and after both have run
python -m oneview.patching snapshot --assess
python -m oneview.patching snapshot --save before

# 6. Baseline scores, then fix the branch servers and score them again
python -m oneview.baseline snapshot --save before
(cd lab/branches && vagrant provision --provision-with harden)
```

**Cost.** Azure Arc is free for the servers themselves. The Key Vault costs a few cents. Azure Update
Manager [charges](https://azure.microsoft.com/pricing/details/azure-update-management-center/) about
$5 per Arc server per month, prorated by the day, for each day a server is connected and has a check or
a schedule: about $1.30 a day for all eight, so disconnect the lab between runs. The baseline checks
are free on Arc servers. Defender for Servers, if you turn it on, costs about $5 (Plan 1) or $15 (Plan 2)
per server per month; Plan 2 includes Update Manager. The AWS servers
fit the free tier. The branch servers cost only your laptop's memory: about 8 GB for all six, so start
fewer if you need to.

## Tests

| What | How | Count |
|---|---|---|
| The onboarding script: inventory checks, host lookup, both systems' scripts, where the secret goes, re-runs, failures | pytest, with a stand-in shell | 20 |
| The patch report: counts by classification on both systems, never-checked servers, rings, before and after, on-demand checks | pytest, with a stand-in `az` and Resource Graph rows | 10 |
| The baseline report: scores, failing rules and reasons, servers without a report, Defender findings, before and after | pytest, with a stand-in `az` and Resource Graph rows | 10 |
| The onboarding identity's scope, the secret's lifetime and rotation, the vault's lock-down | `terraform test`, mocked Azure | 5 |
| The patch windows: two rings, test first, after hours only, pausing a ring, the daily check on both systems | `terraform test`, mocked Azure | 8 |
| The baselines: both systems, Arc servers included, OneView's resource group only; Defender off unless asked for | `terraform test`, mocked Azure | 4 |
| The AWS servers: one per inventory entry, SSH from one address, IMDSv2, encrypted disks | `terraform test`, mocked AWS | 3 |
| Python coverage | pytest-cov | 100% |
| Formatting, validation, linting, security scanning | `terraform fmt` and `validate`, tflint, Checkov, ruff, shellcheck, `ruby -c`, the PowerShell parser | every push |

Checkov passes 18 checks on the Terraform and skips 6, each with its reason written next to it. It passes all
100 checks on the workflow.

## Roadmap

1. **Arc-enable six laptop VMs and two VMs in another cloud** ✅ built and tested offline
2. **Patch schedules with a test ring** in Azure Update Manager: test ring first, branches after hours ✅ built and tested offline
3. **Security baseline checks** (built from CIS) with Azure Policy machine configuration, and Defender for Cloud findings ✅ built and tested offline
4. **Central logs in Sentinel**, and an emergency patch drill that times how long it takes to patch every server

## Layout

```
infra/          Terraform: the onboarding identity, its secret and the Key Vault; patch windows; baselines
lab/            the eight servers: inventory.json, branches/ (Vagrant, with the hardening scripts) and aws/ (Terraform)
onboarding/     onboard.py: connect every server to Azure Arc, and time it
oneview/        reports from Azure Resource Graph: patching.py, baseline.py, and their queries/
tests/          Python tests
```
