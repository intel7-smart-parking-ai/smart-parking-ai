# Chatbot regression

Requires Docker, Python, PowerShell, and the two Markdown questionnaires. The suite starts a disposable API on loopback port 18480, loads synthetic vehicles and alert fixtures, replays all 113 questions, and asserts core safety, payment, and lookup behavior plus 20 wording variants.

```powershell
./tests/chatbot_regression/run.ps1 -QuestionnaireA <path-a> -QuestionnaireB <path-b>
```

Pass `-AgentsEnvFile` to exercise configured LLM providers. The runner prints the temporary results directory. Full questionnaire scoring still requires reviewing the transcripts.
