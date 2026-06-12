import json
import os
import re
import time

from llm_api.deepseek_api import ask_deepseek
from local_tools.a2aj_api import fetch_by_citation, search_cases_multi, _map_fields, _extract_year, _extract_jurisdiction
from local_tools import timing_util as timing
from profiling import timing as prof


def classify_and_normalize(query: str) -> dict:
    """判断输入类型并标准化。"""
    prompt = f"""你是加拿大法律引用专家。分析以下用户输入，完成两件事：
1. 判断输入类型（只能是以下四种之一）：
   - citation_number：已知的引用号，如 "2022 SCC 39"、"[1999] 1 SCR 688"、"RSC 1985, c C-46"
   - case_name：案件名，如 "R v Gladue"、"R. v. Sharma"、"Regina v Jordan"
   - legislation：法条名或法条缩写，如 "Criminal Code"、"CCC"、"Charter"、"CCC s.718.2(e)"
   - concept：法律概念或原则，如 "gladue principle"、"right to housing"、"duty to consult"

2. 标准化输入：
   - 案件名：去掉句号（R. v. → R v），Regina/The Queen → R，去掉末尾的 "case"
   - 法条缩写：展开成完整引用（CCC → Criminal Code, RSC 1985, c C-46）
   - 法条+条款混合：展开法条名，保留条款（CCC s.718.2(e) → Criminal Code, RSC 1985, c C-46, s 718.2(e)）
   - 条款格式：s.718 → s 718（去掉句号）
   - 法语输入同样处理（R. c. → R c）

只返回 JSON，不要任何解释：
{{"type": "类型", "normalized": "标准化后的输入", "original": "原始输入"}}

用户输入：{query}"""

    try:
        t0 = time.time()
        with prof.measure("llm.classify", model="deepseek-chat"):
            content = ask_deepseek(prompt)
        if timing.ENABLE_TIMING:
            timing.report().add_llm("classify_and_normalize", time.time() - t0)
        result = json.loads(content)
        if result.get("type") in ("citation_number", "case_name", "legislation", "concept"):
            return result
    except Exception:
        pass
    return {"type": "case_name", "normalized": query, "original": query}



def _diagnose_parse_failure(raw: str) -> str:
    """Analyze raw LLM output to determine why _parse_llm_output failed."""
    stripped = raw.strip()
    # 1) Check for markdown code fences
    if "```" in stripped:
        # Check if fences are properly balanced
        fence_count = stripped.count("```")
        if fence_count < 2:
            return f"unmatched_backtick_fence(count={fence_count})"
        # Try stripping fences and re-parse
        cleaned = re.sub(r'^```(?:json)?\s*', '', stripped)
        cleaned = re.sub(r'\s*```$', '', cleaned).strip()
        if cleaned:
            try:
                json.loads(cleaned)
                return "fence_strippable_json_ok"  # fences were the only issue
            except json.JSONDecodeError as e:
                pos = e.pos
                snippet = cleaned[max(0, pos-20):pos+20]
                return f"fence_stripped_json_invalid(pos={pos}, snippet={snippet!r})"
        else:
            return "fence_only_no_content"
    # 2) Try direct JSON parse (no fences)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as e:
        pos = e.pos
        snippet = stripped[max(0, pos-20):pos+20]
        return f"json_decode_error(pos={pos}, msg={e.msg}, snippet={snippet!r})"
    # 3) JSON parsed but schema wrong
    if isinstance(obj, dict):
        if "candidates" not in obj:
            keys = list(obj.keys())
            return f"missing_candidates_key(keys={keys})"
        cand = obj["candidates"]
        if not isinstance(cand, list):
            return f"candidates_not_list(type={type(cand).__name__})"
        if len(cand) == 0:
            return "empty_candidates_list"
        return f"unknown_filter(parsed_ok_candidates={len(cand)})"
    if isinstance(obj, list):
        return f"top_level_list_not_dict(len={len(obj)})"
    return f"unexpected_type(type={type(obj).__name__})"


