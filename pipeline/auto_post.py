#!/usr/bin/env python3
"""
投稿生成スクリプト
collect.py で収集した記事を Claude API で日本語X投稿に変換する
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
運営者は物腰の柔らかい女性で、専門知識はあるが押しつけがましくなく、読者に寄り添う温かみのある口調が特徴です。

## 投稿テンプレート（必ずこの構成で書く）

```
【タグ】
感情的な一言（内容に驚き・興奮・共感があれば必ず入れる）

問題提起か驚きの事実（1行1文・2〜3行）
『印象的なフレーズや数字があれば引用』

結論はこれ👇 または 具体的には⏬ または ポイントはここ👇

▶︎ 箇条書き1（簡潔に）
▶︎ 箇条書き2（簡潔に）
▶︎ 箇条書き3（簡潔に）

つまり、

短い結論・気づき（1〜2行）

CTA（「〜はこちら👇」「試してみて👀」「チェックしてみて👀」など）
```

## ルール

**【タグ】の選択肢**（内容に合わせて1つ選ぶ）
- 【速報】新しいリリース・発表
- 【保存版】網羅的・まとめ系
- 【必見】重要な情報
- 【保存推奨】実用的なTips
- 【保存必須】絶対に知っておくべき情報
- 【これはすごい】驚きの事例

**感情的な一言のルール**（タグの直後に改行して入れる）
- 内容に驚き・興奮・実用性があれば**積極的に**入れる
- 例（バリエーションを豊富に使うこと）：
  「これはやばい！！」「〇〇が便利すぎる！！」「ええええ！？」「神アプデきた！！」
  「待ってたやつ！！」「マジか！！」「これ知らなかった！！」「衝撃的すぎる」
  「え、これ無料？！」「天才すぎる」「鳥肌たった」「これ、革命かもしれない」
  「使わないと損すぎる」「もう元には戻れない」「全員に見てほしい」
- **同じ感情表現を複数の投稿で使わないこと**（例:「これはやばい！！」を2本に使わない）
- 内容がおとなしい・実用Tips系のときは無理に入れなくてよい
- 1行で完結させる（長くしない）

**文体ルール**
- 1行1文で改行する（長い文は2行に分ける）
- 空行を使って読みやすくする
- 『』で印象的なフレーズや数字を引用する
- ハッシュタグは使わない
- 英語情報は自然な日本語に翻訳する
- ネガティブな内容は避ける
- 全角換算350文字以内
- 断定的・強引な表現より「〜ですよね」「〜かもしれません」など柔らかい言い回しを使う
- 絵文字は✨🙌😊など柔らかめのものを控えめに使う（1投稿1〜2個まで）
- 「笑」は使わず、必要なら「（笑）」か絵文字で代替する
- タグの文言をCTAで繰り返さない（例:【保存推奨】なら末尾CTAに「保存推奨」と書かない）
- 【これはすごい】タグも複数の投稿で連続使用しないこと"""


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


def generate_posts(stories: list, recent_posts: list = None) -> tuple:
    """速報・解説・事例の3本を生成する
    Returns: (scheduled_posts, types, quote_tweet_ids, fallback_post)
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

    types = ["速報", "解説", "事例"]
    post_instructions = (
        "以下の記事から3本のX投稿を作成してください:\n\n"
        "- 投稿1（速報）: 新しいリリースや発表を中心に。【速報】タグ必須\n"
        "- 投稿2（解説）: 機能の使い方・仕組みの解説。【保存版】【保存推奨】【必見】のいずれかのタグ必須。速報とは異なるトピックで\n"
        "- 投稿3（事例）: 実際の活用事例・驚きの使い方。【これはすごい】【保存必須】のいずれかのタグ必須。投稿1・2とは異なるトピックで\n\n"
    )
    output_format = '{"posts": ["速報投稿", "解説投稿", "事例投稿"]}'
    quote_tweet_ids = [None, None, None]

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

    if len(posts) < 3:
        raise ValueError(f"3本必要ですが{len(posts)}本しか生成されませんでした")

    validated = []
    for i, post in enumerate(posts[:3]):
        wlen = weighted_len(post)
        if wlen > 900:
            print(f"  投稿{i+1}: 文字数超過({wlen}w) → 短縮")
            post = shorten_post(post, 900)
        validated.append(post)

    return validated, types, quote_tweet_ids, None


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
    print(f"=== 投稿生成開始 ({today}) ===")
    print(f"収集済み: {len(stories)}件\n")

    log = load_log()
    recent_posts = [t["text"] for t in log.get("tweets", [])[-20:]]

    print("[1/2] Claude API で投稿を生成中...")
    try:
        posts, types, quote_tweet_ids, fallback_post = generate_posts(stories, recent_posts)
        print(f"  → {len(posts)}本生成完了\n")
    except Exception as e:
        print(f"ERROR: 投稿生成失敗 - {e}")
        sys.exit(1)

    print("[2/2] 投稿を保存中...")
    save_scheduled(posts, types, quote_tweet_ids, today, fallback_post)

    print(f"\n=== 完了: scheduled/{today}.json に保存済み ===")
    print("次: review_post.py でレビュー → save_draft.py で下書き保存")


if __name__ == "__main__":
    main()
