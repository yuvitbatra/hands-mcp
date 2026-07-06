"""get_ui_tree (DESIGN §5.15). AX ground truth alongside OCR."""
from __future__ import annotations

import anyio
from pydantic import BaseModel, Field

from ..registry import ToolRegistry, ToolSpec
from ..retry import RetryPolicy


class GetUiTreeArgs(BaseModel, extra="forbid"):
    app: str | None = None
    max_depth: int = Field(default=8, ge=1, le=20)


def register(registry: ToolRegistry, container) -> None:
    driver = container.driver
    apps = container.apps
    max_nodes = container.config.ax.max_nodes

    def _serialize(node, budget: list[int]) -> dict | None:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        children = []
        for c in node.children:
            s = _serialize(c, budget)
            if s is None:
                break
            children.append(s)
        out: dict = {"role": node.role, "title": node.title,
                     "value": node.value, "actions": list(node.actions),
                     "children": children}
        if node.region is not None:
            out["region"] = {"x": node.region.x, "y": node.region.y,
                             "width": node.region.width,
                             "height": node.region.height}
        return out

    async def get_ui_tree(args: GetUiTreeArgs, ctx) -> dict:
        pid = None
        if args.app is not None:
            needle = args.app.lower()
            for a in await apps.list_running():
                if needle in ((a.bundle_id or "").lower(),
                              a.name.lower()):
                    pid = a.pid
                    break
        tree = await anyio.to_thread.run_sync(
            driver.ax_tree, pid, args.max_depth)
        budget = [max_nodes]
        serialized = _serialize(tree, budget)
        return {"tree": serialized, "truncated": budget[0] <= 0}

    registry.register(ToolSpec(
        "get_ui_tree",
        "Accessibility tree for an app (frontmost if omitted): roles, "
        "titles, values, clickable regions in points. Ground truth where "
        "apps expose it; use find_text where they don't.",
        GetUiTreeArgs, get_ui_tree, "read", RetryPolicy.read(),
        idempotent=True))
