import time, requests, os
from dotenv import load_dotenv
load_dotenv()

key = os.environ["DEEPSEEK_API_KEY"]
url = "https://api.deepseek.com/chat/completions"
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

queries = ["r v oakes", "r v gladue", "r v jordan", "r v sharma", "r v ipeelee", "r v wells"]
conditions = [("THINKING关闭", {"type": "disabled"}), ("THINKING默认(不传)", None)]

for i, query in enumerate(queries):
    label, thinking_body = conditions[i % 2]
    prompt = f"""你是加拿大法律引用专家。分析以下用户输入，完成两件事：
1. 判断输入类型（只能是以下五种之一）：
   - citation_number：已知的引用号，如 "2022 SCC 39"、"[1999] 1 SCR 688"、"RSC 1985, c C-46"
   - case_name：案件名，如 "R v Gladue"、"R. v. Sharma"、"Regina v Jordan"
   - legislation：法条名或法条缩写，如 "Criminal Code"、"CCC"、"Charter"、"CCC s.718.2(e)"
   - bill：联邦法案编号，如 "bill c-22"、"bill c34"、"Bill S-2"、"Bill C 34"
   - concept：法律概念或原则，如 "gladue principle"、"right to housing"、"duty to consult"

2. 标准化输入：
   - 案件名：去掉句号（R. v. → R v），Regina/The Queen → R，去掉末尾的 "case"
   - 法条缩写：展开成完整引用（CCC → Criminal Code, RSC 1985, c C-46）
   - 法条+条款混合：展开法条名，保留条款（CCC s.718.2(e) → Criminal Code, RSC 1985, c C-46, s 718.2(e)）
   - 条款格式：s.718 → s 718（去掉句号）
   - 法案编号：统一归一为 L-DDDD 格式（去掉 Bill 前缀，大写字母，插入横杠 → C-34）
   - 法语输入同样处理（R. c. → R c）

只返回 JSON，不要任何解释：
{{"type": "类型", "normalized": "标准化后的输入", "original": "原始输入"}}

用户输入：{query}"""
    body = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if thinking_body:
        body["extra_body"] = {"thinking": thinking_body}
    t0 = time.time()
    r = requests.post(url, headers=headers, json=body, timeout=60)
    elapsed = time.time() - t0
    data = r.json()
    usage = data.get("usage", {})
    print(f"[{i+1}/6] query={query!r} {label}: {elapsed:.1f}s  status={r.status_code}  "
          f"tokens_in={usage.get('prompt_tokens')}  "
          f"tokens_out={usage.get('completion_tokens')}  "
          f"reasoning_tokens={usage.get('completion_tokens_details', {}).get('reasoning_tokens', 'N/A')}")
    print(f"  内容: {data['choices'][0]['message'].get('content', '')[:200]}")
