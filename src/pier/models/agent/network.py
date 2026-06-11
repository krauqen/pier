from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class NetworkAllowlist(BaseModel):
    domains: list[str] = Field(
        default_factory=list,
        description=(
            "Exact domain or leading-dot suffix allowed by network policy, "
            "for example 'api.anthropic.com' or '.anthropic.com'."
        ),
    )
    proxy_domains: list[str] | None = Field(
        default=None,
        description=(
            "Optional domain policy for HTTP proxy ACLs when it must differ "
            "from the broader agent network allowlist."
        ),
    )

    @field_validator("domains", "proxy_domains")
    @classmethod
    def normalize_domains(cls, domains: list[str] | None) -> list[str] | None:
        if domains is None:
            return None
        normalized: set[str] = set()
        for value in domains:
            domain = value.strip().lower().rstrip(".")
            if not domain:
                raise ValueError("domain cannot be empty")
            if any(char in domain for char in "/:*"):
                raise ValueError("domain must be an exact domain or leading-dot suffix")
            normalized.add(domain)
        return sorted(normalized)
