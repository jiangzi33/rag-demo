import os

# 测试中不调用真实的向量化服务（环境变量优先于 .env），需要时在用例里单独打开
os.environ["EMBEDDING_ENABLED"] = "false"
