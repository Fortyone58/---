import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.3.0"
load_dotenv(ROOT / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(ROOT / 'data/campus_demo.db').as_posix()}")
APP_ENV = os.getenv("APP_ENV", "demo")
JWT_SECRET = os.getenv("JWT_SECRET", "")
TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "12"))
DEMO_PASSWORD = "Demo@2026"  # Public, simulated accounts only. Bootstrap credential is separate.
SKILLS = {
    "office": "办公软件", "excel": "表格处理", "python": "Python", "writing": "文字写作",
    "photography": "摄影", "design": "设计", "communication": "沟通", "organization": "活动组织",
    "s1": "技能 S1（算例）", "s2": "技能 S2（算例）",
}
ROLE_NAMES = {"student": "学生", "unit": "用工单位", "aid": "资助中心", "admin": "系统管理员"}
RULE_VERSION = "prototype-v1.1"
