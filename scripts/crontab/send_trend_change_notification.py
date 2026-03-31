#!/usr/bin/env python3
"""Send trend-change notifications with the latest analysis digest."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from sqlalchemy import and_, desc, or_, select

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.analyzer import AnalysisResult
from src.config import get_config
from src.notification import NotificationService
from src.notification_sender.wechat_sender import WechatSender
from src.storage import AnalysisHistory, DatabaseManager, StockDaily, CryptoKline
from src.services.history_service import HistoryService


logger = logging.getLogger(__name__)


def _build_fallback_result(record) -> AnalysisResult:
    """Fallback to a minimal AnalysisResult when raw_result is unavailable."""
    return AnalysisResult(
        code=record.code,
        name=record.name or record.code,
        sentiment_score=record.sentiment_score or 50,
        trend_prediction=record.trend_prediction or "",
        operation_advice=record.operation_advice or "观望",
        analysis_summary=record.analysis_summary or "",
        model_used=None,
    )


def _load_recent_trends(db: DatabaseManager, history_service: HistoryService, record, limit: int = 5):
    """Load recent successful trend records ending at the current record."""
    with db.get_session() as session:
        rows = session.execute(
            select(AnalysisHistory)
            .where(
                AnalysisHistory.code == record.code,
                or_(
                    AnalysisHistory.created_at < record.created_at,
                    and_(
                        AnalysisHistory.created_at == record.created_at,
                        AnalysisHistory.id <= record.id,
                    ),
                ),
            )
            .order_by(desc(AnalysisHistory.created_at), desc(AnalysisHistory.id))
            .limit(limit * 3)
        ).scalars().all()

    trends = []
    for row in rows:
        rebuilt = history_service.build_analysis_result_from_record(row)
        if rebuilt is not None and not rebuilt.success:
            continue

        trends.append(
            {
                "time": row.created_at.strftime("%m-%d %H:%M") if row.created_at else "",
                "trend": (rebuilt.trend_prediction if rebuilt else row.trend_prediction) or "",
                "advice": (rebuilt.operation_advice if rebuilt else row.operation_advice) or "",
                "score": rebuilt.sentiment_score if rebuilt else (row.sentiment_score or 50),
            }
        )
        if len(trends) >= limit:
            break

    return trends


def _get_latest_price_info(db: DatabaseManager, code: str):
    """Get latest price and date from stock_daily or crypto_kline table."""
    with db.get_session() as session:
        # Try crypto first (check if code looks like crypto symbol)
        if code.isupper() and len(code) >= 6:
            latest_crypto = session.execute(
                select(CryptoKline)
                .where(CryptoKline.code == code)
                .order_by(desc(CryptoKline.open_time))
                .limit(1)
            ).scalar_one_or_none()

            if latest_crypto:
                return {
                    "price": latest_crypto.close,
                    "date": latest_crypto.open_time,
                    "pct_chg": latest_crypto.pct_chg,
                    "is_crypto": True,
                }

        # Try stock daily
        latest_stock = session.execute(
            select(StockDaily)
            .where(StockDaily.code == code)
            .order_by(desc(StockDaily.date))
            .limit(1)
        ).scalar_one_or_none()

        if latest_stock:
            return {
                "price": latest_stock.close,
                "date": latest_stock.date,
                "pct_chg": latest_stock.pct_chg,
                "is_crypto": False,
            }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a trend-change alert to a dedicated WeChat webhook.")
    parser.add_argument("--record-id", type=int, required=True, help="Primary key ID in analysis_history")
    parser.add_argument("--previous-trend", required=True, help="Previous trend value")
    parser.add_argument("--market-label", default="", help="Human-readable market label, e.g. A股 / 港股 / 美股")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")

    config = get_config()
    if not config.trend_change_wechat_webhook_url:
        logger.info("TREND_CHANGE_WECHAT_WEBHOOK_URL 未配置，跳过趋势变化提醒发送")
        return 0

    db = DatabaseManager.get_instance()
    record = db.get_analysis_history_by_id(args.record_id)
    if not record:
        logger.error("未找到分析历史记录 id=%s", args.record_id)
        return 1

    history_service = HistoryService(db_manager=db)
    result = history_service.build_analysis_result_from_record(record) or _build_fallback_result(record)
    if not result.success:
        logger.error("分析历史记录 id=%s 标记为失败，拒绝发送趋势变化提醒", args.record_id)
        return 1

    # Get latest price info from stock_daily or crypto_kline table
    price_info = _get_latest_price_info(db, record.code)
    if price_info:
        result = replace(
            result,
            current_price=price_info["price"],
            change_pct=price_info["pct_chg"],
        )
        if price_info["date"]:
            if not result.market_snapshot:
                result.market_snapshot = {}
            # Format date based on market type
            if price_info.get("is_crypto"):
                result.market_snapshot["price_date"] = price_info["date"].strftime("%m-%d %H:%M")
            else:
                result.market_snapshot["price_date"] = price_info["date"].strftime("%Y-%m-%d")

    recent_trends = _load_recent_trends(db, history_service, record)

    formatter = NotificationService(initialize_channels=False)
    content = formatter.generate_wechat_trend_change_report(
        result=result,
        previous_trend=args.previous_trend,
        market_label=args.market_label,
        changed_at=record.created_at,
        recent_trends=recent_trends,
    )

    sender = WechatSender(
        replace(
            config,
            wechat_webhook_url=config.trend_change_wechat_webhook_url,
        )
    )
    if sender.send_to_wechat(content):
        logger.info("趋势变化提醒发送成功: %s", record.code)
        return 0

    logger.error("趋势变化提醒发送失败: %s", record.code)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
