# Quickstart — Windows 11

This guide installs memvault-os on Windows 11 with Docker Desktop + WSL2. Both the **GPU (vLLM via WSL CUDA)** and **CPU (ONNX)** embedding paths are supported. The MLX path is macOS-only — vLLM is the recommended Windows GPU backend.

## 1. Prerequisites

| Component | Required version | Verify |
|-----------|------------------|--------|
| Windows 11 | 22H2 or later | `winver` |
| WSL2 | latest | `wsl --version` (kernel 5.15+) |
| Docker Desktop | 4.30+ | `docker --version` from PowerShell |
| PowerShell | 7+ recommended | `pwsh --version` |
| Free disk | ≥ 10 GB on the WSL volume | `wsl df -h /` |
| Free RAM | ≥ 6 GB (Docker Desktop reserves 2 GB+) | Task Manager |
| NVIDIA GPU (optional) | Driver ≥ 555 with WSL CUDA | `nvidia-smi` |

### Install WSL2

```powershell
# Run as Administrator
wsl --install -d Ubuntu
# After reboot:
wsl --set-default-version 2
wsl --update
```

Confirm:

```powershell
wsl --list --verbose
# NAME      STATE      VERSION
# Ubuntu    Running    2
```

### Configure Docker Desktop

1. Settings → **General**: enable **Use the WSL 2 based engine**.
2. Settings → **Resources → WSL Integration**: enable for the Ubuntu distro.
3. Settings → **Resources → Advanced**: Memory ≥ 6 GB, Disk image size ≥ 60 GB.
4. (GPU only) Settings → **Resources → WSL Integration**: keep default; GPU is exposed automatically when `nvidia-smi` works in WSL.

Verify GPU exposure:

```powershell
wsl -- nvidia-smi
# +---------------------------------------------------+
# | NVIDIA-SMI 555.xx Driver Version 555.xx CUDA 12.5 |
# +---------------------------------------------------+
```

If `nvidia-smi` works in WSL, the installer will pick the vLLM backend automatically.

## 2. Install in one command

Open **PowerShell** (regular, not admin) in the folder where you want memvault-os cloned, then:

```powershell
iwr -useb https://raw.githubusercontent.com/operonlab/memvault-os/main/scripts/install.ps1 | iex
```

Or, if you've cloned the repo:

```powershell
git clone https://github.com/operonlab/memvault-os.git
cd memvault-os
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The script:

1. Detects WSL2 + Docker Desktop.
2. Detects optional NVIDIA GPU via `wsl -- nvidia-smi`.
3. Generates `.env` (calls `scripts\generate-secrets.ps1`).
4. Picks `EMBED_BACKEND=vllm_proxy` (GPU) or `onnx` (CPU).
5. Prompts for an LLM provider.
6. `docker compose pull && up -d`.
7. Polls health for 90 s.
8. Runs `alembic upgrade head`.
9. Opens `http://localhost:3000` in your default browser.

## 3. Verify

```powershell
curl.exe http://localhost:8080/health/readiness
# {"status":"ok","checks":{"database":"ok","redis":"ok","qdrant":"ok"}}
```

Open <http://localhost:3000>.

## 4. Windows-specific troubleshooting

### Windows Defender / SmartScreen blocks `install.ps1`

PowerShell may refuse to run a script downloaded from the internet:

```
File install.ps1 is not digitally signed.
```

Either bypass for this one script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Or unblock the file:

```powershell
Unblock-File -Path scripts\install.ps1
```

### `docker` works in PowerShell but not WSL

Docker Desktop's WSL integration toggle was off when you installed Ubuntu. Toggle it on (Settings → Resources → WSL Integration → Ubuntu), then close and reopen the WSL terminal.

### WSL CUDA driver missing

Symptom: `wsl -- nvidia-smi` returns "command not found" or driver mismatch.

Fix: install the **Game Ready / Studio driver ≥ 555** on Windows. WSL2 inherits the host driver; do NOT install a separate driver inside Ubuntu. After upgrading Windows driver, restart WSL:

```powershell
wsl --shutdown
```

### Hyper-V conflict with VMware / VirtualBox

Symptom: Docker Desktop won't start, or VMware / VirtualBox throws "VT-x not available".

Cause: Hyper-V (required by WSL2) and third-party hypervisors are mutually exclusive on the same boot. Options:

- Disable Hyper-V to use VMware/VBox: `bcdedit /set hypervisorlaunchtype off` then reboot. memvault-os will not run until you re-enable.
- Use VMware 17+ / VirtualBox 7+, which support running on top of Hyper-V (slower but coexists).
- Recommended: dedicate the Windows host to WSL2 and run other VMs elsewhere.

Re-enable Hyper-V: `bcdedit /set hypervisorlaunchtype auto` + reboot.

### Port already in use

Edit `.env` from PowerShell:

```powershell
Add-Content .env "API_PORT=18080"
Add-Content .env "WEB_PORT=13000"
docker compose -f infra\docker-compose.yml --env-file .env up -d
```

### `host.docker.internal` not resolving inside containers

Docker Desktop on Windows resolves `host.docker.internal` automatically. If a container can't reach it, you're probably running raw Docker inside WSL (not Docker Desktop). Either install Docker Desktop or set the embed backend to `onnx`/`vllm_proxy` (neither needs `host.docker.internal` on Linux).

### vLLM container OOM

Symptom: vLLM container restarts in a loop with `CUDA out of memory`.

Fix: reduce concurrency or model size. Edit `infra/docker-compose.gpu.yml` and lower `--max-num-seqs` and `--gpu-memory-utilization`, or pin a smaller model in `.env`:

```bash
EMBED_MODEL=Qwen/Qwen3-Embedding-0.6B
```

8 GB GPUs typically need the 0.6B model; 12 GB+ can run the 4B variant.

### Slow file IO

If you cloned the repo to `/mnt/c/...` from WSL, IO will be ~10× slower than `~/memvault-os` inside the WSL ext4 filesystem. Always clone inside the Linux home:

```bash
# inside WSL Ubuntu
cd ~ && git clone https://github.com/operonlab/memvault-os.git
```

### Reset everything

```powershell
docker compose -f infra\docker-compose.yml down -v
Remove-Item .env
```

Then re-run `scripts\install.ps1`.

## Next steps

- [`api-reference.md`](./api-reference.md) — all 66 endpoints
- [`operations.md`](./operations.md) — backup, restore, upgrade, doctor
- [`quickstart-linux.md`](./quickstart-linux.md) — Linux equivalent (most options also apply to WSL)
