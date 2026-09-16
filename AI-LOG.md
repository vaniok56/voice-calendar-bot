# AI Log

## AI tool

Enviroment - Opencode
Models used - `openai/gpt-5.6-sol` / `openai/gpt-5.6-terra`.


## Initial prompt

```text
/999md-bot

create the skeleton based on this repo. like a bot template unrelated to anything. /admin_help. /start. /list users. add/remove user, increase/decrease rank.
```

## Three most influential prompts

1. Initial prompt.
2. "{any_prompt} Don't rush. Ask any questions. Don't hesitate."
3. TBD

## AI mistakes and corrections

1. Initial prompt did not result in adding deployment Docker skeleton and logging. After the second prompt, he missed a lot of things, such as pagination in /list_users, added an irrelevant /logs command, and made a single log file `bot.log` instead of one per day. Also exposed bot token in terminal. It needed a lot of back and forth to achieve a simple stable telegram template comfortable for me.

## Written manually

To be recorded during development.

## Why it was written manually

To be recorded during development.

## What I would change in the first ten minutes

To be completed after implementation and testing.
