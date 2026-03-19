# -*- coding: utf-8 -*-
"""
===================================
加密货币代码工具
===================================

提供：
1. 加密货币代码识别（BTCUSDT、ETHUSDT 等）
2. 代码标准化

支持的计价货币后缀：USDT、USDC、BTC、ETH、BNB
"""

import re

# 加密货币代码正则：大写字母开头，以常见计价货币结尾
_CRYPTO_PATTERN = re.compile(r'^[A-Z]{2,10}(USDT|USDC|BTC|ETH|BNB)$')


def is_crypto_code(code: str) -> bool:
    """
    判断代码是否为加密货币交易对。

    Args:
        code: 交易对代码，如 'BTCUSDT', 'ETHUSDT'

    Returns:
        True 表示是加密货币交易对，否则 False

    Examples:
        >>> is_crypto_code('BTCUSDT')
        True
        >>> is_crypto_code('ETHUSDT')
        True
        >>> is_crypto_code('AAPL')
        False
        >>> is_crypto_code('600519')
        False
    """
    normalized = (code or '').strip().upper()
    return bool(_CRYPTO_PATTERN.match(normalized))


def normalize_crypto_code(code: str) -> str:
    """
    标准化加密货币代码为大写。

    Args:
        code: 交易对代码，如 'btcusdt'

    Returns:
        大写代码，如 'BTCUSDT'
    """
    return (code or '').strip().upper()
