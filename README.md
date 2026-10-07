# VLESS Control

**Self-hosted Xray/VLESS administration project** with a Telegram control bot, per-user UUIDs, and alternative connection profiles on separate ports.

> **Development snapshot — not production-ready.** The repository contains a registry, a small admin bot, and a config builder/validator. It does **not** currently install Xray, apply generated configuration, provision firewall rules, automatically revoke clients from a running Xray instance, or provide a guided installer. Never run this on a production server expecting it to configure a VPN. Carrier/operator reachability is not guaranteed.

- Target OS: Ubuntu Server 22.04/24.04 and Debian 12
- Python: 3.11+
- License: MIT (planned; add LICENSE before release)

## Contents

- [Features and current limits](#features-and-current-limits)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Local installation](#local-installation)
- [Telegram bot configuration](#telegram-bot-configuration)
- [How to use the bot](#how-to-use-the-bot)
- [Registry and profile setup](#registry-and-profile-setup)
- [VLESS profile fields](#vless-profile-fields)
- [Xray config builder](#xray-config-builder)
- [Security](#security)
- [Testing](#testing)
- [Server installation status](#server-installation-status)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)

## Features and current limits

### Implemented

- SQLite registry for named users, UUIDs, connection profiles, and user-to-profile assignments.
- Individual UUID per user; the same user's UUID can be issued with several configured profiles/ports.
- Profile fields for VLESS over REALITY/TCP, TLS, or no transport security, plus TCP/WS transport metadata.
- VLESS URI rendering for configured profiles.
- Minimal Telegram bot: owner allowlist, user creation/listing, profile listing, profile assignment, link display, user deactivation.
- Xray JSON builder and structural validator. It builds a document in memory; a separate helper can atomically write a file when explicitly called by code.
- Fallback links are separate profile links. A client generally must import/select a different link; the bot cannot transparently change the network path on a phone.

### Not implemented / not verified

- Xray installation/upgrades/uninstall, systemd service management, firewall configuration, DNS/domain/certificate setup.
- Synchronizing SQLite users/profiles into the Xray runtime config or applying a config change safely.
- End-to-end revocation from an already running Xray service. Current deactivation prevents registry link lookup but is not an Xray runtime revocation mechanism.
- Bot flow for creating/editing profiles, expiring keys, traffic quotas, shared UUIDs, subscriptions, audit logs, or user self-service.
- Automatic port reachability detection from the subscriber's Wi-Fi/mobile operator.
- Compatibility testing on real Russian mobile carriers, regions, devices, and client apps.
- Production security review, database migrations, backup/restore automation, or integration tests on Ubuntu/Debian VMs.

Do not describe the current snapshot as a complete VPN panel or production-ready installer.

## Architecture

```text
Telegram admin (allowlisted numeric user ID)
       │
       ├── registry actions ──> SQLite (users, UUIDs, profiles, assignments)
       └── display VLESS URIs (credential material; handle as secrets)

Xray config builder ──> validated JSON document (not applied automatically)
```

The project's SQLite registry is the current source for bot actions. Generated links are credentials: do not post them in public/group chats or logs.

## Requirements

- Ubuntu Server 22.04/24.04 or Debian 12 (target platforms; no server installer yet).
- Python 3.11+ and `python3-venv`.
- A separate Telegram bot created with BotFather, if using the bot.
- Your numeric Telegram user ID for the admin allowlist.
- A server hostname/IP and already-designed Xray inbound parameters to define connection profiles.

This code does not need root privileges for local development. Do not run the app as root unless a future, reviewed installer explicitly requires it.

## Local installation

```bash
# Clone the public repository
# git clone https://github.com/<OWNER>/vless-control.git
cd vless-control

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

cp .env.example .env
chmod 600 .env
```

Edit `.env` locally (never commit it):

```dotenv
TELEGRAM_BOT_TOKEN=put_your_own_bot_token_here
TELEGRAM_ADMIN_IDS=123456789
DATABASE_PATH=data/vless-control.sqlite3
```

Set `TELEGRAM_ADMIN_IDS` to one or more comma-separated numeric Telegram IDs. If no bot token or no valid admin ID is configured, startup refuses access. Keep the bot token private; if it is exposed, revoke/rotate it through BotFather.

> Environment file loading is not built into the current bot entrypoint. Export the variables in the shell or use a service manager to inject them before starting it.

Run the bot in the foreground:

```bash
set -a
. ./.env
set +a
.venv/bin/vless-control-bot
```

Stop with Ctrl+C. No systemd unit is included yet.

## Telegram bot configuration

1. Create a new BotFather bot; do not reuse a token already polled by another running process.
2. Obtain your Telegram numeric user ID using a trusted method.
3. Put the token and ID in `.env` on the host that will run the bot.
4. Protect `.env` (`chmod 600`) and ensure it is excluded from backups intended for public distribution.
5. Start the bot and send `/start` from the allowlisted account.

Only allowlisted users can invoke management actions. Users outside the allowlist receive a denial on `/start`; other commands are ignored. The current bot is an admin control plane, not a customer-facing issuance bot.

## How to use the bot

Commands (admin only):

- `/start` — show the persistent keyboard.
- `/users` — list the latest users and their active status (no UUIDs shown).
- `/new_user NAME` — create a registry user and a fresh UUID.
- `/profiles` — list active profiles and their host/port/security/transport metadata.
- `/assign USER_ID PROFILE_ID[,PROFILE_ID]` — attach one or more profiles to a user.
- `/links USER_ID` — display that user's VLESS links. Treat each link as a password.
- `/revoke USER_ID` — deactivate that registry user. **This does not yet remove the UUID from a running Xray config.**

The bottom keyboard exposes user/profile listing and points to `/new_user`. It does not yet implement profile creation, arbitrary key editing, or a confirmation dialog for revocation.

### Example workflow

1. Define profile records in SQLite using the Python API (see below).
2. In Telegram, send `/new_user Alice Example`.
3. Note the returned numeric user ID.
4. Send `/assign 1 1,2` to attach profile IDs 1 and 2.
5. Send `/links 1`; deliver the appropriate link privately to that user.
6. Ask the person to import/test the primary and fallback profiles. A server-side check cannot establish that a carrier/device can reach them.

## Registry and profile setup

There is not yet a profile-management command. For development, create profiles through the Python API. Example (replace every placeholder with actual values and do not commit private keys):

```python
from vless_control.registry import Registry

db = Registry("data/vless-control.sqlite3")
profile_id = db.add_profile(
    name="primary-reality",
    host="vpn.example.net",
    port=443,
    security="reality",
    transport="tcp",
    sni="www.example.org",
    public_key="CLIENT_FACING_REALITY_PUBLIC_KEY",
    short_id="0123456789abcdef",
)
print(profile_id)
```

The registry profile schema stores the client-facing public key and short ID. It does **not** currently store the server-side REALITY private key or certificate paths; the Xray builder expects a richer profile mapping supplied by the caller. Keep those server secrets out of Git and avoid persisting them in the public repo.

## VLESS profile fields

- `name`: unique profile label.
- `host`: address clients connect to (IP or domain).
- `port`: external TCP/UDP service port number; current Xray profile builder models TCP listeners.
- `security`: one of `reality`, `tls`, `none`.
- `transport`: `tcp` or `ws`; REALITY is restricted to TCP by the builder.
- `sni`: TLS/REALITY server name.
- `public_key`, `short_id`: client-side REALITY values.
- `path`: WebSocket path when using WS.

Correct values depend on the actual server inbound. Do not copy a sample profile blindly or invent a fallback transport. Port profiles must correspond to real Xray/nginx listeners and permitted firewall rules before client links are distributed.

## Xray config builder

The builder is a library utility, not an installer. Example:

```python
from vless_control.xray_config import build_config, validate_config

config = build_config(profiles, clients)
validate_config(config)
```

The `write_config_atomically(config, path)` helper validates and replaces the destination file via a temporary file. It does **not** create a backup, check the host's active ports, invoke `xray run -test`, restart Xray, or roll back a bad runtime config. Do not point it at a live server config until those safeguards exist and the operator has reviewed the change.

## Security

- Never commit `.env`, bot tokens, private keys, SQLite databases, generated VLESS links/UUIDs, sessions, or host backups.
- A VLESS URI contains access credentials. Share it only through a private channel and never log it.
- Configure numeric admin IDs; missing/empty allowlists must stay fail-closed.
- Use a dedicated bot token and a restricted service account when deploying a future service.
- Current deactivation is a registry-only action, not full runtime revocation. Do not rely on it to terminate access until Xray synchronization is implemented.
- Before any future server configuration change: inspect services and bound ports, verify a backup, validate generated config with the installed Xray binary, apply atomically, check service health, and have a tested rollback.
- Public GitHub means every tracked file and history is visible. Review staged changes for secrets before pushing.

## Testing

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q vless_control tests
```

The unit tests cover registry relationships, UUID reuse over multiple profile links, user deactivation, REALITY parameter validation, duplicate listener detection, and REALITY/TCP restrictions. They do not prove that Xray accepts the generated config or that any client can connect.

## Server installation status

The repository is **not yet an installable server package**. It has no `install.sh`, supported-OS preflight, Xray binary installer, systemd unit, firewall integration, runtime config application, or uninstall script. For now, follow only the local Python/bot steps. Do not run a guessed one-line curl-to-shell installer.

When server installation is implemented, the intended installer must:

1. Support Ubuntu 22.04/24.04 and Debian 12 only, and stop on unsupported OS/release.
2. Require explicit confirmation before changing the host; never silently overwrite existing Xray/nginx/firewall configs.
3. Back up all affected files and verify archives before edits.
4. Inspect occupied ports and existing services before choosing listeners.
5. Validate generated config using the installed Xray executable before applying it.
6. Apply atomically, restart only the dedicated project service, confirm listeners/health, and roll back automatically on failure.
7. Keep credentials/private keys out of Git and ensure uninstall preserves unrelated services/data.

## Troubleshooting

- **Bot refuses to start:** confirm `TELEGRAM_BOT_TOKEN` and at least one numeric `TELEGRAM_ADMIN_IDS` are exported in that process environment.
- **Bot ignores a command:** verify the sender's numeric Telegram ID is allowlisted; commands are deliberately admin-only.
- **`/links` returns no profiles:** ensure the user is active, profiles exist and are active, and `/assign` used existing numeric IDs.
- **Deactivated key still connects:** expected limitation—runtime Xray revocation is not implemented yet.
- **A fallback URI does not connect:** confirm the listener actually exists and the client imported the correct URI. A server-side open port does not prove reachability from that device/operator.
- **REALITY profile is rejected:** check client/server SNI, public/private key pair, short ID, flow, Xray version, and client compatibility against the real inbound.

## Roadmap

- [ ] Profile management through guided Telegram buttons.
- [ ] Durable audit records and explicit revoke confirmation.
- [ ] Config synchronization with unique per-user UUIDs across selected inbounds.
- [ ] Dry-run host preflight for supported Ubuntu/Debian releases.
- [ ] Reviewed, reversible Xray installer and uninstaller.
- [ ] Xray config test/reload with backup and rollback.
- [ ] Safe link delivery to a selected user and multi-profile fallback UX.
- [ ] Expiry/quota controls and operational monitoring.
- [ ] Integration tests on disposable Ubuntu 22.04/24.04 and Debian 12 systems.
