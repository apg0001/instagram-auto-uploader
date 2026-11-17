"""명령줄 진입점: python -m uploader <명령>"""
from __future__ import annotations

import argparse
import fcntl
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import config
from .db import APPROVED, REJECTED


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="uploader", description="인스타그램 자동 업로더")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="게시물 초안 생성 (녹화·합성·캡션·미디어 업로드·노션 등록)")
    g.add_argument("--source", default="lab-wars",
                   help="lab-wars | agent-office | market:premarket | market:close | market:weekly | market:monthly")
    g.add_argument("--kind", choices=["REELS", "CAROUSEL", "STORIES"])
    g.add_argument("--stage", help="예: 3-10")
    tp = sub.add_parser("topic", help="주제를 조사해 카드뉴스 초안 생성 (claude -p + 웹 검색)")
    tp.add_argument("topic", help="예: '이번 주 AI 반도체 이슈 정리'")
    tp.add_argument("--design", choices=["template", "free"], default="template",
                    help="free: 공개 디자인 스킬을 지침으로 Claude가 HTML을 직접 디자인")
    pp = sub.add_parser("post", help="준비된 이미지·영상과 캡션으로 바로 초안 생성")
    pp.add_argument("files", nargs="+", type=Path)
    pp.add_argument("--title", required=True)
    cap = pp.add_mutually_exclusive_group(required=True)
    cap.add_argument("--caption")
    cap.add_argument("--caption-file", type=Path)
    sub.add_parser("sync", help="노션 승인 결과를 가져와 승인 건 게시")
    sub.add_parser("list", help="게시물 목록")
    for name in ("approve", "reject"):
        p = sub.add_parser(name, help=f"노션 없이 직접 {'승인' if name == 'approve' else '반려'}")
        p.add_argument("id", help="게시물 id (앞부분만 써도 됨)")
    sub.add_parser("cleanup", help="보존 기한 지난 미디어 삭제")
    sub.add_parser("refresh-token", help="인스타 장기 토큰 갱신")
    n = sub.add_parser("notion-setup", help="노션 페이지 아래에 게시물 DB 생성")
    n.add_argument("parent", help="통합을 연결한 노션 페이지 URL 또는 id")
    args = ap.parse_args(argv)

    cfg = config.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(cfg.data_dir / "uploader.log")])

    # 동시 실행 방지 (cron에서 sync가 겹치거나 generate와 겹치지 않게)
    lock = open(cfg.data_dir / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if args.cmd == "sync" else 0))
    except BlockingIOError:
        logging.info("다른 작업이 실행 중이라 건너뜀")
        return 0

    from .pipeline import Pipeline  # Playwright 등 무거운 import는 필요할 때만
    pl = Pipeline(cfg)

    if args.cmd in ("generate", "topic", "post"):
        if args.cmd == "generate":
            post = pl.generate(args.source, args.kind, args.stage)
        elif args.cmd == "topic":
            post = pl.generate_topic(args.topic, args.design)
        else:
            text = args.caption if args.caption is not None else args.caption_file.read_text(encoding="utf-8")
            post = pl.post_manual(args.title, text, args.files)
        if post is None:
            print("오늘은 만들 게시물이 없음 (휴장일 등)")
            return 0
        print(f"{post.id}\n{post.caption}\n" + "\n".join(m["url"] for m in post.media))
    elif args.cmd == "sync":
        pl.sync()
    elif args.cmd == "list":
        for p in pl.db.all():
            when = datetime.fromtimestamp(p.created_at).strftime("%m-%d %H:%M")
            print(f"{p.id}  {p.status:<3}  {when}  {p.title}  {p.permalink or ''}")
    elif args.cmd in ("approve", "reject"):
        post = pl.db.find(args.id)
        if not post:
            print("해당 id의 게시물이 없거나 여러 개임", file=sys.stderr)
            return 1
        pl.set_status(post, APPROVED if args.cmd == "approve" else REJECTED)
        print(f"{post.id} → {post.status}")
    elif args.cmd == "cleanup":
        pl.cleanup()
    elif args.cmd == "refresh-token":
        pl.refresh_token()
    elif args.cmd == "notion-setup":
        print("노션 DB 생성:", pl.setup_notion(args.parent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
