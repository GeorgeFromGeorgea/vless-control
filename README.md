# VLESS Control

Self-hosted Xray/VLESS management project with a private Telegram admin bot.

> This is a development project, not a turnkey VPN installer. It supports syncing active registry UUIDs into selected **existing** Xray VLESS inbounds. It does not install Xray, create inbounds, configure DNS/firewalls, or guarantee access through a particular Wi-Fi or mobile operator. Review config changes before applying them to a live host.

**Supported development targets:** Ubuntu Server 22.04/24.04, Debian 12. **Python:** 3.11+.

## Current functionality

- SQLite registry for users, unique UUIDs, named profiles, and assignments.
- Individual UUID per user, reused across that user's assigned profiles/ports.
- VLESS link generation; Reality links include `flow=xtls-rprx-vision`.
- Private-chat-only Telegram bot, numeric admin allowlist, guided buttons for user creation/profile assignment/link retrieval.
- Xray sync CLI: dry-run by default; `--apply` is required to change an existing config.
- Sync preserves unrelated inbounds/settings, but replaces `settings.clients` for each profile inbound managed by the registry.
- Apply validates with `xray run -test -config`, writes a verified timestamped backup, atomically replaces the config, validates again, restarts the configured systemd service, and restores the old file on failure.

## Not implemented

No Xray installation/upgrades/uninstaller, host preflight, firewall or certificate automation, inbound/profile wizard, expiry/traffic quota, end-user bot, audited secret delivery, VM integration tests, or real carrier testing. Deactivating a user in SQLite alone does not revoke a live connection: run the sync command to reconcile the Xray config. Profiles must exist in both the registry and server config; profile name must match the Xray inbound `tag`.

## Local install and bot setup

```bash
git clone https://github.com/GeorgeFromGeorgea/vless-control.git
cd vless-control
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
chmod 600 .env
```

Set the following in `.env` (never commit it):

```dotenv
TELEGRAM_BOT_TOKEN=YOUR_SEPARATE_BOTFATHER_TOKEN
TELEGRAM_ADMIN_IDS=123456789
DATABASE_PATH=data/vless-control.sqlite3
XRAY_CONFIG_PATH=/usr/local/etc/xray/config.json
XRAY_BINARY=/usr/local/bin/xray
XRAY_SERVICE=xray
```

The app reads environment variables directly; it does not load `.env` itself. Export them before each run or configure the service manager:

```bash
set -a; . ./.env; set +a
.venv/bin/vless-control-bot
```

Use a dedicated bot token and trusted numeric Telegram account IDs. The bot refuses startup if token/admin allowlist is absent. It ignores management actions from non-admins and rejects all group-chat control. No systemd unit is supplied yet.

## Telegram menu and commands

Send `/start` to the bot in a private chat:

- **Пользователи** — list latest users and status (UUIDs hidden).
- **Новый ключ** — create a registry user and UUID. This alone does not add it to Xray.
- **Профили** — list active profiles.
- **Назначить профили** — choose user and profile ID(s); same UUID is assigned across those inbounds.
- **Выдать ссылки** — show the selected user's links in the admin's private bot chat. Admin must pass them privately to the intended person. Links are credentials.
- **Отмена** — cancel a guided operation.

Command fallback: `/users`, `/new_user NAME`, `/profiles`, `/assign USER_ID PROFILE_ID[,PROFILE_ID]`, `/links USER_ID`, `/revoke USER_ID`. `/revoke` deactivates the registry identity; actual Xray removal requires the sync step below.

### Create profile records

A profile wizard is not implemented yet. Use the Python API after the corresponding listener already exists in Xray:

```python
from vless_control.registry import Registry

db = Registry("data/vless-control.sqlite3")
profile_id = db.add_profile(
    name="reality-443",  # must match an existing Xray inbound tag
    host="vpn.example.net", port=443,
    security="reality", transport="tcp",
    sni="www.example.org",
    public_key="CLIENT_FACING_REALITY_PUBLIC_KEY",
    short_id="0123456789abcdef",
)
print(profile_id)
```

The profile schema contains client-facing information, not the server private key. Keep private keys, configs, tokens, database files, and generated links out of Git. Do not use placeholder values on a live server.

## Xray sync: dry-run, then reviewed apply

The sync CLI reads active profiles and assignments from `DATABASE_PATH`. It treats each active profile name as a managed inbound tag. Assigned active UUIDs are included there; inactive users are omitted. An active profile with zero active users is synced with an empty clients list. **That clients list is replaced** on each managed inbound; do not point it at an inbound whose clients are managed elsewhere.

### Dry-run (default, no server changes)

Run on the Xray host with access to its config and the project's database:

```bash
set -a; . ./.env; set +a
.venv/bin/vless-control-sync > /tmp/vless-control-candidate.json
```

Inspect the candidate carefully before applying. It can contain existing private configuration, so keep it protected and do not upload/paste it publicly. Default mode does not write Xray config or restart services.

### Apply explicitly

Verify all paths, inbound tags, client ownership, permissions, backup/recovery access, and service name. Then:

```bash
.venv/bin/vless-control-sync --apply
```

The command refuses to create a missing config. It stages the candidate, validates with the configured Xray binary, creates/verifies a timestamped adjacent backup, atomically replaces the file, validates again, and restarts `XRAY_SERVICE` (default `xray`). On a failure it restores the prior bytes and attempts to restart the service with the restored file. Verify the backup and systemd status/logs after the command. The running process might still be using the old in-memory state if restart fails; investigate before retrying.

Advanced file-only mode:

```bash
.venv/bin/vless-control-sync --apply --no-restart
```

This does not reload the running process; use only when intentionally coordinating a later safe reload.

The sync CLI needs OS permission to read/write the Xray config and restart the service. Do not run the full Telegram bot as root merely to grant those privileges; a restricted helper/service policy is a production-hardening task still outstanding. Make an independent backup before first applying on a live host.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q vless_control tests
```

Unit tests use temporary files and mocked Xray/service operations. They do not call Telegram, a real Xray binary, systemd, or a public/mobile network. Passing tests is not production deployment verification.

## Security and operational cautions

- Public repository: review all tracked files/history; never commit `.env`, `.venv`, databases, server config/private keys, backups, VLESS URIs, or credentials.
- Treat every `vless://` URI as a password. The bot currently returns links only to the allowlisted admin in the private control chat.
- Review which Xray inbound tags are managed; only their clients lists are replaced.
- Validate and test on a disposable VM before production. Confirm an independent backup and rollback path.
- A server-side port listener check cannot test a user's specific mobile operator/network. Have the user test primary and fallback profiles in their client; no universal carrier guarantee is possible.

## Roadmap

- OS preflight and reversible Xray installer/uninstaller for Ubuntu 22.04/24.04 and Debian 12.
- Telegram profile create/edit flow with validation and confirmations.
- Separate ownership marker/reconciliation to avoid overwriting client lists owned by other tools.
- Restricted privileged helper, durable audit log, revocation confirmation, expiry/quota support.
- Integration tests on disposable VMs, service unit, monitoring, and recovery guide.

## License

MIT (see `LICENSE`).
