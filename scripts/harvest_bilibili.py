#!/usr/bin/env python3
"""B 站采集：搜视频 → 下音频 → 本地转写 → 进经验语料。

为什么走视频：公众号里没人认真写 B 站运营，但 B 站上「怎么做 B 站」的视频多得多，
而且 UP 主会讲具体数字（完播率、三连、发布时间）。视频没有文字，只能自己转。

管线（全部本地，不上传任何数据）：
    api.bilibili.com 搜索（带 cookie）
      → 过滤时长/播放
      → yt-dlp 下纯音频（m4a，不需要 ffmpeg）
      → faster-whisper 转写（模型可指定，默认 base）
      → docs/playbooks/经验库/corpus/bilibili/<bvid>.txt

用法：
    python3 scripts/harvest_bilibili.py --queries "B站运营" --limit 12
    python3 scripts/harvest_bilibili.py --all --limit 30 --model base
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "docs" / "playbooks" / "经验库" / "corpus" / "bilibili"
MEDIA = Path("/tmp/gv_bili_media")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

RELEVANT = ("运营", "增长", "营销", "流量", "获客", "转化", "私域", "投放",
            "内容", "账号", "变现", "风控", "限流", "封号", "矩阵", "复盘")

QUERIES = [
    "运营自动化 工作流", "增长黑客 案例复盘", "账号矩阵 运营方法",
    "平台风控 限流 机制", "内容运营 方法论", "私域 增长 打法",
    "AI 内容 生产 流程", "投放 ROI 复盘",
]


def opener():
    # Never trade TLS verification for a successful handshake. Use the system
    # trust store (or SSL_CERT_FILE for a managed CA) instead.
    ctx = ssl.create_default_context()
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),
                                     urllib.request.HTTPSHandler(context=ctx))
    op.addheaders = [("User-Agent", UA), ("Referer", "https://www.bilibili.com/"),
                     ("Accept-Language", "zh-CN,zh;q=0.9")]
    try:
        op.open("https://www.bilibili.com/", timeout=15).read()
    except Exception:
        pass
    return op


def dur_seconds(s: str) -> int:
    try:
        parts = [int(x) for x in str(s).split(":")]
    except ValueError:
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def search(op, q: str) -> list:
    url = ("https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword="
           + urllib.parse.quote(q))
    try:
        data = json.loads(op.open(url, timeout=18).read().decode("utf-8", "ignore"))
    except Exception:
        return []
    if data.get("code") != 0:
        return []
    out = []
    for it in (data.get("data") or {}).get("result") or []:
        title = re.sub(r"<[^>]+>", "", it.get("title", ""))
        out.append({
            "bvid": it.get("bvid"),
            "title": title,
            "play": it.get("play") or 0,
            "duration": dur_seconds(it.get("duration")),
            "pubdate": it.get("pubdate"),
            "description": re.sub(r"<[^>]+>", "", it.get("description", "") or ""),
        })
    return out


def download(bvid: str) -> Path | None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    target = MEDIA / f"{bvid}.m4a"
    if target.exists() and target.stat().st_size > 10000:
        return target
    url = f"https://www.bilibili.com/video/{bvid}"
    cmd = [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings",
           "-f", "ba/bestaudio", "-o", str(MEDIA / "%(id)s.%(ext)s"), url]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180, check=True)
    except Exception:
        return None
    for p in MEDIA.glob(f"{bvid}.*"):
        return p
    return None


def load_asr(model_dir: str):
    """优先 Vosk 本地模型（离线、CPU 快、不用下大模型）；没有就退化到 faster-whisper。"""
    import os
    d = model_dir or os.environ.get("VOSK_MODEL", "/tmp/vosk-model-small-cn-0.22")
    if Path(d).exists():
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        return ("vosk", Model(d))
    try:
        from faster_whisper import WhisperModel
        return ("whisper", WhisperModel("tiny", device="cpu", compute_type="int8"))
    except Exception:
        return (None, None)


def transcribe(path: Path, engine) -> str:
    kind, model = engine
    if kind == "vosk":
        import av
        import json as _json
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(model, 16000)
        rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
        with av.open(str(path)) as container:
            for frame in container.decode(audio=0):
                for f in rs.resample(frame):
                    rec.AcceptWaveform(f.to_ndarray().tobytes())
        rec.AcceptWaveform(b"")
        return _json.loads(rec.FinalResult()).get("text", "").strip()
    if kind == "whisper":
        segments, _ = model.transcribe(str(path), language="zh", vad_filter=True,
                                       beam_size=1, condition_on_previous_text=False)
        return "".join(s.text for s in segments).strip()
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--model", default="", help="Vosk 模型目录（默认读 VOSK_MODEL）")
    ap.add_argument("--min-play", type=int, default=300)
    ap.add_argument("--max-seconds", type=int, default=900)
    args = ap.parse_args()

    queries = QUERIES if args.all else [args.queries or QUERIES[0]]
    op = opener()
    seen, picked = set(), []
    for q in queries:
        for it in search(op, q):
            if not it["bvid"] or it["bvid"] in seen:
                continue
            if it["play"] < args.min_play:
                continue
            if not (60 <= it["duration"] <= args.max_seconds):
                continue
            # 关键词泛化后会混进游戏/教程视频，按主题词过滤
            blob = it["title"] + " " + (it.get("description") or "")
            if not any(k in blob for k in RELEVANT):
                continue
            seen.add(it["bvid"])
            picked.append(it)
        if len(picked) >= args.limit:
            break
    print(f"候选 {len(picked)} 个视频")

    engine = load_asr(args.model)
    print(f"ASR：{engine[0] or '不可用'}")

    OUT.mkdir(parents=True, exist_ok=True)
    saved = 0
    for it in picked[:args.limit]:
        audio = download(it["bvid"]) if engine[0] else None
        text = ""
        if audio:
            try:
                text = transcribe(audio, engine)
            except Exception:
                text = ""
        body = (text or "") + "\n" + (it.get("description") or "")
        if len(body.strip()) < 400:
            continue
        published = it.get("pubdate")
        (OUT / f"{it['bvid']}.txt").write_text(
            f"TITLE: {it['title']}\n"
            f"URL: https://www.bilibili.com/video/{it['bvid']}\n"
            f"PUBLISHED: {published}\n"
            f"PLAY: {it['play']}\n"
            f"ASR: {'true' if text else 'false'}\n"
            f"ASR_ENGINE: {engine[0] if text else 'none'}\n"
            f"HUMAN_REVIEWED: false\n\n{body.strip()}",
            encoding="utf-8")
        saved += 1
        flag = "转写" if text else "仅简介"
        print(f"  [{flag}] {it['title'][:34]} | {it['duration']}s | {it['play']} 播放 | {len(body)} 字")
        if audio:
            audio.unlink(missing_ok=True)
    print(f"落盘 {saved} 篇 → {OUT}")


if __name__ == "__main__":
    main()
