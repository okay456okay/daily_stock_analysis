#!/usr/bin/env bash
# 港股定时分析脚本
# crontab: 0 20 * * 1-5

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

set -a
[ -f .env ] && source .env
set +a

FAV_DB_HOST="${FAV_DB_HOST:-127.0.0.1}"
FAV_DB_PORT="${FAV_DB_PORT:-3306}"
FAV_DB_USER="${FAV_DB_USER:-openclaw_trade_tianji}"
FAV_DB_PASSWORD="${FAV_DB_PASSWORD:-}"
FAV_DB_NAME="${FAV_DB_NAME:-openclaw_trade_tianji}"
FAV_DB_TABLE="${FAV_DB_TABLE:-symbols}"
SQLITE_DB="${DATABASE_PATH:-data/stock_analysis.db}"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python"
PYTHON="${VENV_PYTHON:-python}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

RUN_STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
SUCCESS_SQL_FILTER="(raw_result IS NULL OR (instr(raw_result, '\"success\": false') = 0 AND instr(raw_result, '\"success\":false') = 0))"

# 从 MySQL 获取港股标的，格式 0700.HK -> hk00700
SYMBOLS_RAW=$(mysql -h"$FAV_DB_HOST" -P"$FAV_DB_PORT" -u"$FAV_DB_USER" -p"$FAV_DB_PASSWORD" \
    "$FAV_DB_NAME" --batch --skip-column-names \
    -e "SELECT symbol FROM ${FAV_DB_TABLE} WHERE market='hk' AND active=1;" 2>/dev/null)

if [ -z "$SYMBOLS_RAW" ]; then
    log "ERROR: 未获取到港股标的"
    exit 1
fi

# 转换格式：0700.HK -> hk00700（补足5位数字）
SYMBOLS=$(echo "$SYMBOLS_RAW" | sed 's/\.HK$//' | awk '{printf "hk%05d\n", $1}' | tr '\n' ',' | sed 's/,$//')
log "港股标的: $SYMBOLS"

log "开始港股分析..."
$PYTHON main.py --stocks "$SYMBOLS" --no-market-review --no-notify
log "港股分析完成"

# 检查趋势变化
# 注意：DB 中存储的 code 格式需与 main.py 传入一致
IFS=',' read -ra SYMBOL_LIST <<< "$SYMBOLS"
for CODE in "${SYMBOL_LIST[@]}"; do
    RECENT=$(sqlite3 "$SQLITE_DB" \
        "SELECT id, created_at, trend_prediction FROM analysis_history WHERE code='$CODE' AND $SUCCESS_SQL_FILTER ORDER BY created_at DESC LIMIT 2;")
    LATEST_LINE=$(echo "$RECENT" | sed -n '1p')
    PREVIOUS_LINE=$(echo "$RECENT" | sed -n '2p')
    LATEST_ID=$(echo "$LATEST_LINE" | cut -d'|' -f1)
    LATEST_CREATED_AT=$(echo "$LATEST_LINE" | cut -d'|' -f2)
    TREND1=$(echo "$LATEST_LINE" | cut -d'|' -f3-)
    TREND2=$(echo "$PREVIOUS_LINE" | cut -d'|' -f3-)

    if [ -z "$LATEST_ID" ] || [ -z "$LATEST_CREATED_AT" ] || [ -z "$TREND1" ]; then
        log "$CODE: 无成功分析记录，跳过通知"
        continue
    fi

    if [[ "$LATEST_CREATED_AT" < "$RUN_STARTED_AT" ]]; then
        log "$CODE: 本次无成功分析记录，跳过通知"
        continue
    fi

    if [ "$TREND1" != "$TREND2" ] && [ -n "$TREND2" ]; then
        log "$CODE: 趋势变化 $TREND2 -> $TREND1，发送通知"

        if [ -n "${TREND_CHANGE_WECHAT_WEBHOOK_URL:-}" ]; then
            if ! $PYTHON scripts/crontab/send_trend_change_notification.py \
                --record-id "$LATEST_ID" \
                --previous-trend "$TREND2" \
                --market-label "港股"; then
                log "$CODE: 趋势变化通知发送失败"
            fi
        else
            log "$CODE: 未配置 TREND_CHANGE_WECHAT_WEBHOOK_URL，跳过趋势变化通知"
        fi
    else
        log "$CODE: 趋势无变化 ($TREND1)"
    fi
done

log "港股任务完成"
