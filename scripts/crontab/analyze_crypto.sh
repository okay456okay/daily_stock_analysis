#!/usr/bin/env bash
# 加密货币定时分析脚本
# crontab: 15 0,4,8,12,16,20 * * *

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

# 从 MySQL 获取加密货币标的，格式直接是 BTCUSDT 等，无需转换
SYMBOLS_RAW=$(mysql -h"$FAV_DB_HOST" -P"$FAV_DB_PORT" -u"$FAV_DB_USER" -p"$FAV_DB_PASSWORD" \
    "$FAV_DB_NAME" --batch --skip-column-names \
    -e "SELECT symbol FROM ${FAV_DB_TABLE} WHERE market='crypto' AND active=1;" 2>/dev/null)

if [ -z "$SYMBOLS_RAW" ]; then
    log "ERROR: 未获取到加密货币标的"
    exit 1
fi

SYMBOLS=$(echo "$SYMBOLS_RAW" | tr '\n' ',' | sed 's/,$//')
log "加密货币标的: $SYMBOLS"

log "开始加密货币分析..."
# 加密货币不加 --dry-run，需要实际获取数据
$PYTHON main.py --stocks "$SYMBOLS" --no-market-review --no-notify
log "加密货币分析完成"

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
                --market-label "加密货币"; then
                log "$CODE: 趋势变化通知发送失败"
            fi
        else
            log "$CODE: 未配置 TREND_CHANGE_WECHAT_WEBHOOK_URL，跳过趋势变化通知"
        fi
    else
        log "$CODE: 趋势无变化 ($TREND1)"
    fi
done

log "加密货币任务完成"
