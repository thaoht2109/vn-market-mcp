-- 011 cached LLM-written reports; superseded by pipeline/stock_report.py, which
-- renders the report from stored data on demand (nothing to invalidate).
DROP TABLE IF EXISTS report_cache;
