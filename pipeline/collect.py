#!/usr/bin/env python3
"""
コンテンツ収集スクリプト
Reddit → Hacker News → Anthropic公式 → GitHub の順で収集する
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
import json
import os
from datetime import datetime, timezone, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; x-auto-collector/1.0)"}
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "collected")

# ClaudeCode関連キーワード（HN/ML系の広いフォーラム用フィルタ）
KEYWORDS = ["claude", "anthropic", "claude code", "mcp", "ai coding", "agentic"]


def fetch_reddit():
    results = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    targets = [
        ("ClaudeAI", None),           # 全記事対象
        ("MachineLearning", KEYWORDS), # キーワードフィルタあり
        ("LocalLLaMA", KEYWORDS),
    ]

    for subreddit, keywords in targets:
        try:
            url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=25"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()

            sub_count = 0
            for post in resp.json()["data"]["children"]:
                d = post["data"]
                created = datetime.fromtimestamp(d["created_utc"], tz=timezone.utc)
                if created < cutoff:
                    continue
                if keywords:
                    if not any(kw in d["title"].lower() for kw in keywords):
                        continue
                results.append({
                    "source": f"Reddit r/{subreddit}",
                    "title": d["title"],
                    "url": f"https://reddit.com{d['permalink']}",
                    "external_url": d.get("url", ""),
                    "score": d["score"],
                    "comments": d["num_comments"],
                    "created": created.isoformat(),
                })
                sub_count += 1
            print(f"  ✅ Reddit r/{subreddit}: {sub_count}件（直近7日）")
        except Exception as e:
            print(f"  ❌ Reddit r/{subreddit}: {e}")

    return results


def fetch_hackernews():
    results = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    try:
        top_ids = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=10
        ).json()[:80]

        count = 0
        for item_id in top_ids:
            try:
                item = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
                    timeout=5
                ).json()

                if not item or item.get("type") != "story":
                    continue

                created = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)
                if created < cutoff:
                    continue

                text = (item.get("title", "") + " " + item.get("text", "")).lower()
                if not any(kw in text for kw in KEYWORDS):
                    continue

                results.append({
                    "source": "Hacker News",
                    "title": item.get("title", ""),
                    "url": item.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
                    "external_url": item.get("url", ""),
                    "score": item.get("score", 0),
                    "comments": item.get("descendants", 0),
                    "created": created.isoformat(),
                })
                count += 1
            except Exception:
                continue

        print(f"  ✅ Hacker News: {count}件（直近7日）")
    except Exception as e:
        print(f"  ❌ Hacker News: {e}")

    return results


def fetch_anthropic_blog():
    results = []
    try:
        resp = requests.get("https://www.anthropic.com/news", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        results.append({
            "source": "Anthropic公式",
            "title": "[公式] anthropic.com/news を確認してください",
            "url": "https://www.anthropic.com/news",
            "external_url": "https://www.anthropic.com/news",
            "score": 9999,
            "comments": 0,
            "created": datetime.now(timezone.utc).isoformat(),
            "note": "公式ページは手動確認推奨。新着記事があれば優先して取り上げてください。",
        })
        print(f"  ✅ Anthropic公式: チェック用エントリを追加")
    except Exception as e:
        print(f"  ❌ Anthropic公式: {e}")

    return results


def fetch_github():
    results = []
    repos = [
        ("anthropics", "claude-code"),
        ("anthropics", "anthropic-sdk-python"),
        ("anthropics", "anthropic-sdk-typescript"),
    ]
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    for owner, repo in repos:
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/releases",
                headers={**HEADERS, "Accept": "application/vnd.github.v3+json"},
                timeout=10
            )
            resp.raise_for_status()

            count = 0
            for rel in resp.json()[:5]:
                published = datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00"))
                if published < cutoff:
                    break
                results.append({
                    "source": f"GitHub {owner}/{repo}",
                    "title": f"【リリース】{repo} {rel['tag_name']}: {rel['name']}",
                    "url": rel["html_url"],
                    "external_url": rel["html_url"],
                    "body": rel.get("body", "")[:600],
                    "score": 800,
                    "comments": 0,
                    "created": rel["published_at"],
                })
                count += 1

            print(f"  ✅ GitHub {repo}: {count}件（直近7日）")
        except Exception as e:
            print(f"  ❌ GitHub {repo}: {e}")

    return results


def score_and_rank(items):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    valid = []
    for item in items:
        try:
            created = datetime.fromisoformat(item["created"])
            if created < cutoff:
                continue
            hours_ago = (now - created).total_seconds() / 3600
            freshness = max(0.0, 1.0 - hours_ago / (7 * 24))
            item["final_score"] = item["score"] * (1 + freshness) + item.get("comments", 0) * 0.5
            valid.append(item)
        except Exception:
            item["final_score"] = 0.0
            valid.append(item)
    return sorted(valid, key=lambda x: x["final_score"], reverse=True)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = os.path.join(OUTPUT_DIR, f"{today}.json")

    print("=== コンテンツ収集開始 ===\n")

    all_items = []
    all_items.extend(fetch_reddit())
    all_items.extend(fetch_hackernews())
    all_items.extend(fetch_anthropic_blog())
    all_items.extend(fetch_github())

    ranked = score_and_rank(all_items)
    top = ranked[:10]

    output = {
        "collected_at": datetime.now().isoformat(),
        "date": today,
        "total_found": len(all_items),
        "top_stories": top,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n=== 収集完了 ===")
    print(f"合計: {len(all_items)}件 → 上位{len(top)}件を保存")
    print(f"保存先: {output_path}")
    print("\n--- 上位5件 ---")
    for i, item in enumerate(top[:5], 1):
        print(f"{i}. [{item['source']}] {item['title'][:70]}")

    print(f"""
次のステップ:
  Claude Codeに以下を依頼してください:
  「pipeline/collected/{today}.json を読んで、X投稿の下書きを3本作り、
    .company/secretary/inbox/{today}.md に保存してください」
""")


if __name__ == "__main__":
    main()
