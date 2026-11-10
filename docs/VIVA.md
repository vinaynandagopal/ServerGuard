# ServerGuard viva Q&A

Answers describe this project's design. Items marked **[verify]** depend on files I could not see while writing this, so check them against your code before the viva.

## Application and security

**Q1. Why does the app restart services through a helper script and `sudo` instead of running as root?**
Least privilege. The web app is the exposed attack surface, so it runs as an unprivileged user. The only root action it can trigger is one helper, through one sudoers rule, so a compromise of the app does not give general root.

**Q2. How is the helper protected from abuse?**
It is owned by root and not writable by anyone else, so the app user cannot modify it. The sudoers rule names that single path. The rule is validated with `visudo -cf` before installation so a typo cannot break sudo. **[verify]** The helper should also check the service name against an allowlist or a strict pattern, so confirm `scripts/serverguard-restart` does that.

**Q3. Why is `NoNewPrivileges` not set in the systemd unit?**
It prevents `sudo` from gaining privileges, which would silently break recovery. The compensating controls are the root-owned code and helper, the single sudo rule, and the unprivileged user.

**Q4. The dashboard has no login. How is it secured?**
By network isolation. Gunicorn and nginx bind to `127.0.0.1`, the security group opens only SSH from my IP, and I view the dashboard through an SSH tunnel. `verify.yml` fails if anything listens on a non-loopback address.

**Q5. What is the weakness of that approach?**
Anyone with SSH access or a shell on the host can reach the dashboard. For real use I would add authentication and TLS.

**Q6. Why is the app code owned by root?**
So a compromised app process cannot rewrite its own code or the helper. It can write only to `/var/lib/serverguard`.

**Q7. Why Gunicorn and not the Flask dev server?**
The dev server is not designed for production. Gunicorn is a proper WSGI server with process management. I use `--workers 1 --threads 4` because a collector or alert engine running as a background thread would run twice with multiple workers. **[verify]** this matches how `run.py` starts the collector.

**Q8. Why put nginx in front if Gunicorn already listens?**
It is the conventional front end: connection buffering, header handling, and the natural place to add TLS or authentication later. It also keeps the Python server off the edge.

**Q9. Why use Docker if the final deployment uses systemd?**
Docker gives reproducible builds and CI testing. Recovery needs to manage host services via systemd, which a container cannot do without extra privileges, so the production-style deployment is a VM managed by systemd.

## Ansible

**Q10. What is idempotence and how did you test it?**
Running the playbook repeatedly gives the same end state without unnecessary changes. I run `site.yml` twice and the second run reports `changed=0`.

**Q11. Why Ansible instead of a shell script?**
Modules describe the desired state, so reruns are safe and changes are reported. Handlers restart services only when a config actually changed. Shell scripts need manual checks for each of those.

**Q12. What are handlers?**
Tasks that run only when notified by a changed task, for example restarting `serverguard` after the unit file or environment file changes.

**Q13. What does `--check --diff` do?**
A dry run that shows what would change without changing it.

**Q14. What does `ansible-lint` check?**
Style and best practices: fully qualified module names, task naming, risky file permissions, commands without `changed_when`, and so on. CI runs it on every push.

**Q15. Why is the roles layout split into common, app, recovery, systemd, nginx?**
Each role has one responsibility, so it can be understood, tagged, and rerun on its own, for example `--tags recovery`.

## Terraform and AWS

**Q16. Terraform versus Ansible?**
Terraform provisions infrastructure (instance, security group, key pair). Ansible configures what runs on it. Terraform's output writes the Ansible inventory, which connects the two.

**Q17. How do you limit SSH to your IP?**
One ingress rule on TCP 22 with `cidr_blocks = [my_ip_cidr]`. A variable validation requires a `/32`, which rejects `0.0.0.0/0`.

**Q18. What if your IP changes?**
SSH times out. I update `terraform.tfvars` and run `terraform apply`, which modifies only the rule.

**Q19. Where is the private key and why does it matter?**
Generated locally and never given to Terraform. Terraform uploads only the public key, so the private key cannot leak through state.

**Q20. Is Terraform state sensitive?**
Yes, it can hold sensitive values, so state files and tfvars are gitignored and CI fails if they are tracked. Local state is a limitation. A team would use a remote backend such as S3 with locking.

**Q21. What is IMDSv2 and why require it?**
A token-based version of the instance metadata service. Requiring it blocks a common attack where a server-side request forgery leaks instance credentials.

**Q22. Why is the security group's egress open?**
The instance needs outbound access for apt, pip and git. Ingress is what protects it.

**Q23. How do you control cost?**
A billing budget, a small instance type, and `terraform destroy` after each session.

**Q24. What does `terraform plan` do and why save it with `-out`?**
It computes the changes without applying them. Applying the saved plan guarantees that exactly what I reviewed is what runs.

## CI and testing

**Q25. What does CI check?**
`ci.yml` runs the application tests and Docker build. `infra.yml` checks that no state or secrets are committed, runs `terraform fmt -check`, `terraform init -backend=false` and `validate`, then Ansible syntax-check and `ansible-lint`. It needs no AWS credentials.

**Q26. Why did you add a separate workflow for infrastructure?**
It runs in parallel with the app tests and cannot break the existing application pipeline.

**Q27. How are the recovery and sudo features tested?**
**[verify]** Unit tests exercise the logic without touching the real system, and on the deployed host `verify.yml` checks file ownership, the sudoers rule, and that the app user cannot run arbitrary commands with sudo.

## Limitations and future work

**Q28. What would you improve?**
Add authentication and TLS, remote Terraform state, an AMI built with Packer, Molecule tests for the roles, and external alert delivery.

**Q29. Is this production ready?**
No. It is a single node with SQLite and no login. It is designed to show a secure, repeatable deployment pipeline and sound privilege separation.
