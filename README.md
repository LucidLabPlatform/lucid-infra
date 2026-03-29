# lucid-infra

Deployment repo for the split LUCID Central Command stack.

Services:
- `lucid-db`
- `lucid-auth`
- `emqx`
- `lucid-fleet-core`
- `lucid-automation`
- `lucid-ai`
- `lucid-ui`
- `ollama`
- `emqx-provisioner`

Bring the stack up from this repo with:

```bash
cp .env.example .env
docker compose up -d --build
```

Minimal stack for the current DB + broker phase:

```bash
cp .env.minimal.example .env
docker compose -f docker-compose.minimal.yml up -d --build
```

This minimal Compose file only runs:
- `lucid-db`
- `emqx`
- `emqx-provisioner`

It intentionally leaves out `lucid-auth`, so EMQX runs with anonymous MQTT enabled in this variant.

Focused secure stack for the current DB + broker + auth phase:

```bash
cp .env.auth.example .env
docker compose -f docker-compose.auth.yml up -d --build
```

This auth-focused Compose file only runs:
- `lucid-db`
- `lucid-auth`
- `emqx`
- `emqx-provisioner`

In this variant:
- EMQX authenticates local users from its built-in database
- EMQX falls back to LDAP for researcher logins
- EMQX authorizes users from its built-in database ACL rules
- `lucid-auth` provisions the broker users and ACL rules through the EMQX management API

It is the smallest stack that enables MQTT authentication/authorization and LDAP-backed researcher access without bringing up the rest of Central Command.
