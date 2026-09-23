# AI Log

## AI tool

Enviroment - Opencode
Models used - `openai/gpt-5.6-sol` / `openai/gpt-5.6-terra` / `deepseek-ai/DeepSeek-V4.1-Flash`
Skills - caveman/cavecrew, ponytail, unslop

## Initial prompt

```text
/999md-bot

create the skeleton based on this repo. like a bot template unrelated to anything. /admin_help. /start. /list users. add/remove user, increase/decrease rank.
```

## Three most influential prompts

1. "{any_prompt} Don't rush. Don't hesitate to ask any questions."
2. Initial prompt.
3. Elaborate the plan with code snippets to a markdown file regardin implementation of X.

## AI mistakes and corrections

1. Initial prompt did not result in adding deployment Docker skeleton and logging. After the second prompt, he missed a lot of things, such as pagination in /list_users, added an irrelevant /logs command, and made a single log file `bot.log` instead of one per day. Also exposed bot token in terminal. It needed a lot of back and forth to achieve a simple stable telegram template comfortable for me.
2. After asking to use an ASR model to transcribe the messages in telegram - gpt-5.6-sol decided to remove the 7-day retention of the voice messages.
3. The deterministic resolver skiped a lot of edge cases. This helped me to refactor to a more ai driven path of data extraction and resolution.
4. Tutorials AI gave me to wire google calendar were straight up wrong. Googled myself.

## Written manually

1. A bunch of edge cases for the deterministic resolver.
2. This AI log.
3. Tweaking plans written by AI.

## Why it was written manually

To be recorded during development.

## What I would change in the first ten minutes

I should've be more concrete on what I want, instead of me relying on AI assuming it'll understood my request.
