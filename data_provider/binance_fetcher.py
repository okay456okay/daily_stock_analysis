# -*- coding: utf-8 -*-
"""
===================================
BinanceFetcher - 加密货币数据源 (Priority 5)
===================================

数据来源：Binance（通过公开 REST API）
特点：支持合约与现货 K 线，4h 周期
定位：加密货币专用数据源

关键策略：
1. 优先使用合约交易对（fapi.binance.com）
2. 合约失败后 fallback 到现货（api.binance.com）
3. 复用 USE_PROXY/PROXY_HOST/PROXY_PORT 代理配置
"""

import logging
import os
from datetime import datetime, timezone

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .crypto_mapping import is_crypto_code, normalize_crypto_code
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource

logger = logging.getLogger(__name__)

# Binance K 线字段索引
_KLINE_OPEN_TIME = 0
_KLINE_OPEN = 1
_KLINE_HIGH = 2
_KLINE_LOW = 3
_KLINE_CLOSE = 4
_KLINE_VOLUME = 5
_KLINE_QUOTE_VOLUME = 7  # quote asset volume，对应 amount

_FUTURES_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
_SPOT_KLINE_URL = "https://api.binance.com/api/v3/klines"
_INTERVAL = "4h"
_REQUEST_TIMEOUT = 15


