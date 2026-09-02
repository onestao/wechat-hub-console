"""Decoupled WeChat Console package.

The implementation intentionally stays dependency-free to preserve the stdlib
HTTP-server shape used by the upstream linux-wechat-agent console while moving
all WeChat data access behind Core Interface Contract V1.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
