#!/bin/sh
# Регистрирует коннектор CRM в Debezium (409 — уже зарегистрирован).
until curl -sf http://debezium:8083/connectors >/dev/null; do sleep 3; done
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d @/crm-connector.json http://debezium:8083/connectors)
case $code in 201|409) echo "connector: $code" ;; *) echo "connector registration failed: $code"; exit 1 ;; esac
