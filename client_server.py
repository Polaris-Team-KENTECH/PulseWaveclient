from flask import Flask, request, jsonify
import sqlite3
import requests
import json
import random
from datetime import datetime

app = Flask(__name__)

# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
DB_PATH = "pulsewave.db"
AI_SERVER_URL = "http://localhost:8000/ai/predict"  # AI Server 주소로 변경

# ★ 더미 모드 플래그
# True  → AI Server 없이 더미 데이터로 동작 (개발/테스트용)
# False → 실제 AI Server로 요청 (배포용)
USE_DUMMY = True


# ─────────────────────────────────────────
# DB 초기화
# ─────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_data (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id  TEXT NOT NULL,
                received_at TEXT NOT NULL,
                data        TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_position (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_data_id  INTEGER NOT NULL,
                predicted_at TEXT NOT NULL,
                position     TEXT NOT NULL
            )
        """)
        conn.commit()


# ─────────────────────────────────────────
# AI 예측 함수 (더미 / 실제 전환 포인트)
# ─────────────────────────────────────────
def request_ai_predict(raw_data: dict):
    """
    USE_DUMMY=True  → 더미 좌표 반환
    USE_DUMMY=False → 실제 AI Server로 POST
    AI팀 서버 준비되면 USE_DUMMY를 False로 바꾸기만 하면 됨
    """
    if USE_DUMMY:
        return generate_dummy_position()
    else:
        return call_ai_server(raw_data)


def generate_dummy_position():
    """
    AI Server 응답 형식을 그대로 모방한 더미 데이터.
    실제 AI Server 응답 스펙이 바뀌면 여기도 같이 수정할 것.
    """
    return {
        "status": "success",
        "user_position": [
            {
                "user_num": 0,
                "x": round(random.uniform(0.0, 10.0), 2),
                "y": round(random.uniform(0.0, 10.0), 2),
                "z": round(random.uniform(0.0, 3.0), 2),
                "confidence": round(random.uniform(0.7, 1.0), 2),
            }
        ],
        "timestamp": datetime.now().isoformat(),
    }


def call_ai_server(raw_data: dict):
    """
    실제 AI Server 호출 로직.
    USE_DUMMY=False 일 때만 실행됨.
    """
    try:
        response = requests.post(AI_SERVER_URL, json=raw_data, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        app.logger.error("AI Server 연결 실패 — 서버가 켜져 있는지 확인하세요")
        return None
    except requests.exceptions.Timeout:
        app.logger.error("AI Server 응답 타임아웃")
        return None
    except requests.exceptions.HTTPError as e:
        app.logger.error(f"AI Server HTTP 오류: {e}")
        return None


# ─────────────────────────────────────────
# 1) HW 데이터 수신
# POST /api/raw-data
# 담당: 최성민
# ─────────────────────────────────────────
@app.route("/api/raw-data", methods=["POST"])
def receive_raw_data():
    data = request.get_json(silent=True)

    # ── 유효성 검사 (파이썬 담당자가 이 블록을 채워주세요) ──
    if data is None:
        return jsonify({"status": "error", "message": "JSON 파싱 실패"}), 400

    if "request_id" not in data:
        return (
            jsonify({"status": "error", "message": "request_id 필드가 없습니다"}),
            400,
        )

    if "raw_datas" not in data or not isinstance(data["raw_datas"], list):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "raw_datas 필드가 없거나 형식이 잘못됐습니다",
                }
            ),
            400,
        )

    # TODO: raw_datas 각 항목의 csi, device_id, timestamp 등 세부 검증 추가
    # ────────────────────────────────────────────────────────

    # raw_data DB 저장
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO raw_data (request_id, received_at, data) VALUES (?, ?, ?)",
            (data["request_id"], datetime.now().isoformat(), json.dumps(data)),
        )
        raw_data_id = cursor.lastrowid
        conn.commit()

    # AI 예측 → 결과 즉시 저장
    ai_result = request_ai_predict(data)
    if ai_result is not None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO user_position (raw_data_id, predicted_at, position) VALUES (?, ?, ?)",
                (raw_data_id, datetime.now().isoformat(), json.dumps(ai_result)),
            )
            conn.commit()
        return (
            jsonify({"status": "success", "message": "데이터 저장 및 위치 예측 완료"}),
            200,
        )
    else:
        app.logger.warning(f"raw_data_id={raw_data_id} 저장됐으나 AI 예측 실패")
        return (
            jsonify(
                {"status": "success", "message": "데이터 저장 완료 (AI 예측 실패)"}
            ),
            200,
        )


# ─────────────────────────────────────────
# 2) Client 위치 요청
# GET /api/user-position
# 담당: 프론트 담당자
# ─────────────────────────────────────────
@app.route("/api/user-position", methods=["GET"])
def get_user_position():
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT position FROM user_position ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if row is None:
        return (
            jsonify({"status": "no_data", "message": "아직 예측된 위치가 없습니다"}),
            200,
        )

    position = json.loads(row[0])

    # TODO: Client에 맞게 응답 포맷 가공 (프론트 담당자가 수정)
    # 현재 응답 형식:
    # {
    #   "status": "success",
    #   "user_position": [{"user_num": 0, "x": 3.21, "y": 1.42, "z": 0.0, "confidence": 0.87}],
    #   "timestamp": "2026-06-23T10:30:01+09:00"
    # }
    return jsonify(position), 200


# ─────────────────────────────────────────
# 앱 실행
# ─────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
