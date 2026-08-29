"""Pinned assets for the Hermes-managed llama-server MVP."""

from __future__ import annotations


_RELEASE_ROOT = "https://github.com/ggml-org/llama.cpp/releases/download/b10666"

BUILTIN_LOCAL_MANIFEST = {
    "schema": "hermes-local-manifest@1",
    "models": [
        {
            "id": "qwen2.5-7b-instruct",
            "display_name": "Qwen2.5 7B Instruct",
            "family": "qwen2.5",
            "license": "Apache-2.0",
            "license_url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/blob/main/LICENSE",
            "license_file": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/LICENSE",
            "license_file_sha256": "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e",
            "copyright": "Copyright 2024 Alibaba Cloud",
            "tool_calling": True,
            "tool_calling_verified_at": "llama.cpp b10666",
            "recommended": True,
            "releases": [
                {
                    "quant": "Q4_K_M",
                    "context_length": 16384,
                    "urls": [
                        "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
                        "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
                    ],
                    "sha256": [
                        "dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db",
                        "539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a",
                    ],
                    "size_bytes": 4683073632,
                    "vram_estimate_gb": 4.8,
                    "backend": "cuda",
                    "cold_start_s": 4.08,
                    "first_token_ms": 222,
                    "tokens_per_second": 57.4,
                    "gpu": "NVIDIA GeForce RTX 4070",
                    "vram_total_gb": 12.0,
                    "measured_at": "2026-08-28T16:33:16+02:00",
                }
            ],
        }
    ],
    "runtimes": [
        {
            "version": "b10666",
            "platform": "linux",
            "machine": "x86_64",
            "backend": "vulkan",
            "assets": [{
                "kind": "build",
                "url": f"{_RELEASE_ROOT}/llama-b10666-bin-ubuntu-vulkan-x64.tar.gz",
                "sha256": "50fe0c5ffe5d28a8b7c27b083e6f159592eb6d9554c234c434dac43f7bb42588",
            }],
        },
        {
            "version": "b10666",
            "platform": "linux",
            "machine": "aarch64",
            "backend": "vulkan",
            "assets": [{
                "kind": "build",
                "url": f"{_RELEASE_ROOT}/llama-b10666-bin-ubuntu-vulkan-arm64.tar.gz",
                "sha256": "7293e6a49668e89b1d846b93151f3323bf29d99a73933a44264da0ac3cd5938f",
            }],
        },
        {
            "version": "b10666",
            "platform": "win32",
            "machine": "amd64",
            "backend": "vulkan",
            "assets": [{
                "kind": "build",
                "url": f"{_RELEASE_ROOT}/llama-b10666-bin-win-vulkan-x64.zip",
                "sha256": "4543b371864d087f2fabdd99c5eb2903fdb8d603a8ba594942c32d8688c15366",
            }],
        },
        {
            "version": "b10666",
            "platform": "win32",
            "machine": "amd64",
            "backend": "cuda",
            "assets": [
                {
                    "kind": "build",
                    "url": f"{_RELEASE_ROOT}/llama-b10666-bin-win-cuda-12.4-x64.zip",
                    "sha256": "4bf69bd5cd21d9ce02b005fddee5a0d52fde88d076310e49b599065d8dfb3638",
                },
                {
                    "kind": "cuda-runtime",
                    "url": f"{_RELEASE_ROOT}/cudart-llama-bin-win-cuda-12.4-x64.zip",
                    "sha256": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
                },
            ],
        },
        {
            "version": "b10666",
            "platform": "darwin",
            "machine": "arm64",
            "backend": "cpu",
            "assets": [{
                "kind": "build",
                "url": f"{_RELEASE_ROOT}/llama-b10666-bin-macos-arm64.tar.gz",
                "sha256": "f2b5d7b445cfcdab2abe53e0e6e697790094fb902ef2bdaafd23c813bb297cbb",
            }],
        },
        {
            "version": "b10666",
            "platform": "darwin",
            "machine": "x86_64",
            "backend": "cpu",
            "assets": [{
                "kind": "build",
                "url": f"{_RELEASE_ROOT}/llama-b10666-bin-macos-x64.tar.gz",
                "sha256": "5af9cd7fbcc226dbdba8d24e66e07b732903fc58eff0e38d829f04264f8d4601",
            }],
        },
    ],
}
