-- RiverWatch Supabase 테이블 생성 SQL
-- Supabase 대시보드 > SQL Editor 에서 실행

-- 1. 하천 수위 데이터
create table river_readings (
  id bigint generated always as identity primary key,
  station text not null,
  river text not null,
  gu text,
  water_level real,
  embankment_height real,
  level_ratio real,
  status text,
  measured_at timestamptz,
  collected_at timestamptz default now()
);

alter table river_readings enable row level security;
create policy "river_readings_read" on river_readings for select using (true);
create policy "river_readings_insert" on river_readings for insert with check ((current_setting('role') = 'service_role'));

-- 2. 생물 관찰 데이터 (iNaturalist + 시민 제보)
create table species_observations (
  id bigint generated always as identity primary key,
  taxon_name text,
  common_name text,
  korean_name text,
  taxon_id int,
  confidence real,
  latitude real,
  longitude real,
  river text,
  photo_url text,
  observer text,
  source text default 'inaturalist',
  is_invasive boolean default false,
  observed_at timestamptz,
  collected_at timestamptz default now()
);

alter table species_observations enable row level security;
create policy "species_read" on species_observations for select using (true);
create policy "species_insert" on species_observations for insert with check (true);

-- 3. 외래종 경보
create table invasive_alerts (
  id bigint generated always as identity primary key,
  observation_id bigint references species_observations(id),
  species_name text not null,
  alert_level text default 'warning',
  river text,
  latitude real,
  longitude real,
  notified boolean default false,
  created_at timestamptz default now()
);

alter table invasive_alerts enable row level security;
create policy "alerts_read" on invasive_alerts for select using (true);
create policy "alerts_insert" on invasive_alerts for insert with check ((current_setting('role') = 'service_role'));

-- 4. EHI 생태건강성지수
create table ehi_scores (
  id bigint generated always as identity primary key,
  river text not null,
  biodiversity_score real,
  water_stability_score real,
  non_invasive_score real,
  observation_freq_score real,
  ehi_score real,
  grade text,
  species_count int,
  reading_count int,
  calculated_at timestamptz default now()
);

alter table ehi_scores enable row level security;
create policy "ehi_read" on ehi_scores for select using (true);
create policy "ehi_insert" on ehi_scores for insert with check ((current_setting('role') = 'service_role'));

-- 5. 인덱스
create index idx_readings_river on river_readings(river);
create index idx_readings_measured on river_readings(measured_at);
create index idx_species_river on species_observations(river);
create index idx_species_taxon on species_observations(taxon_name);
create index idx_species_invasive on species_observations(is_invasive);

-- 6. 하천명 공백 정리 (기존 데이터)
update river_readings set
  station = trim(station),
  river = trim(river),
  gu = trim(gu)
where station <> trim(station)
   or river <> trim(river)
   or gu <> trim(gu);

update ehi_scores set river = trim(river)
where river <> trim(river);

-- 7. 중복 방지 (V5 보안 패치)
alter table species_observations
  add column if not exists inaturalist_id bigint;
create unique index if not exists uq_species_inat_id
  on species_observations(inaturalist_id);

alter table river_readings
  add constraint uq_readings_station_time unique (station, measured_at);

-- 7. 헬스체크 테이블 (V6 모니터링)
create table if not exists collector_health (
  id bigint generated always as identity primary key,
  collector text not null,
  status text not null default 'ok',
  record_count int default 0,
  error_message text,
  collected_at timestamptz default now()
);
alter table collector_health enable row level security;
create policy "health_read" on collector_health for select using (true);
create policy "health_insert" on collector_health for insert with check ((current_setting('role') = 'service_role'));

-- 8. AI 침수 예측 (Phase 3-1 LSTM)
create table if not exists flood_predictions (
  id bigint generated always as identity primary key,
  station text not null,
  river text not null,
  prediction_hour int not null,
  predicted_level real not null,
  predicted_ratio real,
  predicted_status text,
  confidence real,
  model_version text default 'v1',
  input_snapshot jsonb,
  predicted_at timestamptz not null,
  target_time timestamptz not null,
  created_at timestamptz default now()
);

alter table flood_predictions enable row level security;
create policy "predictions_read" on flood_predictions for select using (true);
create policy "predictions_insert" on flood_predictions for insert with check ((current_setting('role') = 'service_role'));

create index if not exists idx_predictions_station_target on flood_predictions(station, target_time);
create index if not exists idx_predictions_river on flood_predictions(river);
create index if not exists idx_predictions_created on flood_predictions(created_at desc);

