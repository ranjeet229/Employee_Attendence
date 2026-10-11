# Decisions

`employees.emp_code` is unique: it is both the business identity and the protection
against concurrent employee creation. `attendance_logs (emp_code, date)` is unique,
which makes one attendance day atomic. The `(date, emp_code)` indexes support the
ordered list and date-bounded reporting scans. `(department, joined_on)` supports
department headcount lookups. I considered indexing every status/derived metric, but
rejected that because they are low-selectivity fields and the write/index cost is not
justified by these reports.

Two simultaneous punch-ins both attempt the same unique natural key. MongoDB accepts
one insert; the duplicate-key exception from every loser becomes HTTP 409. There is no
check-then-insert race.

The leaderboard uses MongoDB's `$rank`, rather than array position. It filters ranks
only after ranking, so everyone tied at the cutoff is returned; this is the stated
standard competition ranking behaviour.

Department summary starts at employees whose joining date is in scope, then left-joins
logs. Thus an employee with no logs still contributes exactly once to the headcount.

At 100x scale I would maintain a daily departmental rollup collection (updated via a
change stream or transaction/outbox) and use a read replica for analytics.
