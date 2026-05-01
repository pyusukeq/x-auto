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

import anthropic

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
TWEETS_FILE = os.path.join(PIPELINE_DIR, "posted_tweets.json")
SCHEDULED_DIR = os.path.join(PIPELINE_DIR, "scheduled")

SYSTEM_PROMPT = """あなたはAI・Claude Codeの情報を日本のエンジニア・個人開発者向けに発信するXアカウントの運営者です。

## 投稿テンプレート（必ずこの構成で書く）

```
【タグ】

問題提起か驚きの事実（1行1文・2〜3行）
『印象的なフレーズや数字があれば引用』

結論はこれ👇 または 具体的には⏬ または ポイントはここ👇

▶︎ 箇条書き1（簡潔に）
▶︎ 箇条書き2（簡潔に）
▶︎ 箇条書き3（簡潔に）

つまり、

短い結論・気づき（1〜2行）

CTA（「〜はこちら👇」「試してみて👀」「保存推奨」など）
```

## ルール

**【タグ】の選択肢**（内容に合わせて1つ選ぶ）
- 【速報】新しいリリース・発表
- 【保存版】網羅的・まとめ系
- 【必見】重要な情報
- 【保存推奨】実用的なTips
- 【保存必須】絶対に知っておくべき情報
- 【これはすごい】驚きの事例

**文体ルール**
- 1行1文で改行する（長い文は2行に分ける）
- 空行を使って読みやすくする
- 『』で印象的なフレーズや数字を引用する
- ハッシュタグは使わない
- 英語情報は自然な日本語に翻訳する
- ネガティブな内容は避ける
- 全角換算350文字以内"""


def weighted_len(text: str) -> int:
    """X の文字数カウント: ASCII=1, それ以外=2"""
    return sum(2 if ord(c) > 127 else 1 for c in text)


def shorten_post(post: str, limit: int = 900) -> str:
    """文字数超過の投稿を短縮する（X Premium: 全角450文字=900w が上限）"""
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


