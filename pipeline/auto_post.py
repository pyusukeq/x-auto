#!/usr/bin/env python3
"""
自動投稿スクリプト
collect.py で収集した記事を Claude API で日本語X投稿に変換し、X に直接投稿する
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import os
import time
from datetime import datetime

import requests
from requests_oauthlib import OAuth1Session
import anthropic

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
TWEETS_FILE = os.path.join(PIPELINE_DIR, "posted_tweets.json")
SCHEDULED_DIR = os.path.join(PIPELINE_DIR, "scheduled")

SYSTEM_PROMPT = """あなたはAI・Claude Codeの情報を日本のエンジニア・個人開発者向けに発信するXアカウントの運営者です。

投稿ルール:
- 全角換算120文字以内を厳守（超えると投稿が失敗する）
- 冒頭1行でインパクトを出す（読者が続きを読みたくなるような一文）
- 技術的だが初心者にも分かる言葉で書く
- ハッシュタグは最大3個。#ClaudeCode #AIコーディング #個人開発 #AI副業 #MCP から内容に合うものを選ぶ
- 速報型・解説型・事例型のバランスを取る
- 英語情報は必ず自然な日本語に翻訳する
- ネガティブな内容（炎上・批判・バグ報告）は避ける

出力形式（JSONのみ。前後に余計なテキスト・コードブロック不要）:
{"posts": ["投稿1のテキスト", "投稿2のテキスト", "投稿3のテキスト"]}"""


def weighted_len(text: str) -> int:
    """X の文字数カウント: ASCII=1, それ以外=2"""
    return sum(2 if ord(c) > 127 else 1 for c in text)


def shorten_post(post: str, limit: int = 260) -> str:
    """文字数超過の投稿を短縮する"""
    if weighted_len(post) <= limit:
        return post

    lines = post.split("\n")
    hashtag_lines = [l for l in lines if l.strip().startswith("#")]
    body_lines = [l for l in lines if not l.strip().startswith("#")]
    body = "\n".join(body_lines).strip()
    hashtags = "\n".join(hashtag_lines)
    footer = "\n\n" + hashtags if hashtags else ""

    while weighted_len(body + footer) > limit and len(body) > 20:
        body = body[:-3].rstrip("。、・…")

    return body.strip() + footer


def generate_posts(stories: list) -> list[str]:
    """Anthropic API を使って投稿を生成する（リトライあり）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")
    if not api_key.startswith("sk-ant-"):
        raise ValueError(f"ANTHROPIC_API_KEY の形式が不正です（先頭: {api_key[:10]}...）")

    print(f"  APIキー確認: {api_key[:12]}...（長さ: {len(api_key)}文字）")
    import httpx
    client = anthropic.Anthropic(
        api_key=api_key,
        http_client=httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)),
    )

    stories_text = "\n\n".join([
        f"[{i+1}] ソース: {s['source']}\nタイトル: {s['title']}\n"
        f"URL: {s.get('url', '')}\n"
        f"本文: {s.get('body', '')[:300] if s.get('body') else '(本文なし)'}"
        for i, s in enumerate(stories[:8])
    ])

    # リトライ処理（最大3回）
    last_error = None
    for attempt in range(1, 4):
        try:
            print(f"  API呼び出し試行 {attempt}/3...")
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"以下の記事から3本のX投稿を作成してください。\n\n{stories_text}"
                }]
            )
            break
        except Exception as e:
            last_error = e
            print(f"  試行{attempt}失敗: {type(e).__name__}: {e}")
            if attempt < 3:
                time.sleep(5 * attempt)
    else:
        raise last_error

    raw = message.content[0].text.strip()

    # コードブロックが含まれる場合は除去
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            if part.startswith("json"):
                raw = part[4:].strip()
                break
            elif part.strip().startswith("{"):
                raw = part.strip()
                break

    data = json.loads(raw)
    posts = data["posts"]

    # 文字数チェックと短縮
    validated = []
    for i, post in enumerate(posts, 1):
        wlen = weighted_len(post)
        if wlen > 270:
            print(f"  投稿{i}: 文字数超過({wlen}w) → 短縮")
            post = shorten_post(post)
        validated.append(post)

    return validated


def post_to_x(text: str) -> dict:
    """X API v2 で投稿する"""
    oauth = OAuth1Session(
        os.environ["X_API_KEY"],
        client_secret=os.environ["X_API_SECRET"],
        resource_owner_key=os.environ["X_ACCESS_TOKEN"],
        resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    resp = oauth.post(
        "https://api.twitter.com/2/tweets",
        json={"text": text},
    )
    resp.raise_for_status()
    return resp.json()


def load_log() -> dict:
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tweets": []}


def append_log(log: dict, tweet_id: str, text: str, tweet_type: str, date: str):
    log["tweets"].append({
        "id": tweet_id,
        "date": date,
        "type": tweet_type,
        "text": text[:60] + ("..." if len(text) > 60 else ""),
    })
    with open(TWEETS_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def save_scheduled(posts: list, types: list, today: str):
    """3本の投稿を scheduled/ に保存する"""
    os.makedirs(SCHEDULED_DIR, exist_ok=True)
    path = os.path.join(SCHEDULED_DIR, f"{today}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "posts": posts, "types": types}, f, ensure_ascii=False, indent=2)
    print(f"  予約ファイル保存: {path}")


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    collected_path = os.path.join(PIPELINE_DIR, "collected", f"{today}.json")

    if not os.path.exists(collected_path):
        print(f"ERROR: {collected_path} が見つかりません")
        print("collect.py を先に実行してください")
        sys.exit(1)

    with open(collected_path, "r", encoding="utf-8") as f:
        collected = json.load(f)

    stories = collected.get("top_stories", [])
    print(f"=== 自動投稿開始 ({today}) ===")
    print(f"収集済み: {len(stories)}件\n")

    print("[1/3] Claude API で投稿を生成中...")
    try:
        posts = generate_posts(stories)
        print(f"  → {len(posts)}本生成完了\n")
    except Exception as e:
        print(f"ERROR: 投稿生成失敗 - {e}")
        sys.exit(1)

    types = ["速報", "解説", "事例"]

    print("[2/3] 投稿を保存中（昼・夜の分散投稿用）...")
    save_scheduled(posts, types, today)

    print("\n[3/3] 1本目を投稿中（速報）...")
    log = load_log()
    post, post_type = posts[0], types[0]
    preview = post.split("\n")[0][:40]
    print(f"  内容: {preview}...")
    print(f"  文字数: {weighted_len(post)}w")

    try:
        result = post_to_x(post)
        tweet_id = result["data"]["id"]
        append_log(log, tweet_id, post, post_type, today)
        print(f"  投稿完了 ID: {tweet_id}")
    except Exception as e:
        print(f"  投稿失敗: {e}")
        sys.exit(1)

    print(f"\n=== 完了: 1本目投稿済み / 2本目 12:00 / 3本目 19:00 ===")


if __name__ == "__main__":
    main()
