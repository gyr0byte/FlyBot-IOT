# infra/

Broker configuration and deployment files. No Docker: everything runs natively. Not yet created.

Will contain:

- `mosquitto/` — `mosquitto.conf` for development (Windows laptop, ports 1883 and 9001)
  and production (Azure VM, TLS on 8883 and 8884); the ACL file generated from the
  user/topic table in [`docs/message-contract.md`](../docs/message-contract.md); a script
  to build the password file from `.env` (the password file itself is git-ignored).
- **Required broker setting (Phase 2):** Mosquitto must run with `persistence true` (and a
  `persistence_location` on D: for development). Retained messages, including a saved
  `cmd/mode` **e-stop**, `status`, `brain/status` and `brain/weights`, then survive a broker
  restart. Without it, a restarted broker forgets the e-stop and the robot would start in `auto`
  (contract §5.1). Nothing is installed yet.
- `systemd/` — unit files for the brain program and the dashboard on the Ubuntu VM.
- Deployment notes: VM setup, firewall rules, domain and TLS certificates, backups.
