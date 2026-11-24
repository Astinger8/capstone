# PostgreSQL과 레이더 수신 API

레이더가 생성한 상세 이벤트와 주기별 요약을 PostgreSQL에 저장하고, 돌봄 모니터링 화면에서 주간 호흡수와 활동 기록을 조회합니다. [ERD와 데이터 사전](docs/PostgreSQL_ERD_데이터사전.md)에서 테이블 관계와 필드를 확인할 수 있습니다.

## 새 로컬 DB 준비

pgAdmin에서 `capstone` 데이터베이스를 만든 다음 Query Tool에서 [`000_schema.sql`](sql/000_schema.sql), [`001_message_dedup.sql`](sql/001_message_dedup.sql)을 순서대로 한 번씩 실행합니다. `000_schema.sql`은 빈 DB에 적용합니다. 기존 DB의 두 수신 테이블에 `source_id`와 `message_id`가 아직 없으면 `001_message_dedup.sql`을 적용합니다.

새 DB의 첫 시연 대상자를 등록합니다. API 실행기는 기본적으로 대상자 1번을 사용합니다.

```sql
INSERT INTO public.subjects (display_name) VALUES ('[시연] 기본 대상자');
```

## API 실행

PowerShell에서 저장소의 `db` 폴더를 열고 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_vital_api.py
```

프롬프트에 로컬 PostgreSQL 비밀번호를 입력합니다. 접속 기본값은 호스트 `127.0.0.1`, 포트 `5432`, DB 이름 `capstone`, 사용자 `postgres`입니다. 환경 변수 `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`로 변경할 수 있습니다. 시연 대상자 번호는 `VITAL_DEFAULT_SUBJECT_ID`로 지정할 수 있습니다.

[API 테스트 화면](http://127.0.0.1:8000/docs)에서 `POST /api/v1/vital-events`와 `POST /api/v1/vital-summary`를 실행할 수 있습니다. 요청마다 JSON 객체 한 건을 보냅니다. `subject_id`를 생략하면 기본 시연 대상자가 사용됩니다. 동일한 `source_id`와 `message_id`로 같은 요청을 재전송하면 기존 기록 번호가 반환됩니다. 같은 ID로 내용을 바꾸면 `409`가 반환됩니다.

[돌봄 모니터링 화면](http://127.0.0.1:8000/care-monitor)에서 가상 대상자 4명의 기록을 확인하려면, 대상자 1번을 등록한 뒤 다음 명령을 실행하고 기준일을 `2026-11-09`로 선택합니다.

```powershell
.\.venv\Scripts\python.exe seed_care_demo.py
```

## API 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
