-- Tables nothing reads or writes: llm_batch_* lost their only user when llm/batch.py was removed
-- (b43c24d); corporate_events was only written by upsert_corporate_events, which nothing called.
-- All three were empty on the live DB when this was written (2026-10-08).
DROP TABLE IF EXISTS llm_batch_items;
DROP TABLE IF EXISTS llm_batch_jobs;
DROP TABLE IF EXISTS corporate_events;