def _log_retry_event(query: str, attempt: int, raw: str,
                     reason: str, succeeded: bool):
    """Append one retry diagnostic record to profiling/expand_retry_log.jsonl."""
    log_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "profiling", "expand_retry_log.jsonl"
    )
    record = {
        "input_concept": query,
        "attempt": attempt,
        "raw_response": raw,
        "failure_reason": reason,
        "finally_succeeded": succeeded,
    }
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def expand_concept(query: str) -> list:
    """展开法律概念：源头案件 + 法条 + 后续案件（并发验证，结果稳定）。"""
    prompt = f"""You are a Canadian legal citation expert. The user query is: "{query}"

A legal concept can have two types of sources:
(a) Statutory basis — the relevant legislation, code, or section (if one exists)
(b) Case precedents — the landmark decisions that established or developed the concept

List 3-6 candidates covering BOTH types where applicable.
Return ONLY strict JSON, no markdown, no other text:

{{"candidates": [
  {{"name": "Criminal Code, RSC 1985, c C-46, s 718.2(e)", "citation": null, "type": "legislation"}},
  {{"name": "R v Gladue", "citation": "[1999] 1 SCR 688", "type": "case"}},
  {{"name": "R v Ipeelee", "citation": "2012 SCC 13", "type": "case"}}
]}}

Rules:
- "type": "case" for court decisions, "legislation" for statutes / acts / codes.
- For "case": "citation" must contain ONLY the neutral citation number (e.g. "[1999] 1 SCR 688"),
  NOT the case name. If unsure, set to null.
- For "legislation": "citation" should contain the statute citation (e.g. "RSC 1985, c C-46").
  If unsure, set to null; the system will search by name instead.
- "name" must be the full case name or statute title. Do NOT include citation number in name.
- If the concept flows from a statute, include it as one candidate with type "legislation".
  Include at least 2-3 key cases with type "case".
- List only what you are confident about. Quality over quantity."""

    def _parse_llm_output(content: str) -> list | None:
        """解析 LLM 输出：剥离 ```json 标记后 json.loads。"""
        cleaned = content.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        cleaned = cleaned.strip()
        try:
            obj = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        candidates = obj.get("candidates") if isinstance(obj, dict) else obj
        if isinstance(candidates, list):
            return candidates
        return None

    # ── 第 1 次 LLM 调用 ──
    t0 = time.time()
    with prof.measure("llm.expand", model="deepseek-chat", attempt=1):
        raw_first = ask_deepseek(prompt)
    if timing.ENABLE_TIMING:
        timing.report().add_llm("expand_concept (首次)", time.time() - t0)
    items = _parse_llm_output(raw_first)

    # ── 解析失败则重试一次 ──
    if not items:
        # 诊断：记录首次失败原因
        fail_reason = _diagnose_parse_failure(raw_first)
        print(f"[WARN] expand_concept 首次解析失败: {fail_reason}")
        t0 = time.time()
        with prof.measure("llm.expand", model="deepseek-chat", attempt=2):
            raw_retry = ask_deepseek(prompt)
        if timing.ENABLE_TIMING:
            timing.report().add_llm("expand_concept (重试)", time.time() - t0)
        items = _parse_llm_output(raw_retry)
        _log_retry_event(query, 1, raw_first, fail_reason,
                         succeeded=bool(items))

    if not items:
        print(f"[WARN] expand_concept 重试后仍解析失败，返回空。")
        return []

    # ── 并发验证 ──
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _verify_case(name: str, citation: str) -> dict:
        """验证判例候选：citation 优先 /fetch，失败/为空则按案名搜索。"""
        entry = {"verified": False}
        if citation:
            try:
                verified = fetch_by_citation(citation)
                if "error" not in verified and "raw_input" not in verified:
                    entry["verified"] = True
                    entry.update(verified)
                    return entry
            except Exception:
                pass
        if name:
            try:
                results = search_cases_multi(name, size=1, search_type="name")
                if results:
                    mapped = _map_fields(results[0])
                    entry["verified"] = True
                    entry.update(mapped)
                    return entry
            except Exception:
                pass
        entry["warning"] = "⚠️ 未能在数据库验证该判例"
        return entry

    def _verify_legislation(name: str) -> dict:
        """验证法规候选：标准化 → A2AJ /fetch(doc_type=laws)。
        与 search_citation() legislation 路由做法一致。
        """
        import requests as _req
        import os as _os

        entry = {"verified": False}

        # 1. 用 normalization_rules.json 展开缩写
        normalized = name
        rules_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(__file__)),
            "data", "normalization_rules.json"
        )
        try:
            with open(rules_path, encoding='utf-8') as _f:
                rules = json.load(_f)
            abbrevs = rules.get("legislation_abbreviations", {})
            if name in abbrevs:
                normalized = abbrevs[name]
            else:
                for abbr in sorted(abbrevs, key=lambda x: -len(x)):
                    if name.lower().startswith(abbr.lower()):
                        normalized = name[:len(abbr)].replace(abbr, abbrevs[abbr]) + name[len(abbr):]
                        break
        except Exception:
            pass

        # 2. 提取引用号（与 legislation 路由同一正则）
        cit_match = re.search(
            r"(?:RSC|SC|SOR|RRO|O\sReg|BC\sReg|RLRQ)\s[^,]+(?:,\s*c\s[^,]+)?",
            normalized
        )
        base_citation = cit_match.group(0).strip() if cit_match else normalized

        # 3. A2AJ /fetch(doc_type="laws")
        try:
            _t0 = time.time()
            resp = _req.get(
                "https://api.a2aj.ca/fetch",
                params={"citation": base_citation, "doc_type": "laws"},
                timeout=15
            )
            if timing.ENABLE_TIMING:
                timing.report().add_a2aj(f"expand_concept legislation verify ({base_citation[:30]})", time.time() - _t0)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                entry["verified"] = True
                entry["statute_title"] = results[0].get("name_en", normalized)
                entry["neutral_citation"] = results[0].get("citation_en", base_citation)
                return entry
        except Exception:
            pass

        entry["warning"] = "⚠️ 未能在数据库验证该法规"
        return entry

    def verify_one(item: dict) -> dict | None:
        name = item.get("name", "")
        citation = item.get("citation") or ""
        ctype = item.get("type", "case")
        if not name and not citation:
            return None

        entry = {
            "name": name,
            "neutral_citation": citation or None,
            "role": ctype,
            "verified": False,
        }

        if ctype == "legislation":
            result = _verify_legislation(name)
            entry.update(result)
            return entry
        else:
            result = _verify_case(name, citation)
            entry.update(result)
            return entry

    with ThreadPoolExecutor(max_workers=5) as executor:
        fut_map = {executor.submit(verify_one, item): i for i, item in enumerate(items)}
        ordered = [None] * len(items)
        for future in as_completed(fut_map):
            idx = fut_map[future]
            try:
                ordered[idx] = future.result()
            except Exception:
                ordered[idx] = None

    return [r for r in ordered if r is not None]


