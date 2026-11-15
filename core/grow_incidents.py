"""共享 incident 门禁：给 `GrowRunV2` 的库入口一个默认扫描。

为什么单独一层：`core/grow_runbook.py` 需要 `GrowRunV2` 来做供给/状态投影，
如果 `grow_v2` 在模块顶层 import runbook 就会成环。这里只暴露一个
`blocking_incidents()`，内部**延迟** import runbook 的真实扫描；模块加载时
不碰 runbook，因此 `grow_v2` 可以安全地在方法内调用它。扫描逻辑仍然只有
runbook 一份，不存在两套判定。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def blocking_incidents(data_dir, run_path, *, on: Optional[str] = None
                       ) -> List[Dict[str, Any]]:
    """返回 blocking（非 warning）告警；runbook 未就绪时按失败关闭。"""
    from .grow_runbook import blocking_incidents as scan
    return scan(data_dir, run_path, on=on)
