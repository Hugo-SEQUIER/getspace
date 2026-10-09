ALTER TABLE space.memory_jobs DROP CONSTRAINT memory_jobs_request_kind_check;
ALTER TABLE space.memory_jobs ADD CONSTRAINT memory_jobs_request_kind_check
    CHECK (request_kind IN ('remember','build','memora_import'));
ALTER TABLE space.memory_jobs ADD COLUMN import_total integer NOT NULL DEFAULT 0
    CHECK (import_total BETWEEN 0 AND 10000);
ALTER TABLE space.memory_jobs ADD COLUMN import_completed integer NOT NULL DEFAULT 0
    CHECK (import_completed BETWEEN 0 AND import_total);
ALTER TABLE space.memory_jobs ADD CONSTRAINT memory_jobs_import_count_check
    CHECK ((request_kind='memora_import')=(import_total>0));
GRANT UPDATE (import_completed) ON space.memory_jobs TO space_job_worker;
