# infra/

Broker configuration and deployment files. No Docker: everything runs natively. Not yet created.

Will contain:

- `mosquitto/` — `mosquitto.conf` for development (Windows laptop, ports 1883 and 9001)
  and production (Azure VM, TLS on 8883 and 8884); the ACL file generated from the
  user/topic table in [`docs/message-contract.md`](../docs/message-contract.md); a script
  to build the password file from `.env` (the password file itself is git-ignored).
- `systemd/` — unit files for the brain program and the dashboard on the Ubuntu VM.
- Deployment notes: VM setup, firewall rules, domain and TLS certificates, backups.