class BinanceFetcher(BaseFetcher):
    """
    Binance 加密货币数据源

    优先级：5（加密货币专用，不参与 A 股/港股/美股路由）
    数据来源：Binance 公开 REST API

    关键策略：
    - 优先合约 K 线，失败后 fallback 现货
    - 4h 周期，days 参数转换为 limit（每天 6 根）
    - 支持代理
    """

    name = "BinanceFetcher"
    priority = int(os.getenv("BINANCE_PRIORITY", "5"))

    def __init__(self):
        self._proxies = None
        if (
            os.getenv("GITHUB_ACTIONS") != "true"
            and os.getenv("USE_PROXY", "false").lower() == "true"
        ):
            host = os.getenv("PROXY_HOST", "127.0.0.1")
            port = os.getenv("PROXY_PORT", "10809")
            proxy_url = f"http://{host}:{port}"
            self._proxies = {"http": proxy_url, "https": proxy_url}
            logger.debug(f"[BinanceFetcher] 已启用代理: {proxy_url}")

    def _fetch_klines(self, url: str, symbol: str, limit: int) -> list:
        """从指定 URL 获取 K 线数据"""
        resp = requests.get(
            url,
            params={"symbol": symbol, "interval": _INTERVAL, "limit": limit},
            proxies=self._proxies,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            raise DataFetchError(f"Binance 返回空数据: {symbol}")
        return data

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取 Binance K 线原始数据，优先合约，fallback 现货。

        Args:
            stock_code: 交易对，如 'BTCUSDT'
            start_date: 开始日期（用于计算 limit）
            end_date: 结束日期

        Returns:
            原始 K 线 DataFrame
        """
        symbol = normalize_crypto_code(stock_code)

        # 根据日期范围计算 limit（4h 周期，每天 6 根，多取 10% 保证覆盖）
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            days = max((end - start).days, 1)
        except Exception:
            days = 60
        limit = min(days * 6 + 6, 1500)  # Binance 单次最多 1500

        # 优先合约
        source = "futures"
        try:
            logger.debug(f"[BinanceFetcher] 尝试合约 K 线: {symbol}")
            raw = self._fetch_klines(_FUTURES_KLINE_URL, symbol, limit)
            logger.info(f"[BinanceFetcher] {symbol} 合约 K 线获取成功: {len(raw)} 条")
        except Exception as e:
            logger.warning(f"[BinanceFetcher] {symbol} 合约 K 线失败: {e}，尝试现货")
            source = "spot"
            raw = self._fetch_klines(_SPOT_KLINE_URL, symbol, limit)
            logger.info(f"[BinanceFetcher] {symbol} 现货 K 线获取成功: {len(raw)} 条")

        df = pd.DataFrame(raw)
        df.attrs["source"] = source
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        将 Binance K 线格式转换为 STANDARD_COLUMNS。

        Binance K 线列顺序：
        0: open_time, 1: open, 2: high, 3: low, 4: close,
        5: volume, 6: close_time, 7: quote_asset_volume, ...
        """
        result = pd.DataFrame()

        # open_time 为毫秒时间戳，转换为 naive datetime（去掉时区）
        result["date"] = pd.to_datetime(df[_KLINE_OPEN_TIME], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)

        result["open"] = pd.to_numeric(df[_KLINE_OPEN], errors="coerce")
        result["high"] = pd.to_numeric(df[_KLINE_HIGH], errors="coerce")
        result["low"] = pd.to_numeric(df[_KLINE_LOW], errors="coerce")
        result["close"] = pd.to_numeric(df[_KLINE_CLOSE], errors="coerce")
        result["volume"] = pd.to_numeric(df[_KLINE_VOLUME], errors="coerce")
        result["amount"] = pd.to_numeric(df[_KLINE_QUOTE_VOLUME], errors="coerce")

        # 计算涨跌幅
        result["pct_chg"] = result["close"].pct_change() * 100

        result = result.dropna(subset=["date", "close"])
        result = result.reset_index(drop=True)
        return result

    def get_daily_data(
        self,
        stock_code: str,
        start_date: str = None,
        end_date: str = None,
        days: int = 60,
    ) -> pd.DataFrame:
        """
        获取加密货币 4h K 线数据并标准化。

        Args:
            stock_code: 交易对，如 'BTCUSDT'
            start_date: 开始日期
            end_date: 结束日期
            days: 获取天数（默认 60，对应约 360 根 4h K 线）

        Returns:
            标准化 DataFrame（STANDARD_COLUMNS + 技术指标）
        """
        from datetime import date, timedelta

        if end_date is None:
            end_date = date.today().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

        logger.info(f"[BinanceFetcher] 开始获取 {stock_code} 4h K 线: {start_date} ~ {end_date}")

        raw_df = self._fetch_raw_data(stock_code, start_date, end_date)
        df = self._normalize_data(raw_df, stock_code)
        df = self._calculate_indicators(df)

        logger.info(f"[BinanceFetcher] {stock_code} 获取完成: {len(df)} 条")
        return df

    def get_realtime_quote(self, stock_code: str) -> "Optional[UnifiedRealtimeQuote]":
        """
        获取加密货币实时行情，优先合约 ticker，fallback 现货 ticker。

        Args:
            stock_code: 交易对，如 'BTCUSDT'

        Returns:
            UnifiedRealtimeQuote 或 None
        """
        from typing import Optional

        symbol = normalize_crypto_code(stock_code)
        futures_url = f"https://fapi.binance.com/fapi/v1/ticker/24hr"
        spot_url = f"https://api.binance.com/api/v3/ticker/24hr"

        for url, label in [(futures_url, "合约"), (spot_url, "现货")]:
            try:
                resp = requests.get(
                    url,
                    params={"symbol": symbol},
                    proxies=self._proxies,
                    timeout=_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                price = float(data.get("lastPrice", 0))
                if price <= 0:
                    continue
                prev_close = float(data.get("prevClosePrice", 0)) or None
                change_pct = float(data.get("priceChangePercent", 0))
                logger.info(f"[BinanceFetcher] {symbol} {label}实时行情成功: 价格={price}")
                return UnifiedRealtimeQuote(
                    code=symbol,
                    name=symbol,
                    source=RealtimeSource.FALLBACK,
                    price=price,
                    change_pct=change_pct,
                    change_amount=float(data.get("priceChange", 0)) or None,
                    open_price=float(data.get("openPrice", 0)) or None,
                    high=float(data.get("highPrice", 0)) or None,
                    low=float(data.get("lowPrice", 0)) or None,
                    pre_close=prev_close,
                    volume=int(float(data.get("volume", 0))) or None,
                    amount=float(data.get("quoteVolume", 0)) or None,
                )
            except Exception as e:
                logger.warning(f"[BinanceFetcher] {symbol} {label}实时行情失败: {e}")

        return None
