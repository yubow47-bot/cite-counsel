import os
import time
import logging

import requests

from local_tools import timing_util as timing
from local_tools.utils import canlii_session, request_with_retry
from profiling import timing as prof

logger = logging.getLogger(__name__)

CANLII_BASE = "https://api.canlii.org/v1"

# ── Track first HTTP call to CanLII ──
_first_canlii_call = True


def _mark_first_canlii() -> bool:
    global _first_canlii_call
    if _first_canlii_call:
        _first_canlii_call = False
        return True
    return False


def get_case_databases(language: str = "en") -> dict:
    """获取 CanLII 判例数据库列表。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    try:
        t0 = time.time()
        with prof.measure("http.canlii_case_databases", endpoint="/caseBrowse/{language}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/caseBrowse/{language}/",
                params={"api_key": key},
                read_timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"get_case_databases({language})", time.time() - t0)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII get_case_databases failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}


def get_case_metadata(
    database_id: str,
    case_id: str,
    language: str = "en",
) -> dict:
    """获取单个判例的元数据。

    Args:
        database_id: CanLII 数据库 ID。
        case_id: 判例 ID。
        language: "en" 或 "fr"。

    返回原始 JSON，不做字段映射。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    if not database_id:
        return {"error": "database_id required"}
    if not database_id.strip():
        return {"error": "database_id required"}
    if not case_id:
        return {"error": "case_id required"}
    if not case_id.strip():
        return {"error": "case_id required"}

    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    try:
        t0 = time.time()
        with prof.measure("http.canlii_case_metadata", endpoint="/caseBrowse/{language}/{database_id}/{case_id}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/caseBrowse/{language}/{database_id}/{case_id}/",
                params={"api_key": key},
                read_timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(
                f"get_case_metadata({database_id}/{case_id})",
                time.time() - t0,
            )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII get_case_metadata failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}


def get_legislation_databases(language: str = "en") -> dict:
    """获取 CanLII 法规数据库列表。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    try:
        t0 = time.time()
        with prof.measure("http.canlii_legislation_databases", endpoint="/legislationBrowse/{language}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/legislationBrowse/{language}/",
                params={"api_key": key},
                read_timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"get_legislation_databases({language})", time.time() - t0)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII get_legislation_databases failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}


def browse_legislation_in_database(
    database_id: str,
    language: str = "en",
) -> dict:
    """浏览指定法规数据库的全部法规列表。

    Args:
        database_id: CanLII 法规数据库 ID（如 "ons"、"cas"）。
        language: "en" 或 "fr"。

    返回原始 JSON，包含 "legislations" 列表。

    调用方契约：返回值可能是 {"error": "..."} 字典。集成时调用方
    必须先检查 `if "error" in result`，不可直接索引数据键，否则 KeyError。
    """
    if not database_id:
        return {"error": "database_id required"}
    if not database_id.strip():
        return {"error": "database_id required"}

    key = os.environ.get("CANLII_API_KEY")
    if not key:
        return {"error": "CANLII_API_KEY not configured"}

    try:
        _is_first = _mark_first_canlii()
        if _is_first:
            logger.debug("[DUR] CanLII browse_legislation_in_database — FIRST call (DNS + TCP setup expected)")
        _http_t0 = time.perf_counter()
        with prof.measure("http.canlii_legislation_browse", endpoint="/legislationBrowse/{language}/{database_id}/"):
            response = request_with_retry(
                canlii_session, "GET",
                f"{CANLII_BASE}/legislationBrowse/{language}/{database_id}/",
                params={"api_key": key},
                read_timeout=30,
            )
        _http_elapsed = time.perf_counter() - _http_t0
        logger.debug("[DUR] CanLII browse_legislation_in_database(%s) — %.1fms  first=%s", database_id, _http_elapsed * 1000, _is_first)
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(
                f"browse_legislation_in_database({database_id})",
                _http_elapsed,
            )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning("CanLII browse_legislation_in_database failed: %s", type(e).__name__)
        return {"error": "Legal database lookup failed. Try again or enter the citation manually."}
