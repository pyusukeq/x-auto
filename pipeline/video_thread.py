#!/usr/bin/env python3
"""
動画ツイートを解析して日本語スレッドを生成するスクリプト
使用法: python video_thread.py <tweet_url>
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os
import re
import json
import base64
import tempfile
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.join(BASE_DIR, "pipeline")
sys.path.insert(0, PIPELINE_DIR)
from draft_html import save_thread_html

DRAFT_DIR = os.path.join(BASE_DIR, "draft")

import anthropic
import httpx

THREAD_SYSTEM_PROMPT = """あなたはAI・Claude Codeの情報を日本のエンジニア・個人開発者向けに発信するXアカウントの運営者です。
物腰の柔らかい女性で、専門知識を初心者にも分かりやすく伝えることが得意です。
動画コンテンツを分析し、日本語のスレッド投稿を作成します。"""


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


def fetch_tweet_content(url: str) -> dict:
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


def download_video(tweet_url: str, output_dir: str) -> str | None:
    """yt-dlpでツイートの動画をダウンロード。失敗時はNoneを返す"""
    output_path = os.path.join(output_dir, "video.%(ext)s")
    try:
        result = subprocess.run(
            ["yt-dlp", "--no-playlist", "-f", "best[height<=720]/best",
             "-o", output_path, tweet_url],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"  yt-dlp stderr: {result.stderr[:300]}")
            return None
        for f in os.listdir(output_dir):
            if f.startswith("video."):
                return os.path.join(output_dir, f)
        return None
    except FileNotFoundError:
        print("  yt-dlp が見つかりません（スキップ）")
        return None
    except Exception as e:
        print(f"  動画ダウンロードエラー: {e}")
        return None


def extract_frames(video_path: str, output_dir: str, num_frames: int = 6) -> list[str]:
    """ffmpegで均等間隔のフレームを抽出する。ffmpegが使えない場合は空リストを返す"""
    duration = 60.0
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", video_path],
            capture_output=True, text=True, timeout=30
        )
        info = json.loads(probe.stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                duration = float(stream.get("duration", 60.0))
                break
    except FileNotFoundError:
        print("  ffprobe が見つかりません（フレーム抽出をスキップ）")
        return []
    except Exception as e:
        print(f"  動画情報取得エラー: {e}")

    frames = []
    interval = duration / (num_frames + 1)
    for i in range(num_frames):
        timestamp = interval * (i + 1)
        frame_path = os.path.join(output_dir, f"frame_{i:02d}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-ss", str(timestamp), "-i", video_path,
                 "-vframes", "1", "-q:v", "3", "-y", frame_path],
                capture_output=True, timeout=30
            )
        except Exception:
            continue
        if os.path.exists(frame_path):
            frames.append(frame_path)
    return frames


def encode_image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def generate_thread(tweet: dict, frame_paths: list[str], client) -> list[str]:
    """Claude APIでスレッド投稿を生成する"""
    content = []

    for path in frame_paths:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": encode_image_b64(path),
            }
        })

    has_frames = len(frame_paths) > 0
    frame_note = (
        f"上記の動画フレーム（{len(frame_paths)}枚）を参照して、"
        if has_frames
        else "（動画フレームは取得できませんでした。ツイートのテキスト情報のみで生成します）\n\n"
    )

    prompt = f"""{frame_note}以下の動画ツイートを日本語スレッドに変換してください。

【ツイート情報】
著者: @{tweet['author']}
テキスト: {tweet['text']}

【スレッド構成】

投稿1（用語解説）は必ず以下の形式:
【まず前提の用語だけ押さえて】
この動画に出てくる言葉、これだけ知ってればOK↓

-〇〇 ＝　△△
-〇〇 ＝　△△
-〇〇 ＝　△△

（具体例: - skill.md ＝ スキルの中心となる説明ファイル（読み込むとエージェントが使い方を理解する））

