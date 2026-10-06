-- Locator is saved before object I/O; it is not a successful result claim.
ALTER TABLE tool_call_attempts ADD COLUMN staged_tokens INTEGER CHECK (staged_tokens >= 0);
