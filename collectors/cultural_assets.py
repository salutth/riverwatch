"""
서울시 수변 문화재 수집기 — 하천ON Phase 1-1
- 입력: 문화재청 국가문화유산포털 공개 API
- 처리: 서울 전체 문화재 수집 → 21개 하천 근접 문화재 필터링
- 출력: Supabase cultural_assets 테이블 저장
"""

import json
import math
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RIVER_LOCATIONS = {
    "도림천": {"lat": 37.4838, "lng": 126.9295},
    "안양천": {"lat": 37.4750, "lng": 126.8870},
    "중랑천": {"lat": 37.5950, "lng": 127.0500},
    "탄천": {"lat": 37.5000, "lng": 127.0700},
    "불광천": {"lat": 37.5850, "lng": 126.9150},
    "홍제천": {"lat": 37.5750, "lng": 126.9350},
    "방학천": {"lat": 37.6540, "lng": 127.0250},
    "우이천": {"lat": 37.6450, "lng": 127.0130},
    "정릉천": {"lat": 37.5950, "lng": 127.0050},
    "청계천": {"lat": 37.5700, "lng": 127.0000},
    "양재천": {"lat": 37.4720, "lng": 127.0400},
    "성북천": {"lat": 37.5920, "lng": 127.0020},
    "묵동천": {"lat": 37.6150, "lng": 127.0780},
    "전농천": {"lat": 37.5780, "lng": 127.0560},
    "월계천": {"lat": 37.6280, "lng": 127.0580},
    "반포천": {"lat": 37.5050, "lng": 126.9950},
    "여의천": {"lat": 37.5250, "lng": 126.9230},
    "봉원천": {"lat": 37.5650, "lng": 126.9530},
    "녹번천": {"lat": 37.6050, "lng": 126.9280},
    "세곡천": {"lat": 37.4650, "lng": 127.0850},
    "내부천": {"lat": 37.4850, "lng": 126.9680},
    "한강": {"lat": 37.5170, "lng": 126.9600},
}

GRADE_MAP = {
    "11": "국보",
    "12": "보물",
    "13": "사적",
    "14": "사적및명승",
    "15": "명승",
    "16": "천연기념물",
    "17": "국가무형유산",
    "18": "국가민속문화유산",
    "21": "시도유형문화유산",
    "22": "시도무형유산",
    "23": "시도기념물",
    "24": "시도민속문화유산",
    "25": "시도등록유산",
    "31": "문화유산자료",
    "79": "국가등록유산",
    "80": "국가등록문화유산",
}

RISK_PRIORITY = {
    "국보": 5,
    "보물": 4,
    "사적": 4,
    "천연기념물": 4,
    "명승": 3,
    "국가등록유산": 3,
    "시도유형문화유산": 2,
    "시도기념물": 2,
}

MAX_DISTANCE_KM = 1.5


def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_all_seoul_assets():
    """문화재청 API에서 서울 전체 문화재 수집"""
    all_items = []
    page = 1
    page_size = 100

    while True:
        url = (
            f"https://www.cha.go.kr/cha/SearchKindOpenapiList.do"
            f"?ccbaCtcd=11&pageUnit={page_size}&pageIndex={page}"
        )
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                text = raw.decode("utf-8", errors="replace")

            parser = ET.XMLParser()
            parser.entity = {}
            root = ET.fromstring(text, parser=parser)
            items = root.findall(".//item")

            if not items:
                break

            for item in items:
                lat_text = item.findtext("latitude", "").strip()
                lng_text = item.findtext("longitude", "").strip()

                if not lat_text or not lng_text:
                    continue

                try:
                    lat = float(lat_text)
                    lng = float(lng_text)
                except ValueError:
                    continue

                if lat == 0 or lng == 0:
                    continue

                kdcd = item.findtext("ccbaKdcd", "").strip()
                grade = GRADE_MAP.get(kdcd, f"기타({kdcd})")

                all_items.append(
                    {
                        "name": item.findtext("ccbaMnm1", "").strip(),
                        "name_hanja": item.findtext("ccbaMnm2", "").strip(),
                        "grade": grade,
                        "grade_code": kdcd,
                        "district": item.findtext("ccsiName", "").strip(),
                        "latitude": lat,
                        "longitude": lng,
                        "asset_code": item.findtext("ccbaAsno", "").strip(),
                        "cancelled": item.findtext("ccbaCncl", "N").strip(),
                    }
                )

            total = int(root.findtext("totalCnt", "0"))
            if page * page_size >= total:
                break
            page += 1

        except Exception as e:
            print(f"  ⚠️  페이지 {page} 수집 실패: {e}")
            break

    return [a for a in all_items if a["cancelled"] != "Y"]


