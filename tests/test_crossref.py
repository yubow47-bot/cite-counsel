"""Manual CrossRef API data-quality probe for journal articles."""

import requests


def main() -> None:
    doi = "10.1006/bbrc.2001.4705"
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"User-Agent": "McGillCitationTool/0.1 (mailto:test@example.com)"}

    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()["message"]

    print("=== 作者列表 ===")
    for author in data.get("author", []):
        print(f"  {author.get('given')} {author.get('family')}")
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


if __name__ == "__main__":
    main()