alter table flood_predictions
  add constraint uq_predictions unique (station, target_time, predicted_at);

-- 9. K-SAFE 홍수위험지수 (Phase 3-2)
create table if not exists flood_risk_index (
  id bigint generated always as identity primary key,
  station text not null,
  river text not null,
  composite_score real not null,
  risk_grade text not null,
  w1_water_level real,
  w2_weather real,
  w3_prediction real,
  w4_history real,
  cross_validation real,
  disaster_stage text,
  trend_direction text,
  resilience_robustness real,
  resilience_redundancy real,
  resilience_resourcefulness real,
  resilience_rapidity real,
  resilience_average real,
  data_sources int,
  calculated_at timestamptz not null
);

alter table flood_risk_index enable row level security;
create policy "risk_index_read" on flood_risk_index for select using (true);
create policy "risk_index_insert" on flood_risk_index for insert with check ((current_setting('role') = 'service_role'));

create index if not exists idx_risk_index_station on flood_risk_index(station);
create index if not exists idx_risk_index_river on flood_risk_index(river);
create index if not exists idx_risk_index_calculated on flood_risk_index(calculated_at desc);

alter table flood_risk_index
  add constraint uq_flood_risk_index_station_calc unique (station, calculated_at);

-- 10. 침수 경보 (Phase 3-3)
create table if not exists flood_alerts (
  id bigint generated always as identity primary key,
  station text not null,
  river text not null,
  alert_level text not null,
  water_level real,
  embankment_height real,
  level_ratio real,
  message text,
  issued_at timestamptz not null,
  created_at timestamptz default now()
);

alter table flood_alerts enable row level security;
create policy "flood_alerts_read" on flood_alerts for select using (true);
create policy "flood_alerts_insert" on flood_alerts for insert with check ((current_setting('role') = 'service_role'));

alter table flood_alerts
  add constraint uq_flood_alerts_station_issued unique (station, issued_at);

-- 11. 기상 예보
create table if not exists weather_forecasts (
  id bigint generated always as identity primary key,
  river text not null,
  forecast_date date,
  forecast_hour int,
  rain_probability real,
  rain_amount real,
  temperature real,
  humidity real,
  wind_speed real,
  sky_condition text,
  collected_at timestamptz default now()
);

alter table weather_forecasts enable row level security;
create policy "weather_read" on weather_forecasts for select using (true);
create policy "weather_insert" on weather_forecasts for insert with check ((current_setting('role') = 'service_role'));

-- 12. 시민 수질 측정 (시민 입력 — anon INSERT 허용)
create table if not exists citizen_water_quality (
  id bigint generated always as identity primary key,
  river text not null,
  observer text default '시민과학자',
  ph real check (ph >= 0 and ph <= 14),
  dissolved_oxygen real check (dissolved_oxygen >= 0 and dissolved_oxygen <= 30),
  water_temp real check (water_temp >= -5 and water_temp <= 50),
  turbidity real check (turbidity >= 0 and turbidity <= 5000),
  conductivity real check (conductivity >= 0),
  bod real check (bod >= 0),
  cod real check (cod >= 0),
  ss real check (ss >= 0),
  water_color text,
  smell text,
  flow_speed text,
  trash_level text,
  weather text,
  air_temp real,
  humidity real check (humidity >= 0 and humidity <= 100),
  recent_rain text,
  do_grade text,
  memo text,
  photo_data text,
  latitude real,
  longitude real,
  measured_at timestamptz default now(),
  created_at timestamptz default now()
);

alter table citizen_water_quality enable row level security;
create policy "cwq_read" on citizen_water_quality for select using (true);
create policy "cwq_insert" on citizen_water_quality for insert with check (true);

create index if not exists idx_cwq_river on citizen_water_quality(river);
create index if not exists idx_cwq_measured on citizen_water_quality(measured_at desc);

-- 13. 회원 (시민 자가 가입 — anon INSERT 허용, SELECT는 service_role만)
create table if not exists members (
  id bigint generated always as identity primary key,
  name text not null,
  email text not null,
  birth_date text not null check (length(birth_date) = 6),
  district text not null,
  support_donation boolean default false,
  support_auto_debit boolean default false,
  promo_link_active boolean default false,
  registered_at timestamptz default now()
);

alter table members enable row level security;
create policy "members_insert" on members for insert with check (true);
create policy "members_read" on members for select using ((current_setting('role') = 'service_role'));

create unique index if not exists uq_members_email on members(email);
