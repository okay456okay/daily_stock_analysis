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
SQLITE_DB="data/stock_analysis.db"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python"
PYTHON="${VENV_PYTHON:-python}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

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
        "SELECT trend_prediction FROM analysis_history WHERE code='$CODE' ORDER BY created_at DESC LIMIT 2;")
    TREND1=$(echo "$RECENT" | sed -n '1p')
    TREND2=$(echo "$RECENT" | sed -n '2p')

    if [ -z "$TREND1" ]; then
        log "$CODE: 无分析记录，跳过通知"
        continue
    fi

    if [ "$TREND1" != "$TREND2" ] && [ -n "$TREND2" ]; then
        log "$CODE: 趋势变化 $TREND2 -> $TREND1，发送通知"

        HISTORY=$(sqlite3 "$SQLITE_DB" \
            "SELECT created_at, trend_prediction, operation_advice, sentiment_score FROM analysis_history WHERE code='$CODE' ORDER BY created_at DESC LIMIT 5;" \
            | awk -F'|' '{printf "  %s | %s | %s | 评分:%s\n", $1, $2, $3, $4}')

        LATEST=$(sqlite3 "$SQLITE_DB" \
            "SELECT name, trend_prediction, operation_advice, sentiment_score, analysis_summary FROM analysis_history WHERE code='$CODE' ORDER BY created_at DESC LIMIT 1;" \
            | awk -F'|' '{printf "名称:%s\n趋势:%s\n建议:%s\n评分:%s\n摘要:%s", $1, $2, $3, $4, $5}')

        MSG="【港股趋势变化】${CODE}\n趋势: ${TREND2} → ${TREND1}\n\n最近5次分析:\n${HISTORY}\n\n最新详情:\n${LATEST}"

        if [ -n "${WECHAT_WEBHOOK_URL:-}" ]; then
            curl -s -X POST "$WECHAT_WEBHOOK_URL" \
                -H 'Content-Type: application/json' \
                -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$(echo -e "$MSG" | sed 's/"/\\"/g')\"}}" \
                > /dev/null
        fi
    else
        log "$CODE: 趋势无变化 ($TREND1)"
    fi
done

log "港股任务完成"
