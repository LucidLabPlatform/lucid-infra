# lucid-infra

Deployment repo for the LUCID Central Command stack. Uses **git submodules** to pull in service repos.

## Quick start

```bash
# Clone with submodules
git clone --recurse-submodules https://github.com/LucidLabPlatform/lucid-infra.git
cd lucid-infra

# Or if already cloned without submodules
git submodule update --init --recursive

# Configure environment — ALL values are required (no defaults)
cp .env.example .env
# Edit .env and fill in every blank value

# Full stack
docker compose up -d --build

# Minimal stack (DB + EMQX only, anonymous MQTT)
docker compose -f docker-compose.minimal.yml up -d --build

# Auth stack (DB + EMQX + lucid-auth, authenticated MQTT)
docker compose -f docker-compose.auth.yml up -d --build
```

## Submodules

| Submodule | Purpose |
|-----------|---------|
| `lucid-db` | TimescaleDB schema and image |
| `lucid-emqx` | EMQX broker image and provisioner |
| `lucid-auth` | MQTT credential and ACL management |

Update submodules to latest:

```bash
git submodule update --remote --merge
```

## Environment

All configuration lives in a single `.env` file in this repo. **No defaults are baked into code or compose files** — if a required variable is missing, the service will fail to start.

See `.env.example` for the full variable list.

### Optional: LDAP

LDAP authentication is disabled by default. To enable it, set `LDAP_SERVER` and the other `LDAP_*` variables in `.env`. When `LDAP_SERVER` is empty, the EMQX entrypoint automatically strips the LDAP authenticator.

## Compose variants

| File | Services | MQTT auth |
|------|----------|-----------|
| `docker-compose.yml` | All (DB, EMQX, auth, fleet-core, automation, AI, UI, Ollama) | Authenticated |
| `docker-compose.auth.yml` | DB, EMQX, auth, provisioner | Authenticated |
| `docker-compose.minimal.yml` | DB, EMQX, provisioner | Anonymous |
