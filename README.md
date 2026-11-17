# instagram-auto-uploader

라즈베리파이에서 도는 **개인용 인스타그램 자동 업로더**입니다. 게임 플레이를 자동으로 녹화·캡처해 릴스와 캐러셀을 만들고, `claude -p`로 캡션을 쓴 뒤, **노션에서 사람이 승인한 게시물만** 인스타그램에 올립니다.

첫 콘텐츠는 모바일 웹게임 [랩워즈](https://apg0001.github.io/lab-wars/) 소개입니다. 게시물은 게임과 플레이 링크만 소개하고 레포·소스코드는 언급하지 않습니다.

## 흐름

```
cron ─ generate ─▶ Playwright로 랩워즈 플레이 녹화·캡처
                 ─▶ ffmpeg·Pillow로 릴스(1080x1920)·캐러셀(1080x1350) 합성
                 ─▶ claude -p 캡션 (레포 언급 금지·플레이 링크 필수 검증)
                 ─▶ apg0001/ig-media 에 푸시 → GitHub Pages 공개 URL
                 ─▶ 노션 DB에 '초안' 등록 (미리보기·캡션)
사람 ─ 노션에서 미리보기 확인, 캡션 수정, 상태를 '승인' / '반려'로 변경 (예약 날짜도 가능)
cron ─ sync (5분마다) ─▶ 승인 건을 Instagram API로 게시 → '게시됨' + 인스타 링크
cron ─ cleanup (매일) ─▶ 게시·반려 후 7일 지난 미디어를 ig-media에서 히스토리째 삭제
```

인스타그램은 컨테이너를 만들 때 URL에서 파일을 내려받아 보관하므로, 게시 후에는 원본 URL이 필요 없습니다.

## 설치

```bash
sudo apt install -y python3.12-venv ffmpeg fonts-noto-cjk libatomic1
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
git clone https://github.com/apg0001/ig-media.git media-repo
cp .env.example .env    # 토큰 채우기
```

`claude`(Claude Code)가 로그인된 상태여야 캡션을 만들 수 있습니다.

## 노션 연결

1. https://www.notion.so/profile/integrations 에서 **내부 통합**을 만들고 시크릿을 `.env`의 `NOTION_TOKEN`에 넣습니다.
2. 게시물 DB를 둘 노션 페이지를 하나 만들고, 페이지 오른쪽 위 `···` → **연결** → 방금 만든 통합을 추가합니다.
3. DB 생성:

   ```bash
   .venv/bin/python -m uploader notion-setup "<그 페이지 URL>"
   ```

DB 속성: 제목, 상태(초안·승인·반려·게시됨·실패), 형식, 소스, 캡션, 미디어, 예약, 인스타 링크, 오류, ID.

## 인스타그램 연결

1. 인스타 계정을 프로페셔널(비즈니스 권장, 스토리 API 때문)로 전환합니다.
2. Meta 개발자 앱 → Instagram API(Instagram 로그인) → 계정을 테스터로 추가 → **액세스 토큰 생성**.
3. `.env`에 `IG_USER_ID`, `IG_ACCESS_TOKEN`을 넣고 `config.yaml`의 `instagram.enabled`를 `true`로 바꿉니다.

연동 전에는 승인된 게시물이 '승인' 상태로 대기만 합니다.

## 명령

```bash
.venv/bin/python -m uploader generate [--kind REELS|CAROUSEL|STORIES] [--stage 3-10]
.venv/bin/python -m uploader sync            # 노션 결정 반영 + 승인 건 게시
.venv/bin/python -m uploader list
.venv/bin/python -m uploader approve <id>    # 노션 없이 승인 (reject도 있음)
.venv/bin/python -m uploader cleanup
.venv/bin/python -m uploader refresh-token
```

스케줄은 [deploy/crontab](deploy/crontab) 참고 (하루 2번 초안, 5분마다 동기화).

## 구조

```
uploader/
  sources/lab_wars.py  랩워즈 녹화·캡처 (쇼케이스 저장 데이터 주입, 봇 플레이, 카메라 추적)
  compose.py           릴스·캐러셀·스토리 합성
  caption.py           claude -p 캡션 생성·검증
  media_host.py        ig-media 레포 업로드·Pages 대기·히스토리 정리
  notion.py            노션 DB 생성·초안 등록·승인 읽기
  instagram.py         Instagram Graph API 게시·토큰 갱신
  pipeline.py          전체 흐름
  db.py                SQLite 상태 (data/state.db)
config.yaml            설정 (비밀 값은 .env)
```

## 개발

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest
```

기여 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 따릅니다.
