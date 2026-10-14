import json
import os
from datetime import date, timedelta

import boto3
import clickhouse_connect
import jwt
from fastapi import Depends, FastAPI, HTTPException, Query, Request

app = FastAPI()
jwks = jwt.PyJWKClient(os.environ["KC_JWKS_URL"])
s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                  aws_access_key_id=os.environ["S3_ACCESS_KEY"], aws_secret_access_key=os.environ["S3_SECRET_KEY"])
BUCKET, CDN_URL = os.environ["S3_BUCKET"], os.environ["CDN_URL"]


def clickhouse():
    return clickhouse_connect.get_client(host=os.environ["CH_HOST"], password=os.environ["CH_PASSWORD"], database="reports")


def current_user(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "no token")
    token = auth[7:]
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=os.environ["KC_ISSUER"], options={"verify_aud": False})
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid token: {e}")
    return claims["preferred_username"]


@app.get("/reports")
def report(user: str = Depends(current_user), from_: date | None = Query(None, alias="from"), to: date | None = None):
    ch = clickhouse()
    n, processed_to = ch.query("SELECT count(), max(day) FROM user_daily_report_cdc").first_row  # до какого дня загрузил Airflow
    if n == 0:
        raise HTTPException(404, "no processed data yet")
    to = min(to or processed_to, processed_to)
    from_ = from_ or to - timedelta(days=29)
    if from_ > to:
        raise HTTPException(404, f"data is processed up to {processed_to}")
    # Ключ содержит processed_to и две независимые версии: клиента из CRM и набора загруженных дней,
    # поэтому изменение клиента, перезагрузка или удаление дня дают новый ключ, старые объекты и кеш CDN не мешают.
    client_version, telemetry_version = ch.query(
        "SELECT max(client_version), sipHash64(arraySort(groupArray((day, processed_at)))) FROM user_daily_report_cdc WHERE username = {u:String}",
        parameters={"u": user},
    ).first_row
    key = f"{user}/{processed_to}/{client_version}-{telemetry_version}/{from_}_{to}.json"
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return {"url": f"{CDN_URL}/{key}"}
    except s3.exceptions.ClientError:
        pass
    rows = list(ch.query(
        """SELECT day, full_name, prosthesis_model, movements, round(toFloat64(avg_battery), 1) AS avg_battery,
                  min_battery, errors, active_hours
           FROM user_daily_report_cdc
           WHERE username = {u:String} AND day BETWEEN {f:Date} AND {t:Date}
           ORDER BY day""",
        parameters={"u": user, "f": from_, "t": to},
    ).named_results())
    if not rows:
        raise HTTPException(404, "no report for this user and period")
    body = {
        "user": user,
        "full_name": rows[0]["full_name"],
        "prosthesis_model": rows[0]["prosthesis_model"],
        "period": {"from": from_, "to": to, "processed_to": processed_to},
        "days": [{k: v for k, v in r.items() if k not in ("full_name", "prosthesis_model")} for r in rows],
    }
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(body, default=str), ContentType="application/json")
    return {"url": f"{CDN_URL}/{key}"}
