from __future__ import annotations

from pathlib import Path
from typing import Any

from douyin_adapter import DouyinReadAdapter
from xhs_adapter import XhsAdapter, XhsCliAdapter


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
            "xhs": XhsCliAdapter(config, root, profile_dir=xhs_profile_dir),
            "douyin": DouyinReadAdapter(config, root),
        }
        self.command = self.adapters["xhs"].command

    def adapter_for(self, platform: str) -> Any:
        key = str(platform or "xhs").strip().lower() or "xhs"
        if key not in self.adapters:
            raise RuntimeError(f"暂不支持平台：{platform}")
        return self.adapters[key]

    def ensure_authenticated(self) -> None:
        self.adapter_for("xhs").ensure_authenticated()

    def status(self, timeout: int = 30) -> dict[str, Any]:
        return {
            "xhs": self.adapter_for("xhs").status(timeout=timeout),
            "douyin": self.adapter_for("douyin").status(timeout=timeout),
        }

    def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        return self.adapter_for("xhs").search(keyword, page=page)

    def read_note(self, note_id: str) -> dict[str, Any]:
        return self.adapter_for("xhs").read_note(note_id)

    def comments(self, note_id: str, cursor: str = "") -> dict[str, Any]:
        return self.adapter_for("xhs").comments(note_id, cursor=cursor)

    def follow_user(self, user_id: str) -> dict[str, Any]:
        return self.adapter_for("xhs").follow_user(user_id)


__all__ = ["PlatformAdapterRegistry", "XhsAdapter", "XhsCliAdapter", "DouyinReadAdapter"]
