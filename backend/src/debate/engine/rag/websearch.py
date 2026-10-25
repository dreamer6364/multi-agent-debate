"""联网搜索层（v1.4.1）：辩手开赛时可注入实时网络资料。

多 provider，全部静默降级（搜索失败绝不阻塞比赛，回落纯本地证据卡）：
- zhipu : 智谱 web-search-pro（用现有 OPENAI_API_KEY，需搜索资源包）
- tavily: Tavily 搜索 API（TAVILY_API_KEY，免费档 1000 次/月，tavily.com 注册）
- ddgs  : DuckDuckGo（零 key；大陆网络通常不可达）
- none  : 禁用联网，仅本地证据卡

provider 选择：SEARCH_PROVIDER 环境变量显式指定；缺省 auto 探测
（有 TAVILY_API_KEY → tavily，否则 none）。
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from pydantic import BaseModel


class WebEvidence(BaseModel):
    title: str
    url: str
    snippet: str


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc or "web"
    except Exception:  # noqa: BLE001
        return "web"


def _to_card(ev: WebEvidence):
    """适配为证据卡结构，复用 render_evidence 注入通道。"""
    from .corpus import EvidenceCard

    return EvidenceCard(
        card_id="web",
        title=ev.title[:60],
        source=f"网络检索 · {_domain(ev.url)}",
        source_type="网络",
        text=f"{ev.snippet[:220]}\nURL: {ev.url}",
    )


def _search_zhipu(query: str, top_k: int) -> list[WebEvidence]:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        timeout=20,
    )
    resp = client.chat.completions.create(
        model="web-search-pro",
        messages=[{"role": "user", "content": query}],
        stream=False,
    )
    msg = resp.choices[0].message
    # 智谱把检索结果放 tool_calls/retrieve 或正文；正文场景提取标题-链接对
    out: list[WebEvidence] = []
    content = (msg.content or "").strip()
    if content:
        out.append(WebEvidence(title="检索摘要", url=os.environ.get("OPENAI_BASE_URL", "bigmodel.cn"),
                               snippet=content[:600]))
    return out[:top_k]


def _search_tavily(query: str, top_k: int) -> list[WebEvidence]:
    import httpx

    api_key = os.environ.get("TAVILY_API_KEY", "")
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": top_k},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        WebEvidence(title=item.get("title", ""), url=item.get("url", ""),
                    snippet=item.get("content", ""))
        for item in data.get("results", [])
    ][:top_k]


def _search_ddgs(query: str, top_k: int) -> list[WebEvidence]:
    from ddgs import DDGS

    return [
        WebEvidence(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
        for r in DDGS(timeout=10).text(query, max_results=top_k)
    ][:top_k]


_PROVIDERS = {"zhipu": _search_zhipu, "tavily": _search_tavily, "ddgs": _search_ddgs}


def resolve_provider() -> str:
    """SEARCH_PROVIDER 显式优先；缺省 auto：有 TAVILY_API_KEY → tavily，否则 none。"""
    explicit = (os.environ.get("SEARCH_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    return "tavily" if os.environ.get("TAVILY_API_KEY") else "none"


def web_search(query: str, top_k: int = 3, provider: str | None = None) -> list[WebEvidence]:
    """执行搜索；任何失败返回空列表（静默降级，绝不阻塞比赛）。"""
    name = (provider or resolve_provider()).lower()
    fn = _PROVIDERS.get(name)
    if fn is None:
        return []
    try:
        return fn(query, top_k)
    except Exception:  # noqa: BLE001 —— 联网是增强项，失败即回落本地证据卡
        return []


def search_as_cards(query: str, top_k: int = 3, provider: str | None = None):
    """搜索并适配为证据卡（可直接并入 render_evidence 注入）。"""
    return [_to_card(ev) for ev in web_search(query, top_k=top_k, provider=provider)]