投稿2以降（3〜5投稿）: 動画の内容を順を追って解説する。各投稿は独立した情報のまとまりにする。

【制約】
- 各投稿は全角換算280文字以内
- スレッド全体で4〜6投稿
- 専門用語は平易な日本語で説明
- 絵文字は1投稿あたり0〜1個
- ハッシュタグは使わない

以下のJSON形式のみで出力（説明・コメント不要）:
{{
  "posts": [
    "投稿1のテキスト",
    "投稿2のテキスト"
  ]
}}"""

    content.append({"type": "text", "text": prompt})

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=THREAD_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}]
    )

    response_text = message.content[0].text.strip()

    try:
        json_match = re.search(r'\{[\s\S]*?"posts"[\s\S]*?\}', response_text)
        if json_match:
            data = json.loads(json_match.group())
            posts = data.get("posts", [])
            if posts:
                return posts
    except Exception:
        pass

    # フォールバック: 番号付き区切りで分割
    posts = []
    current: list[str] = []
    for line in response_text.split("\n"):
        if re.match(r'^【|^\d+[.。)]|^投稿\d+', line) and current:
            posts.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        posts.append("\n".join(current).strip())
    return [p for p in posts if p]


def main():
    if len(sys.argv) < 2:
        print("使用法: python video_thread.py <tweet_url>")
        sys.exit(1)

    url = sys.argv[1]
    today = datetime.now().strftime("%Y-%m-%d")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    print("=== 動画スレッド生成 ===")
    print(f"URL: {url}\n")

    print("ツイート内容を取得中...")
    try:
        tweet = fetch_tweet_content(url)
        print(f"取得完了: @{tweet['author']}")
        print(f"内容: {tweet['text'][:100]}...\n")
    except Exception as e:
        print(f"ERROR: ツイート取得失敗 - {e}")
        sys.exit(1)

    frame_paths: list[str] = []
    tmp_ctx = tempfile.TemporaryDirectory()
    tmpdir = tmp_ctx.name

    print("動画をダウンロード中...")
    video_path = download_video(url, tmpdir)

    if video_path:
        print(f"ダウンロード完了: {os.path.basename(video_path)}")
        print("フレームを抽出中...")
        frame_paths = extract_frames(video_path, tmpdir)
        print(f"フレーム抽出: {len(frame_paths)}枚\n")
    else:
        print("動画ダウンロード失敗（テキストのみで生成します）\n")

    print("Claude APIでスレッドを生成中...")
    client = anthropic.Anthropic(
        api_key=api_key,
        http_client=httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)),
    )
    try:
        posts = generate_thread(tweet, frame_paths, client)
    except Exception as e:
        print(f"ERROR: スレッド生成失敗 - {e}")
        tmp_ctx.cleanup()
        sys.exit(1)

    tmp_ctx.cleanup()

    print(f"生成完了: {len(posts)}投稿\n")

    os.makedirs(DRAFT_DIR, exist_ok=True)
    draft_path = os.path.join(DRAFT_DIR, f"thread-{today}.md")
    counter = 1
    while os.path.exists(draft_path):
        draft_path = os.path.join(DRAFT_DIR, f"thread-{today}-{counter}.md")
        counter += 1

    jst = datetime.now(timezone(timedelta(hours=9)))
    time_str = jst.strftime("%H:%M JST")

    lines = [f"# 動画スレッド下書き {today}", f"生成: {time_str}", f"元URL: {url}", "", "---", ""]
    for i, post in enumerate(posts, 1):
        lines.append(f"## 投稿{i}")
        lines.append("")
        lines.append(post)
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(draft_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Markdown保存: {draft_path}")

    save_thread_html(posts, url, today)

    print(f"\n=== 生成されたスレッド ===")
    for i, post in enumerate(posts, 1):
        print(f"\n【投稿{i}】\n{post}")


if __name__ == "__main__":
    main()
