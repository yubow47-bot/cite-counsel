import time, requests, os
from dotenv import load_dotenv
load_dotenv()

key = os.environ["DEEPSEEK_API_KEY"]
url = "https://api.deepseek.com/chat/completions"
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

prompt = ("你是加拿大法律引用专家。判断输入类型"
           "（citation_number/case_name/legislation/bill/concept），"
           '只返回JSON：{"type": "类型", "normalized": "标准化", "original": "原始"}。'
           "用户输入：r v oakes")

for label, thinking_body in [("THINKING关闭", {"type": "disabled"}), ("THINKING默认(不传)", None)]:
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
    print(f"{label}: {elapsed:.1f}s  status={r.status_code}  "
          f"tokens_in={usage.get('prompt_tokens')}  "
          f"tokens_out={usage.get('completion_tokens')}  "
          f"reasoning_tokens={usage.get('completion_tokens_details', {}).get('reasoning_tokens', 'N/A')}")
    print(f"  内容: {data['choices'][0]['message'].get('content', '')[:100]}")
