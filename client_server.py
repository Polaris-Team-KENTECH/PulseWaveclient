from flask import Flask, request, jsonify
import sqlite3
import requests
import json
import random
import re
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
# 유효성 검사 유틸 (wifi_channel, bandwidth은 나중에 값보고 수정예정)
# ─────────────────────────────────────────
MAC_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
ALLOWED_BANDWIDTHS = {"20MHz", "40MHz", "80MHz"}


def _is_number(value) -> bool:
    """int 또는 float인지 확인 (bool은 제외)"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_valid_timestamp(value) -> bool:
    """ISO 8601 형식 (예: 2026-06-23T10:30:00+09:00) 확인"""
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def validate_csi_entry(entry, path: str, errors: list):
    """csi 배열의 단일 항목 검증"""
    if not isinstance(entry, dict):
        errors.append(f"{path}: 객체(dict)여야 합니다")
        return

    # subcarrier_index: 0 이상의 정수
    idx = entry.get("subcarrier_index")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
        errors.append(f"{path}.subcarrier_index: 0 이상의 정수여야 합니다")

    # real / imag / amplitude / phase: 숫자
    for field in ("real", "imag", "amplitude", "phase"):
        if field not in entry:
            errors.append(f"{path}.{field}: 필드가 없습니다")
        elif not _is_number(entry[field]):
            errors.append(f"{path}.{field}: 숫자여야 합니다")


def validate_metadata(metadata, path: str, errors: list):
    """metadata 객체 검증"""
    if not isinstance(metadata, dict):
        errors.append(f"{path}: 객체(dict)여야 합니다")
        return

    for mac_field in ("tx_mac", "rx_mac"):
        mac = metadata.get(mac_field)
        if not isinstance(mac, str) or not MAC_PATTERN.match(mac):
            errors.append(
                f"{path}.{mac_field}: MAC 주소 형식(AA:BB:CC:DD:EE:FF)이 아닙니다"
            )

    channel = metadata.get("wifi_channel")
    if not isinstance(channel, int) or isinstance(channel, bool) or not (1 <= channel <= 14):
        errors.append(f"{path}.wifi_channel: 1~14 사이의 정수여야 합니다")

    bandwidth = metadata.get("bandwidth")
    if bandwidth not in ALLOWED_BANDWIDTHS:
        errors.append(
            f"{path}.bandwidth: {sorted(ALLOWED_BANDWIDTHS)} 중 하나여야 합니다"
        )


def validate_position(position, path: str, errors: list):
    """position 객체 검증"""
    if not isinstance(position, dict):
        errors.append(f"{path}: 객체(dict)여야 합니다")
        return

    lat = position.get("latitude")
    if not _is_number(lat) or not (-90.0 <= lat <= 90.0):
        errors.append(f"{path}.latitude: -90 ~ 90 사이의 숫자여야 합니다")

    lon = position.get("longitude")
    if not _is_number(lon) or not (-180.0 <= lon <= 180.0):
        errors.append(f"{path}.longitude: -180 ~ 180 사이의 숫자여야 합니다")

    alt = position.get("altitude")
    if not _is_number(alt):
        errors.append(f"{path}.altitude: 숫자여야 합니다")

    unit = position.get("unit")
    if not isinstance(unit, dict):
        errors.append(f"{path}.unit: 객체(dict)여야 합니다")
    else:
        for unit_field in ("latitude", "longitude", "altitude"):
            if not isinstance(unit.get(unit_field), str):
                errors.append(f"{path}.unit.{unit_field}: 문자열이어야 합니다")

    coord = position.get("coordinate_system")
    if not isinstance(coord, str) or not coord.strip():
        errors.append(f"{path}.coordinate_system: 비어있지 않은 문자열이어야 합니다")


def validate_raw_data_item(item, index: int, errors: list):
    """raw_datas 배열의 단일 항목 검증"""
    path = f"raw_datas[{index}]"

    if not isinstance(item, dict):
        errors.append(f"{path}: 객체(dict)여야 합니다")
        return

    # device_id: 비어있지 않은 문자열
    device_id = item.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        errors.append(f"{path}.device_id: 비어있지 않은 문자열이어야 합니다")

    # timestamp: ISO 8601
    if not _is_valid_timestamp(item.get("timestamp")):
        errors.append(
            f"{path}.timestamp: ISO 8601 형식(예: 2026-06-23T10:30:00+09:00)이어야 합니다"
        )

    # csi: 비어있지 않은 리스트 + 각 항목 검증
    csi = item.get("csi")
    if not isinstance(csi, list) or len(csi) == 0:
        errors.append(f"{path}.csi: 비어있지 않은 배열이어야 합니다")
    else:
        for csi_idx, entry in enumerate(csi):
            validate_csi_entry(entry, f"{path}.csi[{csi_idx}]", errors)

    # rssi: 정수 (일반적으로 -100 ~ 0 dBm 범위)
    rssi = item.get("rssi")
    if not isinstance(rssi, int) or isinstance(rssi, bool):
        errors.append(f"{path}.rssi: 정수여야 합니다")
    elif not (-100 <= rssi <= 0):
        errors.append(f"{path}.rssi: -100 ~ 0 dBm 범위를 벗어났습니다 (값: {rssi})")

    # metadata
    if "metadata" not in item:
        errors.append(f"{path}.metadata: 필드가 없습니다")
    else:
        validate_metadata(item["metadata"], f"{path}.metadata", errors)

    # position
    if "position" not in item:
        errors.append(f"{path}.position: 필드가 없습니다")
    else:
        validate_position(item["position"], f"{path}.position", errors)


def validate_request_body(data):
    """
    요청 body 전체 검증.
    반환값: 오류 메시지 리스트 (비어 있으면 통과)
    """
    errors = []

    if data is None:
        return ["JSON 파싱 실패"]

    if not isinstance(data, dict):
        return ["요청 body는 JSON 객체여야 합니다"]

    # request_id
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        errors.append("request_id: 비어있지 않은 문자열이어야 합니다")

    # raw_datas
    raw_datas = data.get("raw_datas")
    if not isinstance(raw_datas, list) or len(raw_datas) == 0:
        errors.append("raw_datas: 비어있지 않은 배열이어야 합니다")
        return errors  # 배열 자체가 잘못되면 항목 검증은 생략

    for index, item in enumerate(raw_datas):
        validate_raw_data_item(item, index, errors)

    return errors


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

    # ── 유효성 검사 ──
    errors = validate_request_body(data)
    if errors:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "유효성 검사 실패",
                    "errors": errors,
                }
            ),
            400,
        )
    # ────────────────

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