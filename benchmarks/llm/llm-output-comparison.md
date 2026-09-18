# LLM extraction output comparison

What each model turns the transcript into. The deliverable is the resolved event payload the Calendar layer consumes, for every field: `operation`, `event_type`, `title`, ISO `start`, `duration` in minutes, `location`, `recurrence`, and `reminders` in minutes. Everything is anchored to the fixed reference date 2026-09-18 (Friday). `—` is null.

`exact` is whether the whole resolved payload equals the expected one; the reason names the first differing field.

## RO-03 (transcript)

Transcript: `Programează dentistul pentru joi la fără un sfert de șase seara, pe strada Independenței 28.`

Expected payload:

```json
{"operation": "create", "event_type": "appointment", "title": "dentistul", "start": "2026-09-24T17:45", "all_day": false, "duration_minutes": null, "location": "strada Independenței 28", "recurrence": null, "reminders_minutes": [], "complete": true, "unresolved": []}
```

| Model | op | type | title | start | duration | location | recurrence | reminders | exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.5-flash-lite | create | appointment | dentistul | 2026-09-24T17:45 | — | strada Independenței 28 | — | — | yes |
| groq-gpt-oss-120b | create | appointment | dentistul | 2026-09-24T17:45 | — | pe strada Independenței 28 | — | — | no (location) |
| groq-gpt-oss-20b | create | appointment | dentistul | 2026-09-24T17:45 | — | pe strada Independenței 28 | — | — | no (location) |
| groq-qwen3.8-27b | create | appointment | dentistul | 2026-09-24T17:45 | — | strada Independenței 28 | — | — | yes |
| mistral-small | create | appointment | dentist | 2026-09-24T17:45 | — | strada Independenței 28 | — | — | no (title) |
| mistral-medium | create | appointment | dentistul | 2026-09-24T17:45 | — | pe strada Independenței 28 | — | — | no (location) |
| mistral-large | create | appointment | dentistul | 2026-09-24T17:45 | — | strada Independenței 28 | — | — | yes |
| mistral-ministral-14b | create | appointment | — | 2026-09-24T17:45 | — | pe strada Independenței 28 | — | — | no (title) |
| mistral-ministral-8b | create | appointment | — | 2026-09-24T17:45 | — | strada Independenței 28 | — | — | no (title) |
| mistral-ministral-3b | create | appointment | — | 2026-09-24T17:45 | — | pe strada Independenței 28 | — | — | no (title) |

## RU-02 (transcript)

Transcript: `Во вторник в половине третьего у меня лабораторная ФТМ, корпус ФКим, улица Студенцлор девять дробь семь. Напомни за тридцать минут`

Expected payload:

```json
{"operation": "create", "event_type": "class", "title": "лабораторная", "start": "2026-09-22T02:30", "all_day": false, "duration_minutes": null, "location": "ФТМ, корпус ФКим, улица Студенцлор девять дробь семь", "recurrence": null, "reminders_minutes": [30], "complete": true, "unresolved": []}
```

| Model | op | type | title | start | duration | location | recurrence | reminders | exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.5-flash-lite | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |
| groq-gpt-oss-120b | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |
| groq-gpt-oss-20b | create | reminder | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (event_type) |
| groq-qwen3.8-27b | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |
| mistral-small | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |
| mistral-medium | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |
| mistral-large | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |
| mistral-ministral-14b | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |
| mistral-ministral-8b | create | exam | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (event_type) |
| mistral-ministral-3b | create | class | лабораторная ФТМ | 2026-09-22T02:30 | — | корпус ФКим, улица Студенцлор девять дробь семь | — | 30 min | no (title) |

## EN-03 (transcript)

Transcript: `Schedule the dentist for Thursday at 5:54 in the evening, 28 Independencie Street.`

Expected payload:

```json
{"operation": "create", "event_type": "appointment", "title": "the dentist", "start": "2026-09-24T17:54", "all_day": false, "duration_minutes": null, "location": "28 Independencie Street", "recurrence": null, "reminders_minutes": [], "complete": true, "unresolved": []}
```

| Model | op | type | title | start | duration | location | recurrence | reminders | exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.5-flash-lite | create | appointment | dentist | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | no (title) |
| groq-gpt-oss-120b | create | appointment | the dentist | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | yes |
| groq-gpt-oss-20b | create | appointment | dentist | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | no (title) |
| groq-qwen3.8-27b | create | appointment | dentist | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | no (title) |
| mistral-small | create | appointment | the dentist | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | yes |
| mistral-medium | create | appointment | the dentist | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | yes |
| mistral-large | create | appointment | dentist | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | no (title) |
| mistral-ministral-14b | create | appointment | — | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | no (title) |
| mistral-ministral-8b | create | appointment | — | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | no (title) |
| mistral-ministral-3b | create | appointment | — | 2026-09-24T17:54 | — | 28 Independencie Street | — | — | no (title) |

## EN-06 (transcript)

Transcript: `[car hooting] [whistle blowing] Every Monday at eight-- Oh. [clears throat] Every Monday at eight in the morning, I have an English class for nineteen minutes. [car hooting]`

Expected payload:

```json
{"operation": "create", "event_type": "class", "title": "English class", "start": "2026-09-21T08:00", "all_day": false, "duration_minutes": 19, "location": null, "recurrence": {"freq": "weekly", "weekday": 0}, "reminders_minutes": [], "complete": true, "unresolved": []}
```

| Model | op | type | title | start | duration | location | recurrence | reminders | exact |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.5-flash-lite | create | class | English class | 2026-09-21T08:00 | 19 | — | weekly Mon | — | yes |
| groq-gpt-oss-120b | create | class | English class | 2026-09-21T08:00 | 19 | — | weekly Mon | — | yes |
| groq-gpt-oss-20b | create | class | English class | 2026-09-21T08:00 | 19 | — | weekly Mon | — | yes |
| groq-qwen3.8-27b | create | class | English class | 2026-09-21T08:00 | 19 | — | weekly Mon | — | yes |
| mistral-small | create | class | English class | 2026-09-21T08:00 | 19 | — | — | — | no (recurrence) |
| mistral-medium | create | class | an English class | 2026-09-21T08:00 | 19 | — | weekly Mon | — | no (title) |
| mistral-large | create | class | English class | 2026-09-21T08:00 | 19 | — | weekly Mon | — | yes |
| mistral-ministral-14b | create | class | — | 2026-09-21T08:00 | 19 | — | — | — | no (title) |
| mistral-ministral-8b | create | class | English class | 2026-09-21T08:00 | 19 | — | — | — | no (recurrence) |
| mistral-ministral-3b | create | class | — | 2026-09-21T08:00 | 19 | — | — | — | no (title) |

Every model puts the event at the right instant; `exact` splits them on the label wording (`title`, `location`, `event_type`, `recurrence`) and never on the resolved start time. This is the pattern behind the resolution column: the machine-critical fields resolve, the free-text label often does not.
