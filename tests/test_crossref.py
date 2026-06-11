"""Test CrossRef API data quality for journal articles."""
import requests, json

doi = "10.1006/bbrc.2001.4705"
url = f"https://api.crossref.org/works/{doi}"
headers = {"User-Agent": "McGillCitationTool/0.1 (mailto:test@example.com)"}

resp = requests.get(url, headers=headers, timeout=15)
data = resp.json()["message"]

print("=== 作者列表 ===")
for a in data.get("author", []):
    print(f"  {a.get('given')} {a.get('family')}")
print(f"作者总数: {len(data.get('author', []))}")

print("\n=== 期刊 ===")
print(f"全名: {data.get('container-title')}")
print(f"缩写: {data.get('short-container-title')}")

print("\n=== 引用要素 ===")
print(f"标题: {data.get('title')}")
print(f"年份: {data.get('published', {}).get('date-parts')}")
print(f"卷: {data.get('volume')}")
print(f"期: {data.get('issue')}")
print(f"页: {data.get('page')}")
