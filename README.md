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
