from enum import Enum, unique


@unique
class EnvironmentEnum(str, Enum):
    DEV = "dev"
    PROD = "prod"


@unique
class SearchType(str, Enum):
    REPORT = "report"  # 财报
    EXPECT = "expect"  # 预期
    QUANT = "quant"  # 量化
