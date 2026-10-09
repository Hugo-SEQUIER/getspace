-- Keep canonical task schema encrypted and scoped by the existing collection RLS.
GRANT UPDATE (encrypted_schema) ON space.collections TO space_app;
