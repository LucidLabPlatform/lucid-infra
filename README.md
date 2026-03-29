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
