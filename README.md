# memvault-os

<p align="center">
  <strong><a href="README.md">English</a></strong> | <a href="README.zh.md">繁體中文</a>
</p>

<p align="center">
  <a href="https://github.com/operonlab/memvault-os/releases"><img alt="Release" src="https://img.shields.io/github/v/release/operonlab/memvault-os?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.12+-blue?style=flat-square"></a>
  <a href="https://github.com/operonlab/memvault-os/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/operonlab/memvault-os?style=flat-square"></a>
  <a href="https://github.com/operonlab/memvault-os/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/operonlab/memvault-os?style=flat-square"></a>
  <a href="https://deepwiki.com/operonlab/memvault-os"><img alt="DeepWiki" src="https://img.shields.io/badge/DeepWiki-explore-blue?style=flat-square"></a>
</p>

Self-hosted long-term memory for LLM agents — knowledge graph + semantic search + dream-loop reflection, one-click install on macOS / Linux / Windows.

## Features

- **49 REST endpoints** — memory blocks CRUD, hybrid search, KG triples, communities, recall, dream loop, slow-thinker
- **Hybrid search** — Qdrant dense + BM25 fusion, plus Postgres tsvector full-text and CJK ILIKE
- **Knowledge graph** — auto-evolving triples, entity resolution, community summaries, PPR retrieval
- **Cross-platform embeddings** — auto-detects best backend: MLX on Apple Silicon, vLLM on NVIDIA GPU, FastEmbed CPU fallback everywhere
- **Multi-LLM** — bundled LiteLLM proxy, plug any OpenAI / Anthropic / Gemini / DeepSeek key
- **One-click install** — `install.sh` (macOS/Linux) / `install.ps1` (Windows) detects Docker, generates secrets, brings up the stack

## Quick Start

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/operonlab/memvault-os/main/scripts/install.sh | bash
```

```powershell
# Windows
irm https://raw.githubusercontent.com/operonlab/memvault-os/main/scripts/install.ps1 | iex
```

The installer will:
1. Detect your OS and GPU
2. Check Docker is installed and running
3. Generate secure secrets (`.env`)
4. Pick the right embedding backend (MLX / vLLM / FastEmbed)
5. Bring up the stack via `docker compose up -d`
6. Run database migrations
7. Open your browser to the UI at `http://localhost:3000`

## Status

🚧 **Under active development** — extracted from the [Workshop](https://github.com/JonesHong/workshop) modular monolith. First public release coming soon.

## License

MIT — see [LICENSE](./LICENSE).
