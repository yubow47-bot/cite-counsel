import os
import time

import requests

from local_tools import timing_util as timing
from profiling import timing as prof

CANLII_BASE = "https://api.canlii.org/v1"


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
            response = requests.get(
                f"{CANLII_BASE}/caseBrowse/{language}/",
                params={"api_key": key},
                timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"get_case_databases({language})", time.time() - t0)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"CanLII request failed: {e}"}


def browse_cases(
    database_id: str,
    offset: int = 0,
    result_count: int = 10,
    language: str = "en",
) -> dict:
    """浏览指定数据库中的判例列表。

    Args:
        database_id: CanLII 数据库 ID（如 "scc-csc"）。
        offset: 分页偏移量。
        result_count: 返回条数（1-50）。
        language: "en" 或 "fr"。

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
        t0 = time.time()
        with prof.measure("http.canlii_browse_cases", endpoint="/caseBrowse/{language}/{database_id}/"):
            response = requests.get(
                f"{CANLII_BASE}/caseBrowse/{language}/{database_id}/",
                params={
                    "offset": offset,
                    "resultCount": result_count,
                    "api_key": key,
                },
                timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(
                f"browse_cases({database_id}, offset={offset}, count={result_count})",
                time.time() - t0,
            )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"CanLII request failed: {e}"}


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
            response = requests.get(
                f"{CANLII_BASE}/caseBrowse/{language}/{database_id}/{case_id}/",
                params={"api_key": key},
                timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(
                f"get_case_metadata({database_id}/{case_id})",
                time.time() - t0,
            )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"CanLII request failed: {e}"}


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
            response = requests.get(
                f"{CANLII_BASE}/legislationBrowse/{language}/",
                params={"api_key": key},
                timeout=15,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(f"get_legislation_databases({language})", time.time() - t0)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"CanLII request failed: {e}"}


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
        t0 = time.time()
        with prof.measure("http.canlii_legislation_browse", endpoint="/legislationBrowse/{language}/{database_id}/"):
            response = requests.get(
                f"{CANLII_BASE}/legislationBrowse/{language}/{database_id}/",
                params={"api_key": key},
                timeout=30,
            )
        if timing.ENABLE_TIMING:
            timing.report().add_a2aj(
                f"browse_legislation_in_database({database_id})",
                time.time() - t0,
            )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"CanLII request failed: {e}"}
