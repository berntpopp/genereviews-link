"""Verify that PyTorch can see a CUDA device."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import torch
    except ImportError:
        print("torch is not installed", file=sys.stderr)
        return 1
    print(f"torch={torch.__version__}")
    available = torch.cuda.is_available()
    print(f"cuda_available={available}")
    if not available:
        return 1
    print(f"cuda_device={torch.cuda.get_device_name(0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