def search_citation(query: str, classification: dict | None = None) -> list:
    """主入口：分类 → 标准化 → 搜索/验证。

    Args:
        query: 用户原始输入
        classification: 可选。外部已算好的分类结果（避免重复 LLM 调用）。
                       为 None 时自动调用 classify_and_normalize（向后兼容）。
    """
    if classification is None:
        classified = classify_and_normalize(query)
    else:
        classified = classification
    print(f"[DEBUG] 分类结果: {classified}")
    input_type = classified["type"]
    normalized = classified["normalized"]

    # 1. citation_number：按引用号精确查
    if input_type == "citation_number":
        result = fetch_by_citation(normalized)
        if "error" not in result and "raw_input" not in result:
            result["verified"] = True
            return [result]
        return [{
            "name": normalized,
            "verified": False,
            "warning": "⚠️ 未能通过 A2AJ 验证，建议在 CanLII 手动确认",
        }]

    # 2. case_name：按名称搜索 + 可选年份过滤
    elif input_type == "case_name":
        # 从原始输入提取年份，与 DeepSeek 标准化互不干扰
        _, year = _extract_year(classified["original"])
        start_date = f"{year}-01-01" if year else None
        end_date = f"{year}-12-31" if year else None
        if year:
            print(f"[DEBUG] 从原始输入提取到年份: {year} → {start_date} ~ {end_date}")

        # 提取核心关键词（去掉 R v / R c / Regina v 等前缀）
        keyword = re.sub(
            r"^(?:R\s+v|R\s+c|Regina\s+v|The\s+Queen\s+v)\s+",
            "",
            normalized,
            flags=re.IGNORECASE
        ).strip()

        title_matches = []
        fulltext_pool = []
        MAX_BATCHES = 3

        for batch in range(MAX_BATCHES):
            t0 = time.time()
            batch_results = search_cases_multi(
                normalized, size=40, offset=batch * 40,
                start_date=start_date, end_date=end_date,
            )
            if timing.ENABLE_TIMING:
                timing.report().add_a2aj(f"search_cases_multi batch={batch+1} ({normalized[:30]})", time.time() - t0)
            if not batch_results:
                break

            if batch == 0:
                fulltext_pool = batch_results

            for r in batch_results:
                name = r.get("name_en", "")
                if keyword.lower() in name.lower() and r not in title_matches:
                    title_matches.append(r)
                if len(title_matches) >= 2:
                    break

            if len(title_matches) >= 2:
                break

        seen = set()
        final = []
        for r in title_matches + fulltext_pool:
            key = r.get("citation_en") or r.get("name_en")
            if key and key not in seen:
                seen.add(key)
                final.append(r)
            if len(final) >= 5:
                break

        if not final:
            return []
        return [dict(_map_fields(r), verified=True) for r in final]

    # 3. legislation：A2AJ /fetch (doc_type=laws) 验证
    elif input_type == "legislation":
        import requests

        # 从标准化文本中提取基础引用号
        # 匹配 SC/RSC/SOR 等编号（去掉法条名和条款部分）
        cit_match = re.search(
            r"(?:RSC|SC|SOR|RRO|O\sReg|BC\sReg|RLRQ)\s[^,]+(?:,\s*c\s[^,]+)?",
            normalized
        )
        base_citation = cit_match.group(0).strip() if cit_match else normalized
        verified = False
        jurisdiction = None
        chapter = None
        statute_title = normalized
        try:
            t0 = time.time()
            with prof.measure("http.a2aj_legislation", endpoint="/fetch", doc_type="laws"):
                resp = requests.get(
                    "https://api.a2aj.ca/fetch",
                    params={"citation": base_citation, "doc_type": "laws"},
                    timeout=15
                )
            if timing.ENABLE_TIMING:
                timing.report().add_a2aj(f"legislation fetch({base_citation[:30]})", time.time() - t0)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            verified = len(results) > 0
            if results:
                r0 = results[0]
                jurisdiction = _extract_jurisdiction(r0.get("dataset", ""))
                cit_en = r0.get("citation_en", "")
                ch_match = re.search(r'(c\s[\w.-]+)', cit_en)
                chapter = ch_match.group(1) if ch_match else None
                statute_title = r0.get("name_en", normalized)
        except Exception:
            verified = False

        return [{
            "statute_title": statute_title,
            "jurisdiction": jurisdiction,
            "chapter": chapter,
            "verified": verified,
            "warning": "" if verified else "⚠️ 未能通过 A2AJ 验证，建议在 CanLII 手动确认",
        }]

    # 4. concept：概念展开
    elif input_type == "concept":
        return expand_concept(normalized)

    return []
