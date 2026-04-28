# Quickstart — Linux

This guide installs memvault-os on Ubuntu / Debian / Fedora / Arch with one of the three embedding backends (vLLM GPU, ONNX CPU, or remote MLX). It assumes a fresh shell. For the macOS path see the README; for Windows see [`quickstart-windows.md`](./quickstart-windows.md).

## 1. Prerequisites

| Component | Minimum | Notes |
|-----------|---------|-------|
| Docker Engine | 24.0+ | `extra_hosts: host-gateway` requires 24.0 |
| Docker Compose plugin | v2.20+ | Bundled with Docker Desktop / `docker-compose-plugin` package |
| Disk | 5 GB free | Postgres + Qdrant + ONNX model |
| RAM | 4 GB free | LiteLLM + Postgres + Qdrant + api co-resident |
| nvidia-container-toolkit | only for GPU path | required iff you want vLLM embeddings |
| LLM API key | one of OpenAI / Anthropic / Gemini / DeepSeek / Ollama | enforced by installer |

Check:
```bash
docker --version            # Docker version 24.x or higher
docker compose version      # v2.20+
docker info | head -10      # daemon must be running
df -h .                     # ≥ 5 GB free where you'll clone
free -g                     # ≥ 4 GB available
nvidia-smi                  # only required for GPU backend
```

If `docker info` errors with "permission denied", add yourself to the docker group: `sudo usermod -aG docker $USER && newgrp docker`.

## 2. Install in one command

```bash
curl -fsSL https://raw.githubusercontent.com/operonlab/memvault-os/main/scripts/install.sh | bash
```

The installer:

1. Detects OS / arch.
2. Runs `scripts/preflight.sh` (Docker daemon, disk, RAM, port reachability).
3. Clones the repo to `~/memvault-os` (override with `MEMVAULT_INSTALL_DIR`).
4. Generates `.env` with random secrets.
5. **Picks an embedding backend** (see §3).
6. Prompts for an LLM provider (no skip option — LiteLLM needs at least one).
7. `docker compose pull && up -d`.
8. Polls health for 90 s.
9. Runs `alembic upgrade head` and verifies 17 tables.
10. Opens `scripts/post-install.html`.

If you prefer to inspect the script first:

```bash
git clone --depth 1 https://github.com/operonlab/memvault-os.git
cd memvault-os
less scripts/install.sh
bash scripts/install.sh
```

## 3. Three-tier embedding backend

`install.sh` picks one automatically. The decision tree:

```
macOS arm64 ───────────────────────► MLX sidecar (host process via host.docker.internal)
Linux + nvidia-smi works ──────────► vLLM container (docker-compose.gpu.yml)
otherwise ─────────────────────────► ONNX Runtime inside embed-gateway (CPU only)
```

Override by editing `.env`:

```bash
EMBED_BACKEND=onnx          # onnx | vllm_proxy | mlx_proxy
COMPOSE_FILE=infra/docker-compose.yml:infra/docker-compose.gpu.yml
```

### vLLM GPU path — extra steps

Install nvidia-container-toolkit:

```bash
# Ubuntu / Debian
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Then re-run `bash scripts/install.sh` so it picks up the GPU.

### ONNX CPU path — model download

The ONNX backend ships without weights to keep the image small. On first run, the entrypoint downloads `Qwen3-Embedding-0.6B` (~600 MB) into the `embed-models` volume. If your network blocks HuggingFace, mirror the model and set:

```bash
EMBED_MODEL_URL=https://your-mirror/qwen3-embedding-0.6b-onnx.tar.gz
```

If the volume is empty when the api boots, the gateway fails closed (HTTP 503) instead of silently emitting zero vectors.

### MLX (advanced) — Linux pointing at a remote Mac

If you have a Mac on the LAN running `infra/mlx-sidecar/`, set:

```bash
EMBED_BACKEND=mlx_proxy
EMBED_HOST=mlx.lan       # hostname / IP of the Mac
```

## 4. Verify

```bash
curl http://localhost:8080/health/readiness
# {"status":"ok","checks":{"database":"ok","redis":"ok","qdrant":"ok"}}

curl http://localhost:8080/api/memvault/status
# {"counts":{"blocks":0,"triples":0,"entities":0}, "backend":"onnx"}
```

Open `http://localhost:3000` for the web UI.

## 5. Troubleshooting

### Port 8080 / 3000 already in use

```bash
echo "API_PORT=18080" >> .env
echo "WEB_PORT=13000" >> .env
docker compose -f infra/docker-compose.yml --env-file .env up -d
```

### `docker compose pull` aborts with "manifest unknown" or sha256:000…

The repo ships placeholder digests until v1.0.0 release publishes images to ghcr.io. Switch to source build:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env build
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env up -d
```

### GPU detected but vLLM container fails

```bash
docker compose logs vllm
# CUDA error: no kernel image is available
```

Cause: container CUDA version newer than host driver. Either upgrade the driver (`sudo ubuntu-drivers autoinstall`) or pin an older vLLM tag in `infra/docker-compose.gpu.yml`.

### `host.docker.internal` not resolving

Linux Docker < 24.0 doesn't honour `extra_hosts: host-gateway`. Upgrade Docker:

```bash
curl -fsSL https://get.docker.com | sh
```

### Embedding requests time out

```bash
docker compose logs embed-gateway --tail 100
```

Common causes: ONNX model still downloading (give it 2–3 min on first boot), or LLM provider key wrong. Re-run `bash scripts/doctor.sh` for a structured diagnosis.

### Reset everything

```bash
bash scripts/uninstall.sh   # removes containers + volumes; keeps .env
```

## Next steps

- [`api-reference.md`](./api-reference.md) — all 66 endpoints
- [`operations.md`](./operations.md) — backup, restore, upgrade, doctor
- [`plan-v3.2.md`](./plan-v3.2.md) — full design rationale
