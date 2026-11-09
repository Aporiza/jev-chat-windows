# -*- coding: utf-8 -*-
"""让 `pytest` 从任何目录运行都能 import 到项目根下的 core/、app/。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
