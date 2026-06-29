# PulseWave Client Server

## 실행 방법
```bash
pip install -r requirements.txt
python app.py
```

## 엔드포인트 요약

| 엔드포인트 | 메서드 | 담당 | 설명 |
|---|---|---|---|
| /api/raw-data | POST | 최성민 | HW CSI 데이터 수신 |
| /api/user-position | GET | 프론트 담당자 | Client 위치 요청 |

## TODO 목록

- [ ] raw_datas 세부 필드 유효성 검사 추가 (파이썬 담당자)
- [ ] /api/user-position 응답 포맷 Client에 맞게 가공 (프론트 담당자)
- [ ] AI_SERVER_URL 실제 주소로 변경 (최성민)
