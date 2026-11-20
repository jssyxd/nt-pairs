"""「股票 / ETF / pre-IPO」标的的**显式**参考名单与跨场所符号归一化。

CryptoHFTData 的 `/symbols` 只给 symbol 名单，没有资产类别字段，所以「哪些是股票」必须
显式声明（见 `docs/CHD_SCREEN.md` 的「宇宙口径」一节）。名单是人工维护的静态集合，
只用于**缩小扫描范围**；真正的筛选由五道闸门完成，单位不一致的对会被闸门①挡下。

命名约定（实测 2026-11-14）：
* Hyperliquid HIP-3 带 deployer 前缀：`XYZ:SOXL`、`IO:OAI`、`CASH:EWY`、`VNTL:OPENAI`；
* Lighter / OKX / Binance 用裸名：`SOXL`、`SOXL-USDT-SWAP`、`SOXLUSDT`。
"""

from __future__ import annotations

__all__ = [
    "EQUITY_BASES",
    "PRE_IPO_BASES",
    "EQUITY_BASES_ALL",
    "normalize_venue_symbol",
    "base_of",
]

# 大盘股 / 中盘股 / ADR / 非美股票（韩股、日股、港股、A 股）
_EQUITY_BASES: frozenset[str] = frozenset({
    # --- 美股科技 ---
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO", "AMD",
    "INTC", "QCOM", "MU", "MRVL", "SMCI", "TSM", "ASML", "ARM", "MRNA", "LRCX",
    "AMAT", "KLAC", "TXN", "ADI", "NXPI", "MCHP", "MPWR", "ON", "ANET", "SNPS",
    "CDNS", "ORCL", "CRM", "ADBE", "NFLX", "DIS", "NFLX", "SAP", "NOW", "INTU",
    "PANW", "CRWD", "CRCL", "PLTR", "SHOP", "SQ", "PYPL", "NET", "SNOW", "DDOG",
    "ZM", "DOCU", "TEAM", "WDAY", "HUBS", "VEEV", "MNDY", "SAMSUNG",
    # --- 半导体产业链 ---
    "WDC", "STX", "ENTG", "TER", "ONTO", "ACLS", "AEIS", "RMBS", "QRVO", "SWKS",
    "COHR", "LITE", "ALAB", "CRDO", "GFS", "AMKR", "ASX", "KLIC", "MCHP", "SITM",
    "DIOD", "MPWR", "VICR", "INFN", "COHR", "LITE", "AAOI", "NVTS", "PLAB",
    # --- 硬件 / 消费电子 ---
    "DELL", "HPQ", "LOGI", "APH", "GLW", "JBL", "SONY", "LOGI", "FLEX", "BEN",
    "HIMX", "VIVC", "KURA", "GPRO", "IONQ", "RGTI", "QUBT", "QBTS", "WULF",
    # --- 金融 ---
    "JPM", "BAC", "C", "WFC", "GS", "MS", "SCHW", "BLK", "SPGI", "AXP", "V",
    "MA", "AIG", "COF", "TRV", "PGR", "ALL", "CB", "HBAN", "PNC", "USB", "TFC",
    "FITB", "KEY", "RF", "CFG", "MTB", "ZION", "CMA", "WBD", "ALLY", "SOFI",
    "LC", "AFRM", "UPST", "WULF",
    # --- 医药 / 医疗 ---
    "LLY", "JNJ", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BSX", "ISRG",
    "VRTX", "REGN", "ILMN", "AMGN", "GILD", "BMRN", "DXCM", "SYK", "MDT",
    "ZBH", "EW", "GEHC", "ZTS", "STE", "BDX", "BAX", "WAT", "A", "HOLX", "HSIC",
    "ALGN", "ISRG", "PODD", "IDXX", "XRAY", "DHR",
    # --- 工业 / 能源 / 公用 ---
    "GE", "CAT", "DE", "HON", "RTX", "LMT", "NOC", "GD", "BA", "XOM", "CVX",
    "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY", "HAL", "DVN", "HES",
    "FANG", "CTRA", "EQT", "LNG", "KMI", "WMB", "OKE", "TRP", "ET", "TRN",
    "VST", "CEG", "GEV", "BE", "CCJ", "SMR", "OKLO", "SMR", "NNE", "GEV",
    "UNP", "DUK", "NEE", "AEP", "EXC", "LIN", "DOW", "PPG", "SHW", "NUE",
    "FCX", "ADM", "BG", "DE", "CARR", "TT", "GNRC", "JCI", "ETN", "ITW",
    "PH", "EMR", "DOV", "AME", "FTV", "IR", "XYL", "ROK", "IEX", "CMI", "PCAR",
    "RGLD", "WAB", "CRH", "BLD", "AOS", "NDSN", "PNR", "J", "WTS",
    # --- 消费 / 零售 / 餐饮 ---
    "WMT", "COST", "TGT", "HD", "LOW", "KR", "MCD", "SBUX", "NKE", "DASH",
    "ABNB", "UBER", "LYV", "BKNG", "CHWY", "ROST", "TJX", "BURL", "LULU",
    "DECK", "GPS", "ANF", "URBN", "AAP", "FIVE", "SMAR", "ETSY", "W", "DUL",
    "KDP", "MNST", "KHC", "GIS", "KMB", "CL", "CLX", "CHD", "OMC", "IPG",
    "CASY", "BJ", "DG", "DLTR", "SFM",
    # --- 电信 / 媒体 / 出行 ---
    "T", "VZ", "TMUS", "CMCSA", "DISH", "SATS", "LYV", "UAL", "DAL", "AAL",
    "CCL", "RCL", "NCLH", "LUV", "ALK", "JBLU", "SWRK", "HA", "LVS", "WYNN",
    "MGM", "CZR", "PENN", "DKNG", "FLUT", "GENI", "LYV",
    # --- 零售 / 主题股 / 新经济 ---
    "GME", "AMC", "BB", "BYND", "HOOD", "RKT", "SKLZ", "CLOV", "NIO", "XPEV",
    "LI", "RIVN", "LCID", "GM", "F", "AAPL", "RBLX", "SOUN", "AI", "IONQ",
    "MARA", "RIOT", "CLSK", "HUT", "CORZ", "IREN", "CIFR", "MSTR", "COIN",
    "HOOD", "RKLB", "OKLO", "ASTS", "LUNR", "RDW", "MVST", "SMR", "NNE",
    # --- 化工 / 农业 / 其他 ---
    "LYB", "DD", "DOW", "CE", "CTVA", "FMC", "MOS", "NUE", "CAG", "ADM",
    "BG", "DEAN", "K", "GIS", "HRL", "SJM", "COTY", "ELF", "COTY", "TPR",
    # --- 加密相关（与股票同源但为加密叙事）---
    "MSTR", "COIN", "MARA", "RIOT", "CLSK", "HUT", "CORZ", "IREN", "CIFR",
    # --- 韩股 / 日股 / 台股 / 港股 / 印股 ---
    "SKHYNIX", "SAMSUNG", "HYUNDAI", "XIAOMI", "TENCENT", "SMIC", "BYD", "NIO",
    "SAIC", "GOTEM", "SHINHAN", "HYNIX", "SKHX", "SKHY", "KRCOMP", "HANMI",
    "TSM", "ASML", "MU", "UMC", "RXO", "WDC", "SPACEX", "SPCX", "PAXG", "AAVE",
    # --- pre-IPO / 私募 ---
    "OPENAI", "ANTHROPIC", "OAI", "ANTH", "SPACEX", "SPCX", "GROQ", "COHERE",
    "FIGMA", "STRIPE", "DATABRICKS", "SNOW", "COREWEAVE", "CRWV", "NBIS",
    "VANTL", "PRL", "TCNT", "OURA", "MINIMAX", "UNITREE", "ROBOT", "BIOTECH",
    "DEFENSE", "ENERGY", "INFOTECH", "MAG7", "NUCLEAR", "SEMIS", "SOY", "WHEAT",
    "OTHER", "SMALL2000", "US500", "USTECH", "USBOND", "USENERGY", "USOIL",
    "USA100", "USA500", "GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM",
    "BRENTOIL", "WTI", "NATGAS", "CL", "EUR", "GBP", "JPY", "SP500", "JP225",
    "KR200", "KSTR", "UTIL", "SOFI", "OIL", "GAS",
})

