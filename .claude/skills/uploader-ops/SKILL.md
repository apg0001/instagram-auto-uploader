---
name: uploader-ops
description: 이 인스타그램 자동 업로더를 운영·확장할 때의 절차 — 게시물 생성·예약, 노션 대시보드/콘텐츠 기획, 새 소스 추가, 라즈베리파이 부하 규칙, 릴리스 흐름. 게시물을 만들거나, 노션을 만지거나, 소스·생성기를 추가하거나, 릴리스할 때 먼저 읽는다.
---

# 업로더 운영

라즈베리파이 한 대에서 돈다. 구조와 사용법은 README.md가 기준이고, 여기에는 **작업 순서와 지켜야 할 것**만 적는다.

## 1. 파이 부하 규칙 (가장 먼저)
- 무거운 작업(`uploader generate`, pytest, Playwright, ffmpeg)은 `nice -n 19`로, **한 번에 하나만**.
- 시작 전에 다른 무거운 작업이 끝날 때까지 기다린다. 기다리는 명령은 **따로** 실행한다
  (같은 명령줄에 `pytest` 같은 단어가 있으면 pgrep이 자기 자신을 잡아 영원히 기다림):
  ```bash
  until ! pgrep -f "[m]s-playwright|[o]pt/google/chrome|[f]fmpeg|[p]ytest|[v]itest run|[v]ite build|[d]ocker build" >/dev/null; do sleep 15; done
  ```
- `[c]hrome` 단독 패턴 금지 (VS Code의 Claude 프로세스 인자 `--no-chrome`에 걸림).

## 2. 게시물 만들기
```bash
nice -n 19 .venv/bin/python -m uploader generate --source <소스> [--at 2026-11-19T19:00]
```
- 결과는 노션 '📥 확인할 것'에 초안으로 올라간다. 게시는 사람이 승인한 뒤(증시만 자동 승인).
- `--at`은 노션 '예약'에 들어가고, 그 시각 전에는 승인돼도 올라가지 않는다. 하루 한 편 시리즈는 이걸로 미리 쌓는다.
- 만든 뒤 꼭 눈으로 확인: 릴스는 첫 프레임(=커버)과 장면 몇 개를 이어 붙여 본다.
  ```bash
  ffmpeg -v error -i a.jpg -i b.jpg -filter_complex "[0]scale=360:-1[a];[1]scale=360:-1[b];[a][b]hstack=2" sheet.jpg
  ```

## 3. 노션
- 처음 한 번: `notion-setup <페이지>` → `notion-dashboard` (여러 번 실행해도 안전, 새 속성만 추가).
- 콘텐츠 기획 표: 새 요청 → (`plan`이 Claude로 다듬음) 검토 대기 → 사람이 '사용' → 주기마다 초안.
  '생성기'가 정해진 행은 전용 생성기를, 아니면 웹 조사 카드뉴스(`generate_topic`)를 쓴다.
- 보기 API는 `Notion-Version: 2026-03-11` (`notion.VIEWS_VERSION`), 나머지는 2022-06-28.

## 4. 새 소스·생성기 추가 체크리스트
1. `uploader/sources/<이름>.py` 수집, 합성은 `compose.py`/`cards/` 재사용. 주제 색은 `themes.get(<주제>)`/`themes.css(...)`.
2. `Pipeline.generate()` 분기 + `generate_<이름>()` → `_finalize(..., meta={"topic_theme": ...})`.
3. 숫자는 데이터에서 코드로 넣고, Claude에게는 문장만 쓰게 한다. 캡션 해시태그 4~5개.
4. 노션 기획에서 고를 수 있게 하려면 `planner.GENERATORS`와 `run_due` 분기에 추가 후 `notion-dashboard`.
5. 테스트(`tests/`) + README(무엇을 올리나·명령·구조) 갱신.

## 5. 콘텐츠 규칙
- 랩워즈: 레포·소스코드 언급 금지, 게임과 플레이 링크만.
- Agent Office: 로그인 안 한 합성 데모(`/demo`)만 녹화.
- 증시: 숫자는 데이터에서만, 매수·매도 권유 금지, 면책 문구.
- 계정 주인 실명·핸들 쓰지 않기. `.env` 값은 출력하지 않는다(있는지·길이만 확인, `source .env` 금지 — python-dotenv로 읽기).

## 6. Git·릴리스
- 한국어 이슈 → develop에서 `feat/#번호-설명` → 커밋 `[feat] 설명 (#번호)` → PR(develop) → CI Gate 통과 후 병합.
- 릴리스: develop→main PR `[release] x.y.z` → `mergeStateStatus`가 CLEAN일 때 병합 → **병합 후** `gh release create vX.Y.Z` → 백머지 이슈·브랜치·PR.
- 파이 작업본은 늘 `main`으로 돌려 두고, `deploy/crontab`이 바뀌었으면 `crontab deploy/crontab`.
