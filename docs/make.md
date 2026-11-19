# Make 웹훅으로 게시하기

Meta 개발자 계정 없이 인스타에 게시하는 방법입니다. 노션에서 승인한 게시물을 업로더가 Make 웹훅으로 보내면, Make 시나리오가 **Instagram for Business** 모듈로 게시하고 결과(미디어 id)를 돌려줍니다.

```
노션 승인 → 업로더 sync → POST JSON → Make 웹훅 → 라우터(kind) → Instagram 게시 → Webhook response {"id": ...}
```

## 0. 준비물

- **인스타 프로페셔널(비즈니스) 계정**
- **그 계정에 연결된 페이스북 페이지**
  - Make는 페이스북 로그인으로 인스타에 접근하기 때문에 필요합니다.
  - 개발자 등록과 달리 일반 페이스북 로그인만 되면 됩니다.
  - 연결 방법: 인스타 앱 → 프로필 편집 → **페이지** → 연결 또는 새로 만들기. 메타 비즈니스 스위트에서도 연결할 수 있습니다.
- **Make 계정**
  - [make.com](https://www.make.com) 무료 플랜이면 충분합니다.
  - 게시 1건은 4번 안팎의 operation을 씁니다. 하루 2~3건이면 월 한도 안입니다.

## 1. 웹훅 만들기

1. Make → **Create a new scenario**
2. 첫 모듈로 **Webhooks → Custom webhook** → **Add** → 이름(예: `ig-uploader`) → **Save**
3. 나온 주소(`https://hook.….make.com/…`)를 **Copy address**
4. 라즈베리파이 `.env`에 넣습니다. 이 주소를 아는 사람은 누구나 게시를 시킬 수 있으니 비밀로 두세요.
   ```
   MAKE_WEBHOOK_URL=https://hook.….make.com/…
   ```
5. 웹훅 모듈이 "데이터 기다리는 중"일 때, 라즈베리파이에서 아래 명령으로 샘플을 보내 데이터 구조를 인식시킵니다. `kind`가 `TEST`라서 아무 경로에도 걸리지 않고, 실제로 게시되지 않습니다.
   ```bash
   curl -s -X POST "$MAKE_WEBHOOK_URL" -H 'Content-Type: application/json' -d '{
     "post_id":"test","kind":"TEST","caption":"테스트","image_url":"https://apg0001.github.io/ig-media/x.jpg",
     "video_url":"https://apg0001.github.io/ig-media/x.mp4","cover_url":"https://apg0001.github.io/ig-media/c.jpg",
     "media":[{"type":"IMAGE","media_type":"IMAGE","mediaType":"IMAGE","url":"https://apg0001.github.io/ig-media/x.jpg","image_url":"https://apg0001.github.io/ig-media/x.jpg"},{"type":"VIDEO","media_type":"VIDEO","mediaType":"VIDEO","url":"https://apg0001.github.io/ig-media/x.mp4","video_url":"https://apg0001.github.io/ig-media/x.mp4"}],
     "count":2}'
   ```
   `.env`의 값을 쓰려면 먼저 `set -a; . ./.env; set +a` 를 실행하세요.

## 2. 라우터와 게시 모듈

웹훅 뒤에 **Flow Control → Router**를 붙이고, 경로 3개를 만듭니다. 각 경로의 필터는 `kind` **Equal to** 값입니다.

| 필터 `kind` = | 모듈 (Instagram for Business) | 매핑 |
|---|---|---|
| `IMAGE` | **Create a Photo Post** | Photo URL ← `image_url`, Caption ← `caption` |
| `CAROUSEL` | **Create a Carousel Post** | Media ← `media` 배열 (Type ← `type`, URL ← `url`), Caption ← `caption` |
| `REELS` | **Create a Reel Post** | Video URL ← `video_url`, Caption ← `caption`, Cover URL ← `cover_url` (있으면) |

- 첫 모듈을 추가할 때 **Create a connection** → 페이스북으로 로그인 → 권한을 허용하고, **Page**에서 인스타와 연결된 페이지를 고릅니다. 페이지가 안 보이면 0번의 페이지 연결을 다시 확인하세요.
- 스토리(`STORIES`)는 경로가 없으면 아래 4번처럼 실패로 표시됩니다. Make에 스토리 모듈이 있으면 같은 방식으로 경로를 추가하면 됩니다.

## 3. 결과 돌려주기 (필수)

각 경로의 게시 모듈 뒤에 **Webhooks → Webhook response**를 붙입니다.

- Status: `200`
- Body:
  ```json
  {"id": "{{게시 모듈의 ID}}"}
  ```
  `{{…}}` 자리는 바로 앞 Instagram 모듈의 결과 `ID`를 클릭해서 넣습니다.
- 퍼머링크도 받고 싶으면 **Instagram for Business → Get a Media**(등)로 조회한 뒤 `"permalink": "{{…}}"`를 추가합니다. 없어도 게시에는 지장이 없습니다.

이 응답이 없으면 업로더는 게시가 됐는지 알 수 없어서 **실패**로 표시합니다. 같은 게시물이 두 번 올라가는 것을 막기 위해서입니다.

## 4. 켜기

1. 시나리오 저장 → 스케줄을 **Immediately (as data arrives)**로 두고 **ON**
2. `config.yaml`:
   ```yaml
   instagram:
     enabled: true
     mode: make
   ```
3. 노션에서 게시물 하나를 **승인**으로 바꾸고 1분 정도 기다립니다.
   - 성공하면 게시됨으로 바뀌고 id가 기록됩니다.
   - 실패하면 노션에 오류 메시지가 남습니다. Make의 **History**에서 어느 모듈이 실패했는지 볼 수 있습니다.

## 5. 반응 모으기 (선택)

최근 게시물의 좋아요·댓글 수를 노션 게시물 표에 적고, 댓글 중 "이거 다뤄 주세요" 같은 것을 💡 아이디어 수신함에 넣습니다.
업로더가 하루 한 번(21:15) 최근 3일 안에 올린 게시물마다 웹훅을 부릅니다:

```json
{"action": "engagement", "media_id": "17977084647177016"}
```

1. 라우터에 경로를 하나 더 만들고 필터를 `action` **Equal to** `engagement`로 둡니다.
   (게시 경로들은 `kind` 로 거르므로 이 요청과 섞이지 않습니다.)
2. **Instagram for Business → Get a Media**: Media ID ← `media_id`. 결과에서 좋아요 수·댓글 수를 씁니다.
3. 댓글을 읽는 모듈(예: **List Comments** — 이름은 Make 버전에 따라 다를 수 있음): Media ID ← `media_id`.
4. **Flow Control → Array aggregator**: 3번 모듈의 결과를 모으고 `id`, `text` 만 고릅니다. **쓴 사람 이름은 넣지 않습니다.**
5. **Webhooks → Webhook response** (Status 200), Body:
   ```json
   {"id": "{{media_id}}", "like_count": {{2번 좋아요 수}}, "comments_count": {{2번 댓글 수}},
    "comments": {{4번 배열}}}
   ```
6. `config.yaml` 의 `engagement.enabled: true`. 손으로 확인: `.venv/bin/python -m uploader engagement`

Make 작업 수: 게시물 하나에 3~4개 → 하루 12편이면 하루 40개 남짓, 한 달 1,200개 정도.
무료 플랜(월 1,000개)을 넘을 수 있으니 `engagement.days` 를 줄이거나, 공식 API(`mode: graph`)는 이 경로 없이 바로 됩니다.

## 6. 스토리 (선택)

`kind` = `STORIES` 경로를 만들고 Instagram for Business의 스토리 게시 모듈(있는 경우)에 `image_url` 또는 `video_url` 을 넣습니다.
API로 올리는 스토리에는 링크 스티커를 붙일 수 없어서, 새 게시물 알림용 스토리보다는 따로 만든 세로 이미지·영상에 맞습니다.

## 실패했을 때

| 메시지 | 뜻 | 할 일 |
|---|---|---|
| `Make 응답 시간 초과` | 300초 안에 응답이 오지 않음. 릴스 처리가 길면 생길 수 있음 | **인스타에서 실제로 올라갔는지 먼저 확인**한 뒤 필요하면 다시 승인 |
| `Make 응답에 id 없음` | Webhook response 모듈이 없거나 그 전에 끝남 (`Accepted`만 옴) | 3번을 확인. 그 사이 게시됐을 수 있으니 인스타도 확인 |
| `Make 웹훅 4xx/5xx` | 시나리오가 꺼져 있거나 주소가 틀림 | 시나리오 ON, `.env` 주소 확인 |

공식 API로 돌아가려면 `mode: graph`로 바꾸고 `.env`에 `IG_USER_ID` / `IG_ACCESS_TOKEN`을 넣으면 됩니다.
