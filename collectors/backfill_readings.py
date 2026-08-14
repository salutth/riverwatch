"""
과거 수위 데이터 백필 수집기 — Phase 3-1 데이터 확보
서울시 하천수위 API는 실시간만 제공하므로, 이 스크립트는 두 가지 전략을 사용:

전략 1: 실시간 API를 주기적으로 호출하여 Supabase에 쌓기 (기본)
전략 2: 기존 데이터가 부족할 때 합성 이력 생성 (--synthetic 옵션)
  - 현재 관측값 기반으로 자연스러운 수위 변동 패턴을 생성
  - 일교차, 강수 영향, 랜덤 변동을 반영한 시뮬레이션 데이터
  - LSTM 학습의 cold-start 문제 해결용

사용법:
  python collectors/backfill_readings.py              # 실시간 1회 수집
  python collectors/backfill_readings.py --synthetic   # 7일치 합성 이력 생성
  python collectors/backfill_readings.py --synthetic --days 14  # 14일치
"""

import json
import math
import os
import random
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))


def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()


def sb_query(table, params=""):
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_KEY")
    if not sb_url or not sb_key:
        return []
    url = f"{sb_url}/rest/v1/{table}?{params}"
    req = urllib.request.Request(
        url, headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        return []


def sb_upsert(table, records):
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_KEY")
    if not sb_url or not sb_key or not records:
        return 0
    body = json.dumps(records, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{sb_url}/rest/v1/{table}?on_conflict=station,measured_at",
        data=body,
        headers={
            "apikey": sb_key,
            "Authorization": f"Bearer {sb_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal,resolution=ignore-duplicates",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return len(records) if resp.status in (200, 201) else 0
    except Exception as e:
        print(f"  ⚠️  저장 실패: {e}")
        return 0


def fetch_current():
    """현재 실시간 수위를 서울시 API에서 가져온다."""
    api_key = os.environ.get("SEOUL_API_KEY", "")
    if not api_key:
        return []
    url = f"http://openAPI.seoul.go.kr:8088/{api_key}/json/ListRiverStageService/1/100/"
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("ListRiverStageService", {}).get("row", [])
        return rows
    except Exception as e:
        print(f"  ⚠️  API 호출 실패: {e}")
        return []


def parse_station(r):
    """API row를 DB 레코드로 변환."""
    name = r.get("WATG_NM", "").strip()
    river = r.get("RVR_NM", "").strip()
    gu = r.get("GU_OFC_NM", "").strip()
    level = float(r.get("RLTM_RVR_WATL_CNT", "0") or "0")
    emb = float(r.get("EBM_HGT", "0") or "0")
    ratio = (level / emb * 100) if emb > 0 else 0
    status = "danger" if ratio >= 80 else "warning" if ratio >= 50 else "safe"
    measure_time = r.get("DTRSM_DATA_CLCT_TM", "")
    measured_at = None
    if measure_time:
        try:
            measured_at = datetime.strptime(measure_time, "%Y-%m-%d %H:%M").isoformat()
        except ValueError:
            measured_at = measure_time
    return {
        "station": name, "river": river, "gu": gu,
        "water_level": level, "embankment_height": emb,
        "level_ratio": round(ratio, 1), "status": status,
        "measured_at": measured_at,
    }


def generate_synthetic(stations, days=7):
    """현재 관측값 기반으로 자연스러운 과거 수위 이력을 합성한다.

    패턴:
    - 기본 수위: 현재 관측값의 ±30% 범위 내 변동
    - 일교차: 새벽(최저) ~ 오후(최고) sin 패턴
    - 강수 이벤트: 확률적으로 발생, 수위 급등 + 점진적 감소
    - 노이즈: 작은 랜덤 변동
    """
    records = []
    now = datetime.now(KST)
    random.seed(42)

    for stn in stations:
        base_level = stn["water_level"]
        emb = stn["embankment_height"]
        if base_level <= 0 or emb <= 0:
            continue

        rain_events = []
        for d in range(days):
            if random.random() < 0.25:
                rain_hour = random.randint(0, 23)
                rain_intensity = random.uniform(0.3, 2.0)
                rain_duration = random.randint(2, 6)
                rain_events.append((d, rain_hour, rain_intensity, rain_duration))

        for hour_offset in range(days * 24):
            t = now - timedelta(hours=days * 24 - hour_offset)
            day_idx = hour_offset // 24
            hour = t.hour

            diurnal = math.sin((hour - 6) / 24 * 2 * math.pi) * base_level * 0.08
            noise = random.gauss(0, base_level * 0.03)

            rain_effect = 0
            for rd, rh, ri, rdur in rain_events:
                event_start = rd * 24 + rh
                hours_since = hour_offset - event_start
                if 0 <= hours_since < rdur:
                    rain_effect += ri * base_level * 0.4 * (1 - hours_since / rdur)
                elif rdur <= hours_since < rdur + 12:
                    decay = (hours_since - rdur) / 12
                    rain_effect += ri * base_level * 0.4 * (1 - decay) * 0.3

            level = base_level + diurnal + noise + rain_effect
            level = max(0.1, min(level, emb * 0.95))
            ratio = (level / emb * 100) if emb > 0 else 0
            status = "danger" if ratio >= 80 else "warning" if ratio >= 50 else "safe"

            records.append({
                "station": stn["station"],
                "river": stn["river"],
                "gu": stn["gu"],
                "water_level": round(level, 2),
                "embankment_height": emb,
                "level_ratio": round(ratio, 1),
                "status": status,
                "measured_at": t.strftime("%Y-%m-%dT%H:%M:00"),
            })

    return records


def main():
    load_env()

    synthetic = "--synthetic" in sys.argv
    days = 7
    for i, arg in enumerate(sys.argv):
        if arg == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
            if days < 1 or days > 365:
                print("⚠️  --days 범위: 1~365")
                sys.exit(1)

    print("=" * 50)
    if synthetic:
        print(f"📦 과거 수위 합성 데이터 생성 ({days}일)")
    else:
        print("📥 실시간 수위 수집 (1회)")
    print("=" * 50)

    rows = fetch_current()
    if not rows:
        print("❌ 현재 수위 데이터를 가져올 수 없습니다.")
        return

    stations = [parse_station(r) for r in rows]
    print(f"📍 {len(stations)}개 관측소 감지")

    if synthetic:
        records = generate_synthetic(stations, days)
        print(f"🔧 합성 데이터 {len(records)}건 생성")

        batch_size = 200
        total_saved = 0
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            saved = sb_upsert("river_readings", batch)
            total_saved += saved
            pct = min(100, (i + batch_size) / len(records) * 100)
            print(f"  💾 {pct:.0f}% — {total_saved}건 저장")

        print(f"\n✅ 합성 데이터 총 {total_saved}건 Supabase 저장 완료")
        print(f"   → {len(stations)}개 관측소 × {days}일 × 24시간")
        print(f"   → 이제 'python ml/train_lstm.py'로 모델을 재학습하세요")
    else:
        records = stations
        saved = sb_upsert("river_readings", records)
        print(f"💾 {saved}건 저장")
        print("✅ 수집 완료")


if __name__ == "__main__":
    main()
