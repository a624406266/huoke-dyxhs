from __future__ import annotations

from pathlib import Path
from typing import Any

from douyin_adapter import DouyinReadAdapter
from xhs_adapter import XhsAdapter


class PlatformAdapterRegistry:
    def __init__(
        self,
        config: dict[str, Any],
        root: Path,
        *,
        xhs_profile_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.root = root
        self.adapters: dict[str, Any] = {
            "xhs": XhsAdapter(config, root, profile_dir=xhs_profile_dir),
            "douyin": DouyinReadAdapter(config, root),
        }
        self.command = self.adapters["xhs"].command

    def adapter_for(self, platform: str) -> Any:
        key = str(platform or "xhs").strip().lower() or "xhs"
        if key not in self.adapters:
            raise RuntimeError(f"暂不支持平台：{platform}")
        return self.adapters[key]

    def status(self, timeout: int = 30) -> dict[str, Any]:
        return {platform: adapter.status(timeout=timeout) for platform, adapter in self.adapters.items()}
