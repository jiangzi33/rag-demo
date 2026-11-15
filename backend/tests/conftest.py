import os
import tempfile
from pathlib import Path

# 测试必须使用独立的临时库，绝不能连到 .env 中的开发库（测试结束会清空表）。
# conftest 在所有测试模块之前加载，这里强制覆盖，避免某个模块先 import app 时读到 .env 的 DATABASE_URL
_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ.setdefault("ADMIN_USERNAMES", "admin")

# 测试中不调用真实的向量化服务（环境变量优先于 .env），需要时在用例里单独打开
os.environ["EMBEDDING_ENABLED"] = "false"