def find_nearest_river(lat, lng):
    """문화재 좌표에서 가장 가까운 하천과 거리 계산"""
    nearest = None
    min_dist = float("inf")

    for river, coord in RIVER_LOCATIONS.items():
        dist = haversine(lat, lng, coord["lat"], coord["lng"])
        if dist < min_dist:
            min_dist = dist
            nearest = river

    return nearest, round(min_dist, 2)


def save_to_supabase(assets):
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = os.environ.get("SUPABASE_KEY")

    if not sb_url or not sb_key:
        print("  ⚠️  Supabase 환경변수 없음 — 저장 건너뜀")
        return 0

    records = []
    for a in assets:
        records.append(
            {
                "name": a["name"],
                "name_hanja": a["name_hanja"],
                "category": a["grade"],
                "type": a["grade_code"],
                "address": a["district"],
                "latitude": a["latitude"],
                "longitude": a["longitude"],
                "river": a["nearest_river"],
                "description": f"거리:{a['distance_km']}km 코드:{a['asset_code']}",
                "collected_at": datetime.now().isoformat(),
            }
        )

    batch_size = 50
    saved = 0
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        body = json.dumps(batch, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{sb_url}/rest/v1/cultural_assets?on_conflict=name,river",
            data=body,
            headers={
                "apikey": sb_key,
                "Authorization": f"Bearer {sb_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal,resolution=merge-duplicates",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    saved += len(batch)
        except Exception as e:
            print(f"  ⚠️  배치 저장 실패: {e}")

    return saved


def main():
    load_env()

    print("=" * 60)
    print("  서울 수변 문화재 수집기 — 하천ON Phase 1-1")
    print(f"  수집 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    print()

    print("  📡 문화재청 API에서 서울 문화재 수집 중...")
    all_assets = fetch_all_seoul_assets()
    print(f"  총 {len(all_assets)}건 수집 완료")
    print()

    print(f"  🏞️  21개 하천 반경 {MAX_DISTANCE_KM}km 이내 문화재 필터링...")
    riverside_assets = []
    for asset in all_assets:
        river, dist = find_nearest_river(asset["latitude"], asset["longitude"])
        if dist <= MAX_DISTANCE_KM:
            asset["nearest_river"] = river
            asset["distance_km"] = dist
            riverside_assets.append(asset)

    print(f"  수변 문화재: {len(riverside_assets)}건")
    print()

    river_counts = {}
    grade_counts = {}
    for a in riverside_assets:
        r = a["nearest_river"]
        g = a["grade"]
        river_counts[r] = river_counts.get(r, 0) + 1
        grade_counts[g] = grade_counts.get(g, 0) + 1

    print("  --- 하천별 문화재 수 ---")
    for river in sorted(river_counts, key=river_counts.get, reverse=True):
        print(f"  {river:8s}  {river_counts[river]:3d}건")
    print()

    print("  --- 등급별 분류 ---")
    for grade in sorted(grade_counts, key=grade_counts.get, reverse=True)[:10]:
        print(f"  {grade:16s}  {grade_counts[grade]:3d}건")
    print()

    high_risk = [
        a
        for a in riverside_assets
        if a["grade"] in ("국보", "보물", "사적", "천연기념물")
        and a["distance_km"] <= 1.0
    ]
    if high_risk:
        print(f"  🔴 고위험 문화재 (국보·보물·사적, 1km 이내): {len(high_risk)}건")
        for a in sorted(high_risk, key=lambda x: x["distance_km"])[:10]:
            print(
                f"     {a['grade']:6s} {a['name'][:20]:20s} ← {a['nearest_river']} ({a['distance_km']}km)"
            )
        print()

    saved = save_to_supabase(riverside_assets)
    if saved:
        print(f"  💾 Supabase 저장 완료: {saved}건")
    print()


if __name__ == "__main__":
    main()
