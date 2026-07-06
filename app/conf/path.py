from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent.parent.parent

# 日志文件路径
LOG_DIR = BASE_DIR / "logs"

# 环境配置目录
ENV_DIR = BASE_DIR / "env"

# 静态资源目录
STATIC_DIR = BASE_DIR / "static"

# JS资源目录
JS_DIR = STATIC_DIR / "js"

# 报告目录
REPORT_DIR = STATIC_DIR / "reports"

# 报告目录uri
REPORT_DIR_URI = "/static/reports"
