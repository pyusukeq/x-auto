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
from requests_oauthlib import OAuth1Session

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


def _parse_video_tweets(data: dict, cutoff, too_fresh=None, score_multiplier: float = 1.0) -> list:
    """X API レスポンスから動画ツイートをスコアリングして返す"""
    tweets = data.get("data", [])
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    scored = []
    for tweet in tweets:
        created = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
        if created < cutoff:
            continue
        if too_fresh and created > too_fresh:  # 投稿から12時間未満は除外
            continue
        text = tweet["text"]
        if len(text) < 30:
            continue
        m = tweet.get("public_metrics", {})
        score = int((
            m.get("like_count", 0) * 3 +
            m.get("retweet_count", 0) * 5 +
            m.get("quote_count", 0) * 4
        ) * score_multiplier)
        author = users.get(tweet["author_id"], {})
        username = author.get("username", "")
        # entities から pic.x.com の t.co URL を取得（これがあると動画がインライン表示される）
        pic_tco = None
        for url_ent in tweet.get("entities", {}).get("urls", []):
            if url_ent.get("display_url", "").startswith("pic.x.com"):
                pic_tco = url_ent["url"]
                break
        video_url = pic_tco or f"https://x.com/{username}/status/{tweet['id']}/video/1"
        scored.append({
            "tweet_id": tweet["id"],
            "author": username,
            "text": text,
            "url": f"https://x.com/{username}/status/{tweet['id']}",
            "video_url": video_url,
            "like_count": m.get("like_count", 0),
            "retweet_count": m.get("retweet_count", 0),
            "score": score,
            "created": created.isoformat(),
        })
    return scored


def fetch_x_announcements():
    """AnthropicAI・OpenAI公式アカウントとCEO(@sama/@DarioAmodei)の最新ツイートを収集"""
    required = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    if not all(os.environ.get(k) for k in required):
        print("  ⚠️  X認証情報未設定のためスキップ")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    results = []
    try:
        oauth = OAuth1Session(
            os.environ["X_API_KEY"],
            client_secret=os.environ["X_API_SECRET"],
            resource_owner_key=os.environ["X_ACCESS_TOKEN"],
            resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
        )
        query = "(from:AnthropicAI OR from:OpenAI OR from:sama OR from:DarioAmodei) -is:reply -is:retweet"
        params = {
            "query": query,
            "max_results": 20,
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "name,username",
        }
        resp = oauth.get("https://api.twitter.com/2/tweets/search/recent", params=params)
        resp.raise_for_status()
        data = resp.json()

        tweets = data.get("data", [])
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

        for tweet in tweets:
            created = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
            if created < cutoff:
                continue
            text = tweet["text"]
            if len(text) < 30:
                continue
            m = tweet.get("public_metrics", {})
            base_score = m.get("like_count", 0) * 3 + m.get("retweet_count", 0) * 5
            author = users.get(tweet["author_id"], {})
            username = author.get("username", "")
            results.append({
                "source": f"X @{username}",
                "title": text[:100].replace("\n", " "),
                "url": f"https://x.com/{username}/status/{tweet['id']}",
                "body": text[:600],
                "score": max(base_score, 500),  # 公式アカウントは最低500スコアを保証
                "comments": m.get("reply_count", 0),
                "created": created.isoformat(),
            })

        print(f"  ✅ X公式アナウンス(@AnthropicAI/@OpenAI/@sama/@DarioAmodei): {len(results)}件")
    except Exception as e:
        print(f"  ❌ X公式アナウンス: {e}")

    return results


def fetch_x_viral_videos():
    """バズっているAI関連の英語動画ツイートを広く検索し、エンゲージメント上位2件を返す。
    Claude/Anthropic/OpenAI/ChatGPT/CEO発言を横断的に収集し、スコア0（スパム）を除外して選ぶ。
    """
    required = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    if not all(os.environ.get(k) for k in required):
        print("  ⚠️  X認証情報未設定のためスキップ")
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    too_fresh = datetime.now(timezone.utc) - timedelta(hours=12)
    try:
        oauth = OAuth1Session(
            os.environ["X_API_KEY"],
            client_secret=os.environ["X_API_SECRET"],
            resource_owner_key=os.environ["X_ACCESS_TOKEN"],
            resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
        )
        # AI全般を横断して広く検索（絞りすぎない）
        query = (
            "(claude OR anthropic OR openai OR chatgpt OR \"Dario Amodei\" OR \"Sam Altman\""
            " OR \"claude code\" OR MCP OR \"AI agents\" OR \"gpt-4\" OR \"gpt-5\")"
            " has:videos -is:reply -is:retweet lang:en"
        )
        resp = oauth.get(
            "https://api.twitter.com/2/tweets/search/recent",
            params={
                "query": query,
                "max_results": 100,  # API上限（エンゲージメント順でなく最新順のため多めに取得）
                "tweet.fields": "created_at,public_metrics,author_id,entities",
                "expansions": "author_id",
                "user.fields": "name,username",
            },
        )
        resp.raise_for_status()

        all_scored = _parse_video_tweets(resp.json(), cutoff, too_fresh)

        # スコア0（エンゲージメントなし）のスパム・Bot投稿を除外
        viral = [s for s in all_scored if s["score"] > 0]

        if not viral:
            print("  ⚠️  エンゲージメントのある動画ツイートが見つかりませんでした")
            return []

        top2 = sorted(viral, key=lambda x: (x["score"], x["created"]), reverse=True)[:2]
        for i, v in enumerate(top2, 1):
            print(f"  ✅ X動画ツイート{i}: @{v['author']} スコア:{v['score']} (いいね:{v['like_count']} RT:{v['retweet_count']})")
        return top2

    except Exception as e:
        print(f"  ❌ X動画検索: {e}")
        return []


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

    print("  X公式アナウンス・CEO発言を収集中...")
    all_items.extend(fetch_x_announcements())

    print("  X動画ツイート検索中...")
    viral_videos = fetch_x_viral_videos()

    ranked = score_and_rank(all_items)
    top = ranked[:10]

    output = {
        "collected_at": datetime.now().isoformat(),
        "date": today,
        "total_found": len(all_items),
        "top_stories": top,
        "viral_video_tweets": viral_videos,
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
