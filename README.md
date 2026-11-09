# OneView

**Branch servers and another cloud's servers, managed from Azure as if they were Azure resources: Azure Arc,
patching with a test ring, security baselines and central logs.**

[![CI](https://github.com/omdkpatel810-ops/azure-oneview/actions/workflows/ci.yml/badge.svg)](https://github.com/omdkpatel810-ops/azure-oneview/actions/workflows/ci.yml)

> **Status: milestone 1 of 4 is built and tested offline.** The onboarding identity, Key Vault, the
> eight-machine lab and the onboarding script are done: 8 Terraform tests run against mocked Azure and AWS
> providers, and 20 Python tests check the onboarding script. The first real run waits on the Azure
> subscription; its measured results will go here.

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
```

**Cost.** Azure Arc is free for the servers themselves. The Key Vault costs a few cents. The AWS servers
fit the free tier. The branch servers cost only your laptop's memory: about 8 GB for all six, so start
fewer if you need to.

## Tests

| What | How | Count |
|---|---|---|
| The onboarding script: inventory checks, host lookup, both systems' scripts, where the secret goes, re-runs, failures | pytest, with a stand-in shell | 20, 100% coverage |
| The onboarding identity's scope, the secret's lifetime and rotation, the vault's lock-down | `terraform test`, mocked Azure | 5 |
| The AWS servers: one per inventory entry, SSH from one address, IMDSv2, encrypted disks | `terraform test`, mocked AWS | 3 |
| Formatting, validation, linting, security scanning | `terraform fmt` and `validate`, tflint, Checkov, ruff, shellcheck, `ruby -c` | every push |

Checkov passes 18 checks on the Terraform and skips 6, each with its reason written next to it. It passes all
100 checks on the workflow.

## Roadmap

1. **Arc-enable six laptop VMs and two VMs in another cloud** ✅ built and tested offline
2. **Patch schedules with a test ring** in Azure Update Manager: test ring first, branches after hours
3. **CIS baseline checks** with Azure Policy machine configuration, and Defender for Cloud findings
4. **Central logs in Sentinel**, and an emergency patch drill that times how long it takes to patch every server

## Layout

```
infra/          the onboarding identity, its secret and the Key Vault (Terraform)
lab/            the eight servers: inventory.json, branches/ (Vagrant) and aws/ (Terraform)
onboarding/     onboard.py: connect every server to Azure Arc, and time it
tests/          Python tests
```
