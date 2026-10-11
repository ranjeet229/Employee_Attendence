# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` | It did not check MongoDB, so a dead database still reported ready. | Stop MongoDB then call `/health`. | Ping MongoDB and return 503 on failure. |
| 2 | `create_employee` | Check-then-insert races and `created_at` was not UTC/serialized. | Concurrent equal employee creates; response JSON. | Unique index, duplicate-key mapping, UTC epoch conversion. |
| 3 | `list_employees` | Offset began at page 2 and total ignored department. | Request page 1 or filter by department. | Use `(page - 1) * page_size` and count the filter. |
| 4 | time helpers | They mixed naive local time and IST, used banker rounding, and counted any positive overtime. | Non-UTC host, 10:40:01 late punch, 29-minute overtime. | Normalize UTC/IST, truncate seconds, half-up round, enforce thresholds. |
| 5 | `punch_in` | Unknown employee crashes; attendance date ignores overnight rule; duplicate check races. | Unknown code, 01:00 overnight punch, concurrent requests. | Validate employee, calculate R1 date, rely on unique insert. |
| 6 | `list_attendance` | Loaded and sorted every matching record in Python. | Large seeded collection / explain plan. | Indexed Mongo sort, skip, limit and filtered count. |
| 7 | missing endpoints | Punch-out, regularization, analytics and explain routes were absent. | Contract route tests return 404. | Implemented contract routes and aggregation pipelines. |

I did not treat the default shift values as a defect: they agree with the contract.