# 明显是加密 / 稳定币 / 支付的（避免误当股票）
_CRYPTO_BASES: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "MATIC", "DOT", "LINK",
    "LTC", "BCH", "ATOM", "NEAR", "APT", "SUI", "ARB", "OP", "INJ", "TIA",
    "SEI", "PEPE", "FLOKI", "BONK", "WIF", "JUP", "PYTH", "JTO", "STRK", "ZEC",
    "TRX", "TON", "SHIB", "PEOPLE", "FARTCOIN", "POPCAT", "PENGU", "BONK",
    "USDC", "USDT", "DAI", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD",
    "USDE", "USDT0", "XAUT", "PAXG",
})

# 归一化后落在名单里、但明确是 ETF/指数/商品/外汇的（保留在扫描里，但报告中单独标注类别）
ETF_BASES: frozenset[str] = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "EFA", "EEM", "VWO", "FXI", "VGK",
    "INDA", "EWZ", "EWW", "EWC", "EWD", "EWP", "EWS", "EWJ", "EWA", "EWI", "EWO",
    "EWY", "RSP", "SCHD", "VIG", "VTV", "MDY", "IJH", "IJR", "IYC", "IYE", "IYW",
    "IYK", "IYF", "IYH", "IYJ", "IYR", "XLK", "XLF", "XLE", "XLV", "XLI", "XLB",
    "XLY", "XLP", "XLU", "XLC", "XLRE", "VNQ", "XBI", "IBB", "IYT", "XOP", "OIH",
    "XES", "IEO", "KRE", "ITA", "XHB", "XAR", "PAVE", "XME", "KIE", "XSD",
    "SMH", "SOXX", "SOXL", "SOXS", "TQQQ", "SQQQ", "SPXL", "TSDD", "NVDL",
    "TSLL", "GLD", "IAU", "SLV", "USO", "UNG", "DBC", "GSG", "BNO", "DNO",
    "MOO", "BDRY", "SEA", "KWEB", "CQQQ", "ARKK", "IPO", "SILV", "XSD", "SPOT",
})

