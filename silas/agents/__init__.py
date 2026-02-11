from __future__ import annotations

__all__ = ["ProxyAgent", "build_proxy_agent", "run_structured_agent"]


def __getattr__(name: str) -> object:
    if name in {"ProxyAgent", "build_proxy_agent"}:
        from silas.agents.proxy import ProxyAgent, build_proxy_agent

        return {"ProxyAgent": ProxyAgent, "build_proxy_agent": build_proxy_agent}[name]
    if name == "run_structured_agent":
        from silas.agents.structured import run_structured_agent

        return run_structured_agent
    raise AttributeError(name)
