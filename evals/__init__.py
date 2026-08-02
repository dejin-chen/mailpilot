"""MailPilot 离线评测包。"""

import os

# 评测模块被导入时就关闭匿名遥测；不会影响 DeepEval 本地指标功能。
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
