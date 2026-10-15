# EcoTrack AI 2

EcoTrack AI 2 is a climate-action web prototype with a weekly carbon-footprint estimate, personalized tips, sustainability challenges, a progress dashboard, and community leaderboards.

## Run locally

Requires Python 3.10 or newer.

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. The local SQLite database is created as `ecotrack.db` next to `app.py`.

## Deploy to Vercel

1. Push the complete project to a GitHub repository. Keep `app.py`, `requirements.txt`, `vercel.json`, the `api` folder, and the `static` folder at the repository root.
2. In Vercel, import that GitHub repository and deploy it as a Python project using the included `vercel.json`.
3. Optional: add `ANTHROPIC_API_KEY` in **Project Settings → Environment Variables** to enable AI-generated recommendations. You can optionally set `ANTHROPIC_MODEL` to a model ID enabled for your Anthropic account. Without a key, the built-in recommendations are used.
4. Redeploy after changing environment variables.

Test `/api/health` on the deployed domain; it should return `{"app":"EcoTrack AI 2","status":"ok"}`.

## Important deployment limitations

- The name-and-college sign-in is demo-only and is **not secure authentication**. Do not use it for sensitive or private user data.
- Vercel functions have an ephemeral filesystem. This project uses SQLite under `/tmp` on Vercel, so data can disappear and may not be shared across function instances. It is suitable only for a demonstration. For persistent multi-user deployment, replace SQLite with a managed database (for example, PostgreSQL) and implement proper authentication/authorization.
- Carbon values are rough estimates, not audited measurements. Emission factors and assumptions should be sourced and calibrated before making scientific or institutional claims. Electricity and LPG values entered monthly are divided by 4.3 to estimate weekly values.
- AI recommendations require a valid Anthropic API key and a model ID available to that account. The app falls back to rule-based tips if the API fails.

## API health check

`GET /api/health` — confirms the Flask app is responding.

## Main features

- Carbon footprint estimate for transport, energy, food, and waste
- Rule-based recommendations, with optional Anthropic API recommendations
- Saved footprint history and dashboard
- Sustainability challenges and estimated savings
- College and individual leaderboards

## License

Add a license file if you intend to publish or distribute this project.
