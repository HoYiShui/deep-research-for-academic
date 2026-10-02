# 贡献规范（CONTRIBUTING）

## 注释规范

代码与代码注释一律使用**英文**（章程已定），采用 **Google-style docstring**。

### 规则

1. **Module docstring**：一句话 summary + 关键设计说明（如读写不对称）
2. **Class/Function docstring**：一句话 summary + `Args:` / `Returns:`（有参数时）
3. **Inline comment**：解释 **why**，不重复 **what**
4. **禁止**：中文注释、无意义的注释（重复代码本身）

### 示例

```python
"""Domain ports: abstract interfaces consumed by agents.

Agents depend on these interfaces, not concrete implementations; adapters
live in infrastructure. Read/write asymmetry: reads go through RetrievalPort
(embed→hybrid→rerank); writes (ingest) use EmbeddingPort + VectorStorePort directly.
"""


class StateStorePort(Protocol):
    """Persist business truth and phase-level snapshots.

    Implemented by PostgreSQL (V1); the durable source of truth for recovery.
    """

    async def save_snapshot(self, session_id: str, phase: str, state: dict) -> None:
        """Save a phase-level snapshot.

        Args:
            session_id: The research session ID.
            phase: The current pipeline phase.
            state: The PipelineState dict to persist.
        """
```

## 提交规范

遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`）。
AI 生成的提交须附 `Co-Authored-By` 署名。

## 代码风格

- Python 3.11+，类型注解用 `from __future__ import annotations`
- 端口接口用 `typing.Protocol`（结构类型，非 ABC）
- 一行 ≤ 100 字符（ruff line-length=100）
