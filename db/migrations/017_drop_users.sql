-- Results are no longer pushed per user (Hermes answers in the user's own chat; the worker only sends
-- ops alerts through the shared bot), so where/how to reach each user is not needed any more.
DROP TABLE users;
