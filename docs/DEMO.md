# ServerGuard demo guide (about 12 minutes)

## The day before

1. Run the whole pipeline once on AWS and confirm it works (`terraform apply`, both Ansible playbooks, tunnel, dashboard).
2. Run `terraform destroy` afterwards. You will rebuild live, or keep the instance up only if you are sure about cost.
3. Check your public IP has not changed: `curl -s https://checkip.amazonaws.com`
4. Open these in separate terminal tabs: `~/ServerGuard` (repo), `~/ServerGuard/terraform`, `~/ServerGuard/ansible`.
5. Have the GitHub Actions page open on a green run.

## Live script

| # | Minutes | Do | Say |
|---|---|---|---|
| 1 | 1 | Show the dashboard on WSL: `http://127.0.0.1:8080` | "ServerGuard monitors CPU, memory, disk, processes and services, raises alerts, and can restart a failed service." |
| 2 | 1 | Run `pytest -q` | "141 tests, also run in CI." |
| 3 | 1 | Show the architecture diagram in the README | "Terraform provisions, Ansible configures, I reach the dashboard only through an SSH tunnel." |
| 4 | 2 | `cd terraform && terraform plan -out tfplan` and point at the security group | "One ingress rule: port 22 from my /32. `0.0.0.0/0` is rejected by a validation rule." |
| 5 | 2 | `terraform apply tfplan`, then `cat ../ansible/inventory/aws.ini` | "Terraform writes the Ansible inventory, so the two tools are connected without copy-paste." |
| 6 | 3 | `ansible-playbook -i inventory/aws.ini site.yml`, then run it again | "Second run: `changed=0`. That is idempotence." |
| 7 | 1 | `ansible-playbook -i inventory/aws.ini verify.yml` | "Automated checks: loopback binding, helper ownership, sudo limited to one command." |
| 8 | 1 | Run the three `curl -m 5 http://$IP:...` commands | "All time out. The dashboard is not reachable from the internet." |
| 9 | 1 | Start the tunnel, open `http://localhost:8080` in the browser | "Reachable only through SSH." |
| 10 | 2 | On EC2 via SSH, `sudo systemctl stop nginx`, trigger recovery from the dashboard, show it come back | "The app runs one narrow sudo rule for a root-owned helper. It cannot run anything else as root." |
| 11 | 1 | Show the green GitHub Actions run | "CI checks Terraform formatting and validity, lints the Ansible, and runs the tests." |
| 12 | 1 | `terraform destroy` | "Everything is torn down to avoid cost." |

If time is short, skip steps 2 and 11 and run steps 5 and 6 in advance.

## Commands to have ready

```bash
IP=$(cd ~/ServerGuard/terraform && terraform output -raw public_ip)
curl -m 5 http://$IP:8080/ ; echo "exit=$?"
curl -m 5 http://$IP:5000/ ; echo "exit=$?"
curl -m 5 http://$IP/      ; echo "exit=$?"
$(terraform output -raw dashboard_tunnel_command) &
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/
kill %1
```

## If something goes wrong

| Problem | Fallback |
|---|---|
| SSH times out | Your IP changed. Re-run the `terraform.tfvars` line and `terraform apply`. Otherwise demo on WSL localhost, which is the same Ansible code. |
| AWS unreachable or slow | Run the playbooks against WSL localhost and show the saved AWS output as screenshots. |
| Recovery does not trigger | Show `sudo -l -U serverguard` and run the helper manually to explain the mechanism. |
| Tunnel port busy | `pkill -f "ssh .* -L 8080"` and retry. |

Take screenshots of a successful full run beforehand as a backup.
