"""上下文压缩（第八阶段）。

公共 API：
- Compactor：两层压缩协调器
- CompactStats：压缩统计
- StashEvent：单工具结果存盘事件
"""

from mewcode.compaction.compactor import Compactor, CompactStats
from mewcode.compaction.lightweight import StashEvent

__all__ = ["Compactor", "CompactStats", "StashEvent"]
