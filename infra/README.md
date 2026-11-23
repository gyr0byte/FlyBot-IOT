# infra/

Broker configuration, scripts and deployment files. No Docker: everything runs natively.

| Path | What |
|---|---|
| `start-broker.cmd` | Starts the development broker in the foreground (Ctrl+C stops it). |
| `mosquitto/mosquitto.conf` | LAN mode: listeners on all interfaces, 1883 (MQTT) and 9001 (WebSockets). |
| `mosquitto/mosquitto-local.conf` | Localhost-only mode: same ports on 127.0.0.1. |
| `mosquitto/conf.d/flybot.conf` | Shared settings: no anonymous clients, password file, ACL, persistence, logging. |
| `mosquitto/acl` | **Generated** from the contract's permission table by `gen_acl.py`. Do not edit. |
| `mosquitto/gen_acl.py` | Regenerates `acl` from [`docs/message-contract.md`](../docs/message-contract.md) §4. |
| `mosquitto/make_passwords.py` | Fills `.env` with random MQTT passwords and builds the hashed password file. |
| `tests/test_acl.py` | Automated access-control test against the running broker. |

Runtime state lives in `.local/mosquitto/` at the repo root (git-ignored): the hashed password file,
the persistence database `data/mosquitto.db` and the log `log/mosquitto.log`.

Later (Phase 11): TLS listeners (8883, 8884), `systemd/` unit files for the Azure VM, deployment notes.

## Development broker (Windows laptop)

### One-time setup

Everything lives on D:. Nothing is installed as a service, and nothing is written to C:.

1. **Mosquitto 2.1.4** is unpacked (not installed) in `D:\Tools\mosquitto`. It was extracted from the
   official installer with a portable 7-Zip (`D:\Tools\7-Zip`), so there are no registry entries,
   no service and no environment variable. It uses the Microsoft C++ runtime that is already on the PC.
   A different location can be set with `FLYBOT_MOSQUITTO_DIR`.
2. **Python venv** for the tools and tests is in `infra\.venv`. Create it with caches kept on D::
   ```powershell
   $env:PIP_CACHE_DIR = "D:\Tools\cache\pip"; $env:TMP = "D:\Tools\tmp"; $env:TEMP = "D:\Tools\tmp"
   $env:PYTHONPYCACHEPREFIX = "D:\Tools\cache\pycache"
   python -m venv infra\.venv
   infra\.venv\Scripts\python -m pip install -r infra\tests\requirements.txt
   ```
3. **Passwords** (run from the repo root):
   ```powershell
   infra\.venv\Scripts\python infra\mosquitto\make_passwords.py
   ```
   This creates `.env` from `.env.example` if needed, puts a random 32-character password in each
   `MQTT_*_PASS` that is still a placeholder, and builds `.local\mosquitto\passwd`. It never prints
   passwords and keeps existing ones. Use `--rotate` to replace all of them. Restart the broker afterwards.
   The ESP32's password must also be copied into `firmware/include/secrets.h` (Phase 6).

### Start, stop, reset

| Task | How |
|---|---|
| Start (LAN, ESP32 can connect) | `infra\start-broker.cmd`. Needs the firewall rule below for the ESP32. |
| Start (this laptop only) | `infra\start-broker.cmd local`. Use on untrusted Wi-Fi such as college networks. |
| Stop | **Ctrl+C** in the broker window. This is a clean shutdown and saves the database. If it was started some other way: `Stop-Process -Name mosquitto`. |
| Watch the log | `Get-Content .local\mosquitto\log\mosquitto.log -Wait -Tail 20` |
| Reset retained state | Stop the broker, delete `.local\mosquitto\data\mosquitto.db`, start again. **This also clears a saved e-stop**, so the robot will start in `auto` (contract §5.1). |
| Reset everything | Stop the broker, delete `.local\mosquitto\`, re-run `make_passwords.py`, start again. Passwords in `.env` are kept unless you use `--rotate`. |

Clients on the laptop should use host `127.0.0.1`, not `localhost`. On Windows `localhost` tries IPv6
first, which stalls every connection by about 2 s when the broker is in localhost mode.

**Persistence** (`persistence true`, required by the contract): retained messages are saved after
*every* change (`autosave_on_changes`), not on a timer. A saved `cmd/mode` **e-stop**, plus
`status`, `brain/status` and `brain/weights`, therefore survive a broker restart and even a crash.
This was tested by killing the process.

The start script warns if `mosquitto/acl` no longer matches the contract.

### Windows Firewall (needed for the ESP32 to reach port 1883)

The firewall blocks unsolicited inbound connections on all profiles, so the ESP32 is blocked until
you add a rule. Add it **before the first LAN-mode start**. Otherwise Windows shows a
"Windows Security Alert", and clicking *Cancel* there creates a **Block** rule for Mosquitto that
overrides any allow rule. Localhost mode never triggers the prompt.

Rule, run in an **administrator** PowerShell. It allows only TCP 1883, only for this Mosquitto
executable, and only from devices on the same subnet:

```powershell
New-NetFirewallRule -DisplayName "FlyBot MQTT (Mosquitto 1883, LAN)" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 1883 -Program "D:\Tools\mosquitto\mosquitto.exe" `
  -RemoteAddress LocalSubnet -Profile Private
```

`-Profile Private` means the rule only works when your home Wi-Fi is marked **Private**
(Settings → Network & internet → Wi-Fi → your network → Network profile type → Private). Windows
currently treats it as Public. If you prefer to keep it Public, use `-Profile Private,Public`
instead. `LocalSubnet` still limits access to devices on the same network, but then use
`start-broker.cmd local` whenever you're on someone else's Wi-Fi.

Port 9001 (WebSockets) is not opened: the dashboard runs on the laptop itself. Remove the rule with
`Remove-NetFirewallRule -DisplayName "FlyBot MQTT (Mosquitto 1883, LAN)"`.
Some routers and guest networks isolate Wi-Fi clients from each other ("AP/client isolation"). On
those, the ESP32 cannot reach the laptop whatever the firewall says.

### ACL test

With the broker running (either mode), from the repo root:

```powershell
infra\.venv\Scripts\python -m pytest infra\tests -v
```

It reads the permission table from the contract, so it follows contract changes automatically. It
checks every user × every topic for both publish and read, plus wildcard subscriptions, writes outside
the contract's topics, anonymous / wrong-password / unknown-user logins, the WebSocket listener, and
that `mosquitto/acl` is up to date. It takes about 10 s. It never publishes retained messages, so it
cannot overwrite a saved e-stop. Its messages are not contract-valid, so a running brain would count
them as invalid.

Note on how Mosquitto enforces reads: with an ACL file it **grants** every SUBSCRIBE from MQTT 3.1.1
clients, then checks the ACL before delivering each message and silently drops what the client may
not read. The test therefore checks forbidden reads by *delivery*, not by the subscribe reply.

### After a contract change

1. `infra\.venv\Scripts\python infra\mosquitto\gen_acl.py` regenerates `mosquitto/acl`.
2. Restart the broker.
3. Run the ACL test.
