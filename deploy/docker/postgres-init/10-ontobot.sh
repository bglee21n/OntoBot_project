#!/bin/bash
# PostgreSQL 컨테이너 첫 기동 때 한 번(빈 데이터 볼륨일 때만) 돈다 — docker-entrypoint-initdb.d.
# 앱 계정은 슈퍼유저가 아니다(운영 DBA 구성과 같게). pgvector 확장은 슈퍼유저가 DB 마다 만든다.
#
# 환경변수(compose 가 넘긴다):
#   ONTOBOT_DB_USER·ONTOBOT_DB_PASSWORD  앱 계정
#   ONTOBOT_DB_NAMES                     만들 DB 이름(공백 구분). 운영: ontobot_db
#                                        테스트: ontobot_test ontobot_source_test ontobot_eval
set -euo pipefail

: "${ONTOBOT_DB_USER:?}" "${ONTOBOT_DB_PASSWORD:?}" "${ONTOBOT_DB_NAMES:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v app_user="$ONTOBOT_DB_USER" -v app_pw="$ONTOBOT_DB_PASSWORD" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_pw';
SQL

for db in $ONTOBOT_DB_NAMES; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
       -v db="$db" -v app_user="$ONTOBOT_DB_USER" <<'SQL'
CREATE DATABASE :"db" OWNER :"app_user" ENCODING 'UTF8' TEMPLATE template0;
SQL
  # 확장은 따로 둔 스키마에 — 통합 테스트가 public 을 지웠다 다시 만들어도 확장이 남는다(앱 계정은
  # 확장을 만들 수 없다). 검색 경로에 넣어 열 자료형은 그냥 vector 로 쓴다.
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" -v db="$db" <<'SQL'
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions;
GRANT USAGE ON SCHEMA extensions TO PUBLIC;
ALTER DATABASE :"db" SET search_path = public, extensions;
SQL
done
