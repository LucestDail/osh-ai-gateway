# osh-ai-gateway

홈랩 생태계의 **LLM 축**. OpenRouter·Gemini·Vertex 를 OpenAI 호환 인터페이스로 투명 프록시하고 사용량을 집계한다.
개별 서비스가 각자 API 키를 들고 있지 않도록, 이 게이트웨이 하나만 신뢰하게 만드는 것이 목적이다.

```
HARU-Server · my-computer · AIm · osh · myapi · simpleStock · chominjungum
        └────────────── 전부 여기를 경유 ──────────────┘
                         osh-ai-gateway (:8780, /llm/*)
                                  │
                 OpenRouter · Gemini · Vertex
```

> waynai 만 OpenRouter 에 직결한다(유일 이탈). `OPENROUTER_BASE_URL` 오버라이드로 전환 가능하지만,
> **역설적으로 게이트웨이 장애 때 waynai 만 살아남는 구조**라 통일 전에 폴백 설계를 먼저 정할 것.

---

## 🔴 다음 세션이 먼저 알아야 할 것 (2026-09-25 실측)

### 1. `deploy/gateway.conf` 를 믿지 말 것
이 파일이 오랫동안 "홈랩 nginx 라우팅 원본"으로 참조돼 왔는데 **낡은 스냅샷이다.**

| | upstream | 내용 |
|---|---|---|
| 저장소 `deploy/gateway.conf` | 6개 | `listen 80` 블록 하나 |
| 서버 `/etc/nginx/sites-enabled/gateway.conf` | **11개** | 379줄. `listen 18080 ssl` 블록, rate-limit, Basic 인증, LAN 전용 차단 포함 |

waynai·dongnae·chominjungum·probius·printscan·yeokkeum·withvideo 라우팅이 저장소 파일에는 **아예 없다.**
배선 확인은 서버에서 직접 한다:
```bash
ssh <서버> 'cat /etc/nginx/sites-enabled/gateway.conf'
```
⚠️ 서버 안에서도 `sites-available`(구버전)과 `sites-enabled`(실사용)가 **심볼릭 링크가 아니라 별개 파일**이다.
`sites-available` 기준으로 재배포하면 3주치 보안 변경이 롤백된다.

### 2. 서버에서 직접 편집하는 저장소다 — git 에 없는 코드가 돌 수 있다
서버 `~/osh-ai-gateway` 에는 **`.git` 이 없다.** 파일을 직접 고친 흔적(`*.bak-<타임스탬프>`)이 남아 있다.

2026-09-25 대조에서 `app/services/gemini_compat.py` 의 **45줄이 git 에만 없는 상태**로 발견돼 회수·커밋했다(`9932937`).
내용은 OpenRouter thinking 토큰 제어 번역이고, simpleStock 브리핑이 이틀 연속 죽은 장애의 수정이었다.
그 함수는 **HARU·osh·myapi·aim-monitor·my-computer 가 공용으로 탄다.**

👉 **서버에서 고쳤으면 반드시 저장소로 되돌릴 것.** 대조 방법:
```bash
scp -O <서버>:~/osh-ai-gateway/app/services/<파일> /tmp/live.py
diff -u app/services/<파일> /tmp/live.py
```

### 3. 단일 장애점이다
`--workers 1` 단일 프로세스에 7개 서비스가 물려 있다. 특히 **HARU-Server 는 LLM 폴백이 전혀 없다**(README 가 주장하는 4프로바이더 폴백은 실재하지 않는다 — 게이트웨이 경유 2모델뿐).
이 프로세스가 죽으면 개인비서·AIm·osh 브리핑·simpleStock 애널리스트·myapi AI리포트·chominjungum AI출제가 **동시에** 나간다.

---

## 주요 기능

- **투명 프록시** — `/gemini/{path}` · `/vertex/{path}` · `/openrouter/{path}`. hop-by-hop 헤더 제거, SSE 자동 감지
- **Gemini → OpenRouter 번역** (`app/services/gemini_compat.py`) — 호출자는 Gemini API 를 부르는 줄 알지만 실제로는 OpenRouter 사업자가 응답한다
- **thinking 토큰 제어** — `thinkingConfig` → OpenAI `reasoning` 변환. `exclude` 가 아니라 `enabled:false` 를 쓴다(exclude 는 생각을 계속해 시간·과금이 그대로다)
- **서비스별 프로바이더 고정** — `X-Service-Id` 기준 화이트리스트
- **사용량 집계** — SQLite(`usage.db`), `/stats` HTML 대시보드 + `/api/stats` JSON
- **응답 출처 노출** — 실제 응답한 사업자·모델을 `X-Llm-Provider` / `X-Llm-Model` 헤더로 내려보내 소비자 쪽 표시 거짓말을 막는다 (latin-1 제약으로 non-latin 모델명은 헤더만 생략)

## 실행

```bash
# 로컬
./local-start.sh          # venv 생성 → 의존성 → .env 없으면 .env.example 복사 → uvicorn --reload (127.0.0.1:8780)

# 운영 (.25)
sudo systemctl status osh-ai-gateway
```

운영 유닛의 실제 플래그는 `--workers 1 --timeout-keep-alive 75 --backlog 2048` 인데,
저장소의 `deploy/osh-ai-gateway.service` 와 최상위 `osh-ai-gateway.service` **둘 다 이 플래그가 없다**(설정이 3갈래로 어긋나 있음).
기준은 서버의 `systemctl cat osh-ai-gateway` 다.

## 설정

`.env` (저장소에는 `.env.example` 만 있음):

| 키 | 용도 |
|---|---|
| `GEMINI_API_KEY` / `OPENROUTER_API_KEY` | 상위 프로바이더 인증 |
| `OPENROUTER_TRANSLATE_GEMINI` | Gemini→OpenRouter 번역 모드 |
| `OPENROUTER_DEFAULT_MODEL` | 기본 모델 |
| `GATEWAY_INTERNAL_TOKEN` | 내부 서비스 인증. **비우면 인증이 완전히 꺼진다**(fail-open) |
| `VERTEX_CREDENTIALS_PATH` | Vertex 사용 시 |
| `openrouter_provider_pins` | `서비스ID:사업자,사업자;…` 형식 |

- 2026-09-25 기준 `GATEWAY_INTERNAL_TOKEN` 은 **설정되어 있다**(인증 작동 중). 생태계에서 fail-open 이 아닌 몇 안 되는 서비스다.
- ⚠️ `openrouter_provider_pins` 포맷에 오타가 나면 **그 서비스만 조용히 고정이 안 걸린다.** `[provider-pin]` warning 로그를 확인할 것.

## 참고

- 생태계 전체 지도·공통 규약: `../CLAUDE.md`
- 11개 서비스 배선 분석: `../HARU-Server/docs/ECOSYSTEM-2026-08.md` (2026-08 기준이라 포트·라우팅은 낡음)
