#!/usr/bin/env python3
"""
週次分析スクリプト
posted_tweets.json の投稿データをX APIでメトリクス取得し、パフォーマンスレポートを生成する
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import os
import time
from datetime import datetime, timezone, timedelta

from requests_oauthlib import OAuth1Session

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
TWEETS_FILE = os.path.join(PIPELINE_DIR, "posted_tweets.json")
REPORTS_DIR = os.path.join(PIPELINE_DIR, "reports")


def get_oauth():
    return OAuth1Session(
        os.environ["X_API_KEY"],
        client_secret=os.environ["X_API_SECRET"],
        resource_owner_key=os.environ["X_ACCESS_TOKEN"],
        resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def fetch_metrics(tweet_id: str, oauth) -> dict | None:
    try:
        resp = oauth.get(
            f"https://api.twitter.com/2/tweets/{tweet_id}",
            params={"tweet.fields": "public_metrics,created_at"}
        )
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 900))
            wait = max(60, reset - int(time.time()) + 5)
            print(f"  Rate limit. {wait}秒待機...")
            time.sleep(wait)
            resp = oauth.get(
                f"https://api.twitter.com/2/tweets/{tweet_id}",
                params={"tweet.fields": "public_metrics,created_at"}
            )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("public_metrics", {})
    except Exception as e:
        print(f"  メトリクス取得失敗 {tweet_id}: {e}")
        return None


def engagement_score(metrics: dict) -> int:
    return (
        metrics.get("like_count", 0) * 3 +
        metrics.get("retweet_count", 0) * 5 +
        metrics.get("reply_count", 0) * 2 +
        metrics.get("quote_count", 0) * 4 +
        metrics.get("bookmark_count", 0) * 2
    )


def generate_report(results: list, week_label: str) -> str:
    if not results:
        return f"# 週次レポート {week_label}\n\nデータなし\n"

    sorted_results = sorted(results, key=lambda x: x["score"], reverse=True)

    # タイプ別集計
    type_stats: dict = {}
    for r in results:
        t = r["type"]
        if t not in type_stats:
            type_stats[t] = {"count": 0, "total_score": 0, "likes": 0, "retweets": 0}
        type_stats[t]["count"] += 1
        type_stats[t]["total_score"] += r["score"]
        type_stats[t]["likes"] += r["metrics"].get("like_count", 0)
        type_stats[t]["retweets"] += r["metrics"].get("retweet_count", 0)

    lines = [
        f"# 週次パフォーマンスレポート {week_label}",
        f"\n分析対象: {len(results)}件\n",
        "## 投稿ランキング\n",
        "| # | 日付 | タイプ | いいね | RT | 引用 | ブクマ | スコア | 内容 |",
        "|---|------|--------|--------|-----|------|--------|--------|------|",
    ]
    for i, r in enumerate(sorted_results, 1):
        m = r["metrics"]
        lines.append(
            f"| {i} | {r['date']} | {r['type']} | "
            f"{m.get('like_count', 0)} | {m.get('retweet_count', 0)} | "
            f"{m.get('quote_count', 0)} | {m.get('bookmark_count', 0)} | "
            f"{r['score']} | {r['text']} |"
        )

    lines.append("\n## タイプ別平均スコア\n")
    lines.append("| タイプ | 件数 | 平均スコア | 合計いいね | 合計RT |")
    lines.append("|--------|------|------------|------------|--------|")
    for t, s in sorted(
        type_stats.items(),
        key=lambda x: x[1]["total_score"] / max(x[1]["count"], 1),
        reverse=True
    ):
        avg = s["total_score"] / s["count"]
        lines.append(f"| {t} | {s['count']} | {avg:.1f} | {s['likes']} | {s['retweets']} |")

    top = sorted_results[0]
    lines.append("\n## トップ投稿\n")
    lines.append(f"**日付**: {top['date']}  ")
    lines.append(f"**タイプ**: {top['type']}  ")
    m = top["metrics"]
    lines.append(
        f"**スコア**: {top['score']} "
        f"(いいね:{m.get('like_count', 0)} RT:{m.get('retweet_count', 0)} "
        f"引用:{m.get('quote_count', 0)} ブクマ:{m.get('bookmark_count', 0)})  "
    )
    lines.append(f"**内容**: {top['text']}\n")

    lines.append("## 改善メモ\n")
    lines.append("_分析・改善案を記入し、auto_post.py の SYSTEM_PROMPT に反映してください_\n")
    lines.append("- [ ] 来週プロンプトへ反映する点：")
    lines.append("- [ ] 来週試したいフォーマット：")
    lines.append("- [ ] 好調だったトピック：")

    return "\n".join(lines) + "\n"


def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)

    today = datetime.now(timezone.utc).date()
    cutoff_date = (today - timedelta(days=14)).isoformat()
    week_label = today.strftime("%Y-W%W")
    report_path = os.path.join(REPORTS_DIR, f"{week_label}.md")

    print(f"=== 週次分析開始 ({week_label}) ===\n")

    if not os.path.exists(TWEETS_FILE):
        print(f"ERROR: {TWEETS_FILE} が見つかりません")
        sys.exit(1)

    with open(TWEETS_FILE, "r", encoding="utf-8") as f:
        log = json.load(f)

    recent = [t for t in log.get("tweets", []) if t["date"] >= cutoff_date]
    print(f"対象ツイート: {len(recent)}件（直近14日）\n")

    if not recent:
        print("分析対象なし（まだ投稿が蓄積されていません）")
        return

    oauth = get_oauth()
    results = []

    for i, tweet in enumerate(recent, 1):
        print(f"  [{i}/{len(recent)}] {tweet['date']} {tweet['type']}: {tweet['text'][:30]}...")
        metrics = fetch_metrics(tweet["id"], oauth)
        if metrics is not None:
            results.append({
                "id": tweet["id"],
                "date": tweet["date"],
                "type": tweet["type"],
                "text": tweet["text"],
                "metrics": metrics,
                "score": engagement_score(metrics),
            })
        if i < len(recent):
            time.sleep(1)

    print(f"\nメトリクス取得: {len(results)}/{len(recent)}件成功\n")

    report = generate_report(results, week_label)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"レポート保存: {report_path}")

    if results:
        sorted_r = sorted(results, key=lambda x: x["score"], reverse=True)
        print(f"\n--- サマリー ---")
        print(f"トップ : [{sorted_r[0]['type']}] {sorted_r[0]['text'][:40]} (スコア:{sorted_r[0]['score']})")
        if len(sorted_r) > 1:
            print(f"ワースト: [{sorted_r[-1]['type']}] {sorted_r[-1]['text'][:40]} (スコア:{sorted_r[-1]['score']})")


if __name__ == "__main__":
    main()
