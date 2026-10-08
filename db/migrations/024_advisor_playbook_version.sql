-- Version (first 8 hex of sha256) of config/advisor-playbook.md the advisor was given, to compare
-- playbook versions on forward returns. NULL = given before the playbook existed.
ALTER TABLE advisor_views ADD COLUMN playbook_version TEXT;