EQUITY_BASES = _EQUITY_BASES
PRE_IPO_BASES = frozenset({
    "OPENAI", "ANTHROPIC", "OAI", "ANTH", "SPACEX", "SPCX", "COREWEAVE",
    "CRWV", "NBIS", "GROQ", "ANTHROPIC",
})
EQUITY_BASES_ALL = frozenset((_EQUITY_BASES | ETF_BASES) - _CRYPTO_BASES)


def base_of(symbol: str) -> str:
    """去掉场所前缀/后缀，得到「裸名」（SOXL）。"""
    s = symbol.strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    if s.endswith("-USDT-SWAP"):
        s = s[: -len("-USDT-SWAP")]
    elif s.endswith("-USD-SWAP"):
        s = s[: -len("-USD-SWAP")]
    elif s.endswith("-USDC-SWAP"):
        s = s[: -len("-USDC-SWAP")]
    elif s.endswith("USDT"):
        s = s[: -len("USDT")]
    elif s.endswith("USDC"):
        s = s[: -len("USDC")]
    return s.upper()


def normalize_venue_symbol(symbol: str) -> str:
    """`XYZ:SOXL` → `SOXL`；`SOXL-USDT-SWAP` → `SOXL`；`SOXLUSDT` → `SOXL`。"""
    return base_of(symbol)
