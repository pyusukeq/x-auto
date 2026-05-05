#!/usr/bin/env python3
"""
URL指定でX投稿を日本語リライトするスクリプト
使用法: python rewrite_from_url.py <tweet_url>
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os
import re
import requests
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
sys.path.insert(0, PIPELINE_DIR)
from draft_html import save_rewrite_html

DRAFT_DIR = os.path.join(BASE_DIR, "draft")

import anthropic
import httpx

REWRITE_SYSTEM_PROMPT = """あなたはAI・Claude Codeの情報を日本のエンジニア・個人開発者向けに発信するXアカウントの運営者です。
運営者は物腰の柔らかい女性で、専門知識はあるが押しつけがましくなく、読者に寄り添う温かみのある口調が特徴です。

## 投稿テンプレート（必ずこの構成で書く）

```
【タグ】
感情的な一言（内容に驚き・興奮・共感があれば必ず入れる）

問題提起か驚きの事実（1行1文・2〜3行）
『印象的なフレーズや数字があれば引用』

動画URL（3〜4行目に必ず挿入）

結論はこれ👇 または 具体的には⏬ または ポイントはここ👇

▶︎ 箇条書き1（簡潔に）
▶︎ 箇条書き2（簡潔に）
▶︎ 箇条書き3（簡潔に）

つまり、

短い結論・気づき（1〜2行）

CTA（「試してみて👀」「チェックしてみて👀」など）
```

## ルール
- 【タグ】は内容に合わせて選ぶ:【速報】【保存版】【必見】【保存推奨】【保存必須】【これはすごい】
- ハッシュタグは使わない
- 全角換算350文字以内（動画URLのt.co換算23文字を含む）
- タグの文言をCTAで繰り返さない
- 断定的・強引な表現より「〜ですよね」「〜かもしれません」など柔らかい言い回しを使う
- 絵文字は✨🙌😊など柔らかめのものを控えめに使う（1投稿1〜2個まで）"""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            s = data.strip()
            if s:
                self._parts.append(s)

    def get_text(self):
        return " ".join(self._parts)


def ensure_duplicate_url(text: str, url: str) -> str:
    """URLを2回含める（1回目→動画インライン変換、2回目→リンクとして残存）"""
    count = text.count(url)
    if count >= 2:
        return text
    if count == 0:
        # URLがない場合は先頭に2回挿入
        lines = text.split("\n")
        insert_at = min(3, len(lines))
        lines.insert(insert_at, url)
        lines.insert(insert_at + 1, url)
        return "\n".join(lines)
    # 1回だけある場合：直後に2回目を挿入
    idx = text.find(url)
    after = idx + len(url)
    separator = "\n" if text[after:after+1] != "\n" else ""
    return text[:after] + separator + "\n" + url + text[after:]


def fetch_tweet_content(url: str) -> dict:
    """oEmbed APIでツイートのテキストと著者情報を取得する"""
    oembed_url = f"https://publish.twitter.com/oembed?url={url}&omit_script=1"
    resp = requests.get(oembed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    parser = _TextExtractor()
    parser.feed(data.get("html", ""))
    raw_text = parser.get_text()
    raw_text = re.sub(r"—\s*.+?\(@\w+\)\s*\w+ \d+,\s*\d{4}$", "", raw_text).strip()

    return {
        "author": data.get("author_name", ""),
        "text": raw_text,
        "url": url,
    }


def rewrite_post(tweet: dict, client) -> str:
    """Claude APIで日本語X投稿にリライトする（動画URLを3〜4行目に必ず挿入）"""
    url = tweet["url"]
    prompt = (
        f"以下の英語X投稿を日本語でリライトしてください。\n\n"
        f"【元の投稿】\n"
        f"著者: @{tweet['author']}\n"
        f"内容: {tweet['text']}\n\n"
        f"【重要】動画URL「{url}」をフック（感情的な一言）の直後・3〜4行目に必ず挿入してください。"
        f"URLは一切変更・省略しないこと。\n\n"
        f"リライト後の投稿テキストのみを出力してください（説明・コメント不要）。"
    )
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=REWRITE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    text = message.content[0].text.strip()

    # URLを必ず2回含める（インライン表示＋リンク残存のため）
    text = ensure_duplicate_url(text, url)
    return text


def main():
    if len(sys.argv) < 2:
        print("使用法: python rewrite_from_url.py <tweet_url>")
        sys.exit(1)

    url = sys.argv[1]
    today = datetime.now().strftime("%Y-%m-%d")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    print(f"=== URL指定リライト ===")
    print(f"URL: {url}\n")

    print("ツイート内容を取得中...")
    try:
        tweet = fetch_tweet_content(url)
        print(f"取得完了: @{tweet['author']}")
        print(f"内容: {tweet['text'][:120]}...\n")
    except Exception as e:
        print(f"ERROR: ツイート取得失敗 - {e}")
        sys.exit(1)

    print("Claude APIでリライト中...")
    client = anthropic.Anthropic(
        api_key=api_key,
        http_client=httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)),
    )
    try:
        rewritten = rewrite_post(tweet, client)
    except Exception as e:
        print(f"ERROR: リライト失敗 - {e}")
        sys.exit(1)

    # Markdown下書き保存（同日複数回対応）
    os.makedirs(DRAFT_DIR, exist_ok=True)
    draft_path = os.path.join(DRAFT_DIR, f"rewrite-{today}.md")
    counter = 1
    while os.path.exists(draft_path):
        draft_path = os.path.join(DRAFT_DIR, f"rewrite-{today}-{counter}.md")
        counter += 1

    jst = datetime.now(timezone(timedelta(hours=9)))
    time_str = jst.strftime("%H:%M JST")
    content = f"# リライト下書き {today}\n生成: {time_str}\n元URL: {url}\n\n---\n\n{rewritten}\n"
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Markdown保存: {draft_path}")

    # HTML下書き保存（docs/rewrite.html）
    save_rewrite_html(rewritten, url, today)

    print(f"\n=== 内容プレビュー ===\n{rewritten}")


if __name__ == "__main__":
    main()
