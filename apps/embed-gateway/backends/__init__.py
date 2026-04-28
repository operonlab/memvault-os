"""embed-gateway backends — three-track embedding implementations."""

from . import mlx_proxy, onnx_runtime, vllm_proxy

__all__ = ["mlx_proxy", "onnx_runtime", "vllm_proxy"]
