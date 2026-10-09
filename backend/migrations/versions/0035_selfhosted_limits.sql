-- A self-hosted installation sells no plans. Each column's maximum value stands for
-- "no limit": the usage counters keep running, and an operator can still lower a
-- Space's limit with an UPDATE as the migration owner.
ALTER TABLE space.usage_limits ALTER COLUMN storage_limit_bytes SET DEFAULT 9223372036854775807;
ALTER TABLE space.usage_limits ALTER COLUMN item_limit SET DEFAULT 2147483647;
ALTER TABLE space.usage_limits ALTER COLUMN model_request_limit SET DEFAULT 2147483647;
UPDATE space.usage_limits SET storage_limit_bytes=9223372036854775807, item_limit=2147483647,
    model_request_limit=2147483647
WHERE storage_limit_bytes=536870912 AND item_limit=10000 AND model_request_limit=2000;

