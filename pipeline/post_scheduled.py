#!/usr/bin/env python3
"""
分散投稿スクリプト
引数: post_number (2 または 3) — 何本目を投稿するか
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import os
from datetime import datetime

from requests_oauthlib import OAuth1Session

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
SCHEDULED_DIR = os.path.join(PIPELINE_DIR, "scheduled")
TWEETS_FILE = os.path.join(PIPELINE_DIR, "posted_tweets.json")


def post_to_x(text: str, quote_tweet_id: str = None) -> dict:
    oauth = OAuth1Session(
        os.environ["X_API_KEY"],
        client_secret=os.environ["X_API_SECRET"],
        resource_owner_key=os.environ["X_ACCESS_TOKEN"],
        resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    body = {"text": text}
    if quote_tweet_id:
        body["quote_tweet_id"] = quote_tweet_id
    resp = oauth.post("https://api.twitter.com/2/tweets", json=body)
    resp.raise_for_status()
    return resp.json()


def append_log(tweet_id: str, text: str, tweet_type: str, date: str):
    log = {"tweets": []}
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE, "r", encoding="utf-8") as f:
            log = json.load(f)
    log["tweets"].append({
        "id": tweet_id,
        "date": date,
        "type": tweet_type,
        "text": text[:60] + ("..." if len(text) > 60 else ""),
    })
    with open(TWEETS_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def main():
    if len(sys.argv) < 2:
        print("ERROR: 引数が必要です。例: python post_scheduled.py 2")
        sys.exit(1)

    post_number = int(sys.argv[1])  # 2 or 3
    post_index = post_number - 1    # 0-based

    today = datetime.now().strftime("%Y-%m-%d")
    scheduled_path = os.path.join(SCHEDULED_DIR, f"{today}.json")

    if not os.path.exists(scheduled_path):
        print(f"ERROR: {scheduled_path} が見つかりません（本日の生成ファイルがありません）")
        sys.exit(1)

    with open(scheduled_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    posts = data.get("posts", [])
    types = data.get("types", ["速報", "解説", "事例"])
    quote_tweet_ids = data.get("quote_tweet_ids", [None, None, None])

    if post_index >= len(posts):
        print(f"ERROR: {post_number}本目が存在しません（{len(posts)}本のみ）")
        sys.exit(1)

    text = posts[post_index]
    post_type = types[post_index] if post_index < len(types) else "投稿"
    quote_tweet_id = quote_tweet_ids[post_index] if post_index < len(quote_tweet_ids) else None

    print(f"=== {post_number}本目を投稿 ({post_type}) ===")
    print(f"引用ツイート: {quote_tweet_id or 'なし'}")
    print(f"内容: {text.split(chr(10))[0][:50]}...")

    try:
        result = post_to_x(text, quote_tweet_id)
    except Exception as e:
        if quote_tweet_id and ("403" in str(e) or "Forbidden" in str(e)):
            print(f"引用ツイート失敗({e})→通常投稿にフォールバック")
            result = post_to_x(text, None)
        else:
            raise
    tweet_id = result["data"]["id"]
    append_log(tweet_id, text, post_type, today)
    print(f"投稿完了 ID: {tweet_id}")


if __name__ == "__main__":
    main()