def generate_posts(stories: list, viral_video: dict = None, recent_posts: list = None) -> tuple:
    """Anthropic API を使って投稿を生成する（リトライあり）
    Returns: (scheduled_posts, types, quote_tweet_ids, fallback_post)
    - viral_video あり: scheduled=[速報,解説,動画引用], fallback=事例投稿
    - viral_video なし: scheduled=[速報,解説,事例], fallback=None
    """
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

    dedup_instruction = ""
    if recent_posts:
        recent_summary = "\n".join(f"- {p[:120]}" for p in recent_posts[-15:])
        dedup_instruction = (
            f"\n\n【重複禁止】以下は最近投稿した内容です。同じトピック・フレーズは絶対に使わないでください:\n"
            f"{recent_summary}"
        )

    if viral_video:
        types = ["速報", "解説", "動画"]
        quote_tweet_ids = [None, None, None]  # 引用ツイートではなくURLを本文に埋め込む
        num_posts = 4
        video_url = viral_video.get(
            "video_url",
            f"https://x.com/{viral_video['author']}/status/{viral_video['tweet_id']}/video/1"
        )
        post_instructions = (
            "以下の記事から4本のX投稿を作成してください:\n\n"
            "- 投稿1（速報）: 新しいリリースや発表を中心に。【速報】タグ必須\n"
            "- 投稿2（解説）: 機能の使い方・仕組みの解説。【保存版】【保存推奨】【必見】のいずれかのタグ必須。速報とは異なるトピックで\n"
            "- 投稿3（事例・予備）: 実際の活用事例・驚きの使い方。【これはすごい】【保存必須】のいずれかのタグ必須。投稿1・2とは異なるトピックで\n"
            f"- 投稿4（動画紹介）: 以下の英語動画ツイートを日本語で紹介する投稿。\n"
            f"  【重要】動画URL「{video_url}」を必ず投稿の3〜4行目（フックの直後）に含めること。URLは一切変更・省略しないこと。\n"
            f"  投稿テンプレートに従い、動画の価値・驚きを伝える。「この動画が分かりやすい」「海外で話題の〇〇動画」などの表現を使う。\n"
            f"  元ツイート(@{viral_video['author']}): {viral_video['text'][:300]}\n\n"
        )
        output_format = f'{{"posts": ["速報投稿", "解説投稿", "事例投稿(予備)", "動画紹介投稿({video_url}を含む)"]}}'
    else:
        types = ["速報", "解説", "事例"]
        quote_tweet_ids = [None, None, None]
        num_posts = 3
        post_instructions = (
            "以下の記事から3本のX投稿を作成してください:\n\n"
            "- 投稿1（速報）: 新しいリリースや発表を中心に。【速報】タグ必須\n"
            "- 投稿2（解説）: 機能の使い方・仕組みの解説。【保存版】【保存推奨】【必見】のいずれかのタグ必須。速報とは異なるトピックで\n"
            "- 投稿3（事例）: 実際の活用事例・驚きの使い方。【これはすごい】【保存必須】のいずれかのタグ必須。投稿1・2とは異なるトピックで\n\n"
        )
        output_format = '{"posts": ["速報投稿", "解説投稿", "事例投稿"]}'

    prompt = (
        f"{post_instructions}"
        f"{dedup_instruction}\n\n"
        f"=== 記事一覧 ===\n{stories_text}\n\n"
        f"出力形式（JSONのみ。前後に余計なテキスト・コードブロック不要）:\n{output_format}"
    )

    last_error = None
    for attempt in range(1, 4):
        try:
            print(f"  API呼び出し試行 {attempt}/3...")
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
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

    if len(posts) < num_posts:
        raise ValueError(f"{num_posts}本必要ですが{len(posts)}本しか生成されませんでした")

    # 文字数チェックと短縮（全投稿900w上限）
    validated = []
    for i, post in enumerate(posts[:num_posts]):
        wlen = weighted_len(post)
        if wlen > 900:
            print(f"  投稿{i+1}: 文字数超過({wlen}w) → 短縮")
            post = shorten_post(post, 900)
        validated.append(post)

    if viral_video:
        # 動画投稿にURLが含まれているか確認。含まれていなければURLを先頭に追記
        video_post = validated[3]
        if video_url not in video_post and "https://" not in video_post:
            video_post = video_url + "\n\n" + video_post
            print(f"  投稿4: 動画URLが含まれていなかったため追記")
            validated[3] = video_post

        scheduled_posts = [validated[0], validated[1], validated[3]]
        fallback_post = validated[2]
    else:
        scheduled_posts = validated
        fallback_post = None

    return scheduled_posts, types, quote_tweet_ids, fallback_post


def load_log() -> dict:
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tweets": []}


def save_scheduled(posts: list, types: list, quote_tweet_ids: list, today: str, fallback_post: str = None):
    """3本の投稿を scheduled/ に保存する"""
    os.makedirs(SCHEDULED_DIR, exist_ok=True)
    path = os.path.join(SCHEDULED_DIR, f"{today}.json")
    data = {"date": today, "posts": posts, "types": types, "quote_tweet_ids": quote_tweet_ids}
    if fallback_post:
        data["fallback_post"] = fallback_post
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
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
    viral_video = collected.get("viral_video_tweet")
    print(f"=== 自動投稿開始 ({today}) ===")
    print(f"収集済み: {len(stories)}件 / 動画ツイート: {'あり' if viral_video else 'なし'}\n")

    log = load_log()
    recent_posts = [t["text"] for t in log.get("tweets", [])[-20:]]

    print("[1/2] Claude API で投稿を生成中...")
    try:
        posts, types, quote_tweet_ids, fallback_post = generate_posts(stories, viral_video, recent_posts)
        print(f"  → {len(posts)}本生成完了 / フォールバック: {'あり' if fallback_post else 'なし'}\n")
    except Exception as e:
        print(f"ERROR: 投稿生成失敗 - {e}")
        sys.exit(1)

    print("[2/2] 投稿を保存中...")
    save_scheduled(posts, types, quote_tweet_ids, today, fallback_post)

    print(f"\n=== 完了: scheduled/{today}.json に保存済み ===")
    print("次: review_post.py でレビュー → post_scheduled.py 1 で1本目投稿")


if __name__ == "__main__":
    main()
