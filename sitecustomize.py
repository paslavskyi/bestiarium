"""
Local test environment shims.

This module is imported automatically by Python if present on sys.path.
It is used here to smooth over version mismatches in third-party libraries.
"""

from __future__ import annotations

try:
    import pycares  # type: ignore[import]
except ImportError:  # pragma: no cover
    pycares = None  # type: ignore[assignment]


if pycares is not None:
    # Newer versions of aiodns expect several type aliases to exist on pycares,
    # but some pycares versions omit them. They are only used for type hints,
    # so providing placeholder objects is sufficient for runtime.
    _MISSING_ALIASES = (
        "ares_query_a_result",
        "ares_query_aaaa_result",
        "ares_query_cname_result",
        "ares_query_mx_result",
        "ares_query_naptr_result",
        "ares_query_ns_result",
        "ares_query_soa_result",
        "ares_query_srv_result",
        "ares_query_txt_result",
    )
    for _alias in _MISSING_ALIASES:
        if not hasattr(pycares, _alias):  # pragma: no cover
            setattr(pycares, _alias, object)

